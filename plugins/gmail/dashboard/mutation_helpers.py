from __future__ import annotations

from typing import Any


def expected_labels_for_action(
    action: str, snapshot: dict[str, Any] | None, payload: dict[str, Any]
) -> set[str]:
    """Compute the exact label state required by mutation readback."""
    if not snapshot or not isinstance(snapshot.get("labelIds"), list):
        raise ValueError("missing message snapshot")
    expected = set(snapshot["labelIds"])
    if action == "trash":
        expected.add("TRASH")
        expected.discard("INBOX")
    elif action == "archive":
        expected.discard("INBOX")
    elif action == "labels":
        expected.update(payload.get("addLabelIds", []))
        expected.difference_update(payload.get("removeLabelIds", []))
    else:
        raise ValueError("unsupported label mutation")
    return expected