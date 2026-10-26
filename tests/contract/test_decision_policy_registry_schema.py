"""The decision policy registry, executed against a real PostgreSQL 16 server.

`infra/db/migrations/000014_decision_policy_registry.sql` lands the table that
ODP-SD-AMD-001 §3.2 specifies. Its guarantees are constraints, so a text
comparison against the amendment proves nothing about them: this module applies
the migration and then tries to write the rows each constraint claims to
reject, asserting that the rejection names the constraint that was supposed to
do it. An earlier revision of this table shipped without ever being executed.

Two things are proved here that only a running server can prove: the DDL is
valid PostgreSQL, and the constraints reject what §3.2 says they reject --
misassembled identifiers, cross-tenant rollback targets, a second version in
force, and a decision naming a policy version that no row carries.

**Scope limit, stated so the result is not over-read.** `pgserver` ships
neither `uuid-ossp` nor `postgis`, so `000001_baseline_canonical_schema.sql`
cannot be applied verbatim here -- the same reason the repository's own
database tests carry `requires_live_env`. The fixture therefore builds a
dependency stub that is primary-key- and type-compatible with `000001` for the
two objects this migration references (`core.tenants`, `workflow.decisions`).
What follows validates this migration's own DDL, not its integration with the
full baseline; that needs a PostGIS-bearing PostgreSQL 16 and belongs to
pre-deployment verification.

Run locally with the bundled server::

    uv run --python 3.12 --with pgserver --with 'psycopg[binary]' \
        pytest tests/contract/test_decision_policy_registry_schema.py -q \
        -m requires_live_env

These tests are marked ``requires_live_env`` and are excluded from the default
CI marker expression; they skip cleanly when no PostgreSQL 16 is reachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "infra/db/migrations/000014_decision_policy_registry.sql"

live = pytest.mark.requires_live_env

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "11111111-1111-1111-1111-222222222222"

# Primary-key- and type-compatible with 000001 for everything this migration
# references. `workflow.decisions.policy_version_id` is reproduced exactly as
# 000001 declares it -- VARCHAR(100) NOT NULL with nothing behind it -- because
# that column is the whole reason the registry exists (ODP-SD-AMD-001 §3.1).
BASELINE_STUB = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS workflow;

CREATE TABLE core.tenants (
    tenant_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_name VARCHAR(255) NOT NULL
);

CREATE TABLE workflow.decisions (
    decision_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_type     VARCHAR(100) NOT NULL DEFAULT 'site_go_wait_reject',
    entity_type       VARCHAR(100) NOT NULL,
    entity_id         VARCHAR(255) NOT NULL,
    decision_status   VARCHAR(50)  NOT NULL DEFAULT 'proposed',
    policy_version_id VARCHAR(100) NOT NULL,
    created_by        VARCHAR(255) NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

POLICY_COLUMNS = (
    "policy_version_id, policy_label, policy_id, policy_version, policy_kind, "
    "tenant_id, effective_from, effective_to, owner_role, approved_by, approved_at, "
    "input_contract, output_contract, change_reason, rollback_policy_version, "
    "parameters, declared_inputs"
)


def _policy_values(**overrides):
    """A row that every constraint accepts, before overrides are applied.

    Each rejecting case overrides exactly one thing, so a rejection can only be
    attributed to what that case changed.
    """
    row = {
        "policy_label": "heatzone-merge-v1",
        "policy_id": "heatzone-merge",
        "policy_version": "1.0.0",
        "policy_kind": "heatzone_merge",
        "tenant_id": TENANT_A,
        "effective_from": "2026-01-01 00:00:00+00",
        "effective_to": None,
        "owner_role": "ops",
        "approved_by": "architecture_owner",
        "approved_at": "2026-01-01 00:00:00+00",
        "input_contract": "HeatZoneScores",
        "output_contract": "HeatZone",
        "change_reason": "mechanism introduction",
        "rollback_policy_version": None,
        "parameters": '{"threshold": 0.7}',
        "declared_inputs": ["heat_score"],
    }
    row.update(overrides)
    # The identifier is derived, not supplied, unless a case supplies it: that
    # is the rule chk_decision_policy_version_id_format enforces.
    row.setdefault(
        "policy_version_id", f"{row['policy_label']}:{row['tenant_id']}"
    )
    return row


def _insert_policy(conn, **overrides) -> None:
    row = _policy_values(**overrides)
    ordered = [row[name.strip()] for name in POLICY_COLUMNS.split(",")]
    placeholders = ", ".join(
        "%s::jsonb" if name.strip() == "parameters" else "%s"
        for name in POLICY_COLUMNS.split(",")
    )
    conn.execute(
        f"INSERT INTO workflow.decision_policies ({POLICY_COLUMNS}) "
        f"VALUES ({placeholders})",
        ordered,
    )


def _rejected_by(db, **overrides) -> str:
    """Insert a row expected to fail; return the constraint that rejected it."""
    psycopg = db.server.psycopg
    with db.connect(autocommit=True) as conn:
        with pytest.raises(psycopg.errors.DatabaseError) as excinfo:
            _insert_policy(conn, **overrides)
    return excinfo.value.diag.constraint_name or ""


@pytest.fixture
def policy_db(intake_blank_db):
    """A blank PostgreSQL 16 database with the stub and the migration applied."""
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(BASELINE_STUB)
        conn.execute(
            "INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, %s), (%s, %s)",
            (TENANT_A, "tenant-a", TENANT_B, "tenant-b"),
        )
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
    return intake_blank_db


@live
class TestMigrationApplies:
    def test_the_table_lands_in_the_workflow_schema(self, policy_db) -> None:
        """Not a new `governance` schema: decision governance already lives in
        `workflow`, which is where the column pointing at this table sits."""
        with policy_db.connect() as conn:
            found = conn.execute(
                "SELECT n.nspname FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE c.relname = 'decision_policies'"
            ).fetchall()
        assert [row[0] for row in found] == ["workflow"]

    def test_the_primary_key_is_the_tenant_bearing_version_id(self, policy_db) -> None:
        """ODP-SD-AMD-001 §3.2. A (policy_id, policy_version) key cannot carry
        per-tenant rows, which is what the active-version index needs."""
        with policy_db.connect() as conn:
            columns = conn.execute(
                """
                SELECT a.attname
                FROM pg_constraint co
                JOIN pg_class c ON c.oid = co.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN unnest(co.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
                WHERE co.contype = 'p' AND n.nspname = 'workflow'
                  AND c.relname = 'decision_policies'
                ORDER BY k.ord
                """
            ).fetchall()
        assert [row[0] for row in columns] == ["policy_version_id"]

    def test_reapplying_the_migration_changes_nothing(self, policy_db) -> None:
        """The migration claims rerunnability; seeds use ON CONFLICT DO NOTHING."""
        sql = MIGRATION.read_text(encoding="utf-8")
        with policy_db.connect(autocommit=True) as conn:
            before = conn.execute(
                "SELECT count(*) FROM workflow.decision_policies"
            ).fetchone()[0]
            conn.execute(sql)
            after = conn.execute(
                "SELECT count(*) FROM workflow.decision_policies"
            ).fetchone()[0]
        assert before == after


@live
class TestSeededPolicies:
    def test_every_tenant_gets_its_own_policy_rows(self, policy_db) -> None:
        with policy_db.connect() as conn:
            rows = conn.execute(
                "SELECT tenant_id::text, policy_label, policy_version_id "
                "FROM workflow.decision_policies ORDER BY tenant_id, policy_version"
            ).fetchall()
        by_tenant: dict[str, list[tuple[str, str]]] = {}
        for tenant, label, version_id in rows:
            by_tenant.setdefault(tenant, []).append((label, version_id))
        assert set(by_tenant) == {TENANT_A, TENANT_B}
        for tenant, entries in by_tenant.items():
            labels = {label for label, _ in entries}
            assert labels == {
                "four-light-policy-0.0.0-retrofit",
                "four-light-policy-v1",
                "model-performance-drift-policy-v1",
            }
            for label, version_id in entries:
                assert version_id == f"{label}:{tenant}"

    def test_the_seeded_windows_are_half_open_and_leave_no_gap(self, policy_db) -> None:
        """Point-in-time resolution over the seeds: an instant before the
        mechanism existed resolves to the retrofit placeholder, an instant
        after resolves to v1, and neither instant resolves to both."""
        with policy_db.connect() as conn:
            resolved = conn.execute(
                """
                SELECT at, (
                    SELECT policy_label FROM workflow.decision_policies p
                    WHERE p.policy_kind = 'forecast_alert'
                      AND p.tenant_id = %s::uuid
                      AND p.effective_from <= at
                      AND (p.effective_to IS NULL OR at < p.effective_to)
                )
                FROM (VALUES
                    (TIMESTAMPTZ '2025-06-01 00:00:00+00'),
                    (TIMESTAMPTZ '2026-09-01 00:00:00+00')
                ) AS instants(at)
                ORDER BY at
                """,
                (TENANT_A,),
            ).fetchall()
        assert [row[1] for row in resolved] == [
            "four-light-policy-0.0.0-retrofit",
            "four-light-policy-v1",
        ]

    def test_the_current_version_rolls_back_to_its_own_tenants_placeholder(
        self, policy_db
    ) -> None:
        with policy_db.connect() as conn:
            rollback = conn.execute(
                "SELECT rollback_policy_version FROM workflow.decision_policies "
                "WHERE policy_version_id = %s",
                (f"four-light-policy-v1:{TENANT_A}",),
            ).fetchone()[0]
        assert rollback == f"four-light-policy-0.0.0-retrofit:{TENANT_A}"


@live
class TestIdentityIsEnforcedNotConventional:
    def test_a_version_id_that_is_a_bare_label_is_rejected(self, policy_db) -> None:
        assert (
            _rejected_by(policy_db, policy_version_id="heatzone-merge-v1")
            == "chk_decision_policy_version_id_format"
        )

    def test_a_version_id_carrying_another_tenant_is_rejected(self, policy_db) -> None:
        assert (
            _rejected_by(
                policy_db, policy_version_id=f"heatzone-merge-v1:{TENANT_B}"
            )
            == "chk_decision_policy_version_id_format"
        )

    def test_a_label_containing_the_separator_is_rejected(self, policy_db) -> None:
        """Without this the decomposition of the key is not unique."""
        assert (
            _rejected_by(
                policy_db,
                policy_label="heatzone:merge",
                policy_version_id=f"heatzone:merge:{TENANT_A}",
            )
            == "chk_decision_policy_label"
        )

    def test_an_unknown_policy_kind_is_rejected(self, policy_db) -> None:
        assert (
            _rejected_by(policy_db, policy_kind="freeform_judgement")
            == "chk_decision_policy_kind"
        )

    def test_a_version_declaring_no_inputs_is_rejected(self, policy_db) -> None:
        """A policy that states nothing about what it reads cannot be audited
        against ODP-SA-07 §5."""
        assert (
            _rejected_by(policy_db, declared_inputs=[])
            == "chk_decision_policy_inputs"
        )

    def test_a_version_with_no_change_reason_is_rejected(self, policy_db) -> None:
        assert (
            _rejected_by(policy_db, change_reason="")
            == "chk_decision_policy_reason"
        )

    def test_a_window_that_closes_before_it_opens_is_rejected(self, policy_db) -> None:
        assert (
            _rejected_by(policy_db, effective_to="2025-01-01 00:00:00+00")
            == "chk_decision_policy_window"
        )


@live
class TestOnlyOneVersionInForce:
    def test_a_second_open_ended_version_is_rejected(self, policy_db) -> None:
        """The partial unique index is what makes "the current policy" a
        question with one answer."""
        with policy_db.connect(autocommit=True) as conn:
            _insert_policy(conn)
        assert (
            _rejected_by(
                policy_db,
                policy_label="heatzone-merge-v2",
                policy_version="2.0.0",
                effective_from="2026-06-01 00:00:00+00",
            )
            == "idx_decision_policy_active"
        )

    def test_closing_the_outgoing_version_admits_the_incoming_one(
        self, policy_db
    ) -> None:
        """Close-and-insert: the retired row keeps every other field."""
        with policy_db.connect(autocommit=True) as conn:
            _insert_policy(conn)
            conn.execute(
                "UPDATE workflow.decision_policies SET effective_to = %s "
                "WHERE policy_version_id = %s",
                ("2026-06-01 00:00:00+00", f"heatzone-merge-v1:{TENANT_A}"),
            )
            _insert_policy(
                conn,
                policy_label="heatzone-merge-v2",
                policy_version="2.0.0",
                effective_from="2026-06-01 00:00:00+00",
                rollback_policy_version=f"heatzone-merge-v1:{TENANT_A}",
            )
            reasons = conn.execute(
                "SELECT policy_version, change_reason FROM workflow.decision_policies "
                "WHERE policy_id = 'heatzone-merge' ORDER BY policy_version"
            ).fetchall()
        assert reasons == [
            ("1.0.0", "mechanism introduction"),
            ("2.0.0", "mechanism introduction"),
        ]


@live
class TestRollbackTargetsStayWithinTheTenant:
    def test_rolling_back_to_another_tenants_version_is_rejected(
        self, policy_db
    ) -> None:
        """Rolling back across tenants applies someone else's thresholds."""
        assert (
            _rejected_by(
                policy_db,
                rollback_policy_version=f"four-light-policy-v1:{TENANT_B}",
            )
            == "fk_decision_policy_rollback_tenant"
        )

    def test_rolling_back_to_this_tenants_version_is_accepted(self, policy_db) -> None:
        with policy_db.connect(autocommit=True) as conn:
            _insert_policy(
                conn,
                rollback_policy_version=f"four-light-policy-v1:{TENANT_A}",
            )
            stored = conn.execute(
                "SELECT rollback_policy_version FROM workflow.decision_policies "
                "WHERE policy_version_id = %s",
                (f"heatzone-merge-v1:{TENANT_A}",),
            ).fetchone()[0]
        assert stored == f"four-light-policy-v1:{TENANT_A}"


@live
class TestDecisionsAreBoundToRealPolicyVersions:
    """ODP-SD-AMD-001 §3.1/§3.4: `workflow.decisions.policy_version_id` was a
    mandatory column with no table behind it. Any string went in."""

    def _decide(self, conn, policy_version_id: str) -> None:
        conn.execute(
            "INSERT INTO workflow.decisions "
            "(entity_type, entity_id, policy_version_id, created_by) "
            "VALUES ('store', 'store-1', %s, 'tester')",
            (policy_version_id,),
        )

    def test_a_decision_naming_a_real_version_is_accepted(self, policy_db) -> None:
        with policy_db.connect(autocommit=True) as conn:
            self._decide(conn, f"four-light-policy-v1:{TENANT_A}")
            count = conn.execute("SELECT count(*) FROM workflow.decisions").fetchone()[0]
        assert count == 1

    def test_a_decision_naming_a_bare_label_is_rejected(self, policy_db) -> None:
        """The exact string the module constant carries. It is a label, and a
        label is not a key -- before this migration it stored happily."""
        psycopg = policy_db.server.psycopg
        with policy_db.connect(autocommit=True) as conn:
            with pytest.raises(psycopg.errors.ForeignKeyViolation) as excinfo:
                self._decide(conn, "four-light-policy-v1")
        assert excinfo.value.diag.constraint_name == "fk_decisions_policy_version"


# `policy_db` seeds its tenants *before* applying the migration, which models an
# existing deployment being upgraded. A freshly provisioned database is the
# other case, and the one that broke: `alembic upgrade head` runs before any
# tenant exists, so the migration-time backfill has nothing to copy and every
# tenant arrives afterwards.
@pytest.fixture
def freshly_provisioned_db(intake_blank_db):
    """Blank PostgreSQL 16, migrated with no tenants present -- the state a new
    environment is in the moment the migration job finishes."""
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(BASELINE_STUB)
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
    return intake_blank_db


@live
class TestFreshlyProvisionedRuntime:
    def test_the_migration_leaves_no_policy_rows_when_no_tenant_exists_yet(
        self, freshly_provisioned_db
    ) -> None:
        """Not a defect -- the backfill has nothing to copy. Stated explicitly
        because it is why the trigger has to exist: on this database the seed
        alone leaves the registry empty."""
        with freshly_provisioned_db.connect() as conn:
            count = conn.execute(
                "SELECT count(*) FROM workflow.decision_policies"
            ).fetchone()[0]
        assert count == 0

    def test_a_tenant_onboarded_after_the_migration_gets_the_same_seeded_policies(
        self, freshly_provisioned_db
    ) -> None:
        """`core.tenants` is written by the data plane at runtime, so this is
        the ordinary path on any environment provisioned after this migration,
        not an edge case."""
        with freshly_provisioned_db.connect(autocommit=True) as conn:
            conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, %s)",
                (TENANT_A, "onboarded-after-migration"),
            )
            rows = conn.execute(
                "SELECT policy_label, policy_version, rollback_policy_version "
                "FROM workflow.decision_policies WHERE tenant_id = %s::uuid "
                "ORDER BY effective_from, policy_id, policy_version_id",
                (TENANT_A,),
            ).fetchall()

        assert [(row[0], row[1]) for row in rows] == [
            ("four-light-policy-0.0.0-retrofit", "0.0.0-retrofit"),
            ("four-light-policy-v1", "1.0.0"),
            ("model-performance-drift-policy-v1", "1.0.0"),
        ]
        # Ordering is load-bearing: v1's rollback target is a composite foreign
        # key onto the retrofit row, so seeding them the other way round would
        # fail at insert time rather than silently.
        assert rows[0][2] is None
        assert rows[1][2] == f"four-light-policy-0.0.0-retrofit:{TENANT_A}"

    def test_onboarding_two_tenants_keeps_the_registry_tenant_scoped(
        self, freshly_provisioned_db
    ) -> None:
        with freshly_provisioned_db.connect(autocommit=True) as conn:
            conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name) "
                "VALUES (%s, %s), (%s, %s)",
                (TENANT_A, "tenant-a", TENANT_B, "tenant-b"),
            )
            rows = conn.execute(
                "SELECT tenant_id::text, policy_version_id "
                "FROM workflow.decision_policies"
            ).fetchall()

        by_tenant: dict[str, set[str]] = {}
        for tenant, version_id in rows:
            by_tenant.setdefault(tenant, set()).add(version_id)
        assert set(by_tenant) == {TENANT_A, TENANT_B}
        for tenant, version_ids in by_tenant.items():
            assert version_ids == {
                f"four-light-policy-0.0.0-retrofit:{tenant}",
                f"four-light-policy-v1:{tenant}",
                f"model-performance-drift-policy-v1:{tenant}",
            }

    def test_the_runtime_repository_resolves_the_seeded_policy(
        self, freshly_provisioned_db
    ) -> None:
        """The end the blocker was about: not that the table exists, but that
        the class the Postgres bundle actually binds
        (`SqlDecisionPolicyRepository`) returns a usable policy from a database
        the migration job provisioned. A fake engine cannot show that -- the
        previous unit test passed while `alembic upgrade head` was still
        stopping at 0007 and never creating this table."""
        from datetime import UTC, datetime

        from shared.infrastructure.persistence.decision_policy import (
            SqlDecisionPolicyRepository,
        )
        from shared.infrastructure.persistence.postgresql import PostgresEngine

        with freshly_provisioned_db.connect(autocommit=True) as conn:
            conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, %s)",
                (TENANT_A, "onboarded-after-migration"),
            )

        engine = PostgresEngine(
            freshly_provisioned_db.url(),
            bootstrap=False,
            validate_schema=False,
        )
        try:
            repository = SqlDecisionPolicyRepository(engine)
            current = repository.find_effective(
                policy_kind="forecast_alert",
                tenant_id=TENANT_A,
                at=datetime(2026, 9, 2, tzinfo=UTC),
            )
            historical = repository.find_effective(
                policy_kind="forecast_alert",
                tenant_id=TENANT_A,
                at=datetime(2025, 6, 1, tzinfo=UTC),
            )
        finally:
            engine.close()

        assert current is not None
        assert current.policy_id == "four-light-policy"
        assert current.policy_version == "1.0.0"
        assert current.declared_inputs == ("sitescore_gap_ratio",)
        # The thresholds the mechanism must carry over verbatim: shipping the
        # mechanism and moving the numbers are two separate changes.
        assert [
            (t["level"], t["value"]) for t in current.parameters["thresholds"]
        ] == [("RED", -0.35), ("ORANGE", -0.20), ("YELLOW", -0.10)]

        # Point-in-time, not latest-version: re-resolving a pre-mechanism
        # instant must land on the retrofit placeholder.
        assert historical is not None
        assert historical.policy_version == "0.0.0-retrofit"
