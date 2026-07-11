#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal E2E worker/scheduler heartbeat.")
    parser.add_argument("--interval", type=float, default=15.0)
    args = parser.parse_args()
    path = Path(os.environ.get("ODP_E2E_WORKER_HEARTBEAT", "/storage/worker-heartbeat.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        event = {
            "time": datetime.now(UTC).isoformat(),
            "worker": "product-e2e-scheduler",
            "status": "alive",
            "persistence": os.environ.get("ODP_PERSISTENCE", "memory"),
            "db_path": os.environ.get("ODP_DB_PATH", ""),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
