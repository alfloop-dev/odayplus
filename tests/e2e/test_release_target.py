from __future__ import annotations

import json
from pathlib import Path

from delivery_toolchain.e2e._release_target import (
    release_pr_head_command,
    release_pr_label,
    release_pr_number,
    release_pr_view_command,
)


def test_release_target_commands_follow_manifest_pr_number(tmp_path: Path) -> None:
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"release_target": {"pr": 123}}), encoding="utf-8")

    assert release_pr_number(queue) == 123
    assert release_pr_label(queue) == "PR #123"
    assert release_pr_view_command(queue, "headRefOid", "state") == "gh pr view 123 --json headRefOid,state"
    assert release_pr_head_command(queue) == "gh pr view 123 --json headRefOid --jq .headRefOid"
