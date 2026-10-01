from __future__ import annotations

import base64
import importlib.util
import json
import sys

import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
import os
HERMES_SOURCE = Path(os.environ["HERMES_SOURCE"])
if str(HERMES_SOURCE) not in sys.path:
    sys.path.insert(0, str(HERMES_SOURCE))

spec = importlib.util.spec_from_file_location(
    "gmail_plugin_api", REPO / "plugins/gmail/dashboard/plugin_api.py"
)
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)

from hermes_constants import reset_hermes_home_override, set_hermes_home_override


def setUpModule():
    # A regression must never turn an offline test into a real network call.
    network = patch('socket.socket.connect', side_effect=AssertionError('Network forbidden in offline tests'))
    network.start()
    unittest.addModuleCleanup(network.stop)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(mid="m1", labels=None, body="plain body", html=None, attachment=False):
    headers = [
        {"name": "From", "value": "Sender <sender@example.com>"},
        {"name": "To", "value": "user@example.com"},
        {"name": "Subject", "value": "Subject " + mid},
        {"name": "Date", "value": "Sat, 26 Sep 2026 12:00:00 -0400"},
    ]
    parts = []
    if body is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(body)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    if attachment:
        parts.insert(0, {"mimeType": "text/plain", "filename": "secret.txt", "body": {"data": b64("attachment secret")}})
    return {
        "id": mid,
        "threadId": "t" + mid,
        "labelIds": list(labels if labels is not None else ["INBOX", "L1"]),
        "snippet": "snippet <untrusted>",
        "payload": {"mimeType": "multipart/alternative", "headers": headers, "parts": parts},
    }


class FakeRequest:
    def __init__(self, fake, kind, fn):
        self.fake, self.kind, self.fn = fake, kind, fn

    def execute(self, num_retries=None):
        self.fake.executions.append((self.kind, num_retries))
        if num_retries != 0:
            raise AssertionError("all provider requests must explicitly disable retries")
        failure = self.fake.failures.pop(self.kind, None)
        if failure:
            raise RuntimeError(failure)
        return self.fn()


class FakeMessages:
    def __init__(self, fake):
        self.f = fake

    def list(self, **kwargs):
        self.f.calls.append(("list", kwargs))
        return FakeRequest(self.f, "list", lambda: {
            "messages": [{"id": key} for key in list(self.f.messages)[:kwargs["maxResults"]]],
            **({"nextPageToken": "next-token"} if self.f.next_page else {}),
        })

    def get(self, **kwargs):
        self.f.calls.append(("get", kwargs))
        return FakeRequest(self.f, "get", lambda: self.f.messages[kwargs["id"]])

    def send(self, **kwargs):
        self.f.calls.append(("send", kwargs))

        def send():
            self.f.mutations += 1
            raw = base64.urlsafe_b64decode(kwargs["body"]["raw"] + "===")
            parsed = BytesParser(policy=policy.default).parsebytes(raw)
            new = message("sent1", labels=["SENT"], body=parsed.get_content())
            new["payload"]["headers"] = [
                {"name": "From", "value": self.f.account},
                {"name": "To", "value": str(parsed["To"])},
                {"name": "Cc", "value": str(parsed["Cc"] or "")},
                {"name": "Subject", "value": str(parsed["Subject"])},
                {"name": "Date", "value": "now"},
            ]
            self.f.messages["sent1"] = new
            return {"id": "sent1", "threadId": "tsent1"}

        return FakeRequest(self.f, "send", send)

    def modify(self, **kwargs):
        self.f.calls.append(("modify", kwargs))

        def modify():
            self.f.mutations += 1
            current = self.f.messages[kwargs["id"]]
            labels = set(current["labelIds"])
            labels.update(kwargs["body"].get("addLabelIds", []))
            labels.difference_update(kwargs["body"].get("removeLabelIds", []))
            current["labelIds"] = sorted(labels)
            return {"id": current["id"], "threadId": current["threadId"], "labelIds": current["labelIds"]}

        return FakeRequest(self.f, "modify", modify)


