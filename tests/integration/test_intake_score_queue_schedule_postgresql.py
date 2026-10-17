"""Real database scheduling and clocks, without model or source admission claims."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from shared.infrastructure.persistence import job_queue as queue_module
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import JobDeliveryState, JobFenceRejectedError, JobRequest, JobStatus
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_intake_score_delivery_postgresql import (
    _approved_score,
    _physical,
    _promotion_client,
)
from tests.integration.test_intake_score_delivery_postgresql import (
    _claim as _claim_score,
)
from tests.integration.test_intake_score_delivery_postgresql import (
    explicit_local_signing_key as explicit_local_signing_key,
)
from tests.integration.test_operator_intake_workflow_postgresql import (
    bridge_database as bridge_database,
)

pytestmark = pytest.mark.requires_live_env


def _claim(queue, method="claim_next"):
    return queue.claim_next() if method == "claim_next" else queue.lease(45)


def _set_due(engine, job_id, seconds):
    # Mirror scheduling metadata in the same transaction as the authoritative
    # due time. The payload remains reference-only.
    with engine.lock:
        due = engine.query_one(
            "SELECT statement_timestamp()+(? * interval '1 second') AS due", (seconds,),
        )["due"]
        engine.execute("UPDATE workflow.jobs SET next_attempt_at=? WHERE job_id=?", (due, job_id))
        engine.execute("UPDATE durable_jobs SET available_at=? WHERE job_id=?", (due, job_id))


def _retry_job(engine, queue, *, state="RETRYING", due_seconds=600, tenant=None):
    tenant = tenant or str(uuid4())
    job, _ = queue.enqueue(
        JobRequest("intake-sitescore", {"tenant_id": tenant}), correlation_id=str(uuid4()),
    )
    engine.execute(
        "INSERT INTO workflow.jobs(job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
        "status,checkpoint,max_attempts,timeout_at,next_attempt_at,lease_expires_at,payload,correlation_id) "
        "VALUES (?,?,'intake-sitescore','candidate',?,?, 'SCORE_QUEUED',5,"
        "statement_timestamp()+interval '1 day',statement_timestamp()+(? * interval '1 second'),"
        "statement_timestamp()-interval '1 second','{}',?)",
        (job.job_id, tenant, str(uuid4()), state, due_seconds, str(uuid4())),
    )
    queue.update_status(job.job_id, JobStatus.QUEUED, delivery_state=JobDeliveryState.RETRYING)
    _set_due(engine, job.job_id, due_seconds)
    return queue.get(job.job_id)


@pytest.mark.parametrize("method", ["claim_next", "lease"])
def test_generic_postgresql_queue_does_not_require_workflow_schema(intake_blank_db, method):
    engine = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    try:
        assert engine.query_one("SELECT to_regclass('workflow.jobs') AS name")["name"] is None
        queue = DurableJobQueue(engine)
        job, _ = queue.enqueue(JobRequest("ordinary", {}), correlation_id="synthetic")
        assert _claim(queue, method).job_id == job.job_id
    finally:
        engine.close()


@pytest.mark.parametrize("method", ["claim_next", "lease"])
def test_future_retry_is_not_claimed_and_does_not_hide_other_work(intake_db, method):
    engine = PostgresEngine(intake_db.url(), validate_schema=False)
    try:
        queue = DurableJobQueue(engine, canonical_intake_score_schedule=True)
        pending = _retry_job(engine, queue)
        other, _ = queue.enqueue(JobRequest("ordinary", {}), correlation_id="synthetic")
        assert _claim(queue, method).job_id == other.job_id
        assert _claim(queue, method) is None
        assert queue.get(pending.job_id) == pending
        _set_due(engine, pending.job_id, -1)
        claimed = _claim(queue, method)
        assert claimed.job_id == pending.job_id
        assert claimed.attempts == pending.attempts + 1
        assert claimed.delivery_state == JobDeliveryState.RETRYING
    finally:
        engine.close()


@pytest.mark.parametrize("year", [2000, 2099])
def test_due_and_claim_lease_use_database_clock_not_worker_clock(intake_db, monkeypatch, year):
    engine = PostgresEngine(intake_db.url(), validate_schema=False)
    try:
        queue = DurableJobQueue(engine, canonical_intake_score_schedule=True)
        pending = _retry_job(engine, queue)

        class SkewedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(year, 1, 1, tzinfo=UTC).astimezone(tz or UTC)

        monkeypatch.setattr(queue_module, "datetime", SkewedClock)
        assert queue.claim_next() is None
        assert queue.get(pending.job_id) == pending
        _set_due(engine, pending.job_id, -1)
        before = datetime.fromisoformat(engine.query_one("SELECT statement_timestamp() AS now")["now"])
        claimed = queue.claim_next()
        after = datetime.fromisoformat(engine.query_one("SELECT statement_timestamp() AS now")["now"])
        assert claimed.job_id == pending.job_id
        assert before <= claimed.heartbeat_at <= after
        assert (claimed.lease_expires_at - claimed.heartbeat_at).total_seconds() == 45
        version = queue.heartbeat(claimed.job_id, claimed.version, claimed.fence_token)
        renewed = queue.get(claimed.job_id)
        after_heartbeat = datetime.fromisoformat(engine.query_one("SELECT statement_timestamp() AS now")["now"])
        assert renewed.version == version == claimed.version + 1
        assert after <= renewed.heartbeat_at <= after_heartbeat
        assert (renewed.lease_expires_at - renewed.heartbeat_at).total_seconds() == 45
    finally:
        engine.close()


def test_canonical_delivery_without_schedule_is_parked_without_consuming_attempt(intake_db):
    engine = PostgresEngine(intake_db.url(), validate_schema=False)
    try:
        queue = DurableJobQueue(engine, canonical_intake_score_schedule=True)
        pending = _retry_job(engine, queue, due_seconds=-1)
        engine.execute("UPDATE durable_jobs SET available_at=NULL WHERE job_id=?", (pending.job_id,))
        assert queue.claim_next() is None
        assert queue.get(pending.job_id) == pending
    finally:
        engine.close()


def test_postgresql_heartbeat_cannot_resurrect_expired_delivery(intake_db):
    engine = PostgresEngine(intake_db.url(), validate_schema=False)
    try:
        queue = DurableJobQueue(engine, canonical_intake_score_schedule=True)
        pending = _retry_job(engine, queue, due_seconds=-1)
        claimed = queue.claim_next()
        assert claimed.job_id == pending.job_id
        engine.execute(
            "UPDATE durable_jobs SET lease_expires_at=statement_timestamp()-interval '1 second' WHERE job_id=?",
            (claimed.job_id,),
        )
        expired = queue.get(claimed.job_id)
        with pytest.raises(JobFenceRejectedError):
            queue.heartbeat(claimed.job_id, claimed.version, claimed.fence_token)
        assert queue.get(claimed.job_id) == expired
    finally:
        engine.close()


def test_schedule_update_locked_in_transaction_cannot_be_claimed_early(intake_db):
    engine = PostgresEngine(intake_db.url(), validate_schema=False)
    replica = PostgresEngine(intake_db.url(), bootstrap=False, validate_schema=False)
    try:
        queue = DurableJobQueue(engine, canonical_intake_score_schedule=True)
        other_queue = DurableJobQueue(replica, canonical_intake_score_schedule=True)
        pending = _retry_job(engine, queue, due_seconds=-1)
        other, _ = queue.enqueue(JobRequest("ordinary", {}), correlation_id="synthetic")
        with intake_db.connect(autocommit=False) as connection:
            # Same canonical-then-delivery lock order used by the bridge. A
            # concurrent poll sees the committed old due time, but must skip
            # this locked delivery row and still make progress on other work.
            connection.execute("SELECT job_id FROM workflow.jobs WHERE job_id=%s FOR UPDATE", (pending.job_id,))
            connection.execute("SELECT job_id FROM odp_runtime.durable_jobs WHERE job_id=%s FOR UPDATE", (pending.job_id,))
            connection.execute(
                "UPDATE workflow.jobs SET next_attempt_at=statement_timestamp()+interval '10 minutes' WHERE job_id=%s",
                (pending.job_id,),
            )
            connection.execute(
                "UPDATE odp_runtime.durable_jobs SET available_at=(SELECT next_attempt_at FROM workflow.jobs WHERE job_id=%s) WHERE job_id=%s",
                (pending.job_id, pending.job_id),
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                claimed = pool.submit(other_queue.claim_next).result(timeout=5)
                assert claimed.job_id == other.job_id
            connection.commit()
        assert queue.claim_next() is None
        assert queue.get(pending.job_id) == pending
    finally:
        replica.close()
        engine.close()


@pytest.mark.parametrize("checkpoint", ["due", "rescheduled_future", "cancelled"])
def test_canonical_retry_heartbeat_before_begin_respects_due_and_cancellation(
    bridge_database, monkeypatch, checkpoint,
):
    from modules.listing.application.intake_score_delivery import IntakeScoreJobRepository

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        repository = IntakeScoreJobRepository(bundle)
        _, _, receipt = _approved_score(bridge_database, bundle, _promotion_client(bundle))
        job_id = receipt["site_score_job_id"]
        first = _claim_score(bundle, job_id)
        attempt = repository.begin_attempt(first)
        repository.fail_attempt(first, attempt, "SYNTHETIC_TRANSIENT_BOUNDARY_PROBE", True)
        _set_due(bundle.engine, job_id, -1)
        retry = _claim_score(bundle, job_id)
        if checkpoint == "rescheduled_future":
            bundle.engine.execute(
                "UPDATE workflow.jobs SET next_attempt_at=statement_timestamp()+interval '1 hour' WHERE job_id=?",
                (job_id,),
            )
        elif checkpoint == "cancelled":
            bundle.engine.execute(
                "UPDATE workflow.jobs SET cancellation_requested_at=statement_timestamp(),version=version+1 WHERE job_id=?",
                (job_id,),
            )
        canonical_before = _physical(bridge_database, job_id)
        delivery_before = bundle.job_queue.get(job_id)
        assert canonical_before["status"] == "RETRYING"
        if checkpoint == "due":
            version = repository.heartbeat(job_id, retry.version, retry.fence_token)
            delivery_after = bundle.job_queue.get(job_id)
            assert delivery_after.version == version == delivery_before.version + 1
            assert delivery_after.lease_expires_at >= delivery_before.lease_expires_at
            # Before begin, only the delivery lease changes; the canonical
            # attempt, acceptance, retry checkpoint and counter remain intact.
            assert _physical(bridge_database, job_id) == canonical_before
            next_attempt = repository.begin_attempt(retry)
            assert next_attempt is not None
            running = _physical(bridge_database, job_id)
            assert running["status"] == "RUNNING"
            assert running["attempt"] == canonical_before["attempt"] + 1
            assert running["payload"]["worker_acceptance"] == canonical_before["payload"]["worker_acceptance"]
        else:
            with pytest.raises(JobFenceRejectedError):
                repository.heartbeat(job_id, retry.version, retry.fence_token)
            assert _physical(bridge_database, job_id) == canonical_before
            assert bundle.job_queue.get(job_id) == delivery_before
    finally:
        bundle.engine.close()


def test_canonical_queue_claim_is_visible_under_forced_tenant_rls(intake_db):
    """A production worker must not need a superuser to see a due score job."""
    from psycopg import sql

    engine = PostgresEngine(intake_db.url(), validate_schema=False)
    role = "synthetic_queue_" + uuid4().hex
    with intake_db.connect() as connection:
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA odp_runtime TO {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT SELECT,UPDATE ON odp_runtime.durable_jobs TO {}").format(sql.Identifier(role)))
    try:
        queue = DurableJobQueue(engine, canonical_intake_score_schedule=True)
        pending = _retry_job(engine, queue, due_seconds=-1)
        with engine.lock:
            # Delivery polling has no tenant context or permission to read any
            # workflow business row. It must rely only on delivery metadata.
            engine.execute("SET LOCAL ROLE " + role)  # internally generated ASCII identifier
            actual_role = engine.query_one("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert actual_role == {"rolsuper": False, "rolbypassrls": False}
            unscoped = queue.claim_next()
        assert unscoped is not None, "Delivery polling must not read tenant-protected business rows"
        assert unscoped.job_id == pending.job_id
        # Grant SELECT only after the successful poll, then demonstrate that
        # RLS is still active: the canonical row is hidden until tenant binding.
        with intake_db.connect() as connection:
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA workflow TO {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT SELECT ON workflow.jobs TO {}").format(sql.Identifier(role)))
        with engine.lock:
            engine.execute("SET LOCAL ROLE " + role)
            assert engine.query_one("SELECT job_id FROM workflow.jobs WHERE job_id=?", (pending.job_id,)) is None
            engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (pending.payload["tenant_id"],))
            assert engine.query_one("SELECT job_id FROM workflow.jobs WHERE job_id=?", (pending.job_id,))["job_id"] == pending.job_id
    finally:
        with intake_db.connect() as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        engine.close()


def test_existing_postgresql_queue_upgrades_availability_without_losing_jobs(intake_blank_db):
    engine = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    job, _ = DurableJobQueue(engine).enqueue(JobRequest("ordinary", {}), correlation_id="synthetic")
    # Represent the previous runtime schema and fingerprint. Existing runtime
    # bootstraps must add the nullable column without recreating the queue.
    engine.execute("ALTER TABLE durable_jobs DROP COLUMN available_at")
    engine.execute("DELETE FROM schema_migrations WHERE migration_id LIKE ?", ("000008_postgresql_runtime_persistence:sha256:%",))
    engine.close()
    reopened = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    try:
        assert reopened.query_one("SELECT available_at FROM durable_jobs WHERE job_id=?", (job.job_id,)) == {"available_at": None}
        assert DurableJobQueue(reopened).claim_next().job_id == job.job_id
        reopened.apply_runtime_migration()
        assert DurableJobQueue(reopened).get(job.job_id).attempts == 1
    finally:
        reopened.close()


def test_applied_schedule_schema_bootstraps_without_runtime_ddl_permission(intake_blank_db):
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    admin = PostgresEngine(intake_blank_db.url(), validate_schema=False)
    job, _ = DurableJobQueue(admin).enqueue(JobRequest("ordinary", {}), correlation_id="synthetic")
    admin.close()
    role = "synthetic_runtime_" + uuid4().hex
    with intake_blank_db.connect() as connection:
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA odp_runtime TO {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT SELECT ON odp_runtime.schema_migrations TO {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT SELECT,UPDATE ON odp_runtime.durable_jobs TO {}").format(sql.Identifier(role)))

    def configure(connection):
        connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        connection.commit()

    try:
        with ConnectionPool(intake_blank_db.url(), min_size=1, max_size=1,
                            kwargs={"row_factory": dict_row}, configure=configure) as pool:
            runtime = PostgresEngine(intake_blank_db.url(), pool=pool, validate_schema=False)
            assert runtime.query_one("SELECT has_schema_privilege(current_user,'odp_runtime','CREATE') AS can_ddl") == {"can_ddl": False}
            assert DurableJobQueue(runtime).claim_next().job_id == job.job_id
            runtime.apply_runtime_migration()
    finally:
        with intake_blank_db.connect() as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_restricted_worker_claim_accept_compute_and_commit_preserve_tenant_rls(
    bridge_database, monkeypatch,
):
    from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

    from psycopg import sql

    from modules.sitescore.application.intake_score_worker import handle_intake_score
    from modules.sitescore.tests.test_sitescore_production_runtime import RecordingRuntime
    from shared.infrastructure.persistence.factory import build_persistence
    from tests.intake_registry_fixture import TENANT

    admin = _bundle(bridge_database, monkeypatch)
    _, _, receipt = _approved_score(bridge_database, admin, _promotion_client(admin))
    job_id, candidate_id = receipt["site_score_job_id"], receipt["candidate_site_id"]
    role = "synthetic_worker_" + uuid4().hex
    restricted = None
    with bridge_database.connect() as connection:
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(role)))
        schemas = connection.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'"
        ).fetchall()
        for (schema,) in schemas:
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
    try:
        # Configure every physical connection, without retaining an outer SQL
        # transaction across inference. Never print or persist this local DSN.
        parts = urlsplit(bridge_database.url())
        query = [*parse_qsl(parts.query), ("options", "-c role=" + role)]
        restricted_url = urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))
        with monkeypatch.context() as configured:
            configured.setenv("ODAY_DATABASE_URL", restricted_url)
            restricted = build_persistence(mode="postgresql")
        assert restricted.engine.query_one("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user") == {"rolsuper": False, "rolbypassrls": False}
        assert restricted.engine.query_one("SELECT job_id FROM workflow.jobs WHERE job_id=?", (job_id,)) is None
        claimed = _claim_score(restricted, job_id)

        class ObservedRuntime(RecordingRuntime):
            def infer(self, **kwargs):
                # An independent connection sees committed acceptance before
                # compute; the worker's previous tenant context has ended.
                physical = _physical(bridge_database, job_id)
                assert physical["status"] == "RUNNING"
                assert physical["payload"]["worker_acceptance"]["job_id"] == job_id
                assert restricted.engine.query_one("SELECT job_id FROM workflow.jobs WHERE job_id=?", (job_id,)) is None
                return super().infer(**kwargs)

        runtime = ObservedRuntime()
        handle_intake_score(claimed, restricted, model_runtime=runtime)
        finished = _physical(bridge_database, job_id)
        assert finished["status"] == "SUCCEEDED"
        assert len(runtime.calls) == 1
        reports = restricted._scoped_repository("sitescore_repository", TENANT)
        report = reports.get_report(finished["result"]["report_id"])
        assert report.candidate_site_id == candidate_id
        assert reports.get_prediction_run(finished["result"]["prediction_run_id"]) is not None
        assert restricted.job_queue.get(job_id).status == JobStatus.SUCCEEDED
        assert restricted.engine.query_one("SELECT job_id FROM workflow.jobs WHERE job_id=?", (job_id,)) is None
        with admin.engine.lock:
            admin.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (TENANT,))
            assert admin.engine.query_one("SELECT job_id FROM workflow.jobs WHERE job_id=? AND status='SUCCEEDED'", (job_id,))["job_id"] == job_id
        events = admin.outbox_repository.get_unpublished_events()
        assert len([event for event in events if event.event_type == "candidate.promotion_completed"
                    and event.payload.get("candidate_site_id") == candidate_id]) == 1
        admin.assisted_intake_store.refresh(TENANT)
        assert any(audit["resource_type"] == "job" and audit["resource_id"] == job_id
                   and audit["action"] == "sitescore.completed"
                   for audit in admin.assisted_intake_store.workflow_audits.values())
    finally:
        if restricted is not None:
            restricted.engine.close()
        admin.engine.close()
        with bridge_database.connect() as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
