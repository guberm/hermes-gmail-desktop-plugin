"""Staged, profile-scoped Gmail API for the Hermes dashboard plugin.

The module intentionally does not import the Google Workspace CLI helper: that
helper caches a profile path at import time, exits the process on errors, and
persists refreshed credentials.  This backend resolves the Hermes home for
every call and keeps refreshes in memory only.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from urllib.parse import unquote, urlsplit

from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

import httplib2
import requests
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

def expected_labels_for_action(
    action: str, snapshot: dict[str, Any] | None, payload: dict[str, Any]
) -> set[str]:
    """Compute the exact label state required by mutation readback."""
    if not snapshot or not isinstance(snapshot.get("labelIds"), list):
        raise ValueError("missing message snapshot")
    expected = set(snapshot["labelIds"])
    if action == "trash":
        expected.add("TRASH")
        expected.discard("INBOX")
    elif action == "archive":
        expected.discard("INBOX")
    elif action == "labels":
        expected.update(payload.get("addLabelIds", []))
        expected.difference_update(payload.get("removeLabelIds", []))
    else:
        raise ValueError("unsupported label mutation")
    return expected


router = APIRouter()

_REQUEST_TIMEOUT_SECONDS = 10.0
_TICKET_TTL_SECONDS = 300.0
_MAX_TICKETS = 256
_MAX_SCOPES = 128
_MAX_BODY_BYTES = 256 * 1024
_MAX_PROVIDER_TEXT = 16 * 1024
_MAX_THREAD_MESSAGES = 100
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,256}$")
_RFC_MESSAGE_ID_RE = re.compile(r"^<[^<>\s@]+@[^<>\s@]+>$")
_BACKEND_INSTANCE = secrets.token_urlsafe(24)
_lock = threading.RLock()
_scopes: "OrderedDict[str, ScopeBinding]" = OrderedDict()
_tickets: "OrderedDict[str, Ticket]" = OrderedDict()
_now: Callable[[], float] = time.time

ScopeText = Annotated[str, StringConstraints(min_length=20, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]
MessageId = Annotated[str, StringConstraints(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]
LabelId = Annotated[str, StringConstraints(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SendPrepare(_StrictModel):
    scope: ScopeText
    action: Literal["send"]
    to: Annotated[str, StringConstraints(min_length=3, max_length=4096)]
    cc: Annotated[str, StringConstraints(max_length=4096)]
    subject: Annotated[str, StringConstraints(max_length=998)]
    body: Annotated[str, StringConstraints(max_length=_MAX_BODY_BYTES)]

    @field_validator("to", "cc")
    @classmethod
    def valid_recipients(cls, value: str, info: Any) -> str:
        if info.field_name == "to" or value:
            _parse_addresses(value)
        return value

    @field_validator("subject")
    @classmethod
    def valid_subject(cls, value: str) -> str:
        _reject_header_controls(value)
        return value

    @field_validator("body")
    @classmethod
    def bounded_body_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > _MAX_BODY_BYTES:
            raise ValueError("body is too large")
        return value


class ArchivePrepare(_StrictModel):
    scope: ScopeText
    action: Literal["archive"]
    messageId: MessageId


class TrashPrepare(_StrictModel):
    scope: ScopeText
    action: Literal["trash"]
    messageId: MessageId


class ReplyPrepare(_StrictModel):
    scope: ScopeText
    action: Literal["reply"]
    threadId: MessageId
    to: Annotated[str, StringConstraints(min_length=3, max_length=4096)]
    cc: Annotated[str, StringConstraints(max_length=4096)]
    subject: Annotated[str, StringConstraints(max_length=998)]
    body: Annotated[str, StringConstraints(max_length=_MAX_BODY_BYTES)]

    @field_validator("to", "cc")
    @classmethod
    def valid_recipients(cls, value: str, info: Any) -> str:
        if info.field_name == "to" or value:
            _parse_addresses(value)
        return value

    @field_validator("subject")
    @classmethod
    def valid_subject(cls, value: str) -> str:
        _reject_header_controls(value)
        return value

    @field_validator("body")
    @classmethod
    def bounded_body_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > _MAX_BODY_BYTES:
            raise ValueError("body is too large")
        return value


class LabelsPrepare(_StrictModel):
    scope: ScopeText
    action: Literal["labels"]
    messageId: MessageId
    addLabelIds: Annotated[list[LabelId], Field(max_length=100)]
    removeLabelIds: Annotated[list[LabelId], Field(max_length=100)]

    @field_validator("addLabelIds", "removeLabelIds")
    @classmethod
    def unique_labels(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("label IDs must be unique")
        return value

    @model_validator(mode="after")
    def valid_change(self) -> "LabelsPrepare":
        if not self.addLabelIds and not self.removeLabelIds:
            raise ValueError("at least one label change is required")
        if set(self.addLabelIds) & set(self.removeLabelIds):
            raise ValueError("a label cannot be both added and removed")
        return self


class BatchPrepare(_StrictModel):
    scope: ScopeText
    action: Literal["batch"]
    operation: Literal["archive", "trash", "star", "unstar", "read", "unread"]
    messageIds: Annotated[list[MessageId], Field(min_length=1, max_length=20)]

    @field_validator("messageIds")
    @classmethod
    def unique_message_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("message IDs must be unique")
        return value


PrepareRequest = Annotated[SendPrepare | ReplyPrepare | ArchivePrepare | TrashPrepare | LabelsPrepare | BatchPrepare, Field(discriminator="action")]


class CommitRequest(_StrictModel):
    scope: ScopeText
    confirmationToken: ScopeText
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def explicit_boolean_consent(cls, value: Any) -> bool:
        # Literal[True] otherwise accepts 1 and 1.0 even with strict=True.
        if value is not True:
            raise ValueError('explicit boolean confirmation is required')
        return value


@dataclass(frozen=True)
class ScopeBinding:
    backend: str
    home: str
    account: str
    created: float


@dataclass(frozen=True)
class Ticket:
    scope: str
    action: str
    account: str
    payload: dict[str, Any]
    preview: dict[str, Any]
    message_snapshot: dict[str, Any] | list[dict[str, Any]] | None
    expires: float


class _AuthUnavailable(RuntimeError):
    pass


class _BoundedAuthRequest:
    """google-auth request adapter with a hard timeout and no retry adapter."""

    def __init__(self) -> None:
        from google.auth.transport.requests import Request as GoogleRequest

        session = requests.Session()
        # requests' default HTTPAdapter does not retry requests.
        self._request = GoogleRequest(session=session)

    def __call__(self, url: str, method: str = "GET", body: bytes | None = None,
                 headers: dict[str, str] | None = None, timeout: float | None = None,
                 **kwargs: Any) -> Any:
        del timeout  # Callers cannot widen the backend's bound.
        return self._request(
            url=url,
            method=method,
            body=body,
            headers=headers,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            **kwargs,
        )


class _SingleAttemptAuthorizedHttp:
    """httplib2-compatible transport without 401 replay or HTTP retries."""

    def __init__(self, credentials: Any) -> None:
        self.credentials = credentials
        self.session = requests.Session()
        self.auth_request = _BoundedAuthRequest()

    def request(self, uri: str, method: str = "GET", body: bytes | str | None = None,
                headers: dict[str, str] | None = None, **kwargs: Any) -> tuple[Any, bytes]:
        del kwargs
        request_headers = dict(headers or {})
        # before_request may refresh an already-expired token once, before the
        # Gmail operation.  A 401 response is returned as-is and never replayed.
        self.credentials.before_request(self.auth_request, method, uri, request_headers)
        response = self.session.request(
            method=method,
            url=uri,
            data=body,
            headers=request_headers,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        result = httplib2.Response(dict(response.headers))
        result.status = response.status_code
        result.reason = response.reason
        return result, response.content


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in {"script", "style", "template", "noscript"}:
            self.suppressed += 1
        elif not self.suppressed and tag.lower() in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "template", "noscript"} and self.suppressed:
            self.suppressed -= 1
        elif not self.suppressed and tag.lower() in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line)


_HTML_ALLOWED_TAGS = frozenset({
    "a", "b", "blockquote", "br", "caption", "code", "dd", "del", "div", "dl", "dt",
    "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "li", "ol", "p", "pre",
    "s", "small", "span", "strike", "strong", "sub", "sup", "table", "tbody", "td",
    "tfoot", "th", "thead", "tr", "u", "ul",
})
_HTML_VOID_TAGS = frozenset({"br", "hr"})
_HTML_DROP_CONTENT_TAGS = frozenset({
    "applet", "audio", "button", "canvas", "embed", "form", "frame", "frameset", "head",
    "iframe", "math", "noscript", "object", "script", "select", "style", "svg", "template",
    "textarea", "video",
})
_HTML_DROP_TAGS = frozenset({"base", "input", "link", "meta", "source"})
_HTML_ATTRIBUTE_TAGS = {
    "a": frozenset({"href", "title"}),
    "blockquote": frozenset({"cite"}),
    "td": frozenset({"align", "colspan", "rowspan"}),
    "th": frozenset({"align", "colspan", "rowspan", "scope"}),
    "p": frozenset({"align"}),
    "div": frozenset({"align"}),
    "table": frozenset({"align"}),
}
_HTML_ALIGNMENT = frozenset({"left", "center", "right", "justify"})
_HTML_MAX_OUTPUT_CHARS = 1024 * 1024


def _safe_html_href(value: str | None) -> str | None:
    if not value or value != value.strip() or "\\" in value or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return None
    candidate = value
    if len(candidate) > 4096 or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", candidate, re.IGNORECASE):
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if unquote(scheme).lower() != scheme:
        return None
    if scheme in {"http", "https"}:
        return candidate if parsed.netloc and parsed.hostname and parsed.username is None and parsed.password is None else None
    if scheme == "mailto":
        return candidate if parsed.path and not parsed.netloc else None
    return None


def _safe_html_image_url(value: str | None) -> str | None:
    candidate = _safe_html_href(value)
    if candidate is None:
        return None
    try:
        return candidate if urlsplit(candidate).scheme.lower() == "https" else None
    except ValueError:
        return None


class _InertHtmlSanitizer(HTMLParser):
    """Small fail-closed Gmail body allowlist; no source/style/resource attributes survive."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.open_tags: list[str] = []
        self.suppressed: list[str] = []
        self.output_size = 0

    def _emit(self, value: str) -> None:
        if not value or self.output_size >= _HTML_MAX_OUTPUT_CHARS:
            return
        remaining = _HTML_MAX_OUTPUT_CHARS - self.output_size
        value = value[:remaining]
        self.parts.append(value)
        self.output_size += len(value)

    def _close_open(self, tag: str) -> None:
        if tag not in self.open_tags:
            return
        index = len(self.open_tags) - 1 - self.open_tags[::-1].index(tag)
        for opened in reversed(self.open_tags[index:]):
            self._emit(f"</{opened}>")
        del self.open_tags[index:]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.suppressed:
            if tag in _HTML_DROP_CONTENT_TAGS:
                self.suppressed.append(tag)
            return
        if tag in _HTML_DROP_CONTENT_TAGS:
            self.suppressed.append(tag)
            return
        if tag == "img":
            alt = next((value for key, value in attrs if key.lower() == "alt" and value), "")
            src = next((value for key, value in attrs if key.lower() == "src" and value), None)
            safe_src = _safe_html_image_url(src)
            safe_attrs = [("alt", alt[:512])] if alt else []
            if safe_src:
                safe_attrs.append(("data-email-src", safe_src))
            attr_text = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in safe_attrs)
            self._emit(f"<img{attr_text}>")
            return
        if tag in _HTML_DROP_TAGS or tag not in _HTML_ALLOWED_TAGS:
            return
        if tag in {"p", "li", "tr"}:
            self._close_open(tag)
        elif tag in {"td", "th"}:
            self._close_open("td")
            self._close_open("th")
        safe_attrs: list[tuple[str, str]] = []
        allowed = _HTML_ATTRIBUTE_TAGS.get(tag, frozenset())
        for name, value in attrs:
            name = name.lower()
            if (name != "title" and name not in allowed) or value is None:
                continue
            value = value.strip()
            if name == "href":
                value = _safe_html_href(value)
                if value is None:
                    continue
            elif name in {"rowspan", "colspan"}:
                if not value.isdecimal() or not 1 <= int(value) <= 100:
                    continue
            elif name == "align":
                value = value.lower()
                if value not in _HTML_ALIGNMENT:
                    continue
            elif name == "scope":
                value = value.lower()
                if value not in {"row", "col", "rowgroup", "colgroup"}:
                    continue
            elif name == "cite":
                value = _safe_html_href(value)
                if value is None:
                    continue
            elif name == "title":
                value = value[:1024]
            safe_attrs.append((name, value))
        if tag == "a" and any(name == "href" for name, _ in safe_attrs):
            safe_attrs.extend((name, value) for name, value in (("rel", "noopener noreferrer nofollow"),))
        attr_text = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in safe_attrs)
        self._emit(f"<{tag}{attr_text}>")
        if tag not in _HTML_VOID_TAGS:
            self.open_tags.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.suppressed:
            if tag in self.suppressed:
                index = len(self.suppressed) - 1 - self.suppressed[::-1].index(tag)
                del self.suppressed[index:]
            return
        if tag not in self.open_tags:
            return
        self._close_open(tag)

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self._emit(html.escape(data, quote=False))

    def result(self) -> str:
        for opened in reversed(self.open_tags):
            self._emit(f"</{opened}>")
        self.open_tags.clear()
        return "".join(self.parts)


