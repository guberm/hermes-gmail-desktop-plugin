import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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

    def test_more_unread_action_prepares_and_commits_through_snapshot_validation(self):
        scope = "scope_token_value_1234567890"
        account = "owner@example.com"
        binding = MODULE.ScopeBinding("backend", "/profile", account, 0)
        unread_label = {"id": "UNREAD", "name": "UNREAD", "type": "system"}
        message = {"id": "m1", "threadId": "t1", "labelIds": ["INBOX"], "subject": "Hi"}
        request = SimpleNamespace(query_params={})
        body = MODULE.LabelsPrepare(scope=scope, action="labels", messageId="m1", addLabelIds=["UNREAD"], removeLabelIds=[])

        with patch.object(MODULE, "_binding", return_value=binding), \
             patch.object(MODULE, "_provider_context", return_value=(object(), account, "/profile")), \
             patch.object(MODULE, "_list_user_labels", return_value=[unread_label]), \
             patch.object(MODULE, "_get_message", return_value=({}, message)):
            prepared = MODULE.prepare_action(request, body)

        self.assertEqual(prepared["preview"]["action"], "labels")
        self.assertEqual(prepared["preview"]["addLabels"], [unread_label])
        self.assertEqual(prepared["preview"]["message"], message)

        ticket = MODULE.Ticket(
            scope, "labels", account,
            {"messageId": "m1", "addLabelIds": ["UNREAD"], "removeLabelIds": []},
            prepared["preview"], MODULE._message_snapshot(message), 9999999999,
        )
        changed = {**message, "labelIds": ["INBOX", "UNREAD"]}
        class Messages:
            def modify(self, **kwargs):
                self.kwargs = kwargs
                return "modify-request"
        messages = Messages()
        service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: messages))
        with patch.object(MODULE, "_binding", return_value=binding), \
             patch.object(MODULE, "_consume_ticket", return_value=ticket), \
             patch.object(MODULE, "_provider_context", return_value=(service, account, "/profile")), \
             patch.object(MODULE, "_get_message", side_effect=[({}, message), ({}, changed)]), \
             patch.object(MODULE, "_list_user_labels", return_value=[unread_label]), \
             patch.object(MODULE, "_execute", side_effect=lambda req: {"id": "m1"} if req == "modify-request" else {}):
            result = MODULE.commit_action(request, MODULE.CommitRequest(scope=scope, confirmationToken="t" * 20, confirmed=True))

        self.assertEqual(result, {"status": "verified", "id": "m1", "threadId": "t1"})
        self.assertEqual(messages.kwargs["body"], {"addLabelIds": ["UNREAD"], "removeLabelIds": []})

    def test_batch_star_prepare_and_commit_reviews_all_snapshots_once(self):
        scope = "scope_token_value_1234567890"
        account = "owner@example.com"
        binding = MODULE.ScopeBinding("backend", "/profile", account, 0)
        starred = {"id": "STARRED", "name": "STARRED", "type": "system"}
        messages_before = [
            {"id": f"m{i}", "threadId": f"t{i}", "labelIds": ["INBOX"], "subject": f"S{i}"}
            for i in range(1, 3)
        ]
        request = SimpleNamespace(query_params={})
        body = MODULE.BatchPrepare(scope=scope, action="batch", operation="star", messageIds=["m1", "m2"])
        with patch.object(MODULE, "_binding", return_value=binding), \
             patch.object(MODULE, "_provider_context", return_value=(object(), account, "/profile")), \
             patch.object(MODULE, "_list_user_labels", return_value=[starred]), \
             patch.object(MODULE, "_get_message", side_effect=[({}, m) for m in messages_before]):
            prepared = MODULE.prepare_action(request, body)

        self.assertEqual(prepared["preview"]["action"], "batch")
        self.assertEqual(prepared["preview"]["operation"], "star")
        self.assertEqual([m["id"] for m in prepared["preview"]["messages"]], ["m1", "m2"])
        ticket = MODULE._tickets[prepared["confirmationToken"]]

        class Messages:
            def __init__(self):
                self.calls = []
            def modify(self, **kwargs):
                self.calls.append(kwargs)
                return (kwargs["id"], kwargs["body"])
        messages = Messages()
        service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: messages))
        post = [{**item, "labelIds": ["INBOX", "STARRED"]} for item in messages_before]
        reads = [({}, item) for item in messages_before] + [({}, item) for item in post]
        with patch.object(MODULE, "_binding", return_value=binding), \
             patch.object(MODULE, "_provider_context", return_value=(service, account, "/profile")), \
             patch.object(MODULE, "_list_user_labels", return_value=[starred]), \
             patch.object(MODULE, "_get_message", side_effect=reads), \
             patch.object(MODULE, "_execute", side_effect=lambda req: {"id": req[0]}):
            result = MODULE.commit_action(request, MODULE.CommitRequest(scope=scope, confirmationToken=prepared["confirmationToken"], confirmed=True))

        self.assertEqual(result, {"status": "verified", "id": "m1", "ids": ["m1", "m2"]})
        self.assertEqual([call["id"] for call in messages.calls], ["m1", "m2"])
        self.assertTrue(all(call["body"] == {"addLabelIds": ["STARRED"], "removeLabelIds": []} for call in messages.calls))
        self.assertEqual(ticket.action, "batch")

    def test_batch_prepare_rejects_duplicate_or_over_limit_ids(self):
        scope = "scope_token_value_1234567890"
        with self.assertRaises(Exception):
            MODULE.BatchPrepare(scope=scope, action="batch", operation="archive", messageIds=["m1", "m1"])
        with self.assertRaises(Exception):
            MODULE.BatchPrepare(scope=scope, action="batch", operation="archive", messageIds=[f"m{i}" for i in range(21)])

    def test_batch_commit_rejects_stale_member_before_any_mutation(self):
        scope = "scope_token_value_1234567890"
        account = "owner@example.com"
        binding = MODULE.ScopeBinding("backend", "/profile", account, 0)
        snapshot = {"id": "m1", "threadId": "t1", "labelIds": ["INBOX"], "subject": "reviewed"}
        ticket = MODULE.Ticket(scope, "batch", account, {"operation": "archive", "messageIds": ["m1"], "addLabelIds": [], "removeLabelIds": ["INBOX"]},
            {"action": "batch", "operation": "archive", "messages": [snapshot]}, [MODULE._message_snapshot(snapshot)], 9999999999)
        changed = {**snapshot, "subject": "changed after review"}
        request = SimpleNamespace(query_params={})
        class Messages:
            def modify(self, **kwargs):
                raise AssertionError("stale batch must not mutate")
        service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: Messages()))
        with patch.object(MODULE, "_binding", return_value=binding), \
             patch.object(MODULE, "_consume_ticket", return_value=ticket), \
             patch.object(MODULE, "_provider_context", return_value=(service, account, "/profile")), \
             patch.object(MODULE, "_get_message", return_value=({}, changed)):
            with self.assertRaises(MODULE.HTTPException) as raised:
                MODULE.commit_action(request, MODULE.CommitRequest(scope=scope, confirmationToken="t" * 20, confirmed=True))
        self.assertEqual(raised.exception.status_code, 409)

    def test_batch_commit_reads_every_member_after_partial_mutation_failure(self):
        scope = "scope_token_value_1234567890"
        account = "owner@example.com"
        binding = MODULE.ScopeBinding("backend", "/profile", account, 0)
        messages_before = [
            {"id": f"m{i}", "threadId": f"t{i}", "labelIds": ["INBOX"], "subject": f"S{i}"}
            for i in range(1, 3)
        ]
        ticket = MODULE.Ticket(scope, "batch", account,
            {"operation": "star", "messageIds": ["m1", "m2"], "addLabelIds": ["STARRED"], "removeLabelIds": []},
            {"action": "batch", "operation": "star", "messages": messages_before,
             "labels": [{"id": "STARRED", "name": "STARRED", "type": "system"}]},
            [MODULE._message_snapshot(message) for message in messages_before], 9999999999)
        post = [{**message, "labelIds": ["INBOX", "STARRED"]} for message in messages_before]
        reads = [({}, item) for item in messages_before] + [({}, item) for item in post]
        request = SimpleNamespace(query_params={})
        class Messages:
            def __init__(self):
                self.calls = []
            def modify(self, **kwargs):
                self.calls.append(kwargs["id"])
                return kwargs["id"]
        messages = Messages()
        service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: messages))
        read_ids = []
        def get_message(_service, message_id):
            read_ids.append(message_id)
            return reads.pop(0)
        def execute(request):
            if request == "m2":
                raise RuntimeError("provider mutation outcome uncertain")
            return {"id": request}

        with patch.object(MODULE, "_binding", return_value=binding), \
             patch.object(MODULE, "_consume_ticket", return_value=ticket), \
             patch.object(MODULE, "_provider_context", return_value=(service, account, "/profile")), \
             patch.object(MODULE, "_get_message", side_effect=get_message), \
             patch.object(MODULE, "_list_user_labels", return_value=[{"id": "STARRED", "name": "STARRED", "type": "system"}]), \
             patch.object(MODULE, "_execute", side_effect=execute):
            with self.assertRaises(MODULE.HTTPException) as raised:
                MODULE.commit_action(request, MODULE.CommitRequest(scope=scope, confirmationToken="t" * 20, confirmed=True))

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(messages.calls, ["m1", "m2"])
        self.assertEqual(read_ids, ["m1", "m2", "m1", "m2"])
        self.assertEqual(reads, [])


if __name__ == "__main__":
    unittest.main()