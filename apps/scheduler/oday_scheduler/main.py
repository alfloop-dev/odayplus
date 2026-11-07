from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.jobs.queue import JobRequest
from shared.observability import (
    ProductionMetricsExporter,
    SpanKind,
    Telemetry,
    TraceContext,
    new_correlation_id,
)

logger = logging.getLogger("oday-scheduler")

#: Primary env var naming the tenant every scheduled ingest job runs as, with
#: the process-wide ``ODP_TENANT_ID`` (already used by the backfill CLI) as the
#: fallback for single-tenant deployments.
SCHEDULED_TENANT_ENV_VAR = "ODP_SCHEDULED_INGESTION_TENANT_ID"
FALLBACK_TENANT_ENV_VAR = "ODP_TENANT_ID"
MISSING_TENANT_REASON_CODE = "scheduled_ingestion_tenant_missing"
INVALID_CUTOVER_MODE_REASON_CODE = "external_data_cutover_mode_invalid"
CUTOVER_SKIP_REASON_CODE = "external_fetch_decommissioned"

#: The one recurring job a tick enqueues while odayplus still owns external
#: fetch. ODP-XR-CUTOVER-PREP-002 made that conditional rather than removing it:
#: see :meth:`ODayScheduler.recurring_job_types`.
EXTERNAL_FETCH_JOB_TYPE = "external-fetch"

#: Refreshes the production Store Ops document from canonical stores and KPIs
#: (ODP-STOREOPS-STATE-MATERIALIZER-001). Only scheduled where the Store Ops
#: repository is live: SQLite and in-memory bundles keep their seeded state.
STORE_OPS_MATERIALIZE_JOB_TYPE = "store-ops-materialize"


class SchedulerTenantConfigurationError(RuntimeError):
    """Fail-closed: the scheduler has no configured tenant to enqueue work for.

    A scheduled ingest carries no authenticated principal, so the tenant can
    only come from deployment config. Enqueueing without one would hand the
    worker a job it must either reject or — worse — run against the unscoped
    default store, so the tick refuses to enqueue at all.
    """

    code = MISSING_TENANT_REASON_CODE


class SchedulerCutoverConfigurationError(RuntimeError):
    """Fail-closed: the deployment's cutover mode is not one this release knows.

    Treated exactly like a missing tenant: the tick enqueues nothing and says so
    every time, until an operator fixes the variable. Guessing a mode would mean
    either fetching from a deployment an operator believes is cut over, or
    silently stopping ingestion on one they believe is not.
    """

    code = INVALID_CUTOVER_MODE_REASON_CODE


class SchedulerNativeIntakeHandoffError(RuntimeError):
    """A native phase failed; earlier durable admissions remain valid."""

    code = "native_intake_handoff_incomplete"


def scheduler_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-scheduler"}


def resolve_scheduled_tenant_id(env: Mapping[str, str] | None = None) -> str:
    """Read the configured scheduled-ingestion tenant; ``""`` when unset."""
    source = os.environ if env is None else env
    for name in (SCHEDULED_TENANT_ENV_VAR, FALLBACK_TENANT_ENV_VAR):
        value = str(source.get(name, "") or "").strip()
        if value:
            return value
    return ""


