"""ODay Plus platform runtime composition root (ODP-FLOW-011).

This module is the single place that wires the first-version deployment units
from ODP-SD-03 §4 together so ``migrations``, ``seed``, ``core-api``,
``worker`` and ``scheduler`` can start from *one* persistence bundle and share
the same durable job model (ODP-SD-08 §3).

Deployment units (ODP-SD-03 §4):

  ============  =========  ======================================================
  Unit          Type       Responsibility (this module's factory)
  ============  =========  ======================================================
  opsboard-web  Frontend   Next.js UI — not built here (calls ``core-api``).
  core-api      API        FastAPI app: ``build_server`` / :func:`main`.
  worker        Worker     Durable job execution: :func:`build_worker`.
  scheduler     Scheduler  Recurring job enqueue: :func:`build_scheduler`.
  migrations    Data       Applied by the durable engine on bootstrap.
  ============  =========  ======================================================

The composition contract enforced here is: **api, worker and scheduler run
against the same** :class:`PersistenceBundle`. In durable mode that bundle owns
one on-disk SQLite database whose schema is applied from
``infra/db/migrations`` at build time, so a job the API/scheduler enqueues is
the job the worker claims and the result survives a process restart
(backup/recovery, ODP-SD-08 §3.2).

Entry points:

    python -m apps.api.server            # run core-api (uvicorn)
    python -m apps.worker.oday_worker    # run the worker loop
    python -m apps.scheduler.oday_scheduler  # run the scheduler loop
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.scheduler.oday_scheduler.main import ODayScheduler
    from apps.worker.oday_worker.main import ODayWorker

logger = logging.getLogger("oday-server")


@dataclass(frozen=True)
class ServiceBoundary:
    """A first-version deployment unit from ODP-SD-03 §4."""

    unit: str
    workload: str
    responsibility: str


# Declarative boundary map (ODP-SD-03 §4). Kept in code so the cross-flow gate
# can assert the runtime composes exactly these units and no domain reinvents a
# shared platform component (ODP-AC-SD03-003).
SERVICE_BOUNDARIES: tuple[ServiceBoundary, ...] = (
    ServiceBoundary("opsboard-web", "Frontend", "UI; calls core-api, no direct DB access"),
    ServiceBoundary("core-api", "API", "Auth, domain routers, Job/Approval/Audit APIs"),
    ServiceBoundary("worker", "Worker", "Durable job execution: integration, forecast, effect eval"),
    ServiceBoundary("scheduler", "Scheduler", "Recurring enqueue: external sync, forecast, data quality"),
)


def bootstrap_runtime(
    *,
    persistence: PersistenceBundle | None = None,
    prime_scheduled_jobs: bool = False,
) -> PersistenceBundle:
    """Build (or accept) the shared persistence bundle for the runtime.

    In durable mode :func:`build_persistence` opens the SQLite engine, which
    applies the ``infra/db/migrations`` schema on bootstrap — this is the
    "migrations run" step. When ``prime_scheduled_jobs`` is set the scheduler is
    run once so a freshly-migrated database is seeded with its baseline recurring
    work before the worker starts (the "seed" step). Both are idempotent.
    """
    bundle = persistence or build_persistence()
    logger.info(
        "Runtime bootstrapped: mode=%s durable=%s", bundle.mode, bundle.is_durable
    )
    if prime_scheduled_jobs:
        scheduler = build_scheduler(bundle)
        scheduler.run_once()
        logger.info("Runtime seeded: baseline scheduled jobs enqueued")
    return bundle


def build_server(persistence: PersistenceBundle | None = None) -> Any:
    """Return the FastAPI ``core-api`` app bound to ``persistence``.

    Returns ``None`` when FastAPI is not installed (the same contract as
    ``apps.api.oday_api.main.create_app``), so this module imports cleanly in a
    dependency-light environment.
    """
    from apps.api.oday_api.main import create_app

    bundle = persistence or bootstrap_runtime()
    return create_app(persistence=bundle)


def build_worker(persistence: PersistenceBundle) -> ODayWorker:
    """Construct the runtime worker bound to the shared bundle."""
    from apps.worker.oday_worker.main import ODayWorker

    return ODayWorker(persistence=persistence)


def build_scheduler(persistence: PersistenceBundle) -> ODayScheduler:
    """Construct the runtime scheduler bound to the shared bundle."""
    from apps.scheduler.oday_scheduler.main import ODayScheduler

    return ODayScheduler(persistence=persistence)


def main() -> None:  # pragma: no cover - process entry point
    """Run the ``core-api`` deployment unit under uvicorn."""
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("ODP_API_HOST", "0.0.0.0")
    port = int(os.environ.get("ODP_API_PORT", "8000"))
    # Migrations (durable mode) and app composition happen inside build_server.
    application = build_server()
    uvicorn.run(application, host=host, port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