def sanitize_email_html(source: str) -> str:
    """Return inert formatting markup; drop active elements, CSS and resource loads."""
    parser = _InertHtmlSanitizer()
    try:
        parser.feed(source[:_MAX_BODY_BYTES])
        parser.close()
        return parser.result()
    except Exception:
        return ""


def _extract_html_body(payload: Any) -> str:
    rich: list[tuple[str, bool]] = []
    _walk_parts(payload, "text/html", rich)
    return sanitize_email_html(rich[0][0]) if rich else ""


def _current_home() -> str:
    from hermes_constants import get_hermes_home

    return str(Path(get_hermes_home()).expanduser().resolve(strict=False))


def _load_credentials() -> Any:
    """Resolve and load credentials for the call's active profile only."""
    from google.oauth2.credentials import Credentials

    token_path = Path(_current_home()) / "google_token.json"
    if not token_path.is_file():
        raise _AuthUnavailable("missing credentials")
    # Deliberately omit scopes: preserve exactly the grants stored by setup.
    credentials = Credentials.from_authorized_user_file(str(token_path))
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(_BoundedAuthRequest())  # memory only; never write token_path
    if not credentials.valid:
        raise _AuthUnavailable("invalid credentials")
    return credentials


def _build_gmail_service() -> Any:
    from googleapiclient.discovery import build

    return build(
        "gmail",
        "v1",
        http=_SingleAttemptAuthorizedHttp(_load_credentials()),
        cache_discovery=False,
        static_discovery=True,
        num_retries=0,
    )


