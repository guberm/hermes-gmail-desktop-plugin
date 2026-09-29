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

if __name__ == "__main__":
    unittest.main()