class ODayScheduler:
    def __init__(
        self,
        persistence: PersistenceBundle | None = None,
        telemetry: Telemetry | None = None,
        tenant_id: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.persistence = persistence or build_persistence()
        self.job_queue = self.persistence.job_queue
        self.telemetry = telemetry or Telemetry("oday-scheduler")
        self.env = os.environ if env is None else env
        self.last_native_handoff: dict[str, Any] = {}
        self.tenant_id = (
            str(tenant_id).strip()
            if tenant_id is not None
            else resolve_scheduled_tenant_id(self.env)
        )

    def _require_tenant_id(self) -> str:
        if self.tenant_id:
            return self.tenant_id
        raise SchedulerTenantConfigurationError(
            "Scheduled ingestion has no configured tenant scope: set "
            f"{SCHEDULED_TENANT_ENV_VAR} (or {FALLBACK_TENANT_ENV_VAR}) on the "
            "oday-scheduler deployment, or construct ODayScheduler(tenant_id=...). "
            "No external-fetch job was enqueued "
            f"(code={MISSING_TENANT_REASON_CODE})"
        )

    def _resolve_cutover_mode(self) -> str:
        """Read the cutover switch for this tick.

        Imported lazily: the switch is defined by the read facade, and a
        scheduler process should not pay for that import unless a tick actually
        runs. Resolved per tick, never cached, so pulling the kill switch takes
        effect on the next tick instead of the next redeploy.
        """
        from modules.external_data.application.market_data_facade import (
            MarketDataValidationError,
            resolve_cutover_mode,
        )

        try:
            return resolve_cutover_mode(self.env)
        except MarketDataValidationError as exc:
            raise SchedulerCutoverConfigurationError(
                f"{exc} No external-fetch job was enqueued "
                f"(code={INVALID_CUTOVER_MODE_REASON_CODE})"
            ) from exc

    def _store_ops_live(self) -> bool:
        repository = getattr(self.persistence, "store_ops_repository", None)
        return bool(getattr(repository, "is_live", False))

    def recurring_job_types(self) -> tuple[str, ...]:
        """Job types a tick enqueues under the current cutover mode.

        ``store-ops-materialize`` runs wherever the Store Ops repository is live
        (PostgreSQL), independent of the cutover. ``external-fetch`` only runs
        while odayplus still owns external fetch, so after the cutover a
        non-live deployment schedules nothing. The deployment unit stays either
        way: health and metrics still report, and a future recurring job
        registers here.
        """
        store_ops = (STORE_OPS_MATERIALIZE_JOB_TYPE,) if self._store_ops_live() else ()
        return store_ops + self._external_fetch_job_types()

    def _external_fetch_job_types(self) -> tuple[str, ...]:
        from modules.external_data.application.market_data_facade import (
            legacy_external_fetch_enabled,
        )

        return (EXTERNAL_FETCH_JOB_TYPE,) if legacy_external_fetch_enabled(self.env) else ()

    def _enqueue_store_ops_materialize(self, tenant_id: str, correlation_id: str) -> None:
        try:
            self.job_queue.enqueue(
                JobRequest(
                    job_type=STORE_OPS_MATERIALIZE_JOB_TYPE,
                    payload={"tenant_id": tenant_id},
                    idempotency_key=(
                        f"store-ops-materialize:{tenant_id}:"
                        f"{datetime.now(UTC).strftime('%Y%m%d%H%M')}"
                    ),
                ),
                correlation_id=correlation_id,
            )
            self.telemetry.logger.info(
                "Scheduler enqueued store-ops-materialize job",
                correlation_id=correlation_id,
                actor="scheduler",
                resource="job/store-ops-materialize",
                action="enqueue",
                result="ok",
            )
        except Exception as exc:
            self.telemetry.logger.error(
                f"Failed to enqueue scheduled job: {exc}",
                correlation_id=correlation_id,
                actor="scheduler",
                resource="scheduler/tick",
                error_code=type(exc).__name__,
            )

    def _run_native_intake(self, tenant_id: str, correlation_id: str) -> None:
        """Resume existing owners from canonical state, without a second queue.

        A matching-policy reference in the existing parser deployment opts a
        source into this tick. It selects authority, never creates or approves
        it. Owners independently verify all current positive permissions.
        """
        from modules.external_data.application.native_parser_deployments import (
            DEPLOYMENTS_ENV,
            parse_native_parser_deployments,
        )
        from modules.listing.application.native_intake_job_runtime import native_intake_uuid

        report: dict[str, Any] = {"configured": False, "partitions": [], "errors": []}
        self.last_native_handoff = report
        try:
            deployments = parse_native_parser_deployments(self.env.get(DEPLOYMENTS_ENV))
            report["configured"] = bool(deployments.matching_policy_versions)
            if not report["configured"]:
                return
            tenant_id = native_intake_uuid(tenant_id)
        except Exception as error:
            report["errors"].append({"phase": "configuration", "error_class": type(error).__name__})
            raise SchedulerNativeIntakeHandoffError(SchedulerNativeIntakeHandoffError.code) from None

        from apps.api.oday_api.security.dependencies import default_boundary
        from modules.opsboard.auth import Credentials

        def consume(phase, partition, command):
            receipts = partition[phase] = []
            try:
                for receipt in command():
                    receipts.append({key: receipt[key] for key in
                        ("job_id", "intake_id", "match_case_id", "decision_id") if key in receipt})
            except Exception as error:
                report["errors"].append({**{key: partition[key] for key in ("tenant_id", "source_id") if key in partition},
                    "phase": phase, "error_class": type(error).__name__})

        def scan(factory, partition, phase, *, progress_key=None, **selection):
            owner = factory()
            try:
                yield from owner.admit_partition(tenant_id, limit=25, **selection)
            finally:
                progress = getattr(owner, "last_partition_progress", None)
                if progress is not None:
                    partition[progress_key or phase + "_progress"] = progress
                    if progress["conflicts"]:
                        report["errors"].append({**{key: partition[key] for key in ("tenant_id", "source_id") if key in partition},
                            "phase": phase, "reason": "native_partition_conflicts"})

        # These are tenant-scoped native owners: they consume only the actual
        # submission/state/request events in their own configured realm. Each
        # admission retains its current human/source/configuration checks.
        tenant_progress: dict[str, Any] = {"tenant_id": tenant_id}
        report["tenant_phases"] = tenant_progress
        consume("identity_starts", tenant_progress,
            lambda: scan(self.persistence.canonical_intake_start, tenant_progress, "identity_starts"))
        consume("identity_checks", tenant_progress,
            lambda: scan(self.persistence.canonical_intake_identity, tenant_progress, "identity_checks"))
        consume("source_policy_rechecks", tenant_progress,
            lambda: scan(self.persistence.canonical_intake_source_policy_recheck, tenant_progress, "source_policy_rechecks"))
        consume("source_policy_checks", tenant_progress,
            lambda: scan(self.persistence.canonical_intake_source_policy, tenant_progress, "source_policy_checks"))
        consume("retrieval_admissions", tenant_progress,
            lambda: scan(self.persistence.canonical_intake_retrieval, tenant_progress, "retrieval_admissions"))
        consume("assisted_entry_admissions", tenant_progress,
            lambda: scan(self.persistence.canonical_intake_assisted_entry, tenant_progress, "assisted_entry_admissions"))

        for source, policy in deployments.matching_policy_versions:
            partition: dict[str, Any] = {"source_id": source}
            report["partitions"].append(partition)
            consume("parser_admissions", partition, lambda source=source, partition=partition: scan(
                self.persistence.canonical_intake_parser, partition, "parser_admissions", progress_key="parser_progress",
                source_id=source, release_id=deployments.release_for_source(source)))

            consume("matching_proposals", partition, lambda source=source, policy=policy: self.persistence.canonical_match_cases().match_partition(
                tenant_id, source_id=source, policy_version_id=policy, boundary=default_boundary(),
                credentials=Credentials(bearer_token=self.env.get("ODP_NATIVE_INTAKE_MATCHER_BEARER_TOKEN")), limit=25))
            consume("exact_admissions", partition, lambda source=source, policy=policy: self.persistence.canonical_intake_identity(
                identity_service_boundary=default_boundary).confirm_exact_partition(
                tenant_id, source_id=source, policy_version_id=policy,
                replay_store=self.persistence.assisted_intake_store, limit=25))

        if report["errors"]:
            raise SchedulerNativeIntakeHandoffError(SchedulerNativeIntakeHandoffError.code) from None
        self.telemetry.logger.info("Scheduler native Intake handoff completed",
            correlation_id=correlation_id, actor="scheduler", resource="scheduler/native-intake", result="ok")

    def run_once(self) -> None:
        """Orchestrate and enqueue the scheduled tasks.

        Raises :class:`SchedulerTenantConfigurationError` when no tenant is
        configured and :class:`SchedulerCutoverConfigurationError` when the
        cutover mode is unreadable; nothing is enqueued in either case.

        Once the cutover selects ``PLATFORM_PRIMARY`` the tick still runs and
        still checks its tenant -- a deployment that lost its scheduled-ingestion
        tenant is misconfigured whether or not there is work to enqueue -- but it
        enqueues no external fetch, so no provider credential can be reached on a
        schedule. A live Store Ops repository still gets its materialize job.
        Moving the mode back, or pulling the kill switch, restores enqueueing on
        the very next tick without a redeploy.
        """
        correlation_id = new_correlation_id()
        self.last_native_handoff = {}
        context = TraceContext(
            correlation_id=correlation_id,
            actor_id="scheduler",
        )
        with self.telemetry.tracer.start_span("scheduler-tick", SpanKind.WORKER, context=context):
            self.telemetry.logger.info(
                "Scheduler tick start",
                correlation_id=correlation_id,
                actor="scheduler",
                resource="scheduler/tick",
            )
            mode = self._resolve_cutover_mode()
            tenant_id = self._require_tenant_id()
            job_types = self.recurring_job_types()
            if STORE_OPS_MATERIALIZE_JOB_TYPE in job_types:
                self._enqueue_store_ops_materialize(tenant_id, correlation_id)
            if EXTERNAL_FETCH_JOB_TYPE not in job_types:
                self.telemetry.logger.info(
                    "Scheduler tick enqueued no external-fetch job: external fetch is "
                    f"decommissioned on this deployment (cutover mode {mode})",
                    correlation_id=correlation_id,
                    actor="scheduler",
                    resource="scheduler/tick",
                    action="skip",
                    result="ok",
                    error_code=CUTOVER_SKIP_REASON_CODE,
                )
                self._run_native_intake(tenant_id, correlation_id)
                return
            try:
                self.job_queue.enqueue(
                    JobRequest(
                        job_type=EXTERNAL_FETCH_JOB_TYPE,
                        payload={
                            "tenant_id": tenant_id,
                            "provider_id": "listing.partner_feed",
                            "schedule_id": "hourly-listing",
                            "freshness_sla_hours": 6,
                        },
                        # The tenant belongs in the key: two tenants sharing one
                        # window must not collapse into a single queued job.
                        idempotency_key=(
                            f"scheduled-fetch:{tenant_id}:"
                            f"{datetime.now(UTC).strftime('%Y%m%d%H%M')}"
                        ),
                    ),
                    correlation_id=correlation_id,
                )
                self.telemetry.logger.info(
                    "Scheduler enqueued external-fetch job",
                    correlation_id=correlation_id,
                    actor="scheduler",
                    resource="job/external-fetch",
                    action="enqueue",
                    result="ok",
                )
            except Exception as exc:
                self.telemetry.logger.error(
                    f"Failed to enqueue scheduled job: {exc}",
                    correlation_id=correlation_id,
                    actor="scheduler",
                    resource="scheduler/tick",
                    error_code=type(exc).__name__,
                )
            self._run_native_intake(tenant_id, correlation_id)

    def export_metrics(self) -> dict[str, Any] | None:
        """Export scheduler metrics snapshot via ProductionMetricsExporter if exact 40-char release SHA is present in environment."""
        from shared.runtime_config import get_release_identity

        sha = get_release_identity().lower()
        if sha and len(sha) == 40 and sha != "local":
            try:
                exporter = ProductionMetricsExporter(release_sha=sha, registry=self.telemetry.metrics)
                return exporter.export_metrics()
            except Exception as exc:
                self.telemetry.logger.error(
                    f"Scheduler metrics export failed: {exc}",
                    correlation_id="unknown",
                    resource="scheduler/metrics",
                    error_code=type(exc).__name__,
                )
                raise
        return None

    def loop(self, stop_event: Any = None, interval: float = 30.0) -> None:
        while stop_event is None or not stop_event.is_set():
            try:
                self.run_once()
            except (
                SchedulerTenantConfigurationError,
                SchedulerCutoverConfigurationError,
                SchedulerNativeIntakeHandoffError,
            ) as exc:
                # Keep reporting configuration or phase errors on every tick.
                # Native failures can follow durable admissions; canonical
                # state and last_native_handoff retain that partial progress.
                self.telemetry.logger.error(
                    f"Scheduler tick skipped: {exc}",
                    correlation_id="unknown",
                    actor="scheduler",
                    resource="scheduler/tick",
                    error_code=exc.code,
                )
            try:
                self.export_metrics()
            except Exception as exc:
                self.telemetry.logger.error(
                    f"Scheduler lifecycle export_metrics failed: {exc}",
                    correlation_id="unknown",
                    resource="scheduler/metrics",
                    error_code=type(exc).__name__,
                )
            time.sleep(interval)