_service_factory: Callable[[], Any] = _build_gmail_service


def _execute(request: Any) -> dict[str, Any]:
    result = request.execute(num_retries=0)
    if not isinstance(result, dict):
        raise ValueError("invalid provider response")
    return result


def _provider_error(*, mutation_started: bool = False) -> HTTPException:
    if mutation_started:
        return HTTPException(
            status_code=502,
            detail="Action outcome is uncertain. Check Gmail before trying again.",
        )
    return HTTPException(status_code=502, detail="Gmail service is unavailable.")


def _provider_context(binding: ScopeBinding | None = None) -> tuple[Any, str, str]:
    home = _current_home()
    try:
        service = _service_factory()
        profile = _execute(service.users().getProfile(userId="me"))
        account = _clean_provider_text(profile.get("emailAddress"), 320)
        if not account or "@" not in account or "\r" in account or "\n" in account:
            raise ValueError("invalid account")
    except HTTPException:
        raise
    except Exception:
        raise _provider_error() from None
    if binding and (binding.backend != _BACKEND_INSTANCE or binding.home != home or binding.account != account):
        raise HTTPException(status_code=409, detail="Gmail connection changed; refresh status.")
    return service, account, home


def _reject_query_extras(request: Request, allowed: set[str]) -> None:
    # Hermes consumes profile as its authenticated routing envelope.
    extras = set(request.query_params.keys()) - allowed - {"profile"}
    if extras:
        raise HTTPException(status_code=422, detail="Unexpected query parameter.")


