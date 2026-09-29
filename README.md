# Hermes Desktop Gmail plugin

This repository contains the opt-in Hermes Desktop Gmail UI and its profile-scoped dashboard backend.

## Contents

- `desktop-plugins/gmail/` - Hermes Desktop plugin source and its original setup/safety notes.
- `plugins/gmail/dashboard/` - dashboard backend source and plugin manifest.
- `tests/` - focused offline source-contract tests for the shipped integration.

## Install and setup

1. Copy `desktop-plugins/gmail/plugin.js` to `~/.hermes/desktop-plugins/gmail/plugin.js` and retain the plugin directory structure.
2. Copy `plugins/gmail/dashboard/plugin_api.py` and `manifest.json` to `~/.hermes/plugins/gmail/dashboard/`.
3. Enable the `gmail` dashboard backend in the active Hermes profile configuration (`plugins.enabled`) and enable Gmail under Hermes Desktop Settings > Plugins. These are separate opt-in switches.
4. Configure Google OAuth for the active Hermes profile through the existing Hermes Google Workspace setup. The backend resolves that profile's managed token at request time; never copy credentials into this repository or Desktop plugin.
5. Reload desktop plugins (or restart/reload the relevant Hermes services) if the plugin does not appear.

The backend imports FastAPI, Pydantic, Google auth/API libraries and Hermes runtime modules from the host installation. This repository does not vendor those dependencies; install/run through a compatible Hermes environment. The Desktop host must provide `@hermes/plugin-sdk` and React.

## Use

Open Gmail from the Desktop sidebar or command palette. Search, read message/thread text, and inspect labels. Label changes, archive, send, and threaded reply show an exact preview and require explicit confirmation. Email content is untrusted text; attachments are not loaded or rendered. Do not retry an action after an uncertain result without checking Gmail first.

## Checks

Run the repository's offline source-contract checks with:

```sh
python -m unittest discover -s tests -v
node --check desktop-plugins/gmail/plugin.js
python -m py_compile plugins/gmail/dashboard/plugin_api.py
```

The HTTP backend routes require the Hermes runtime and dependencies, so the offline tests do not exercise live OAuth or Gmail API calls.
