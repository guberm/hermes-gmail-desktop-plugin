import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "plugins/gmail/dashboard/plugin_api.py"
MODULE_NAME = "hermes_dashboard_plugin_gmail"
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, BACKEND)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = MODULE
SPEC.loader.exec_module(MODULE)
expected_labels_for_action = MODULE.expected_labels_for_action


class MutationBehaviorTests(unittest.TestCase):
    def test_backend_loads_through_hermes_standalone_file_path(self):
        self.assertTrue(hasattr(MODULE, "router"))
        self.assertTrue(
            any(getattr(route, "path", None) == "/status" for route in MODULE.router.routes)
        )

    def test_trash_readback_requires_trash_and_removes_inbox(self):
        self.assertEqual(
            expected_labels_for_action(
                "trash", {"labelIds": ["INBOX", "IMPORTANT"]}, {"messageId": "m1"}
            ),
            {"TRASH", "IMPORTANT"},
        )

    def test_trash_readback_rejects_missing_snapshot(self):
        with self.assertRaises(ValueError):
            expected_labels_for_action("trash", None, {"messageId": "m1"})


if __name__ == "__main__":
    unittest.main()