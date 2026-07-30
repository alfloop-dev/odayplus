"""Deployment watch-window status metric emission and durable receipt artifact management."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.observability.metrics import MetricsRegistry, default_registry

DEFAULT_RECEIPT_PATH = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "watch_window_receipt.json"


def record_deployment_watch_window_status(
    release_sha: str,
    status: int = 1,
    *,
    registry: MetricsRegistry | None = None,
    receipt_path: str | Path | None = None,
    watch_window_minutes: int = 15,
) -> dict[str, Any]:
    """Emit the deployment_watch_window_status gauge metric and persist a durable watch-window receipt.

    status: 1 for WATCH_PASSED, 0 for WATCH_FAILED.
    """
    if not release_sha:
        raise ValueError("release_sha must be provided and non-empty")

    status_str = "WATCH_PASSED" if status == 1 else "WATCH_FAILED"
    reg = registry or default_registry()
    reg.set(
        "deployment_watch_window_status",
        float(status),
        labels={"release_sha": release_sha, "status": status_str},
    )

    out_path = Path(receipt_path or DEFAULT_RECEIPT_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    receipt = {
        "release_sha": release_sha,
        "status": status_str,
        "status_code": status,
        "recorded_at": datetime.now(UTC).isoformat(),
        "watch_window_minutes": watch_window_minutes,
        "metric_name": "deployment_watch_window_status",
    }
    out_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def verify_watch_window_receipt(
    expected_release_sha: str,
    receipt_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify that a durable watch-window receipt exists, matches the expected release SHA, and passed."""
    out_path = Path(receipt_path or DEFAULT_RECEIPT_PATH)
    if not out_path.exists():
        raise FileNotFoundError(f"Watch-window receipt artifact absent at '{out_path}'.")

    try:
        receipt = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Watch-window receipt artifact at '{out_path}' is malformed: {e}") from e

    sha = receipt.get("release_sha")
    if sha != expected_release_sha:
        raise ValueError(
            f"Release SHA mismatch in watch-window receipt: expected '{expected_release_sha}', got '{sha}'."
        )

    status_code = receipt.get("status_code")
    status = receipt.get("status")
    if status_code != 1 and status != "WATCH_PASSED":
        raise ValueError(f"Watch-window verification failed with status '{status}' (code {status_code}).")

    return receipt