class FakeLabels:
    def __init__(self, fake): self.f = fake
    def list(self, **kwargs):
        self.f.calls.append(("labels", kwargs))
        return FakeRequest(self.f, "labels", lambda: {"labels": self.f.labels})


class FakeUsers:
    def __init__(self, fake): self.f = fake
    def getProfile(self, **kwargs):
        self.f.calls.append(("profile", kwargs))
        return FakeRequest(self.f, "profile", lambda: {"emailAddress": self.f.account})
    def messages(self): return FakeMessages(self.f)
    def labels(self): return FakeLabels(self.f)


class FakeService:
    def __init__(self, account):
        self.account = account
        self.messages = {"m1": message()}
        self.labels = [
            {"id": "INBOX", "name": "Inbox", "type": "system"},
            {"id": "L1", "name": "Projects", "type": "user"},
            {"id": "L2", "name": "Later", "type": "user"},
        ]
        self.calls = []
        self.executions = []
        self.failures = {}
        self.mutations = 0
        self.next_page = True
    def users(self): return FakeUsers(self)


class BackendTests(unittest.TestCase):
    def setUp(self):
        api._scopes.clear()
        api._tickets.clear()
        self.clock = 1000.0
        api._now = lambda: self.clock
        self.a = Path("/profiles/A")
        self.b = Path("/profiles/B")
        self.services = {
            str(self.a): FakeService("a@example.com"),
            str(self.b): FakeService("b@example.com"),
        }
        self.home_token = set_hermes_home_override(self.a)
        api._service_factory = lambda: self.services[api._current_home()]
        app = FastAPI()
        app.include_router(api.router, prefix="/api/plugins/gmail")
        self.client = TestClient(app)

    def tearDown(self):
        reset_hermes_home_override(self.home_token)
        api._service_factory = api._build_gmail_service
        api._now = __import__("time").time

    def status(self):
        response = self.client.get("/api/plugins/gmail/status")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def prepare_send(self, scope, **overrides):
        body = {"scope": scope, "action": "send", "to": "Dest <dest@example.com>",
                "cc": "cc@example.com", "subject": "Hello", "body": "exact body"}
        body.update(overrides)
        return self.client.post("/api/plugins/gmail/actions/prepare", json=body)

    def test_status_poll_stable_and_hermes_profile_envelope_allowed(self):
        first = self.status()
        second = self.client.get('/api/plugins/gmail/status?profile=A')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first, second.json())
        response = self.client.get('/api/plugins/gmail/labels', params={'profile': 'A', 'scope': first['scope']})
        self.assertEqual(response.status_code, 200)

    def test_status_search_pagination_and_exact_contract(self):
        status = self.status()
        response = self.client.get("/api/plugins/gmail/search", params={
            "scope": status["scope"], "q": "is:unread", "maxResults": 20, "pageToken": "p0"
        })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["nextPageToken"], "next-token")
        self.assertEqual(set(data["messages"][0]), {"id", "threadId", "from", "to", "cc", "subject", "date", "snippet", "labelIds"})
        list_call = next(call for call in self.services[str(self.a)].calls if call[0] == "list")
        self.assertEqual(list_call[1]["pageToken"], "p0")
        self.assertTrue(all(retries == 0 for _, retries in self.services[str(self.a)].executions))

    def test_message_plain_text_preferred_html_sanitized_and_attachments_ignored(self):
        status = self.status()
        fake = self.services[str(self.a)]
        fake.messages["m1"] = message(body=None, html="<style>x</style><script>steal()</script><p>Hello <b>world</b></p>", attachment=True)
        response = self.client.get("/api/plugins/gmail/messages/m1", params={"scope": status["scope"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["body"], "Hello world")
        self.assertNotIn("steal", response.json()["body"])
        self.assertNotIn("attachment secret", response.json()["body"])
        fake.messages["m1"] = message(body="safe plain", html="<b>ignored</b>")
        self.assertEqual(self.client.get("/api/plugins/gmail/messages/m1", params={"scope": status["scope"]}).json()["body"], "safe plain")
        fake.messages["m1"] = message(body="x" * (api._MAX_BODY_BYTES + 100))
        capped = self.client.get("/api/plugins/gmail/messages/m1", params={"scope": status["scope"]}).json()
        self.assertEqual(len(capped["body"].encode()), api._MAX_BODY_BYTES)
        self.assertTrue(capped["bodyTruncated"])

    def test_html_sanitizer_keeps_formatting_and_drops_active_markup_and_urls(self):
        safe = api.sanitize_email_html(
            '<p align="center">Hello <strong>world</strong> <a href="https://example.com/x" onclick="bad()">link</a></p>'
            '<script>steal()</script><form><input value="secret"></form>'
            '<img src="https://remote.example/pixel" onerror="bad()" alt="remote image">'
            '<svg onload="bad()"><script>svg()</script></svg>'
            '<a href="javascript:alert(1)">unsafe link</a>'
            '<style>@import url(https://remote.example/x);</style>'
        )
        self.assertIn('<strong>world</strong>', safe)
        self.assertIn('href="https://example.com/x"', safe)
        self.assertIn('data-email-src="https://remote.example/pixel"', safe)
        for forbidden in ('onclick', 'onerror', 'script', 'steal()', 'javascript:', '<form', '<input', '<svg', '<style', ' src='):
            self.assertNotIn(forbidden, safe)

    def test_html_sanitizer_keeps_safe_remote_image_as_inert_data_only(self):
        safe = api.sanitize_email_html(
            '<img src="https://images.example.test/a.png?x=1&amp;y=2" alt="photo">'
        )
        self.assertIn('data-email-src="https://images.example.test/a.png?x=1&amp;y=2"', safe)
        self.assertIn('alt="photo"', safe)
        self.assertNotIn(' src=', safe)

    def test_html_sanitizer_rejects_unsafe_image_urls(self):
        for url in (
            'http://images.example.test/a.png',
            'javascript:alert(1)',
            'data:image/png;base64,AAAA',
            'https://user:pass@images.example.test/a.png',
            'https://images.example.test/\ntracker.png',
        ):
            with self.subTest(url=url):
                safe = api.sanitize_email_html(f'<img src="{url}" alt="blocked">')
                self.assertNotIn('data-email-src=', safe)
                self.assertNotIn('src=', safe)
                self.assertNotIn(url.strip(), safe)

    def test_html_sanitizer_preserves_only_safe_clickable_link_protocols(self):
        safe = api.sanitize_email_html(
            '<a href="https://example.test/path?q=1&amp;x=2">https</a>'
            '<a href="mailto:person@example.test">mail</a>'
            '<a href="javascript:alert(1)">bad</a>'
            '<a href="https://user:pass@example.test/">credentialed</a>'
            '<a href="data:text/html,boom">data</a>'
        )
        self.assertIn('href="https://example.test/path?q=1&amp;x=2"', safe)
        self.assertIn('href="mailto:person@example.test"', safe)
        for forbidden in ('javascript:', 'user:pass', 'data:text/html'):
            self.assertNotIn(forbidden, safe)

    def test_profile_scope_isolation_A_B_A_at_call_time(self):
        a_status = self.status()
        token_b = set_hermes_home_override(self.b)
        try:
            b_status = self.status()
            self.assertNotEqual(a_status["scope"], b_status["scope"])
            blocked = self.client.get("/api/plugins/gmail/labels", params={"scope": a_status["scope"]})
            self.assertEqual(blocked.status_code, 409)
            self.assertFalse(any(kind == "labels" for kind, _ in self.services[str(self.b)].calls))
        finally:
            reset_hermes_home_override(token_b)
        ok = self.client.get("/api/plugins/gmail/labels", params={"scope": a_status["scope"]})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["labels"][1]["name"], "Projects")

    def test_strict_validation_happens_before_provider_calls(self):
        scope = self.status()["scope"]
        fake = self.services[str(self.a)]
        cases = [
            {"scope": scope, "action": "send", "to": "x@example.com\r\nBcc: evil@example.com", "cc": "", "subject": "x", "body": "x"},
            {"scope": scope, "action": "send", "to": "not-an-address", "cc": "", "subject": "x", "body": "x"},
            {"scope": scope, "action": "archive", "messageId": "m1", "extra": True},
            {"scope": scope, "action": "labels", "messageId": "m1", "addLabelIds": ["L1", "L1"], "removeLabelIds": []},
            {"scope": scope, "action": "labels", "messageId": "m1", "addLabelIds": [], "removeLabelIds": []},
            {"scope": scope, "action": "labels", "messageId": "m1", "addLabelIds": ["L1"], "removeLabelIds": ["L1"]},
        ]
        before = len(fake.calls)
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/plugins/gmail/actions/prepare", json=body).status_code, 422)
        self.assertEqual(len(fake.calls), before)
        self.assertEqual(self.client.get("/api/plugins/gmail/search", params={"scope": scope, "maxResults": 999}).status_code, 422)
        self.assertEqual(self.client.get("/api/plugins/gmail/labels", params={"scope": scope, "extra": "x"}).status_code, 422)
        valid_send = {"scope": scope, "action": "send", "to": "x@example.com", "cc": "", "subject": "x", "body": "x"}
        self.assertEqual(self.client.post("/api/plugins/gmail/actions/prepare?extra=x", json=valid_send).status_code, 422)
        self.assertEqual(len(fake.calls), before)

    def test_send_prepare_preview_commit_readback_single_use_and_plain_mime(self):
        scope = self.status()["scope"]
        prepared = self.prepare_send(scope)
        self.assertEqual(prepared.status_code, 200, prepared.text)
        data = prepared.json()
        self.assertEqual(data["scope"], scope)
        self.assertEqual(data["expiresAt"], 1300.0)
        self.assertEqual(data["preview"], {
            "action": "send", "account": "a@example.com", "to": "Dest <dest@example.com>",
            "cc": "cc@example.com", "subject": "Hello", "body": "exact body"
        })
        fake = self.services[str(self.a)]
        self.assertEqual(fake.mutations, 0)  # prepare/cancel path has no effect
        commit_body = {"scope": scope, "confirmationToken": data["confirmationToken"], "confirmed": True}
        committed = self.client.post("/api/plugins/gmail/actions/commit", json=commit_body)
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(committed.json(), {"status": "verified", "id": "sent1", "threadId": "tsent1"})
        self.assertEqual(fake.mutations, 1)
        send_call = next(item for item in fake.calls if item[0] == "send")
        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(send_call[1]["body"]["raw"] + "==="))
        self.assertEqual(parsed.get_content_type(), "text/plain")
        self.assertEqual(parsed.get_content(), "exact body")
        again = self.client.post("/api/plugins/gmail/actions/commit", json=commit_body)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(fake.mutations, 1)

    def test_archive_stale_preview_prevents_effect_and_consumes_ticket(self):
        scope = self.status()["scope"]
        prepared = self.client.post("/api/plugins/gmail/actions/prepare", json={
            "scope": scope, "action": "archive", "messageId": "m1"
        })
        self.assertEqual(prepared.status_code, 200, prepared.text)
        preview = prepared.json()["preview"]
        self.assertEqual(preview["removeLabels"], [{"id": "INBOX", "name": "Inbox"}])
        self.services[str(self.a)].messages["m1"]["labelIds"].append("L2")
        body = {"scope": scope, "confirmationToken": prepared.json()["confirmationToken"], "confirmed": True}
        response = self.client.post("/api/plugins/gmail/actions/commit", json=body)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.services[str(self.a)].mutations, 0)
        self.assertEqual(self.client.post("/api/plugins/gmail/actions/commit", json=body).status_code, 409)

    def test_archive_verified_removes_only_inbox(self):
        scope = self.status()["scope"]
        prepared = self.client.post("/api/plugins/gmail/actions/prepare", json={"scope": scope, "action": "archive", "messageId": "m1"}).json()
        response = self.client.post("/api/plugins/gmail/actions/commit", json={
            "scope": scope, "confirmationToken": prepared["confirmationToken"], "confirmed": True
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.services[str(self.a)].messages["m1"]["labelIds"], ["L1"])
        modify = next(item for item in self.services[str(self.a)].calls if item[0] == "modify")
        self.assertEqual(modify[1]["body"], {"addLabelIds": [], "removeLabelIds": ["INBOX"]})

    def test_labels_existing_user_only_preview_and_verified_commit(self):
        scope = self.status()["scope"]
        invalid = self.client.post("/api/plugins/gmail/actions/prepare", json={
            "scope": scope, "action": "labels", "messageId": "m1", "addLabelIds": ["INBOX"], "removeLabelIds": []
        })
        self.assertEqual(invalid.status_code, 422)
        prepared = self.client.post("/api/plugins/gmail/actions/prepare", json={
            "scope": scope, "action": "labels", "messageId": "m1", "addLabelIds": ["L2"], "removeLabelIds": ["L1"]
        })
        self.assertEqual(prepared.status_code, 200, prepared.text)
        preview = prepared.json()["preview"]
        self.assertEqual(preview["addLabelIds"], ["L2"])
        self.assertEqual(preview["addLabels"], [{"id": "L2", "name": "Later", "type": "user"}])
        committed = self.client.post("/api/plugins/gmail/actions/commit", json={
            "scope": scope, "confirmationToken": prepared.json()["confirmationToken"], "confirmed": True
        })
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(set(self.services[str(self.a)].messages["m1"]["labelIds"]), {"INBOX", "L2"})

    def test_expired_ticket_and_confirmed_must_be_literal_true(self):
        scope = self.status()["scope"]
        prepared = self.prepare_send(scope).json()
        body = {"scope": scope, "confirmationToken": prepared["confirmationToken"], "confirmed": False}
        self.assertEqual(self.client.post("/api/plugins/gmail/actions/commit", json=body).status_code, 422)
        self.clock = 1300.1
        body["confirmed"] = True
        self.assertEqual(self.client.post("/api/plugins/gmail/actions/commit", json=body).status_code, 409)
        self.assertEqual(self.services[str(self.a)].mutations, 0)

    def test_provider_errors_are_sanitized_and_mutation_failure_is_uncertain_no_retry(self):
        scope = self.status()["scope"]
        fake = self.services[str(self.a)]
        fake.failures["list"] = "SECRET-TOKEN raw provider JSON"
        failed_read = self.client.get("/api/plugins/gmail/search", params={"scope": scope})
        self.assertEqual(failed_read.status_code, 502)
        self.assertEqual(failed_read.json()["detail"], "Gmail service is unavailable.")
        self.assertNotIn("SECRET", failed_read.text)
        prepared = self.prepare_send(scope).json()
        fake.failures["send"] = "SECRET-TOKEN raw provider JSON"
        commit_body = {"scope": scope, "confirmationToken": prepared["confirmationToken"], "confirmed": True}
        failed = self.client.post("/api/plugins/gmail/actions/commit", json=commit_body)
        self.assertEqual(failed.status_code, 502)
        self.assertIn("uncertain", failed.json()["detail"])
        self.assertNotIn("SECRET", failed.text)
        self.assertEqual(sum(1 for kind, _ in fake.executions if kind == "send"), 1)
        self.assertEqual(self.client.post("/api/plugins/gmail/actions/commit", json=commit_body).status_code, 409)

    def test_failed_readback_is_uncertain_and_not_retried(self):
        scope = self.status()["scope"]
        prepared = self.prepare_send(scope).json()
        fake = self.services[str(self.a)]
        original_get = FakeMessages.get

        def fail_sent(resource, **kwargs):
            if kwargs["id"] == "sent1":
                resource.f.calls.append(("get", kwargs))
                return FakeRequest(resource.f, "readback", lambda: resource.f.messages["sent1"])
            return original_get(resource, **kwargs)
        with patch.object(FakeMessages, "get", fail_sent):
            fake.failures["readback"] = "provider secret"
            response = self.client.post("/api/plugins/gmail/actions/commit", json={
                "scope": scope, "confirmationToken": prepared["confirmationToken"], "confirmed": True
            })
        self.assertEqual(response.status_code, 502)
        self.assertIn("uncertain", response.json()["detail"])
        self.assertEqual(fake.mutations, 1)
        self.assertEqual(sum(1 for kind, _ in fake.executions if kind == "readback"), 1)

    def test_numeric_confirmation_is_not_explicit_boolean_consent(self):
        scope = self.status()['scope']
        ticket = self.prepare_send(scope).json()['confirmationToken']
        for value in [1, 1.0, 'true', None]:
            with self.subTest(value=value):
                response = self.client.post('/api/plugins/gmail/actions/commit', json={
                    'scope': scope, 'confirmationToken': ticket, 'confirmed': value
                })
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.services[str(self.a)].mutations, 0)

    def test_renamed_label_invalidates_exact_preview(self):
        scope = self.status()['scope']
        ticket = self.client.post('/api/plugins/gmail/actions/prepare', json={
            'scope': scope, 'action': 'labels', 'messageId': 'm1',
            'addLabelIds': ['L2'], 'removeLabelIds': []
        }).json()['confirmationToken']
        self.services[str(self.a)].labels[2]['name'] = 'Renamed after approval'
        response = self.client.post('/api/plugins/gmail/actions/commit', json={
            'scope': scope, 'confirmationToken': ticket, 'confirmed': True
        })
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.services[str(self.a)].mutations, 0)

    def test_commit_cannot_follow_profile_or_account_switch(self):
        scope = self.status()['scope']
        prepared = self.prepare_send(scope).json()
        body = {'scope': scope, 'confirmationToken': prepared['confirmationToken'], 'confirmed': True}
        token_b = set_hermes_home_override(self.b)
        try:
            self.assertEqual(self.client.post('/api/plugins/gmail/actions/commit', json=body).status_code, 409)
            self.assertEqual(self.services[str(self.b)].mutations, 0)
        finally:
            reset_hermes_home_override(token_b)
        self.services[str(self.a)].account = 'changed@example.com'
        self.assertEqual(self.client.post('/api/plugins/gmail/actions/commit', json=body).status_code, 409)
        self.assertEqual(self.services[str(self.a)].mutations, 0)

    def test_mutation_response_alone_does_not_prove_label_change(self):
        scope = self.status()['scope']
        prepared = self.client.post('/api/plugins/gmail/actions/prepare', json={
            'scope': scope, 'action': 'archive', 'messageId': 'm1'
        }).json()
        def noop(resource, **kwargs):
            return FakeRequest(resource.f, 'modify', lambda: {'id': 'm1', 'threadId': 'tm1'})
        with patch.object(FakeMessages, 'modify', noop):
            response = self.client.post('/api/plugins/gmail/actions/commit', json={
                'scope': scope, 'confirmationToken': prepared['confirmationToken'], 'confirmed': True
            })
        self.assertEqual(response.status_code, 502)
        self.assertIn('uncertain', response.json()['detail'])

    def test_scope_and_ticket_caches_are_bounded(self):
        for i in range(api._MAX_SCOPES + 5):
            self.services[str(self.a)].account = f'account{i}@example.com'
            self.status()
        self.assertEqual(len(api._scopes), api._MAX_SCOPES)
        scope = self.status()["scope"]
        for _ in range(api._MAX_TICKETS + 5):
            self.assertEqual(self.prepare_send(scope).status_code, 200)
        self.assertEqual(len(api._tickets), api._MAX_TICKETS)


