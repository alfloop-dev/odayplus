"""Durable PostgreSQL persistence round-trip (ODP-GAP-PERSIST-001).

Proves the task's core acceptance: intake, decision, and audit records written
through the *same* durable repositories used by the product API survive a full
session teardown and are read back verbatim by a FRESH connection — the property
an in-memory backend cannot provide.

The PostgreSQL-backed tests are marked ``requires_live_env`` and are excluded
from the default CI marker expression. They run against either:

- ``ODP_TEST_PG_DSN`` pointing at a reachable PostgreSQL, or
- an ephemeral PostgreSQL 16 provisioned by the ``pgserver`` package (bundled
  binaries, no root) — the same mechanism the assisted-intake schema suite uses.

When neither is available the fixture skips. The in-memory contrast test
(``test_memory_mode_does_not_persist``) has no such dependency and always runs.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from modules.sitescore.domain.scoring import SiteScoreRecommendation
from shared.audit.events import AuditEvent
from shared.infrastructure.persistence.factory import _memory_bundle, _postgres_bundle
from shared.workflow.sitescore import DecisionStatus, SiteScoreDecision


def _write_records(bundle) -> None:
    """Write one intake, one decision, and one audit record through the bundle."""
    # Intake / OperatorConsole path (durable document store).
    bundle.store_ops_repository.save_idempotency_result(
        "intake-idem-1", {"issueId": "ISS-1", "status": "acknowledged"}
    )

    # Decision path (SiteScore decision store).
    bundle.sitescore_decision_store.save_decision(
        SiteScoreDecision(
            decision_id="DEC-1",
            candidate_site_id="CAND-1",
            report_id="RPT-1",
            report_version=1,
            recommendation=SiteScoreRecommendation.GO,
            status=DecisionStatus.APPROVED,
            policy_version="policy-2026.1",
            model_version="model-2026.1",
            created_by="reviewer",
            created_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        )
    )

    # Audit path (columnar, correlation-indexed, WORM-chained).
    bundle.audit_log.record(
        AuditEvent(
            event_type="decision.approved",
            actor="reviewer",
            action="approve",
            resource="sitescore/DEC-1",
            outcome="success",
            correlation_id="corr-1",
            metadata={"decision_id": "DEC-1"},
        )
    )


@pytest.fixture(scope="session")
def pg_conninfo():
    psycopg = pytest.importorskip(
        "psycopg", reason="durable Postgres tests need the psycopg driver"
    )

    dsn = os.environ.get("ODP_TEST_PG_DSN")
    if dsn:
        try:
            psycopg.connect(dsn, autocommit=True).close()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"ODP_TEST_PG_DSN unreachable: {exc}")
        yield psycopg.conninfo.conninfo_to_dict(dsn)
        return

    pgserver = pytest.importorskip(
        "pgserver",
        reason="no ODP_TEST_PG_DSN and pgserver (bundled PostgreSQL 16) unavailable",
    )
    import tempfile

    data_dir = tempfile.mkdtemp(prefix="gap-persist-pg16-")
    server = pgserver.get_server(data_dir)
    try:
        yield psycopg.conninfo.conninfo_to_dict(server.get_uri())
    finally:
        server.cleanup()


@pytest.mark.requires_live_env
def test_durable_round_trip_survives_fresh_session(pg_conninfo):
    # Session 1: write through the durable Postgres bundle, then dispose it.
    bundle = _postgres_bundle(conninfo=pg_conninfo)
    assert bundle.mode == "postgres"
    assert bundle.is_durable
    _write_records(bundle)
    bundle.engine.close()

    # Session 2: a brand-new connection/bundle on the same database reads it back.
    fresh = _postgres_bundle(conninfo=pg_conninfo)
    try:
        intake = fresh.store_ops_repository.get_idempotency_result("intake-idem-1")
        assert intake == {"issueId": "ISS-1", "status": "acknowledged"}

        decision = fresh.sitescore_decision_store.get_decision("DEC-1")
        assert decision is not None
        assert decision.recommendation is SiteScoreRecommendation.GO
        assert decision.status is DecisionStatus.APPROVED
        assert decision.policy_version == "policy-2026.1"

        events = fresh.audit_log.list_events(correlation_id="corr-1")
        assert len(events) == 1
        assert events[0].action == "approve"
        assert events[0].metadata == {"decision_id": "DEC-1"}
        # The audit hash-chain verifies after the restart.
        fresh.audit_log.verify_chain().raise_for_tamper()
    finally:
        fresh.engine.close()


@pytest.mark.requires_live_env
def test_schema_migrations_tracked_once_only(pg_conninfo):
    from shared.infrastructure.persistence.postgres_engine import (
        _SCHEMA_FILES,
        PostgresEngine,
    )

    first = PostgresEngine(pg_conninfo)
    try:
        revisions = first.applied_revisions()
        expected = [name.split("_", 1)[0] for name in _SCHEMA_FILES]
        assert revisions == expected
    finally:
        first.close()

    # Re-bootstrapping the same database must be a no-op: no new rows, no error.
    second = PostgresEngine(pg_conninfo)
    try:
        assert second.applied_revisions() == expected
        rows = second.query("SELECT COUNT(*) AS n FROM schema_migrations")
        assert int(rows[0]["n"]) == len(expected)
    finally:
        second.close()


def test_memory_mode_does_not_persist():
    """The durability property is exclusive to the durable backends.

    Rebuilding an in-memory bundle starts from empty state, so the same
    round-trip that Postgres passes must FAIL to read anything back here — this
    is what "passing only in durable mode" means.
    """
    bundle = _memory_bundle()
    _write_records(bundle)

    fresh = _memory_bundle()
    assert fresh.store_ops_repository.get_idempotency_result("intake-idem-1") is None
    assert fresh.sitescore_decision_store.get_decision("DEC-1") is None
    assert fresh.audit_log.list_events(correlation_id="corr-1") == []
