from __future__ import annotations


def worker_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-worker"}
