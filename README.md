# Hermes Desktop Gmail plugin

This repository contains one unified Hermes plugin package: the Desktop Gmail UI, the dashboard API backend, and the agent-side package declaration. The package is profile-safe and does not contain OAuth credentials or tokens.

## Layout

- `plugins/gmail/plugin.yaml` and `plugins/gmail/__init__.py` - Hermes agent package declaration and inert agent-side wrapper.
- `plugins/gmail/desktop/plugin.js` - Hermes Desktop runtime plugin. Hermes Desktop materializes this file into `~/.hermes/desktop-plugins/gmail/` and inventories it as the `gmail` package.
- `plugins/gmail/dashboard/manifest.json` and `plugin_api.py` - profile-scoped dashboard backend mounted at `/api/plugins/gmail/`.
- `tests/` - focused offline source-contract tests.

The `plugins/<name>/desktop/plugin.js` path is the unified-package inventory surface. The Desktop loader evaluates this file as an ES module, so validate with `node --input-type=module --check` (plain `node --check` on `.js` may parse as CommonJS and miss module grammar errors). Do not install a second copy under `~/.hermes/desktop-plugins/gmail/` by hand; current Hermes Desktop creates that app-level materialized copy and writes its `.hermes-package.json` pairing marker.

## Install and setup

1. Install the package under `~/.hermes/plugins/gmail/` while preserving this layout.
2. Keep `gmail` in the active profile's `plugins.enabled` list. This is the backend trust gate and is separate from the Desktop UI switch.
3. Configure Google OAuth through the existing Hermes Google Workspace setup for the active profile. The backend resolves the active profile's managed token at request time, keeps refreshes in memory, and never copies credentials into this repository or the Desktop plugin.
4. Start or reload Hermes Desktop. It reconciles the unified package into `~/.hermes/desktop-plugins/gmail/`, then discovers the runtime entry. Enable Gmail in Capabilities → Plugins; the Gmail sidebar row is registered at `/gmail` with label `Gmail`.

## Safety

Search, reading, and thread inspection are read-only. Label changes, archive, send, and threaded replies use an exact preview plus a separate explicit confirmation. Email content is untrusted text; attachments are not loaded or rendered. Do not retry an uncertain mutation without checking Gmail first.

## Checks

```sh
python -m unittest discover -s tests -v
node --test tests/*.mjs
python -m py_compile plugins/gmail/__init__.py plugins/gmail/dashboard/plugin_api.py
```

The HTTP backend routes require the Hermes runtime and dependencies, so the offline tests do not exercise live OAuth or Gmail API calls.
