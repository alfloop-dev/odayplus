#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.scheduler.oday_scheduler.main import ODayScheduler
from apps.worker.oday_worker.main import ODayWorker
from shared.infrastructure.persistence.factory import build_persistence


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal E2E worker/scheduler heartbeat.")
    parser.add_argument("--interval", type=float, default=15.0)
    args = parser.parse_args()
    
    path = Path(os.environ.get("ODP_E2E_WORKER_HEARTBEAT", "/storage/worker-heartbeat.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)

    # Initialize persistence, worker, and scheduler
    persistence = build_persistence()
    
    stop_event = threading.Event()
    
    worker = ODayWorker(persistence)
    scheduler = ODayScheduler(persistence)
    
    worker_thread = threading.Thread(target=worker.loop, args=(stop_event,), daemon=True)
    scheduler_thread = threading.Thread(target=scheduler.loop, args=(stop_event, 30.0), daemon=True)
    
    worker_thread.start()
    scheduler_thread.start()

    try:
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
    except KeyboardInterrupt:
        stop_event.set()
        
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
