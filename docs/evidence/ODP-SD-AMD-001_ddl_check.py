"""Executable check for the DDL proposed in docs/design/ODP-SD-AMD-001.md.

Extracts every ```sql fenced block from the amendment and, against a real
PostgreSQL instance, verifies three things:

A. the DDL applies on top of a baseline dependency stub;
B. every block is re-runnable (the amendment claims idempotency);
C. each new CHECK constraint actually rejects the rows it claims to reject.

Run with::

    uv run --no-project python docs/evidence/ODP-SD-AMD-001_ddl_check.py

Scope limit, stated so the result is not over-read: the bundled `pgserver`
build ships neither `uuid-ossp` nor `postgis`, so `000001_baseline_canonical_
schema.sql` cannot be applied verbatim here -- the same reason the repository's
own database tests carry `requires_live_env`. This script therefore builds a
dependency stub that is PK- and type-compatible with `000001` for the columns
the amendment references, and shims `uuid_generate_v4()` onto `gen_random_uuid()`.
It validates the amendment's own DDL, not the baseline.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

import pgserver

REPO = pathlib.Path(__file__).resolve().parents[2]
AMENDMENT = REPO / "docs/design/ODP-SD-AMD-001.md"

# A fenced block counts as executable only if it opens with a DDL/DML verb.
# Section 6.3 embeds a dbt select-list fragment, which is not a statement.
EXECUTABLE_PREFIXES = ("CREATE", "ALTER", "DROP", "DO", "INSERT", "UPDATE", "COMMENT")

STUB = """
CREATE SCHEMA IF NOT EXISTS core;      CREATE SCHEMA IF NOT EXISTS workflow;
CREATE SCHEMA IF NOT EXISTS expansion; CREATE SCHEMA IF NOT EXISTS operations;
CREATE SCHEMA IF NOT EXISTS pricing;   CREATE SCHEMA IF NOT EXISTS asset;
CREATE SCHEMA IF NOT EXISTS network;   CREATE SCHEMA IF NOT EXISTS learning;
CREATE SCHEMA IF NOT EXISTS geo;

CREATE OR REPLACE FUNCTION uuid_generate_v4() RETURNS uuid
    AS $f$ SELECT gen_random_uuid() $f$ LANGUAGE sql;

CREATE TABLE core.tenants (
    tenant_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_name VARCHAR(255) NOT NULL);