def _binding(scope: str) -> ScopeBinding:
    if not _TOKEN_RE.fullmatch(scope):
        raise HTTPException(status_code=422, detail="Invalid scope.")
    home = _current_home()
    with _lock:
        binding = _scopes.get(scope)
        if binding:
            _scopes.move_to_end(scope)
    if not binding or binding.backend != _BACKEND_INSTANCE or binding.home != home:
        raise HTTPException(status_code=409, detail="Gmail connection changed; refresh status.")
    return binding


def _new_scope(home: str, account: str) -> str:
    scope = secrets.token_urlsafe(32)
    binding = ScopeBinding(_BACKEND_INSTANCE, home, account, _now())
    with _lock:
        _scopes[scope] = binding
        while len(_scopes) > _MAX_SCOPES:
            _scopes.popitem(last=False)
    return scope


def _store_ticket(ticket: Ticket) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with _lock:
        for old_token in [key for key, value in _tickets.items() if value.expires <= now]:
            _tickets.pop(old_token, None)
        _tickets[token] = ticket
        while len(_tickets) > _MAX_TICKETS:
            _tickets.popitem(last=False)
    return token


def _consume_ticket(token: str, scope: str) -> Ticket:
    # Pop first: every commit attempt consumes the ticket, including stale or
    # failed operations, so callers cannot unknowingly replay a mutation.
    with _lock:
        ticket = _tickets.pop(token, None)
    if not ticket or ticket.scope != scope or ticket.expires <= _now():
        raise HTTPException(status_code=409, detail="Confirmation expired or already used.")
    return ticket


def _reject_header_controls(value: str) -> None:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ValueError("header controls are not allowed")


def _parse_addresses(value: str) -> tuple[str, ...]:
    _reject_header_controls(value)
    message = EmailMessage(policy=policy.default)
    try:
        message["To"] = value
        header = message["To"]
        addresses = tuple(
            f"{address.username}@{address.domain}".lower()
            for address in header.addresses
            if address.username and address.domain
        )
    except Exception as exc:
        raise ValueError("invalid recipient list") from exc
    if getattr(header, "defects", ()) or not addresses:
        raise ValueError("invalid recipient list")
    # Ensure no parsed mailbox silently disappeared.
    if any(any(ch.isspace() for ch in mailbox) for mailbox in addresses):
        raise ValueError("invalid recipient list")
    return addresses


