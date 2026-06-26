from __future__ import annotations


def scheduler_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-scheduler"}
