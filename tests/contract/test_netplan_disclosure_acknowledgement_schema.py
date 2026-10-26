"""The acknowledgement table, executed against a real PostgreSQL 16 server.

`infra/db/migrations/000017_netplan_constraint_disclosure.sql` claims four
things that only a running server can demonstrate: the DDL is valid PostgreSQL,
the receipt cannot be rewritten, a signature cannot cite another tenant's
policy, and only the two structurally-inexpressible classes can be signed for.
Reading the file back proves none of them -- an earlier table in this repository
shipped without ever being executed.

The application enforces the class restriction from policy data, which is the
right place for it: policy is versioned and supersedable. But policy data can be
superseded to something more permissive, and the whole point of a signed
acknowledgement is that it means the same thing when read back later. The CHECK
constraint here is the floor a policy edit cannot lower, so both are tested --
the application rule in
`tests/integration/test_netplan_constraint_disclosure_approval.py`, the floor
here.

**Scope limit, stated so the result is not over-read.** As in
`test_decision_policy_registry_schema.py`, `pgserver` ships neither `uuid-ossp`
nor `postgis`, so `000001_baseline_canonical_schema.sql` cannot be applied
verbatim. The fixture builds a dependency stub that is primary-key- and
type-compatible for the objects this migration references. What follows
validates this migration's own DDL, not its integration with the full baseline.

Run locally with the bundled server::

    uv run --python 3.12 --with pgserver --with 'psycopg[binary]' \
        pytest tests/contract/test_netplan_disclosure_acknowledgement_schema.py \
        -q -m requires_live_env

Marked ``requires_live_env`` and excluded from the default CI marker
expression; skips cleanly when no PostgreSQL 16 is reachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_MIGRATION = REPO_ROOT / "infra/db/migrations/000014_decision_policy_registry.sql"
MIGRATION = REPO_ROOT / "infra/db/migrations/000017_netplan_constraint_disclosure.sql"

live = pytest.mark.requires_live_env

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "11111111-1111-1111-1111-222222222222"

POLICY_A = f"netplan-constraint-disclosure-policy-v1:{TENANT_A}"
POLICY_B = f"netplan-constraint-disclosure-policy-v1:{TENANT_B}"

# Primary-key- and type-compatible with 000001 for everything the two
# migrations reference. `network` is created here because 000017 puts the
# acknowledgement table in it, alongside `network.network_plans`.
BASELINE_STUB = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS workflow;
CREATE SCHEMA IF NOT EXISTS network;

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

ACK_COLUMNS = (
    "acknowledgement_id, scenario_id, tenant_id, acknowledged_classes, "
    "actor_id, actor_role, reason, policy_version_id, policy_label, "
    "policy_version, solver_problem_hash, model_version, approval_receipt_id, "
    "acknowledged_at, receipt_hash"
)


def _ack_values(**overrides):
    """A row every constraint accepts, before overrides.

    Each rejecting case overrides exactly one field, so a rejection can only be
    attributed to what that case changed.
    """
    row = {
        "acknowledgement_id": "netplan-disclosure-ack-0001",
        "scenario_id": "netplan-scenario-0001",
        "tenant_id": TENANT_A,
        "acknowledged_classes": ["LEASE", "SEQUENCING"],
        "actor_id": "principal://network-strategy-director",
        "actor_role": "network-planning-authority",
        "reason": "lease pipeline confirmed offline; Q3 build order agreed",
        "policy_version_id": POLICY_A,
        "policy_label": "netplan-constraint-disclosure-policy-v1",
        "policy_version": "1.0.0",
        "solver_problem_hash": "a" * 64,
        "model_version": "netplan-network-baseline-v1",
        "approval_receipt_id": "receipt-netplan-0001",
        "acknowledged_at": "2026-09-02 09:00:00+00",
        "receipt_hash": "b" * 64,
    }
    row.update(overrides)
    return row


def _insert_ack(conn, **overrides) -> None:
    row = _ack_values(**overrides)
    names = [name.strip() for name in ACK_COLUMNS.split(",")]
    conn.execute(
        f"INSERT INTO network.netplan_constraint_acknowledgements ({ACK_COLUMNS}) "
        f"VALUES ({', '.join(['%s'] * len(names))})",
        [row[name] for name in names],
    )


def _rejected_by(db, **overrides) -> str:
    """Insert a row expected to fail; return the constraint that rejected it."""
    psycopg = db.server.psycopg
    with db.connect(autocommit=True) as conn:
        with pytest.raises(psycopg.errors.DatabaseError) as excinfo:
            _insert_ack(conn, **overrides)
    return excinfo.value.diag.constraint_name or ""


@pytest.fixture
def disclosure_db(intake_blank_db):
    """A blank PostgreSQL 16 with the stub, the registry and 000017 applied.

    The registry migration runs first because 000017's composite foreign key
    references it and its seed inserts a policy row per tenant -- the same
    ordering alembic enforces through `down_revision`.
    """
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(BASELINE_STUB)
        conn.execute(
            "INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, %s), (%s, %s)",
            (TENANT_A, "tenant-a", TENANT_B, "tenant-b"),
        )
        conn.execute(REGISTRY_MIGRATION.read_text(encoding="utf-8"))
        conn.execute(MIGRATION.read_text(encoding="utf-8"))
    return intake_blank_db


@live
class TestMigrationApplies:
    def test_the_table_lands_in_the_network_schema(self, disclosure_db) -> None:
        """Alongside `network.network_plans`, which is what it is about."""
        with disclosure_db.connect() as conn:
            found = conn.execute(
                "SELECT n.nspname FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace "
                "WHERE c.relname = 'netplan_constraint_acknowledgements'"
            ).fetchall()
        assert [row[0] for row in found] == ["network"]

    def test_a_well_formed_acknowledgement_is_accepted(self, disclosure_db) -> None:
        """The baseline every rejection below is measured against.

        Without this, a table that rejected everything would pass the whole
        rest of this module.
        """
        with disclosure_db.connect(autocommit=True) as conn:
            _insert_ack(conn)
            stored = conn.execute(
                "SELECT acknowledged_classes, actor_role FROM "
                "network.netplan_constraint_acknowledgements "
                "WHERE acknowledgement_id = %s",
                ("netplan-disclosure-ack-0001",),
            ).fetchone()
        assert sorted(stored[0]) == ["LEASE", "SEQUENCING"]
        assert stored[1] == "network-planning-authority"

    def test_the_migration_is_rerunnable(self, disclosure_db) -> None:
        """Every statement is guarded, so a replay onto a database that already
        has the table is a no-op rather than an error."""
        with disclosure_db.connect(autocommit=True) as conn:
            conn.execute(MIGRATION.read_text(encoding="utf-8"))
            count = conn.execute(
                "SELECT count(*) FROM workflow.decision_policies "
                "WHERE policy_kind = 'netplan_action'"
            ).fetchone()[0]
        # Two tenants, one seed each, and the replay inserted no duplicates.
        assert count == 2


@live
class TestTheSeededPolicy:
    def test_one_disclosure_policy_is_seeded_per_existing_tenant(
        self, disclosure_db
    ) -> None:
        with disclosure_db.connect() as conn:
            rows = conn.execute(
                "SELECT tenant_id::text, policy_version_id, policy_version "
                "FROM workflow.decision_policies "
                "WHERE policy_id = 'netplan-constraint-disclosure-policy' "
                "ORDER BY tenant_id::text"
            ).fetchall()
        assert [(row[0], row[1], row[2]) for row in rows] == [
            (TENANT_A, POLICY_A, "1.0.0"),
            (TENANT_B, POLICY_B, "1.0.0"),
        ]

    def test_a_tenant_onboarded_later_gets_the_policy_too(
        self, disclosure_db
    ) -> None:
        """The trigger, not just the backfill.

        Tenants are written by the data plane at runtime, strictly after
        `alembic upgrade head`. A backfill-only seed would leave every tenant
        onboarded afterwards resolving nothing -- and because `decide()` refuses
        on an unresolvable policy, that is not reduced governance but no
        approvals at all.
        """
        late_tenant = "11111111-1111-1111-1111-333333333333"
        with disclosure_db.connect(autocommit=True) as conn:
            conn.execute(
                "INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, %s)",
                (late_tenant, "tenant-c"),
            )
            found = conn.execute(
                "SELECT policy_version_id FROM workflow.decision_policies "
                "WHERE tenant_id = %s AND policy_kind = 'netplan_action'",
                (late_tenant,),
            ).fetchall()
        assert [row[0] for row in found] == [
            f"netplan-constraint-disclosure-policy-v1:{late_tenant}"
        ]

    def test_the_seeded_parameters_match_the_shipped_module(
        self, disclosure_db
    ) -> None:
        """The SQL seed and `shared/governance/netplan_disclosure.py` are two
        writers of one rule. Where they could drift, they would drift into
        permitting different things."""
        from shared.governance import default_netplan_disclosure_policy

        with disclosure_db.connect() as conn:
            parameters, declared = conn.execute(
                "SELECT parameters, declared_inputs FROM workflow.decision_policies "
                "WHERE policy_version_id = %s",
                (POLICY_A,),
            ).fetchone()

        expected = default_netplan_disclosure_policy(TENANT_A)
        assert sorted(parameters["required_classes"]) == sorted(
            expected.parameters["required_classes"]
        )
        assert sorted(parameters["acknowledgeable_classes"]) == sorted(
            expected.parameters["acknowledgeable_classes"]
        )
        assert sorted(parameters["authorized_acknowledgement_roles"]) == sorted(
            expected.parameters["authorized_acknowledgement_roles"]
        )
        assert sorted(declared) == sorted(expected.declared_inputs)


@live
class TestTheReceiptCannotBeRewritten:
    """Immutability as a database rule, not a convention in the calling code.

    `receipt_hash` makes a rewrite *detectable* when the row is read back
    through code that recomputes it. That assumes the reader checks. Refusing
    the write means a direct `psql` edit fails where it is attempted.
    """

    def test_an_update_is_refused(self, disclosure_db) -> None:
        psycopg = disclosure_db.server.psycopg
        with disclosure_db.connect(autocommit=True) as conn:
            _insert_ack(conn)
            with pytest.raises(psycopg.errors.DatabaseError) as excinfo:
                conn.execute(
                    "UPDATE network.netplan_constraint_acknowledgements "
                    "SET reason = %s WHERE acknowledgement_id = %s",
                    ("rewritten after the fact", "netplan-disclosure-ack-0001"),
                )
        assert "append-only" in str(excinfo.value)

    def test_a_delete_is_refused(self, disclosure_db) -> None:
        """Deleting removes the evidence that a plan with known unmodelled
        constraints was approved, which is the record's whole purpose."""
        psycopg = disclosure_db.server.psycopg
        with disclosure_db.connect(autocommit=True) as conn:
            _insert_ack(conn)
            with pytest.raises(psycopg.errors.DatabaseError) as excinfo:
                conn.execute(
                    "DELETE FROM network.netplan_constraint_acknowledgements "
                    "WHERE acknowledgement_id = %s",
                    ("netplan-disclosure-ack-0001",),
                )
        assert "append-only" in str(excinfo.value)

    def test_the_row_survives_both_attempts_unchanged(self, disclosure_db) -> None:
        """Refusing is not enough if the statement partially applied."""
        psycopg = disclosure_db.server.psycopg
        with disclosure_db.connect(autocommit=True) as conn:
            _insert_ack(conn)
            for statement, params in (
                (
                    "UPDATE network.netplan_constraint_acknowledgements "
                    "SET actor_id = %s WHERE acknowledgement_id = %s",
                    ("principal://someone-else", "netplan-disclosure-ack-0001"),
                ),
                (
                    "DELETE FROM network.netplan_constraint_acknowledgements "
                    "WHERE acknowledgement_id = %s",
                    ("netplan-disclosure-ack-0001",),
                ),
            ):
                with pytest.raises(psycopg.errors.DatabaseError):
                    conn.execute(statement, params)
            stored = conn.execute(
                "SELECT actor_id, reason FROM "
                "network.netplan_constraint_acknowledgements "
                "WHERE acknowledgement_id = %s",
                ("netplan-disclosure-ack-0001",),
            ).fetchone()
        assert stored[0] == "principal://network-strategy-director"
        assert stored[1].startswith("lease pipeline confirmed")

    def test_a_second_signature_is_added_rather_than_replacing_the_first(
        self, disclosure_db
    ) -> None:
        """Superseding is an insert. Both signatures remain readable, which is
        what lets "who accepted this, and when" stay answerable."""
        with disclosure_db.connect(autocommit=True) as conn:
            _insert_ack(conn)
            _insert_ack(
                conn,
                acknowledgement_id="netplan-disclosure-ack-0002",
                solver_problem_hash="c" * 64,
                receipt_hash="d" * 64,
                acknowledged_at="2026-09-03 09:00:00+00",
            )
            count = conn.execute(
                "SELECT count(*) FROM network.netplan_constraint_acknowledgements "
                "WHERE scenario_id = %s",
                ("netplan-scenario-0001",),
            ).fetchone()[0]
        assert count == 2


