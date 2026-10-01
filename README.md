# Hermes Desktop Gmail plugin

This repository contains one unified Hermes plugin package: the Desktop Gmail UI, the dashboard API backend, and the agent-side package declaration. The package is profile-safe and does not contain OAuth credentials or tokens.

## Layout

- `plugins/gmail/plugin.yaml` and `plugins/gmail/__init__.py` - Hermes agent package declaration and inert agent-side wrapper.
- `plugins/gmail/desktop/plugin.js` - Hermes Desktop runtime plugin. Hermes Desktop materializes this file into `~/.hermes/desktop-plugins/gmail/` and inventories it as the `gmail` package.
- `plugins/gmail/dashboard/manifest.json` and `plugin_api.py` - profile-scoped dashboard backend mounted at `/api/plugins/gmail/`.
- `tests/` - offline backend behavior tests, source/package contracts, and Node.js ESM/preference helper checks.

The `plugins/<name>/desktop/plugin.js` path is the unified-package inventory surface. The Desktop loader evaluates this file as an ES module, so validate with `node --input-type=module --check` (plain `node --check` on `.js` may parse as CommonJS and miss module grammar errors). Do not install a second copy under `~/.hermes/desktop-plugins/gmail/` by hand; current Hermes Desktop creates that app-level materialized copy and writes its `.hermes-package.json` pairing marker.

## Install and setup

1. Install the package under `~/.hermes/plugins/gmail/` while preserving this layout.
2. Keep `gmail` in the active profile's `plugins.enabled` list. This is the backend trust gate and is separate from the Desktop UI switch.
3. Configure Google OAuth through the existing Hermes Google Workspace setup for the active profile. The backend resolves the active profile's managed token at request time, keeps refreshes in memory, and never copies credentials into this repository or the Desktop plugin.
4. Start or reload Hermes Desktop. It reconciles the unified package into `~/.hermes/desktop-plugins/gmail/`, then discovers the runtime entry. Enable Gmail in Capabilities → Plugins; the Gmail sidebar row is registered at `/gmail` with label `Gmail`.

## Safety

Search, reading, and thread inspection are read-only except opening an unread message, which now explicitly removes only its `UNREAD` label and verifies the profile/account-bound backend readback. If that operation fails, the UI preserves the unread state and offers a retry. Other label changes, archive, send, and threaded replies use an exact preview plus a separate explicit confirmation. Delete moves a message to Gmail Trash, never permanent deletion; its confirmation prompt can be disabled in More, but the backend prepare ticket, account/profile binding, exact message snapshot validation, and readback verification remain required. Email HTML is backend-sanitized to inert allowlisted formatting and rendered through React elements. A deterministic CSS projection retains only bounded layout, spacing, typography, alignment and border declarations; colors, resources, selector rules, positioning/visibility tricks and active CSS are discarded. Newsletter tables and images fit the panel, blocked images have visible placeholders, and unlabelled 1x1 pixels are suppressed. Approved links and bare HTTP(S) URLs open only after a pointer click or Enter key through the host bridge; unsafe links are inert. Remote images remain blocked until explicit opt-in, with only exact allowlisted YouTube image CDN HTTP hosts upgraded to HTTPS as inert data. More includes a profile/account-scoped, default-off **Always show images** option that warns remote images may track opens; toggling it on is explicit consent and off immediately removes image sources. All enabled image requests use HTTPS and `no-referrer`. `Open in browser` uses Gmail's numeric account slot plus `authuser` and opens only after a click; Gmail API thread IDs may not be durable web permalinks. Email body and link foregrounds use the active theme, and selected subjects are shown once. `<br>` / `<hr>` render safely. Forms, SVG/MathML, CSS resources, event handlers, and other non-HTTPS images are removed or inert. Attachments are not loaded or rendered. Do not retry an uncertain mutation without checking Gmail first.

## Mailbox preferences and More

In Gmail, open the **More** dropdown in the mailbox header (available both in the inbox and message detail). The toggles save persistently for the current Hermes profile and Gmail account:

- **Auto-refresh every 60 seconds** — off by default; enable it to poll the current search at a bounded one-minute interval, or turn it off to stop automatic polling.
- **Show unread only** — off by default; enable it to append Gmail's `is:unread` operator to the current search, or turn it off to return to the unfiltered search.
- **Confirm before deleting** — on by default; turn it off to skip the additional on-screen confirmation after pressing Delete; turn it back on to require that review dialog. Delete remains a deliberate button press either way, and the backend safeguards remain active.
- **Always show images** — off by default; when enabled, remote HTML images load as messages open. Remote images may track opens. Turning the option off immediately removes image sources; the per-message **Load images (N)** action remains available.

With a message selected, **Mark as unread (review first)** and **Mark as read (review first)** also appear in More when applicable. They use the same preview/confirm/commit/readback flow as other label changes. Selecting an unread message automatically verifies its read state by removing only `UNREAD`.

## Checks

```sh
python -m unittest discover -s tests -v
node --test tests/*.mjs
python -m py_compile plugins/gmail/__init__.py plugins/gmail/dashboard/plugin_api.py
```

The HTTP backend routes require the Hermes runtime and dependencies, so the offline tests do not exercise live OAuth or Gmail API calls.