def _clean_provider_text(value: Any, limit: int = _MAX_PROVIDER_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _header_map(payload: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(payload, dict):
        return result
    headers = payload.get("headers", [])
    if not isinstance(headers, list):
        return result
    for item in headers[:200]:
        if isinstance(item, dict):
            name = _clean_provider_text(item.get("name"), 128).lower()
            if name in {"from", "to", "cc", "subject", "date", "message-id", "in-reply-to", "references"} and name not in result:
                result[name] = _clean_provider_text(item.get("value"))
    return result


def _public_message(raw: dict[str, Any]) -> dict[str, Any]:
    message_id = _clean_provider_text(raw.get("id"), 256)
    thread_id = _clean_provider_text(raw.get("threadId"), 256)
    if not _ID_RE.fullmatch(message_id) or not _ID_RE.fullmatch(thread_id):
        raise ValueError("invalid message identity")
    headers = _header_map(raw.get("payload"))
    labels = raw.get("labelIds", [])
    if not isinstance(labels, list):
        labels = []
    label_ids = [item for item in labels[:500] if isinstance(item, str) and _ID_RE.fullmatch(item)]
    return {
        "id": message_id,
        "threadId": thread_id,
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "snippet": _clean_provider_text(raw.get("snippet")),
        "labelIds": label_ids,
    }


def _decode_body_data(value: Any) -> tuple[str, bool]:
    if not isinstance(value, str) or not value:
        return "", False
    # Bound encoded input before decoding to avoid provider-controlled allocation.
    max_encoded = ((_MAX_BODY_BYTES + 2) // 3) * 4
    was_truncated = len(value.rstrip("=")) > max_encoded
    encoded = value[:max_encoded]
    encoded += "=" * (-len(encoded) % 4)
    try:
        decoded = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return "", False
    if len(decoded) > _MAX_BODY_BYTES:
        decoded = decoded[:_MAX_BODY_BYTES]
        was_truncated = True
    return decoded.decode("utf-8", errors="replace"), was_truncated


def _walk_parts(part: Any, mime: str, found: list[tuple[str, bool]]) -> None:
    if not isinstance(part, dict) or found:
        return
    if _clean_provider_text(part.get("filename"), 1024):
        return  # Never interpret an attachment as the message body.
    part_mime = _clean_provider_text(part.get("mimeType"), 128).lower()
    body = part.get("body")
    if part_mime == mime and isinstance(body, dict) and body.get("data"):
        found.append(_decode_body_data(body.get("data")))
        return
    parts = part.get("parts", [])
    if isinstance(parts, list):
        for child in parts[:200]:
            _walk_parts(child, mime, found)
            if found:
                return


def _extract_body(payload: Any) -> tuple[str, bool]:
    plain: list[tuple[str, bool]] = []
    _walk_parts(payload, "text/plain", plain)
    if plain:
        text, truncated = plain[0]
    else:
        rich: list[tuple[str, bool]] = []
        _walk_parts(payload, "text/html", rich)
        if rich:
            parser = _TextExtractor()
            try:
                parser.feed(rich[0][0])
                parser.close()
                text = html.unescape(parser.text())
                truncated = rich[0][1]
            except Exception:
                text, truncated = "", False
        else:
            text, truncated = "", False
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_BODY_BYTES:
        text = encoded[:_MAX_BODY_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    return text, truncated


def _get_message(service: Any, message_id: str, *, full: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    params: dict[str, Any] = {"userId": "me", "id": message_id, "format": "full" if full else "metadata"}
    if not full:
        params["metadataHeaders"] = ["From", "To", "Cc", "Subject", "Date", "Message-ID", "In-Reply-To", "References"]
    raw = _execute(service.users().messages().get(**params))
    public = _public_message(raw)
    if public["id"] != message_id:
        raise ValueError("message identity mismatch")
    if full:
        body, truncated = _extract_body(raw.get("payload", {}))
        public["body"] = body
        public["htmlBody"] = _extract_html_body(raw.get("payload", {}))
        if truncated:
            public["bodyTruncated"] = True
    return raw, public


def _thread_items(raw_thread: dict[str, Any], thread_id: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    messages = raw_thread.get("messages", [])
    if not isinstance(messages, list) or len(messages) > _MAX_THREAD_MESSAGES:
        raise ValueError("invalid or oversized thread")
    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw in messages:
        if not isinstance(raw, dict):
            raise ValueError("invalid thread message")
        public = _public_message(raw)
        if public["threadId"] != thread_id:
            raise ValueError("thread identity mismatch")
        body, truncated = _extract_body(raw.get("payload", {}))
        public["body"] = body
        public["htmlBody"] = _extract_html_body(raw.get("payload", {}))
        if truncated:
            public["bodyTruncated"] = True
        items.append((raw, public))
    if not items:
        raise ValueError("empty thread")
    # Gmail normally returns thread messages chronologically, but do not base
    # reply headers on provider array order. internalDate is the canonical
    # received/sent timestamp and is bounded numeric metadata from Gmail.
    def chronology(item: tuple[dict[str, Any], dict[str, Any]]) -> int:
        value = item[0].get("internalDate")
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
    items.sort(key=chronology)  # Python's stable sort preserves provider order for ties.
    return items


def _get_thread(service: Any, thread_id: str) -> tuple[dict[str, Any], list[tuple[dict[str, Any], dict[str, Any]]]]:
    raw = _execute(service.users().threads().get(userId="me", id=thread_id, format="full"))
    returned_id = _clean_provider_text(raw.get("id"), 256)
    if returned_id != thread_id or not _ID_RE.fullmatch(returned_id):
        raise ValueError("thread identity mismatch")
    return raw, _thread_items(raw, thread_id)


def _valid_rfc_message_id(value: Any) -> str:
    candidate = _clean_provider_text(value, 998).strip()
    if not _RFC_MESSAGE_ID_RE.fullmatch(candidate):
        raise ValueError("invalid RFC message ID")
    return candidate


def _thread_reply_headers(latest_raw: dict[str, Any]) -> tuple[str, str]:
    headers = _header_map(latest_raw.get("payload"))
    in_reply_to = _valid_rfc_message_id(headers.get("message-id", ""))
    references_value = headers.get("references", "").strip()
    _reject_header_controls(references_value)
    refs: list[str] = []
    if references_value:
        tokens = references_value.split()
        if len(tokens) > 100:
            tokens = tokens[-100:]
        refs.extend(_valid_rfc_message_id(token) for token in tokens)
    if not refs or refs[-1] != in_reply_to:
        refs.append(in_reply_to)
    references = " ".join(dict.fromkeys(refs))
    if len(references) > _MAX_PROVIDER_TEXT:
        raise ValueError("References header too large")
    return in_reply_to, references



def _list_user_labels(service: Any) -> list[dict[str, str]]:
    raw = _execute(service.users().labels().list(userId="me"))
    items = raw.get("labels", [])
    if not isinstance(items, list):
        raise ValueError("invalid labels")
    labels: list[dict[str, str]] = []
    for item in items[:1000]:
        if not isinstance(item, dict):
            continue
        label_id = _clean_provider_text(item.get("id"), 256)
        name = _clean_provider_text(item.get("name"), 1024)
        kind = _clean_provider_text(item.get("type"), 64)
        if _ID_RE.fullmatch(label_id) and name and kind in {"user", "system"}:
            labels.append({"id": label_id, "name": name, "type": kind})
    return labels


def _mime_raw(payload: dict[str, Any]) -> str:
    message = EmailMessage(policy=policy.SMTP)
    message["To"] = payload["to"]
    if payload["cc"]:
        message["Cc"] = payload["cc"]
    message["Subject"] = payload["subject"]
    if payload.get("inReplyTo"):
        message["In-Reply-To"] = payload["inReplyTo"]
        message["References"] = payload["references"]
    # set_content appends a newline; set_payload preserves the exact reviewed
    # body while still producing a standards-compliant text/plain MIME part.
    message.set_type("text/plain")
    message.set_param("charset", "utf-8")
    message.set_payload(payload["body"], charset="utf-8")
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _message_snapshot(message: dict[str, Any]) -> dict[str, Any]:
    return {key: (sorted(value) if key == "labelIds" else value) for key, value in message.items()}


@router.get("/status")
def status(request: Request) -> dict[str, str]:
    _reject_query_extras(request, set())
    service, account, home = _provider_context()
    del service
    with _lock:
        scope = next((key for key, value in _scopes.items() if value.home == home and value.account == account), None)
        if scope is None:
            scope = _new_scope(home, account)
    return {"scope": scope, "account": account}


@router.get("/search")
def search(
    request: Request,
    scope: ScopeText,
    q: Annotated[str, Query(max_length=512)] = "",
    maxResults: Annotated[int, Query(ge=1, le=50)] = 20,
    pageToken: Annotated[str, Query(max_length=2048)] = "",
) -> dict[str, Any]:
    _reject_query_extras(request, {"scope", "q", "maxResults", "pageToken"})
    binding = _binding(scope)
    service, _, _ = _provider_context(binding)
    try:
        params: dict[str, Any] = {"userId": "me", "q": q, "maxResults": maxResults}
        if pageToken:
            params["pageToken"] = pageToken
        result = _execute(service.users().messages().list(**params))
        metas = result.get("messages", [])
        if not isinstance(metas, list):
            raise ValueError("invalid messages")
        messages: list[dict[str, Any]] = []
        for meta in metas[:maxResults]:
            if not isinstance(meta, dict) or not _ID_RE.fullmatch(str(meta.get("id", ""))):
                continue
            _, item = _get_message(service, str(meta["id"]))
            messages.append(item)
        response: dict[str, Any] = {"messages": messages}
        next_token = _clean_provider_text(result.get("nextPageToken"), 2048)
        if next_token:
            response["nextPageToken"] = next_token
        return response
    except Exception:
        raise _provider_error() from None


@router.get("/messages/{message_id}")
def message_detail(request: Request, message_id: MessageId, scope: ScopeText) -> dict[str, Any]:
    _reject_query_extras(request, {"scope"})
    binding = _binding(scope)
    service, _, _ = _provider_context(binding)
    try:
        _, message = _get_message(service, message_id, full=True)
        return message
    except Exception:
        raise _provider_error() from None


@router.get("/threads/{thread_id}")
def thread_detail(request: Request, thread_id: MessageId, scope: ScopeText) -> dict[str, Any]:
    _reject_query_extras(request, {"scope"})
    binding = _binding(scope)
    service, _, _ = _provider_context(binding)
    try:
        raw, items = _get_thread(service, thread_id)
        return {
            "id": thread_id,
            "historyId": _clean_provider_text(raw.get("historyId"), 256),
            "messages": [public for _, public in items],
        }
    except Exception:
        raise _provider_error() from None


@router.get("/labels")
def labels(request: Request, scope: ScopeText) -> dict[str, Any]:
    _reject_query_extras(request, {"scope"})
    binding = _binding(scope)
    service, _, _ = _provider_context(binding)
    try:
        return {"labels": _list_user_labels(service)}
    except Exception:
        raise _provider_error() from None


@router.post("/actions/prepare")
def prepare_action(request: Request, body: PrepareRequest) -> dict[str, Any]:
    _reject_query_extras(request, set())
    binding = _binding(body.scope)
    service, account, _ = _provider_context(binding)
    expires = _now() + _TICKET_TTL_SECONDS
    snapshot: dict[str, Any] | list[dict[str, Any]] | None = None
    try:
        if isinstance(body, SendPrepare):
            payload = {"to": body.to, "cc": body.cc, "subject": body.subject, "body": body.body}
            preview: dict[str, Any] = {"action": "send", "account": account, **payload}
        elif isinstance(body, ReplyPrepare):
            _, items = _get_thread(service, body.threadId)
            latest_raw, latest = items[-1]
            in_reply_to, references = _thread_reply_headers(latest_raw)
            payload = {
                "threadId": body.threadId,
                "to": body.to,
                "cc": body.cc,
                "subject": body.subject,
                "body": body.body,
                "inReplyTo": in_reply_to,
                "references": references,
                "latestMessageId": latest["id"],
            }
            preview = {
                "action": "reply",
                "account": account,
                "threadId": body.threadId,
                "latestMessage": latest,
                "to": body.to,
                "cc": body.cc,
                "subject": body.subject,
                "body": body.body,
            }
        elif isinstance(body, BatchPrepare):
            operation = body.operation
            if operation == "archive":
                add_ids, remove_ids = [], ["INBOX"]
            elif operation == "star":
                add_ids, remove_ids = ["STARRED"], []
            elif operation == "unstar":
                add_ids, remove_ids = [], ["STARRED"]
            elif operation == "unread":
                add_ids, remove_ids = ["UNREAD"], []
            elif operation == "read":
                add_ids, remove_ids = [], ["UNREAD"]
            else:
                add_ids, remove_ids = [], []
            all_labels = _list_user_labels(service)
            labels_by_id = {item["id"]: item for item in all_labels}
            if any(label_id not in labels_by_id or labels_by_id[label_id]["type"] != "system"
                   for label_id in add_ids + remove_ids):
                raise HTTPException(status_code=422, detail="Required Gmail system label is unavailable.")
            messages: list[dict[str, Any]] = []
            snapshots: list[dict[str, Any]] = []
            for message_id in body.messageIds:
                _, message = _get_message(service, message_id)
                if operation == "archive" and "INBOX" not in message["labelIds"]:
                    raise HTTPException(status_code=409, detail="Every selected message must still be in the inbox.")
                if operation == "trash" and "TRASH" in message["labelIds"]:
                    raise HTTPException(status_code=409, detail="A selected message is already in Trash.")
                messages.append(message)
                snapshots.append(_message_snapshot(message))
            payload = {"operation": operation, "messageIds": list(body.messageIds),
                       "addLabelIds": add_ids, "removeLabelIds": remove_ids}
            preview = {"action": "batch", "operation": operation, "account": account,
                       "messages": messages, "labels": [labels_by_id[label_id] for label_id in add_ids + remove_ids],
                       "effect": f"{operation.title()} {len(messages)} selected messages"}
            snapshot = snapshots
        elif isinstance(body, ArchivePrepare):
            _, message = _get_message(service, body.messageId)
            if "INBOX" not in message["labelIds"]:
                raise HTTPException(status_code=409, detail="Message is no longer in the inbox.")
            snapshot = _message_snapshot(message)
            payload = {"messageId": body.messageId, "removeLabelIds": ["INBOX"]}
            preview = {
                "action": "archive",
                "account": account,
                "message": message,
                "removeLabels": [{"id": "INBOX", "name": "Inbox"}],
            }
        elif isinstance(body, TrashPrepare):
            _, message = _get_message(service, body.messageId)
            if "TRASH" in message["labelIds"]:
                raise HTTPException(status_code=409, detail="Message is already in Trash.")
            snapshot = _message_snapshot(message)
            payload = {"messageId": body.messageId}
            preview = {
                "action": "trash",
                "account": account,
                "message": message,
                "effect": "Move this message to Trash",
            }
        else:
            all_labels = _list_user_labels(service)
            by_id = {item["id"]: item for item in all_labels if item["type"] == "user"}
            requested = set(body.addLabelIds) | set(body.removeLabelIds)
            # UNREAD and STARRED are the only system-label changes surfaced
            # by the Desktop More/card controls. Other system labels are immutable.
            system_labels = {item["id"]: item for item in all_labels if item["type"] == "system"}
            allowed_system = {item for item in ("UNREAD", "STARRED") if item in system_labels}
            allowed = set(by_id) | allowed_system
            if not requested.issubset(allowed):
                raise HTTPException(status_code=422, detail="Only existing user labels, UNREAD, and STARRED may be changed.")
            for label_id in requested & allowed_system:
                by_id[label_id] = system_labels[label_id]
            _, message = _get_message(service, body.messageId)
            snapshot = _message_snapshot(message)
            payload = {
                "messageId": body.messageId,
                "addLabelIds": list(body.addLabelIds),
                "removeLabelIds": list(body.removeLabelIds),
            }
            preview = {
                "action": "labels",
                "account": account,
                "message": message,
                "addLabelIds": list(body.addLabelIds),
                "removeLabelIds": list(body.removeLabelIds),
                "addLabels": [by_id[item] for item in body.addLabelIds],
                "removeLabels": [by_id[item] for item in body.removeLabelIds],
            }
    except HTTPException:
        raise
    except Exception:
        raise _provider_error() from None

    ticket = Ticket(body.scope, body.action, account, payload, preview, snapshot, expires)
    token = _store_ticket(ticket)
    return {"scope": body.scope, "confirmationToken": token, "expiresAt": expires, "preview": preview}


@router.post("/actions/commit")
def commit_action(request: Request, body: CommitRequest) -> dict[str, Any]:
    _reject_query_extras(request, set())
    binding = _binding(body.scope)
    ticket = _consume_ticket(body.confirmationToken, body.scope)
    # Account/home verification occurs after consumption and before any effect.
    service, account, _ = _provider_context(binding)
    if ticket.account != account:
        raise HTTPException(status_code=409, detail="Gmail connection changed; refresh status.")

    payload = ticket.payload
    if ticket.action == "batch":
        snapshots = ticket.message_snapshot
        if not isinstance(snapshots, list) or len(snapshots) != len(payload["messageIds"]):
            raise HTTPException(status_code=409, detail="Batch review is incomplete; action not performed.")
        current_messages: list[dict[str, Any]] = []
        for message_id, reviewed in zip(payload["messageIds"], snapshots, strict=True):
            try:
                _, current = _get_message(service, message_id)
            except Exception:
                raise HTTPException(status_code=502, detail="Action not performed; Gmail verification failed.") from None
            if _message_snapshot(current) != reviewed:
                raise HTTPException(status_code=409, detail="A selected message changed after confirmation; action not performed.")
            current_messages.append(current)
        if payload["addLabelIds"] or payload["removeLabelIds"]:
            try:
                current_labels = {item["id"]: item for item in _list_user_labels(service)}
            except Exception:
                raise HTTPException(status_code=502, detail="Action not performed; Gmail verification failed.") from None
            if any(current_labels.get(item["id"]) != item for item in ticket.preview["labels"]):
                raise HTTPException(status_code=409, detail="Gmail labels changed after confirmation; action not performed.")
    elif ticket.action in {"archive", "trash", "labels"}:
        try:
            _, current = _get_message(service, payload["messageId"])
        except Exception:
            raise HTTPException(status_code=502, detail="Action not performed; Gmail verification failed.") from None
        if _message_snapshot(current) != ticket.message_snapshot:
            raise HTTPException(status_code=409, detail="Message changed after confirmation; action not performed.")
        if ticket.action == 'labels':
            try:
                current_labels = {item['id']: item for item in _list_user_labels(service)}
            except Exception:
                raise HTTPException(status_code=502, detail='Action not performed; Gmail verification failed.') from None
            reviewed_labels = ticket.preview['addLabels'] + ticket.preview['removeLabels']
            if any(current_labels.get(item['id']) != item for item in reviewed_labels):
                raise HTTPException(status_code=409, detail='Labels changed after confirmation; action not performed.')

    if ticket.action == "reply":
        try:
            _, current_items = _get_thread(service, payload["threadId"])
            current_raw, current_latest = current_items[-1]
            current_in_reply_to, current_references = _thread_reply_headers(current_raw)
        except Exception:
            raise HTTPException(status_code=502, detail="Action not performed; Gmail thread verification failed.") from None
        if (
            current_latest["id"] != payload["latestMessageId"]
            or current_in_reply_to != payload["inReplyTo"]
            or current_references != payload["references"]
        ):
            raise HTTPException(status_code=409, detail="Thread changed after confirmation; action not performed.")

    try:
        if ticket.action == "batch":
            results: list[dict[str, Any]] = []
            for message_id in payload["messageIds"]:
                if payload["operation"] == "trash":
                    result = _execute(service.users().messages().trash(userId="me", id=message_id))
                else:
                    result = _execute(service.users().messages().modify(
                        userId="me", id=message_id,
                        body={"addLabelIds": payload["addLabelIds"], "removeLabelIds": payload["removeLabelIds"]},
                    ))
                if _clean_provider_text(result.get("id"), 256) != message_id:
                    raise ValueError("invalid batch result")
                results.append(result)
        elif ticket.action in {"send", "reply"}:
            send_body: dict[str, Any] = {"raw": _mime_raw(payload)}
            if ticket.action == "reply":
                send_body["threadId"] = payload["threadId"]
            result = _execute(
                service.users().messages().send(
                    userId="me",
                    body=send_body,
                )
            )
        elif ticket.action == "trash":
            result = _execute(
                service.users().messages().trash(
                    userId="me",
                    id=payload["messageId"],
                )
            )
        else:
            modify_body = {
                "addLabelIds": payload.get("addLabelIds", []),
                "removeLabelIds": payload["removeLabelIds"],
            }
            result = _execute(
                service.users().messages().modify(
                    userId="me",
                    id=payload["messageId"],
                    body=modify_body,
                )
            )
    except Exception:
        # A provider error can happen after Gmail applied the current member.
        # Read every reviewed member before returning an uncertain outcome; do
        # not retry or continue issuing mutations from this consumed ticket.
        if ticket.action == "batch":
            for message_id in payload["messageIds"]:
                try:
                    _get_message(service, message_id)
                except Exception:
                    pass
        raise _provider_error(mutation_started=True) from None

    # Read back from Gmail; the mutation response itself is not treated as proof.
    try:
        if ticket.action == "batch":
            verified_batch: list[dict[str, Any]] = []
            readback_failed = False
            for message_id, snapshot in zip(payload["messageIds"], ticket.message_snapshot, strict=True):
                try:
                    _, verified_message = _get_message(service, message_id)
                    expected = expected_labels_for_action(
                        "trash" if payload["operation"] == "trash" else "labels", snapshot, payload
                    )
                    if verified_message["id"] != message_id or set(verified_message["labelIds"]) != expected:
                        readback_failed = True
                    verified_batch.append(verified_message)
                except Exception:
                    readback_failed = True
            if readback_failed or len(verified_batch) != len(payload["messageIds"]):
                raise ValueError("one or more batch members failed readback")
            response = {"status": "verified", "id": verified_batch[0]["id"], "ids": [m["id"] for m in verified_batch]}
            return response
        result_id = _clean_provider_text(result.get("id"), 256)
        if not _ID_RE.fullmatch(result_id) or (ticket.action in {"archive", "trash", "labels"} and result_id != payload["messageId"]):
            raise ValueError("invalid result")
        raw_verified, verified = _get_message(service, result_id, full=ticket.action in {"send", "reply"})
        if ticket.action in {"send", "reply"}:
            verified_headers = _header_map(raw_verified.get("payload"))
            if (
                verified.get("subject") != payload["subject"]
                or verified.get("body") != payload["body"]
                or _parse_addresses(verified.get("to", "")) != _parse_addresses(payload["to"])
                or (tuple() if not payload["cc"] else _parse_addresses(payload["cc"]))
                != (tuple() if not verified_headers.get("cc", "") else _parse_addresses(verified_headers["cc"]))
            ):
                raise ValueError("sent message mismatch")
            if ticket.action == "reply":
                if (
                    verified["threadId"] != payload["threadId"]
                    or _valid_rfc_message_id(verified_headers.get("in-reply-to", "")) != payload["inReplyTo"]
                    or " ".join(_valid_rfc_message_id(item) for item in verified_headers.get("references", "").split())
                    != payload["references"]
                ):
                    raise ValueError("threaded reply mismatch")
        else:
            expected = expected_labels_for_action(ticket.action, ticket.message_snapshot, payload)
            if set(verified["labelIds"]) != expected:
                raise ValueError("label state mismatch")
    except Exception:
        raise _provider_error(mutation_started=True) from None

    response: dict[str, Any] = {"status": "verified", "id": verified["id"]}
    if verified.get("threadId"):
        response["threadId"] = verified["threadId"]
    return response