@live
class TestTheConstraintsRejectWhatTheyClaimTo:
    def test_a_blocking_class_cannot_be_signed_for(self, disclosure_db) -> None:
        """The floor under the policy data.

        CONSTRUCTION is unmodelled only when a cap was withheld, and a withheld
        input must not become an accepted risk. A policy edit can widen the
        application rule; it cannot widen this.
        """
        assert (
            _rejected_by(disclosure_db, acknowledged_classes=["CONSTRUCTION"])
            == "chk_netplan_disclosure_ack_class_names"
        )

    def test_a_partly_valid_class_list_is_rejected_whole(
        self, disclosure_db
    ) -> None:
        """`<@` is a subset test, so one bad name rejects the row.

        Accepting the LEASE half and dropping COVERAGE would store a signature
        that reads as covering more than it does.
        """
        assert (
            _rejected_by(
                disclosure_db, acknowledged_classes=["LEASE", "COVERAGE"]
            )
            == "chk_netplan_disclosure_ack_class_names"
        )

    def test_an_empty_class_list_is_rejected(self, disclosure_db) -> None:
        """A signature naming nothing records that a dialog was dismissed."""
        assert (
            _rejected_by(disclosure_db, acknowledged_classes=[])
            == "chk_netplan_disclosure_ack_classes"
        )

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_reason_is_rejected(self, disclosure_db, blank: str) -> None:
        """Whitespace is checked, not just emptiness: `btrim` is what makes a
        space bar an invalid rationale."""
        assert (
            _rejected_by(disclosure_db, reason=blank)
            == "chk_netplan_disclosure_ack_reason"
        )

    @pytest.mark.parametrize(
        "field", ["solver_problem_hash", "receipt_hash"]
    )
    def test_a_blank_binding_hash_is_rejected(
        self, disclosure_db, field: str
    ) -> None:
        """An unbound signature answers for every plan, which is no signature."""
        assert (
            _rejected_by(disclosure_db, **{field: "   "})
            == "chk_netplan_disclosure_ack_hashes"
        )

    def test_another_tenants_policy_cannot_govern_this_signature(
        self, disclosure_db
    ) -> None:
        """Tenant B's disclosure policy may waive classes tenant A's does not.

        A single-column reference would have proved only that the policy
        version exists somewhere.
        """
        assert (
            _rejected_by(disclosure_db, policy_version_id=POLICY_B)
            == "fk_netplan_disclosure_ack_policy_version"
        )

    def test_a_policy_version_no_row_carries_is_rejected(
        self, disclosure_db
    ) -> None:
        assert (
            _rejected_by(
                disclosure_db,
                policy_version_id=f"netplan-constraint-disclosure-policy-v9:{TENANT_A}",
            )
            == "fk_netplan_disclosure_ack_policy_version"
        )

    def test_a_tenant_no_row_carries_is_rejected(self, disclosure_db) -> None:
        unknown = "11111111-1111-1111-1111-999999999999"
        assert _rejected_by(
            disclosure_db, tenant_id=unknown, policy_version_id=POLICY_A
        ) in {
            "netplan_constraint_acknowledgements_tenant_id_fkey",
            "fk_netplan_disclosure_ack_policy_version",
        }

    def test_the_same_acknowledgement_id_cannot_be_inserted_twice(
        self, disclosure_db
    ) -> None:
        """The primary key is the in-database half of the store's refusal to
        overwrite; the other half is `InMemoryNetPlanRepository`."""
        psycopg = disclosure_db.server.psycopg
        with disclosure_db.connect(autocommit=True) as conn:
            _insert_ack(conn)
            with pytest.raises(psycopg.errors.DatabaseError) as excinfo:
                _insert_ack(conn, reason="a different reason, same id")
        assert excinfo.value.diag.constraint_name == (
            "netplan_constraint_acknowledgements_pkey"
        )
