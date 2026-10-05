import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "plugins/gmail/dashboard/plugin_api.py"
PLUGIN = ROOT / "plugins/gmail/desktop/plugin.js"


class ShippedSourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend_text = BACKEND.read_text(encoding="utf-8")
        cls.backend_ast = ast.parse(cls.backend_text, filename=str(BACKEND))
        cls.plugin_text = PLUGIN.read_text(encoding="utf-8")

    def test_unified_package_layout(self):
        self.assertTrue((ROOT / "plugins/gmail/plugin.yaml").is_file())
        self.assertTrue((ROOT / "plugins/gmail/__init__.py").is_file())
        self.assertTrue((ROOT / "plugins/gmail/desktop/plugin.js").is_file())
        self.assertTrue((ROOT / "plugins/gmail/dashboard/manifest.json").is_file())

    def test_dashboard_backend_has_no_package_relative_imports(self):
        imports = [
            node for node in ast.walk(self.backend_ast)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        ]
        self.assertEqual(imports, [])

    def test_thread_route_and_reply_confirmation_contract_are_present(self):
        routes = [
            node for node in ast.walk(self.backend_ast)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "get"
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
                and dec.args[0].value == "/threads/{thread_id}"
                for dec in node.decorator_list
            )
        ]
        self.assertEqual(len(routes), 1)
        self.assertIn("confirmed: Literal[True]", self.backend_text)
        self.assertIn("service.users().threads().get", self.backend_text)
        self.assertIn("\"threadId\": body.threadId", self.backend_text)
        self.assertIn("send_body[\"threadId\"] = payload[\"threadId\"]", self.backend_text)
        self.assertIn("\"In-Reply-To\"", self.backend_text)
        self.assertIn("\"References\"", self.backend_text)

    def test_profile_scoping_and_no_token_persistence(self):
        self.assertIn("Path(_current_home()) / \"google_token.json\"", self.backend_text)
        self.assertIn("binding.home != home", self.backend_text)
        self.assertIn("binding.account != account", self.backend_text)
        self.assertNotIn("credentials.to_json()", self.backend_text)
        self.assertNotIn("token_path.write", self.backend_text)

    def test_desktop_integration_wires_thread_view_and_confirmed_commit(self):
        self.assertIn("\x27/threads/\x27", self.plugin_text)
        self.assertIn("\x27/actions/prepare\x27", self.plugin_text)
        self.assertIn("\x27/actions/commit\x27", self.plugin_text)
        self.assertIn("confirmed: true", self.plugin_text)
        self.assertIn("threadId", self.plugin_text)
        self.assertIn("SIDEBAR_NAV_AREA", self.plugin_text)
        self.assertIn("/gmail", self.plugin_text)
        self.assertIn("label: \x27Gmail\x27", self.plugin_text)

    def test_trash_is_profile_scoped_and_confirmed(self):
        self.assertIn('class TrashPrepare', self.backend_text)
        self.assertIn('action: Literal["trash"]', self.backend_text)
        self.assertIn('messages().trash', self.backend_text)
        self.assertIn("action('Delete'", self.plugin_text)
        self.assertIn("prepare('trash')", self.plugin_text)
        self.assertIn('confirmed: true', self.plugin_text)

    def test_mobile_detail_resets_its_scroll_container(self):
        self.assertIn('detailScroll.current?.scrollTo({ top: 0', self.plugin_text)
        self.assertIn("ref: detailPanel, 'aria-label': 'Message detail'", self.plugin_text)
        self.assertIn("requestAnimationFrame(() => detailPanel.current?.scrollIntoView({ block: 'start', behavior: 'auto' }))", self.plugin_text)
        self.assertIn("'aria-label': 'Message detail'", self.plugin_text)

    def test_selected_subject_is_not_repeated_and_email_colors_follow_theme(self):
        self.assertEqual(self.plugin_text.count('selectedMessage?.subject ||'), 1)
        self.assertIn("color: 'var(--ui-text-primary)'", self.plugin_text)
        self.assertIn("a: { color: 'var(--ui-accent)'", self.plugin_text)
        self.assertNotIn("color: '#202124'", self.plugin_text)

    def test_gmail_browser_route_uses_numeric_account_slot(self):
        self.assertIn('mail/u/0/?authuser=${encodeURIComponent(account)}#all/${encodeURIComponent(threadId)}', self.plugin_text)
        self.assertNotIn('mail/u/${encodeURIComponent(account)}/', self.plugin_text)

    def test_desktop_runtime_uses_only_permitted_imports_and_inlines_reply_all(self):
        imports = []
        for line in self.plugin_text.splitlines():
            stripped = line.strip()
            if stripped.startswith('import '):
                imports.append(stripped)
        self.assertTrue(all("'./" not in line and '"./' not in line for line in imports))
        self.assertNotIn("reply_recipients.mjs", self.plugin_text)
        self.assertIn("export function deriveReplyAllRecipients", self.plugin_text)
        self.assertIn("beginReply(selectedMessage, thread.data, true)", self.plugin_text)
        self.assertIn("deriveReplyAllRecipients(latest, identity.account)", self.plugin_text)
        self.assertIn("maxRecipients = 100", self.plugin_text)

    def test_optional_preferences_are_profile_account_scoped_and_menu_wired(self):
        self.assertIn("settingsStorageKey(profile, identity.account)", self.plugin_text)
        self.assertIn("ctx.storage.get(preferencesKey, null)", self.plugin_text)
        self.assertIn("ctx.storage.set(preferencesKey, settings)", self.plugin_text)
        self.assertIn("refetchInterval: autoRefreshInterval(settings)", self.plugin_text)
        self.assertIn("inboxQuery(search.q, settings.unreadOnly)", self.plugin_text)
        self.assertIn("DropdownMenuTrigger", self.plugin_text)
        for label in ("Auto-refresh every 60 seconds", "Show unread only", "Confirm before deleting"):
            self.assertIn(label, self.plugin_text)
        self.assertIn("prepare('trash')", self.plugin_text)
        self.assertIn("await commit(commitWithoutPrompt)", self.plugin_text)
        self.assertIn("shouldConfirmDelete(current.settings)", self.plugin_text)

    def test_more_read_state_actions_use_confirmed_label_pipeline(self):
        self.assertIn("Mark as unread (review first)", self.plugin_text)
        self.assertIn("Mark as read (review first)", self.plugin_text)
        self.assertIn("prepare('labels-add', 'UNREAD')", self.plugin_text)
        self.assertIn("prepare('labels-remove', 'UNREAD')", self.plugin_text)
        self.assertIn('Only existing user labels, UNREAD, and STARRED may be changed.', self.backend_text)
        self.assertIn('confirmed: true', self.plugin_text)
        self.assertIn('confirmationToken: approved.confirmationToken', self.plugin_text)

    def test_trash_never_becomes_permanent_delete_and_keeps_snapshot_readback(self):
        self.assertIn('messages().trash', self.backend_text)
        self.assertNotIn('messages().delete', self.backend_text)
        self.assertIn('_message_snapshot(current) != ticket.message_snapshot', self.backend_text)
        self.assertIn('expected_labels_for_action(ticket.action, ticket.message_snapshot, payload)', self.backend_text)

    def test_package_version_bumped(self):
        self.assertIn('version: 1.0.15', (ROOT / 'plugins/gmail/plugin.yaml').read_text(encoding='utf-8'))

if __name__ == "__main__":
    unittest.main()
