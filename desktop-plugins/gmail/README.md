Hermes Desktop Gmail plugin

Plugin id: gmail
Plugin file: ~/.hermes/desktop-plugins/gmail/plugin.js
Default profile backend: ~/.hermes/plugins/gmail/dashboard/plugin_api.py

Setup
1. This plugin is opt-in. In Hermes Desktop, open Settings > Plugins and enable Gmail. It stays disabled until explicitly enabled.
2. The profile backend must be enabled in the Hermes profile config under plugins.enabled (separate from the desktop plugin switch). The default profile uses the reviewed Gmail backend in ~/.hermes/plugins/gmail/dashboard; do not copy its token or credentials into the desktop plugin.
3. OAuth must be configured through the existing Hermes Google Workspace setup for the active profile. The backend resolves the active Hermes home when called and reads that profile's google_token.json. If it cannot authenticate, it reports backend unavailable; configure OAuth using the Google Workspace setup workflow and restart/reload the backend as appropriate.
4. In Desktop, use the Gmail sidebar entry or command palette > Open Gmail. Search, read messages and threads, and inspect labels. Label changes, archive, send, and threaded replies always require reviewing the exact account/action in the confirmation dialog and selecting Confirm action.
5. Reply starts a threaded Gmail reply to the latest message in the selected conversation. The backend validates and uses Gmail threadId plus RFC Message-ID/References headers; it rechecks the thread before sending and reads the sent message back for verification.

Safety
- The plugin includes no OAuth credentials or access tokens.
- The backend uses the Hermes-managed profile token; it does not write a refreshed token back to disk.
- Email content is untrusted input. Viewing does not load attachments, execute links, or render HTML.
- If an action's outcome is uncertain, do not retry blindly; check Gmail first.

After files are installed, use the desktop command palette's Reload desktop plugins if the plugin does not appear automatically. No emails are sent until a user reviews the send preview and confirms it.