CREATE TABLE core.brands  (brand_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE core.stores  (store_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE geo.h3_cells (geo_cell_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE learning.prediction_runs (
    prediction_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE learning.predictions (
    prediction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE workflow.decisions (
    decision_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    policy_version_id VARCHAR(100) NOT NULL);
CREATE TABLE operations.alerts (
    alert_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    alert_level VARCHAR(50) NOT NULL DEFAULT 'green',
    alert_reason_code VARCHAR(100) NOT NULL,
    evidence_json JSONB NOT NULL,
    opened_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(50) NOT NULL DEFAULT 'open');
CREATE TABLE operations.forecast_outputs (
    forecast_output_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE expansion.listings (listing_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE expansion.heatzone_scores (
    heatzone_score_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE expansion.site_score_runs (
    sitescore_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE asset.valuation_runs (
    valuation_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
CREATE TABLE network.network_plans (
    network_plan_id UUID PRIMARY KEY DEFAULT uuid_generate_v4());
"""

TENANT = "'11111111-1111-1111-1111-111111111111'"
STORE = "'22222222-2222-2222-2222-222222222222'"
CELL = "'33333333-3333-3333-3333-333333333333'"
VALUATION = "'44444444-4444-4444-4444-444444444444'"
FORECAST = "'55555555-5555-5555-5555-555555555555'"
ALERT = "'66666666-6666-6666-6666-666666666666'"
POLICY = "'four-light-policy-v1'"

SEED = f"""
INSERT INTO core.tenants (tenant_id, tenant_name) VALUES ({TENANT}, 't1');
INSERT INTO core.stores (store_id) VALUES ({STORE});
INSERT INTO geo.h3_cells (geo_cell_id) VALUES ({CELL});
INSERT INTO asset.valuation_runs (valuation_run_id) VALUES ({VALUATION});
INSERT INTO operations.forecast_outputs (forecast_output_id) VALUES ({FORECAST});
INSERT INTO operations.alerts (alert_id, store_id, alert_reason_code, evidence_json)
    VALUES ({ALERT}, {STORE}, 'sitescore_gap', '{{}}'::jsonb);
INSERT INTO workflow.decision_policies (
    policy_version_id, policy_id, policy_version, policy_kind, tenant_id,
    effective_from, owner_role, approved_by, approved_at,
    input_contract, output_contract, change_reason, parameters, declared_inputs)
VALUES ({POLICY}, 'four-light-policy', '1.0.0', 'forecast_alert', {TENANT}, now(),
    'ops', 'approver', now(), 'in', 'out',
    'mechanism introduction, thresholds unchanged', '{{"thresholds":[]}}'::jsonb,
    ARRAY['sitescore_gap_ratio', 'data_quality.staleness_days']);
"""

DEAL = (
    "INSERT INTO asset.deal_outcomes (tenant_id, valuation_run_id, outcome_kind,"
    " realized_transaction_price, realized_transaction_at, no_deal_reason_code,"
    " no_deal_note, duration_days, recorded_by, recorded_at, source_authority,"
    " correlation_id) VALUES "
)
FEEDBACK = (
    "INSERT INTO operations.forecast_feedback (tenant_id, store_id, feedback_kind,"
    " target_alert_id, target_forecast_output_id, corrected_metric, observed_value,"
    " corrected_value, correction_unit, effective_from, effective_to, reason_code,"
    " submitted_by, submitted_at, approval_status, correlation_id) VALUES "
)
COMPOSITION = (
    "INSERT INTO expansion.heatzone_composition (zone_id, tenant_id, member_cell_id,"
    " composition_kind, parent_zone_id, decided_by, decided_at,"
    " decision_policy_version_id, override_reason) VALUES "
)
GATE = (
    "INSERT INTO pricing.exploration_gates (tenant_id, budget_limit, budget_consumed,"
    " effective_from, effective_to, approved_by, rollback_condition,"
    " decision_policy_version_id) VALUES "
)
POLICY_INSERT = (
    "INSERT INTO workflow.decision_policies (policy_version_id, policy_id,"
    " policy_version, policy_kind, tenant_id, effective_from, owner_role, approved_by,"
    " approved_at, input_contract, output_contract, change_reason, parameters,"
    " declared_inputs) VALUES "
)

# (label, statement, should_be_accepted)
CASES: list[tuple[str, str, bool]] = [
    ("deal: CLOSED with price+date",
     DEAL + f"({TENANT},{VALUATION},'CLOSED',1000,now(),NULL,NULL,30,'u',now(),'moi','c1')", True),
    ("deal: CLOSED missing price",
     DEAL + f"({TENANT},{VALUATION},'CLOSED',NULL,now(),NULL,NULL,30,'u',now(),'moi','c2')", False),
    ("deal: CLOSED missing date",
     DEAL + f"({TENANT},{VALUATION},'CLOSED',1000,NULL,NULL,NULL,30,'u',now(),'moi','c3')", False),
    ("deal: non-CLOSED without reason",
     DEAL + f"({TENANT},{VALUATION},'WITHDRAWN',NULL,NULL,NULL,NULL,30,'u',now(),'moi','c4')",
     False),
    ("deal: non-CLOSED carrying price",
     DEAL + f"({TENANT},{VALUATION},'WITHDRAWN',1000,now(),'PRICE_GAP',NULL,30,'u',now(),"
     "'moi','c5')", False),
    ("deal: reason OTHER without note",
     DEAL + f"({TENANT},{VALUATION},'EXPIRED',NULL,NULL,'OTHER',NULL,30,'u',now(),'moi','c6')",
     False),
    ("deal: negative duration_days",
     DEAL + f"({TENANT},{VALUATION},'EXPIRED',NULL,NULL,'CONDITION',NULL,-1,'u',now(),"
     "'moi','c7')", False),
    ("feedback: ALERT_DISPOSITION on alert",
     FEEDBACK + f"({TENANT},{STORE},'ALERT_DISPOSITION',{ALERT},NULL,NULL,NULL,NULL,NULL,"
     "'2026-01-01','2026-01-31','r','u',now(),'AUTO_ACCEPTED','f1')", True),
    ("feedback: no target at all",
     FEEDBACK + f"({TENANT},{STORE},'CONTEXT_ANNOTATION',NULL,NULL,NULL,NULL,NULL,NULL,"
     "'2026-01-01','2026-01-31','r','u',now(),'AUTO_ACCEPTED','f2')", False),
    ("feedback: OUTCOME_CORRECTION on prediction",
     FEEDBACK + f"({TENANT},{STORE},'OUTCOME_CORRECTION',NULL,{FORECAST},'revenue',10,20,"
     "'TWD','2026-01-01','2026-01-31','r','u',now(),'PENDING','f3')", True),
    ("feedback: OUTCOME_CORRECTION auto-accepted",
     FEEDBACK + f"({TENANT},{STORE},'OUTCOME_CORRECTION',NULL,{FORECAST},'revenue',10,20,"
     "'TWD','2026-01-01','2026-01-31','r','u',now(),'AUTO_ACCEPTED','f4')", False),
    ("feedback: OUTCOME_CORRECTION without values",
     FEEDBACK + f"({TENANT},{STORE},'OUTCOME_CORRECTION',NULL,{FORECAST},NULL,NULL,NULL,NULL,"
     "'2026-01-01','2026-01-31','r','u',now(),'PENDING','f5')", False),
    ("feedback: annotation carrying correction",
     FEEDBACK + f"({TENANT},{STORE},'CONTEXT_ANNOTATION',{ALERT},NULL,'revenue',10,20,'TWD',"
     "'2026-01-01','2026-01-31','r','u',now(),'AUTO_ACCEPTED','f6')", False),
    ("feedback: APPROVED without approver",
     FEEDBACK + f"({TENANT},{STORE},'OUTCOME_CORRECTION',NULL,{FORECAST},'revenue',10,20,"
     "'TWD','2026-01-01','2026-01-31','r','u',now(),'APPROVED','f7')", False),
    ("feedback: effective_to before from",
     FEEDBACK + f"({TENANT},{STORE},'ALERT_DISPOSITION',{ALERT},NULL,NULL,NULL,NULL,NULL,"
     "'2026-02-01','2026-01-01','r','u',now(),'AUTO_ACCEPTED','f8')", False),
    ("composition: MERGED decided by system",
     COMPOSITION + f"('MZ-0123456789abcdef',{TENANT},{CELL},'MERGED',NULL,'system',now(),"
     f"{POLICY},NULL)", True),
    ("composition: zone_id reusing cell uuid",
     COMPOSITION + f"({CELL},{TENANT},{CELL},'MERGED',NULL,'system',now(),{POLICY},NULL)",
     False),
    ("composition: SPLIT_CHILD without parent",
     COMPOSITION + f"('MZ-00000000000000aa',{TENANT},{CELL},'SPLIT_CHILD',NULL,'system',"
     f"now(),{POLICY},NULL)", False),
    ("composition: human decision without reason",
     COMPOSITION + f"('MZ-00000000000000bb',{TENANT},{CELL},'MERGED',NULL,'alice',now(),"
     f"{POLICY},NULL)", False),
    ("composition: system decision with override",
     COMPOSITION + f"('MZ-00000000000000cc',{TENANT},{CELL},'MERGED',NULL,'system',now(),"
     f"{POLICY},'because')", False),
    ("gate: valid authorization",
     GATE + f"({TENANT},1000,0,now(),now()+interval '30 day','a','revert on breach',{POLICY})",
     True),
    ("gate: consumed beyond limit",
     GATE + f"({TENANT},1000,2000,now(),now()+interval '30 day','a','revert',{POLICY})", False),
    ("gate: effective_to not after from",
     GATE + f"({TENANT},1000,0,now(),now(),'a','revert',{POLICY})", False),
    ("gate: empty rollback_condition",
     GATE + f"({TENANT},1000,0,now(),now()+interval '1 day','a','',{POLICY})", False),
    ("policy: second active version, same policy_id",
     POLICY_INSERT + f"('four-light-policy-v2','four-light-policy','2.0.0','forecast_alert',"
     f"{TENANT},now(),'ops','a',now(),'in','out','x','{{}}'::jsonb,"
     "ARRAY['sitescore_gap_ratio'])", False),
    ("policy: empty declared_inputs",
     POLICY_INSERT + f"('other-v1','other','1.0.0','heatzone_merge',{TENANT},now(),'ops','a',"
     "now(),'in','out','x','{}'::jsonb, ARRAY[]::text[])", False),
    ("alert: deterioration before opened_at",
     "UPDATE operations.alerts SET deterioration_confirmed_at = opened_at - interval '1 day'"
     f" WHERE alert_id = {ALERT}", False),
    ("alert: disposition outside enum",
     f"UPDATE operations.alerts SET disposition = 'MAYBE' WHERE alert_id = {ALERT}", False),
    ("alert: valid disposition",
     f"UPDATE operations.alerts SET disposition = 'TRUE_POSITIVE' WHERE alert_id = {ALERT}",
     True),
    ("heatzone_scores: absorption_ratio above 1",
     "INSERT INTO expansion.heatzone_scores (absorption_ratio, absorbed_demand,"
     " absorption_basis_at) VALUES (1.5, 10, now())", False),
    ("heatzone_scores: absorbed without basis_at",
     "INSERT INTO expansion.heatzone_scores (absorbed_demand) VALUES (10)", False),
]


def sql_blocks() -> list[str]:
    """Return the amendment's executable SQL blocks, in document order.

    Selection is by leading keyword rather than by position, so inserting a
    new SQL block into the amendment does not silently shift what gets skipped.
    """
    doc = AMENDMENT.read_text(encoding="utf-8")
    blocks = re.findall(r"```sql\n(.*?)```", doc, re.S)
    executable = []
    for block in blocks:
        first = next(
            (ln for ln in block.strip().splitlines() if ln.strip()
             and not ln.lstrip().startswith("--")),
            "",
        )
        if first.lstrip().upper().startswith(EXECUTABLE_PREFIXES):
            executable.append(block)
        else:
            print(f"  (skipping non-statement block: {first.strip()[:60]!r})")
    return executable


class Runner:
    """Applies SQL through psql with ON_ERROR_STOP so failures are not silent."""

    def __init__(self, uri: str) -> None:
        self._uri = uri
        self._dir = pathlib.Path(tempfile.mkdtemp(prefix="odp-amd-sql-"))
        self._n = 0

    def __call__(self, sql: str) -> tuple[bool, str]:
        self._n += 1
        path = self._dir / f"s{self._n}.sql"
        path.write_text(sql, encoding="utf-8")
        try:
            pgserver.psql([self._uri, "-v", "ON_ERROR_STOP=1", "-q", "-X",
                           "--single-transaction", "-f", str(path)])
        except subprocess.CalledProcessError as exc:
            return False, ((exc.stderr or "") + (exc.stdout or "")).strip()
        except Exception as exc:  # pgserver wraps failures in its own error type
            return False, str(exc)
        return True, ""


def main() -> int:
    blocks = sql_blocks()
    pgdata = pathlib.Path(tempfile.mkdtemp(prefix="odp-amd-pg-"))
    server = pgserver.get_server(pgdata, cleanup_mode="stop")
    run = Runner(server.get_uri())

    ok, err = run(STUB)
    print("dependency stub:", "OK" if ok else f"FAIL\n{err[:700]}")
    if not ok:
        server.cleanup()
        return 1

    print("\n=== A. amendment DDL applies ===")
    apply_fails = 0
    for i, block in enumerate(blocks, 1):
        ok, err = run(block)
        head = block.strip().splitlines()[0][:52]
        print(f"  block {i:>2} {'OK  ' if ok else 'FAIL'} {head}")
        if not ok:
            apply_fails += 1
            print("       " + err.replace("\n", "\n       ")[:700])
    print(f"  -> {len(blocks) - apply_fails}/{len(blocks)} applied")

    print("\n=== B. idempotency (re-run every block) ===")
    idem_fails = 0
    for i, block in enumerate(blocks, 1):
        ok, err = run(block)
        if not ok:
            idem_fails += 1
            print(f"  block {i:>2} NOT IDEMPOTENT")
            print("       " + err.replace("\n", "\n       ")[:500])
    print(f"  -> {len(blocks) - idem_fails}/{len(blocks)} re-runnable")

    if apply_fails:
        server.cleanup()
        return 1

    run(SEED)

    print("\n=== C. constraint behaviour ===")
    mismatches = 0
    for label, statement, expected in CASES:
        accepted, err = run(statement)
        if accepted != expected:
            mismatches += 1
        want = "accept" if expected else "reject"
        got = "accept" if accepted else "reject"
        mark = "OK      " if accepted == expected else "MISMATCH"
        print(f"  {mark} {label:<42} expected={want} got={got}")
        if accepted != expected and err:
            print("           " + err.replace("\n", "\n           ")[:300])
    print(f"  -> {len(CASES) - mismatches}/{len(CASES)} behaved as designed")

    server.cleanup()
    return 1 if (apply_fails or idem_fails or mismatches) else 0


if __name__ == "__main__":
    sys.exit(main())
