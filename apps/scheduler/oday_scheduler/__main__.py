"""Run the ODay Plus scheduler deployment unit (ODP-SD-03 §4).

    python -m apps.scheduler.oday_scheduler

Periodically enqueues the baseline recurring jobs (ODP-SD-08 §6) onto the shared
persistence bundle for the worker to claim.
"""

from __future__ import annotations

import logging
import os

from apps.api.server import bootstrap_runtime, build_scheduler


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO)
    interval = float(os.environ.get("ODP_SCHEDULER_INTERVAL", "30"))
    bundle = bootstrap_runtime()
    build_scheduler(bundle).loop(interval=interval)


if __name__ == "__main__":  # pragma: no cover
    main()
