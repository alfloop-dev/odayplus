"""Run the ODay Plus worker deployment unit (ODP-SD-03 §4).

    python -m apps.worker.oday_worker

Claims and executes durable jobs from the shared persistence bundle until the
process is stopped.
"""

from __future__ import annotations

import logging

from apps.api.server import bootstrap_runtime, build_worker


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO)
    bundle = bootstrap_runtime()
    build_worker(bundle).loop()


if __name__ == "__main__":  # pragma: no cover
    main()