class CredentialAndTransportTests(unittest.TestCase):
    def test_credentials_resolve_A_B_A_without_scopes_or_persistence(self):
        calls = []
        class Credentials:
            expired = False
            refresh_token = "refresh"
            valid = True
            @classmethod
            def from_authorized_user_file(cls, path, *args, **kwargs):
                calls.append((path, args, kwargs))
                return cls()
        homes = [Path("/profiles/A"), Path("/profiles/B"), Path("/profiles/A")]
        with patch("pathlib.Path.is_file", return_value=True), patch("google.oauth2.credentials.Credentials", Credentials):
            for home in homes:
                token = set_hermes_home_override(home)
                try: api._load_credentials()
                finally: reset_hermes_home_override(token)
        self.assertEqual([item[0] for item in calls], ["/profiles/A/google_token.json", "/profiles/B/google_token.json", "/profiles/A/google_token.json"])
        self.assertTrue(all(args == () and kwargs == {} for _, args, kwargs in calls))

    def test_transport_returns_401_without_refresh_replay_or_retry(self):
        class Creds:
            def __init__(self): self.before = 0
            def before_request(self, request, method, uri, headers):
                self.before += 1
                headers["authorization"] = "Bearer fake-test-token"
        class Response:
            status_code = 401
            reason = "Unauthorized"
            headers = {"content-type": "application/json"}
            content = b'{"error":"unauthorized"}'
        class Session:
            def __init__(self): self.calls = []
            def request(self, **kwargs): self.calls.append(kwargs); return Response()
        creds = Creds()
        transport = api._SingleAttemptAuthorizedHttp(creds)
        transport.session = Session()
        from googleapiclient.errors import HttpError
        from googleapiclient.http import HttpRequest
        request = HttpRequest(
            transport,
            lambda response, content: content,
            "https://gmail.googleapis.test/x",
            method="POST",
            body=b"{}",
        )
        with self.assertRaises(HttpError) as raised:
            request.execute(num_retries=0)
        self.assertEqual(raised.exception.resp.status, 401)
        self.assertEqual(creds.before, 1)
        self.assertEqual(len(transport.session.calls), 1)
        self.assertFalse(transport.session.calls[0]["allow_redirects"])
        self.assertEqual(transport.session.calls[0]["timeout"], api._REQUEST_TIMEOUT_SECONDS)

    def test_real_service_builder_uses_single_attempt_transport(self):
        from unittest.mock import Mock
        from googleapiclient.errors import HttpError
        credentials = Mock(universe_domain='googleapis.com')
        response = Mock(status_code=503, reason='Unavailable',
                        headers={'content-type': 'application/json'},
                        content=b'{"error":{"message":"synthetic failure"}}')
        with patch.object(api, '_load_credentials', return_value=credentials):
            service = api._build_gmail_service()
        transport = service._http
        self.assertIsInstance(transport, api._SingleAttemptAuthorizedHttp)
        with patch.object(transport.session, 'request', return_value=response) as outbound:
            with self.assertRaises(HttpError):
                api._execute(service.users().messages().send(userId='me', body={'raw': 'ZmFrZQ=='}))
        self.assertEqual(outbound.call_count, 1)
        self.assertEqual(credentials.before_request.call_count, 1)
        kwargs = outbound.call_args.kwargs
        self.assertEqual(kwargs['method'], 'POST')
        self.assertIn('/gmail/v1/users/me/messages/send', kwargs['url'])
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(json.loads(kwargs['data']), {'raw': 'ZmFrZQ=='})

    def test_manifest_contract(self):
        manifest = json.loads((REPO / "plugins/gmail/dashboard/manifest.json").read_text())
        self.assertEqual(manifest["name"], "gmail")
        self.assertNotIn("tab", manifest)  # Desktop owns /gmail; no web-dashboard bundle.
        self.assertEqual(manifest["api"], "plugin_api.py")


if __name__ == "__main__":
    unittest.main()
