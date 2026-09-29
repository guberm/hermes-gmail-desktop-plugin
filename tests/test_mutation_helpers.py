import unittest

from plugins.gmail.dashboard.mutation_helpers import expected_labels_for_action


class MutationBehaviorTests(unittest.TestCase):
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