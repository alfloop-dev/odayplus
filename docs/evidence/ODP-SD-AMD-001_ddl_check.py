"""Executable check for the DDL proposed in docs/design/ODP-SD-AMD-001.md.

Extracts every ```sql fenced block from the amendment and, against a real
PostgreSQL instance, verifies three things:

A. the DDL applies on top of a baseline dependency stub;
B. every block is re-runnable (the amendment claims idempotency);
C. each new CHECK constraint actually rejects the rows it claims to reject,
   *and* rejects them for the stated reason.

Run with::

    uv run --no-project --python 3.12 --with pgserver \
        python docs/evidence/ODP-SD-AMD-001_ddl_check.py

Two properties of section C are worth stating, because an earlier revision of
this script had neither:

1. **Isolation.** Every negative case brings its own dependency rows
   (`asset.valuation_runs`, `geo.h3_cells`, `workflow.decisions`). Sharing them
   across cases meant a row could be rejected by a primary key or a unique
   index left behind by an earlier case rather than by the constraint the case
   claims to exercise, which makes the case pass while proving nothing.
2. **Named cause.** Every rejecting case declares the constraint or index name
   it expects, and the case only counts as behaving-as-designed when that name
   appears in PostgreSQL's error. A handful of rows necessarily violate two
   coupled absorption constraints at once (a ratio above 1 is also arithmetically
   inconsistent with its inputs); those cases declare both names and accept
   either, which still excludes rejection for an unrelated reason.

Scope limit, stated so the result is not over-read: the bundled `pgserver`
build ships neither `uuid-ossp` nor `postgis`, so `000001_baseline_canonical_
schema.sql` cannot be applied verbatim here -- the same reason the repository's
own database tests carry `requires_live_env`. This script therefore builds a
dependency stub that is PK- and type-compatible with `000001` for the columns
the amendment references, and shims `uuid_generate_v4()` onto `gen_random_uuid()`.
It validates the amendment's own DDL, not the baseline.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass

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
INSERT INTO core.tenants (tenant_id, tenant_name) VALUES
    ('11111111-1111-1111-1111-111111111111', 't1'),
    ('11111111-1111-1111-1111-222222222222', 't2')
ON CONFLICT DO NOTHING;
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
TENANT_2 = "'11111111-1111-1111-1111-222222222222'"
STORE = "'22222222-2222-2222-2222-222222222222'"
CELL = "'33333333-3333-3333-3333-333333333333'"
FORECAST = "'55555555-5555-5555-5555-555555555555'"
RECALC_OUTPUT = "'55555555-5555-5555-5555-666666666666'"
ALERT = "'66666666-6666-6666-6666-666666666666'"
PREDICTION_RUN = "'99999999-9999-9999-9999-999999999999'"
GATE_ID = "'88888888-8888-8888-8888-888888888888'"

# Section 3.2 naming rule: policy_version_id = policy_label || ':' || tenant_id.
POLICY_LABEL = "four-light-policy-v1"
POLICY = f"'{POLICY_LABEL}:{TENANT[1:-1]}'"

SEED = f"""
INSERT INTO core.stores (store_id) VALUES ({STORE}) ON CONFLICT DO NOTHING;
INSERT INTO geo.h3_cells (geo_cell_id) VALUES ({CELL}) ON CONFLICT DO NOTHING;
INSERT INTO operations.forecast_outputs (forecast_output_id)
    VALUES ({FORECAST}), ({RECALC_OUTPUT}) ON CONFLICT DO NOTHING;
INSERT INTO learning.prediction_runs (prediction_run_id)
    VALUES ({PREDICTION_RUN}) ON CONFLICT DO NOTHING;
INSERT INTO operations.alerts (alert_id, store_id, alert_reason_code, evidence_json, forecast_output_id, decision_policy_version_id)
    VALUES ({ALERT}, {STORE}, 'sitescore_gap', '{{}}'::jsonb, {FORECAST}, {POLICY}) ON CONFLICT DO NOTHING;
INSERT INTO pricing.exploration_gates (gate_id, tenant_id, budget_limit, budget_consumed, effective_from, effective_to, approved_by, rollback_condition, decision_policy_version_id)
    VALUES ({GATE_ID}, {TENANT}, 1000, 0, now(), now() + interval '30 day', 'ops', 'rollback on limit', {POLICY})
ON CONFLICT DO NOTHING;
"""


def uid(tag: str) -> str:
    """A stable, case-unique UUID literal, so cases cannot collide by accident."""
    h = hashlib.sha256(tag.encode()).hexdigest()
    return f"'{h[0:8]}-{h[8:12]}-4{h[13:16]}-8{h[17:20]}-{h[20:32]}'"


def corr(label: str) -> str:
    return "c" + hashlib.sha256(label.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Case:
    label: str
    statement: str
    accepted: bool
    #: constraint / index names, one of which must appear in the rejection.
    expects: tuple[str, ...] = ()
    #: per-case dependency rows, applied in the same transaction as `statement`.
    setup: str = ""

    def sql(self) -> str:
        return f"{self.setup}\n{self.statement}" if self.setup else self.statement


# --- section 6.2: asset.deal_outcomes -------------------------------------

DEAL_COLUMNS = (
    "INSERT INTO asset.deal_outcomes (tenant_id, valuation_run_id, outcome_kind,"
    " realized_transaction_price, realized_transaction_at, no_deal_reason_code,"
    " no_deal_note, duration_days, deal_terms, recorded_by, recorded_at,"
    " source_authority, correlation_id) VALUES "
)
FULL_TERMS = (
    "'{\"payment_method\": \"mortgage_70\", \"handover_date\": \"2026-12-01\","
    " \"contingencies\": []}'::jsonb"
)
TERMS_MISSING_HANDOVER = (
    "'{\"payment_method\": \"mortgage_70\", \"contingencies\": []}'::jsonb"
)
TERMS_BAD_CONTINGENCIES = (
    "'{\"payment_method\": \"mortgage_70\", \"handover_date\": \"2026-12-01\","
    " \"contingencies\": \"none\"}'::jsonb"
)


def deal_case(
    label: str,
    *,
    kind: str,
    accepted: bool,
    price: str = "NULL",
    at: str = "NULL",
    reason: str = "NULL",
    note: str = "NULL",
    duration: str = "30",
    terms: str = "NULL",
    expects: tuple[str, ...] = (),
    valuation: str | None = None,
    seed_valuation: bool = False,
) -> Case:
    setup = ""
    if valuation is None:
        valuation = uid("valuation:" + label)
        seed_valuation = True
    if seed_valuation:
        setup = f"INSERT INTO asset.valuation_runs (valuation_run_id) VALUES ({valuation});"
    statement = (
        DEAL_COLUMNS + f"({TENANT},{valuation},'{kind}',{price},{at},{reason},{note},"
        f"{duration},{terms},'u',now(),'moi','{corr(label)}')"
    )
    return Case(label, statement, accepted, expects, setup)


SHARED_VALUATION = uid("valuation:shared-for-unique-index")

DEAL_CASES = [
    deal_case("deal: CLOSED with price, date and terms",
              kind="CLOSED", price="1000", at="now()", terms=FULL_TERMS, accepted=True),
    deal_case("deal: CLOSED missing price",
              kind="CLOSED", at="now()", terms=FULL_TERMS, accepted=False,
              expects=("chk_deal_outcome_closed_fields",)),
    deal_case("deal: CLOSED missing date",
              kind="CLOSED", price="1000", terms=FULL_TERMS, accepted=False,
              expects=("chk_deal_outcome_closed_fields",)),
    deal_case("deal: non-CLOSED without reason",
              kind="WITHDRAWN", accepted=False,
              expects=("chk_deal_outcome_closed_fields",)),
    deal_case("deal: non-CLOSED carrying price",
              kind="WITHDRAWN", price="1000", at="now()", reason="'PRICE_GAP'",
              accepted=False, expects=("chk_deal_outcome_closed_fields",)),
    deal_case("deal: reason OTHER without note",
              kind="EXPIRED", reason="'OTHER'", accepted=False,
              expects=("chk_deal_outcome_other_note",)),
    deal_case("deal: negative duration_days",
              kind="EXPIRED", reason="'CONDITION'", duration="-1", accepted=False,
              expects=("chk_deal_outcome_duration",)),
    deal_case("deal: CLOSED without deal_terms",
              kind="CLOSED", price="1000", at="now()", accepted=False,
              expects=("chk_deal_outcome_terms_completeness",)),
    deal_case("deal: CLOSED terms missing handover_date",
              kind="CLOSED", price="1000", at="now()", terms=TERMS_MISSING_HANDOVER,
              accepted=False, expects=("chk_deal_outcome_terms_completeness",)),
    deal_case("deal: CLOSED terms with non-array contingencies",
              kind="CLOSED", price="1000", at="now()", terms=TERMS_BAD_CONTINGENCIES,
              accepted=False, expects=("chk_deal_outcome_terms_completeness",)),
    deal_case("deal: non-CLOSED carrying deal_terms",
              kind="EXPIRED", reason="'CONDITION'", terms=FULL_TERMS, accepted=False,
              expects=("chk_deal_outcome_terms_completeness",)),
    # This pair shares one valuation on purpose: it is the only case that tests
    # the one-outcome-per-valuation unique index.
    deal_case("deal: first outcome for a valuation",
              kind="CLOSED", price="1000", at="now()", terms=FULL_TERMS,
              valuation=SHARED_VALUATION, seed_valuation=True, accepted=True),
    deal_case("deal: second outcome for the same valuation",
              kind="EXPIRED", reason="'CONDITION'", valuation=SHARED_VALUATION,
              accepted=False, expects=("idx_deal_outcome_valuation",)),
]


# --- section 4.3: operations.forecast_feedback ----------------------------

FEEDBACK_COLUMNS = (
    "INSERT INTO operations.forecast_feedback (tenant_id, store_id, feedback_kind,"
    " target_alert_id, target_forecast_output_id, target_prediction_id,"
    " corrected_metric, observed_value, corrected_value, correction_unit,"
    " effective_from, effective_to, reason_code, submitted_by, submitted_at,"
    " approval_status, approved_by, approved_at, applied_status,"
    " not_applied_reason_code, recalculation_forecast_output_id,"
    " recalculation_run_id, applied_at, correlation_id) VALUES "
)


def feedback_case(
    label: str,
    *,
    kind: str,
    approval: str,
    accepted: bool,
    alert: str = "NULL",
    forecast: str = "NULL",
    prediction: str = "NULL",
    metric: str = "NULL",
    observed: str = "NULL",
    corrected: str = "NULL",
    unit: str = "NULL",
    eff_from: str = "'2026-01-01'",
    eff_to: str = "'2026-01-31'",
    approved_by: str = "NULL",
    approved_at: str = "NULL",
    applied: str = "'PENDING_APPLICATION'",
    not_applied_reason: str = "NULL",
    recalc_output: str = "NULL",
    recalc_run: str = "NULL",
    applied_at: str = "NULL",
    expects: tuple[str, ...] = (),
) -> Case:
    statement = (
        FEEDBACK_COLUMNS
        + f"({TENANT},{STORE},'{kind}',{alert},{forecast},{prediction},"
        f"{metric},{observed},{corrected},{unit},{eff_from},{eff_to},'r','u',now(),"
        f"'{approval}',{approved_by},{approved_at},{applied},{not_applied_reason},"
        f"{recalc_output},{recalc_run},{applied_at},'{corr(label)}')"
    )
    return Case(label, statement, accepted, expects)


CORRECTION = dict(metric="'revenue'", observed="10", corrected="20", unit="'TWD'")

FEEDBACK_CASES = [
    feedback_case("feedback: ALERT_DISPOSITION on alert",
                  kind="ALERT_DISPOSITION", alert=ALERT, approval="AUTO_ACCEPTED",
                  applied="'APPLIED_DISPOSITION'", applied_at="now()", accepted=True),
    feedback_case("feedback: no target at all",
                  kind="CONTEXT_ANNOTATION", approval="AUTO_ACCEPTED",
                  applied="'APPLIED_TRAINING_EXCLUSION'", applied_at="now()",
                  accepted=False, expects=("chk_feedback_has_target",)),
    feedback_case("feedback: OUTCOME_CORRECTION on forecast output",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="PENDING",
                  **CORRECTION, accepted=True),
    feedback_case("feedback: OUTCOME_CORRECTION auto-accepted",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="AUTO_ACCEPTED",
                  **CORRECTION, accepted=False,
                  expects=("chk_feedback_correction_needs_approval",)),
    feedback_case("feedback: OUTCOME_CORRECTION without values",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="PENDING",
                  accepted=False, expects=("chk_feedback_correction_target",)),
    feedback_case("feedback: annotation carrying correction",
                  kind="CONTEXT_ANNOTATION", alert=ALERT, approval="AUTO_ACCEPTED",
                  applied="'APPLIED_TRAINING_EXCLUSION'", applied_at="now()",
                  **CORRECTION, accepted=False,
                  expects=("chk_feedback_correction_exclusive",)),
    feedback_case("feedback: APPROVED without approver",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="APPROVED",
                  **CORRECTION, accepted=False,
                  expects=("chk_feedback_approver_present",)),
    feedback_case("feedback: effective_to before from",
                  kind="ALERT_DISPOSITION", alert=ALERT, approval="AUTO_ACCEPTED",
                  applied="'APPLIED_DISPOSITION'", applied_at="now()",
                  eff_from="'2026-02-01'", eff_to="'2026-01-01'", accepted=False,
                  expects=("chk_feedback_period",)),
    feedback_case("feedback: NOT_APPLIED without reason",
                  kind="ALERT_DISPOSITION", alert=ALERT, approval="REJECTED",
                  approved_by="'approver'", approved_at="now()",
                  applied="'NOT_APPLIED'", accepted=False,
                  expects=("chk_feedback_not_applied_reason",)),
    feedback_case("feedback: NOT_APPLIED with reason",
                  kind="ALERT_DISPOSITION", alert=ALERT, approval="REJECTED",
                  approved_by="'approver'", approved_at="now()",
                  applied="'NOT_APPLIED'", not_applied_reason="'REJECTED_BY_APPROVER'",
                  accepted=True),
    feedback_case("feedback: APPLIED_RECALCULATION with no provenance",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="APPROVED",
                  approved_by="'approver'", approved_at="now()", **CORRECTION,
                  applied="'APPLIED_RECALCULATION'", applied_at="now()", accepted=False,
                  expects=("chk_feedback_recalculation_provenance",)),
    feedback_case("feedback: APPLIED_RECALCULATION with only run id",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="APPROVED",
                  approved_by="'approver'", approved_at="now()", **CORRECTION,
                  applied="'APPLIED_RECALCULATION'", recalc_run=PREDICTION_RUN,
                  applied_at="now()", accepted=False,
                  expects=("chk_feedback_recalculation_provenance",)),
    feedback_case("feedback: APPLIED_RECALCULATION with output id",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="APPROVED",
                  approved_by="'approver'", approved_at="now()", **CORRECTION,
                  applied="'APPLIED_RECALCULATION'", recalc_output=RECALC_OUTPUT,
                  recalc_run=PREDICTION_RUN, applied_at="now()", accepted=True),
    feedback_case("feedback: recalculation columns without recalculation status",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="PENDING",
                  **CORRECTION, recalc_output=RECALC_OUTPUT, accepted=False,
                  expects=("chk_feedback_recalculation_provenance",)),
    feedback_case("feedback: OUTCOME_CORRECTION PENDING yet applied",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="PENDING",
                  **CORRECTION, applied="'APPLIED_RECALCULATION'",
                  recalc_output=RECALC_OUTPUT, applied_at="now()", accepted=False,
                  expects=("chk_feedback_applied_requires_approval",
                           "chk_feedback_pending_applied_status")),
    feedback_case("feedback: OUTCOME_CORRECTION REJECTED yet applied",
                  kind="OUTCOME_CORRECTION", forecast=FORECAST, approval="REJECTED",
                  approved_by="'approver'", approved_at="now()", **CORRECTION,
                  applied="'APPLIED_RECALCULATION'", recalc_output=RECALC_OUTPUT,
                  applied_at="now()", accepted=False,
                  expects=("chk_feedback_applied_requires_approval",
                           "chk_feedback_rejected_applied_status")),
    feedback_case("feedback: CONTEXT_ANNOTATION PENDING yet applied",
                  kind="CONTEXT_ANNOTATION", alert=ALERT, approval="PENDING",
                  applied="'APPLIED_TRAINING_EXCLUSION'", applied_at="now()",
                  accepted=False,
                  expects=("chk_feedback_applied_requires_approval",
                           "chk_feedback_pending_applied_status")),
    feedback_case("feedback: annotation routed to recalculation path",
                  kind="CONTEXT_ANNOTATION", alert=ALERT, approval="AUTO_ACCEPTED",
                  applied="'APPLIED_RECALCULATION'", recalc_output=RECALC_OUTPUT,
                  applied_at="now()", accepted=False,
                  expects=("chk_feedback_kind_applied_status",)),
    feedback_case("feedback: disposition routed to training exclusion path",
                  kind="ALERT_DISPOSITION", alert=ALERT, approval="AUTO_ACCEPTED",
                  applied="'APPLIED_TRAINING_EXCLUSION'", applied_at="now()",
                  accepted=False, expects=("chk_feedback_kind_applied_status",)),
    feedback_case("feedback: applied without applied_at",
                  kind="ALERT_DISPOSITION", alert=ALERT, approval="AUTO_ACCEPTED",
                  applied="'APPLIED_DISPOSITION'", accepted=False,
                  expects=("chk_feedback_applied_at",)),
    feedback_case("feedback: pending carrying applied_at",
                  kind="ALERT_DISPOSITION", alert=ALERT, approval="PENDING",
                  applied="'PENDING_APPLICATION'", applied_at="now()", accepted=False,
                  expects=("chk_feedback_applied_at",)),
]


# --- section 5.2: expansion.heatzone_composition --------------------------

COMPOSITION_COLUMNS = (
    "INSERT INTO expansion.heatzone_composition (zone_id, tenant_id, member_cell_id,"
    " composition_kind, parent_zone_id, decided_by, decided_at,"
    " decision_policy_version_id, override_reason) VALUES "
)


def composition_case(
    label: str,
    *,
    zone: str,
    kind: str,
    accepted: bool,
    parent: str = "NULL",
    decided_by: str = "'system'",
    override: str = "NULL",
    cell: str | None = None,
    prefix: str = "",
    policy: str = POLICY,
    expects: tuple[str, ...] = (),
) -> Case:
    setup = ""
    if cell is None:
        cell = uid("cell:" + label)
        setup = f"INSERT INTO geo.h3_cells (geo_cell_id) VALUES ({cell});"
    statement = prefix + (
        COMPOSITION_COLUMNS + f"({zone},{TENANT},{cell},'{kind}',{parent},{decided_by},"
        f"now(),{policy},{override})"
    )
    return Case(label, statement, accepted, expects, setup)


COMPOSITION_CASES = [
    composition_case("composition: MERGED decided by system",
                     zone="'MZ-0123456789abcdef'", kind="MERGED", cell=CELL,
                     accepted=True),
    composition_case("composition: zone_id reusing cell uuid",
                     zone=CELL, kind="MERGED", accepted=False,
                     expects=("chk_composition_zone_id_format",)),
    composition_case("composition: SPLIT_CHILD without parent",
                     zone="'MZ-00000000000000aa'", kind="SPLIT_CHILD", accepted=False,
                     expects=("chk_composition_parent",)),
    composition_case("composition: human decision without reason",
                     zone="'MZ-00000000000000bb'", kind="MERGED", decided_by="'alice'",
                     accepted=False, expects=("chk_composition_override_reason",)),
    composition_case("composition: system decision with override",
                     zone="'MZ-00000000000000cc'", kind="MERGED", override="'because'",
                     accepted=False, expects=("chk_composition_override_reason",)),
    composition_case("composition: policy binding without tenant suffix",
                     zone="'MZ-00000000000000dd'", kind="MERGED",
                     policy=f"'{POLICY_LABEL}'", accepted=False,
                     expects=("heatzone_composition_decision_policy_version_id_fkey",)),
    composition_case("composition: second active record on same cell",
                     zone="'MZ-0123456789abcde1'", kind="MERGED", cell=CELL,
                     accepted=False,
                     expects=("idx_heatzone_composition_active_member",)),
    composition_case("composition: append-only override after revert",
                     zone="'MZ-0123456789abcde2'", kind="MERGED", cell=CELL,
                     decided_by="'alice'", override="'manual override after revert'",
                     prefix=("UPDATE expansion.heatzone_composition SET reverted_at = now()"
                             f" WHERE tenant_id = {TENANT} AND member_cell_id = {CELL};\n"),
                     accepted=True),
]


# --- section 7: pricing.exploration_gates / exploration_decisions ---------

GATE_COLUMNS = (
    "INSERT INTO pricing.exploration_gates (tenant_id, budget_limit, budget_consumed,"
    " effective_from, effective_to, approved_by, rollback_condition,"
    " decision_policy_version_id) VALUES "
)
EXPLORATION_DECISION_COLUMNS = (
    "INSERT INTO pricing.exploration_decisions (decision_id, gate_id, tenant_id, sku_id,"
    " store_id, baseline_price, explored_price, budget_consumed, algorithm) VALUES "
)


def exploration_case(
    label: str,
    *,
    accepted: bool,
    tenant: str = TENANT,
    baseline: str = "100.00",
    explored: str = "105.00",
    budget: str = "5.00",
    gate: str = GATE_ID,
    expects: tuple[str, ...] = (),
) -> Case:
    decision = uid("decision:" + label)
    setup = (
        "INSERT INTO workflow.decisions (decision_id, policy_version_id) VALUES "
        f"({decision},{POLICY});"
    )
    statement = (
        EXPLORATION_DECISION_COLUMNS
        + f"({decision},{gate},{tenant},'SKU-001',{STORE},{baseline},{explored},"
        f"{budget},'THOMPSON_SAMPLING')"
    )
    return Case(label, statement, accepted, expects, setup)


GATE_CASES = [
    Case("gate: valid authorization",
         GATE_COLUMNS + f"({TENANT},1000,0,now(),now()+interval '30 day','a',"
         f"'revert on breach',{POLICY})", True),
    Case("gate: consumed beyond limit",
         GATE_COLUMNS + f"({TENANT},1000,2000,now(),now()+interval '30 day','a','revert',"
         f"{POLICY})", False, ("chk_gate_budget_consumed",)),
    Case("gate: effective_to not after from",
         GATE_COLUMNS + f"({TENANT},1000,0,now(),now(),'a','revert',{POLICY})",
         False, ("chk_gate_window",)),
    Case("gate: empty rollback_condition",
         GATE_COLUMNS + f"({TENANT},1000,0,now(),now()+interval '1 day','a','',{POLICY})",
         False, ("chk_gate_rollback_condition",)),
    Case("gate: accumulated consumption beyond limit",
         f"UPDATE pricing.exploration_gates SET budget_consumed = 1001 WHERE gate_id = {GATE_ID}",
         False, ("chk_gate_budget_consumed",)),
    exploration_case("exploration_decision: valid record", accepted=True),
    exploration_case("exploration_decision: gate tenant mismatch", tenant=TENANT_2,
                     accepted=False, expects=("fk_exploration_decisions_gate_tenant",)),
    exploration_case("exploration_decision: negative explored price", explored="-105.00",
                     accepted=False, expects=("chk_exploration_decision_prices",)),
    exploration_case("exploration_decision: negative budget_consumed", budget="-5.00",
                     accepted=False, expects=("chk_exploration_decision_budget",)),
]


# --- section 3.2 / 3.4: workflow.decision_policies and its bindings -------

POLICY_INSERT = (
    "INSERT INTO workflow.decision_policies (policy_version_id, policy_label, policy_id,"
    " policy_version, policy_kind, tenant_id, effective_from, owner_role, approved_by,"
    " approved_at, input_contract, output_contract, change_reason, parameters,"
    " declared_inputs) VALUES "
)


def policy_case(
    label: str,
    *,
    version_id: str,
    policy_label: str,
    policy_id: str,
    version: str,
    accepted: bool,
    tenant: str = TENANT,
    kind: str = "forecast_alert",
    inputs: str = "ARRAY['sitescore_gap_ratio']",
    expects: tuple[str, ...] = (),
) -> Case:
    statement = (
        POLICY_INSERT + f"('{version_id}','{policy_label}','{policy_id}','{version}',"
        f"'{kind}',{tenant},now(),'ops','a',now(),'in','out','x','{{}}'::jsonb,{inputs})"
    )
    return Case(label, statement, accepted, expects)


POLICY_CASES = [
    Case("policy: tenant 2 receives active policy resolution",
         "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM workflow.decision_policies WHERE"
         f" tenant_id = {TENANT_2} AND policy_kind = 'forecast_alert' AND effective_to"
         " IS NULL) THEN RAISE EXCEPTION 'no policy for t2'; END IF; END $$;", True),
    Case("policy: retrofit row exists per tenant",
         "DO $$ BEGIN IF (SELECT count(*) FROM workflow.decision_policies WHERE"
         " policy_label = 'four-light-policy-0.0.0-retrofit') <> (SELECT count(*) FROM"
         " core.tenants) THEN RAISE EXCEPTION 'retrofit rows missing'; END IF; END $$;",
         True),
    policy_case("policy: second active version for same tenant and policy_id",
                version_id=f"four-light-policy-v2:{TENANT[1:-1]}",
                policy_label="four-light-policy-v2", policy_id="four-light-policy",
                version="2.0.0", accepted=False,
                expects=("idx_decision_policy_active",)),
    policy_case("policy: empty declared_inputs",
                version_id=f"other-v1:{TENANT[1:-1]}", policy_label="other-v1",
                policy_id="other", version="1.0.0", kind="heatzone_merge",
                inputs="ARRAY[]::text[]", accepted=False,
                expects=("chk_decision_policy_inputs",)),
    policy_case("policy: version id without tenant suffix",
                version_id="four-light-policy-v9", policy_label="four-light-policy-v9",
                policy_id="four-light-policy", version="9.0.0", accepted=False,
                expects=("chk_decision_policy_version_id_format",)),
    policy_case("policy: label containing the separator",
                version_id=f"bad:label:{TENANT[1:-1]}", policy_label="bad:label",
                policy_id="bad", version="1.0.0", kind="netplan_action", accepted=False,
                expects=("chk_decision_policy_label",)),
    policy_case("policy: active version for a different tenant, same policy_id",
                version_id=f"other-policy-v1:{TENANT_2[1:-1]}",
                policy_label="other-policy-v1", policy_id="other-policy",
                version="1.0.0", tenant=TENANT_2, accepted=True),
]


# --- section 3.4 / 4.2: operations.alerts --------------------------------

UNSEEN_FORECAST = uid("forecast:unknown-policy-case")

ALERT_CASES = [
    Case("alert: evaluation identity duplicate for same forecast and policy",
         "INSERT INTO operations.alerts (store_id, alert_reason_code, evidence_json,"
         f" forecast_output_id, decision_policy_version_id) VALUES ({STORE},"
         f"'sitescore_gap','{{}}'::jsonb,{FORECAST},{POLICY})",
         False, ("idx_alerts_forecast_policy",)),
    Case("alert: policy binding without tenant suffix",
         "INSERT INTO operations.alerts (store_id, alert_reason_code, evidence_json,"
         f" forecast_output_id, decision_policy_version_id) VALUES ({STORE},"
         f"'sitescore_gap','{{}}'::jsonb,{UNSEEN_FORECAST},'{POLICY_LABEL}')",
         False, ("fk_alerts_decision_policy",),
         "INSERT INTO operations.forecast_outputs (forecast_output_id) VALUES"
         f" ({UNSEEN_FORECAST});"),
    Case("alert: deterioration before opened_at",
         "UPDATE operations.alerts SET deterioration_confirmed_at = opened_at -"
         f" interval '1 day' WHERE alert_id = {ALERT}",
         False, ("chk_alerts_deterioration_order",)),
    Case("alert: disposition outside enum",
         f"UPDATE operations.alerts SET disposition = 'MAYBE' WHERE alert_id = {ALERT}",
         False, ("chk_alerts_disposition",)),
    Case("alert: valid disposition",
         f"UPDATE operations.alerts SET disposition = 'TRUE_POSITIVE' WHERE alert_id = {ALERT}",
         True),
]


# --- section 5.1: expansion.heatzone_scores absorption -------------------

def heatzone_case(
    label: str,
    *,
    absorbed: str,
    remaining: str,
    ratio: str,
    basis: str,
    accepted: bool,
    expects: tuple[str, ...] = (),
) -> Case:
    statement = (
        "INSERT INTO expansion.heatzone_scores (absorbed_demand, remaining_demand,"
        f" absorption_ratio, absorption_basis_at) VALUES ({absorbed},{remaining},"
        f"{ratio},{basis})"
    )
    return Case(label, statement, accepted, expects)


# A ratio outside [0, 1] and a negative demand are also arithmetically inconsistent
# with their own inputs, so those two rows legitimately violate a second absorption
# constraint; both names are accepted rather than pretending only one can fire.
HEATZONE_CASES = [
    heatzone_case("heatzone: complete and consistent absorption",
                  absorbed="25", remaining="75", ratio="0.25", basis="now()",
                  accepted=True),
    heatzone_case("heatzone: fully unabsorbed zone",
                  absorbed="0", remaining="120", ratio="0", basis="now()",
                  accepted=True),
    heatzone_case("heatzone: zone with no demand at all",
                  absorbed="0", remaining="0", ratio="0", basis="now()", accepted=True),
    heatzone_case("heatzone: absorbed without remaining",
                  absorbed="10", remaining="NULL", ratio="1", basis="now()",
                  accepted=False, expects=("chk_heatzone_absorption_complete",)),
    heatzone_case("heatzone: absorbed without basis_at",
                  absorbed="10", remaining="90", ratio="0.1", basis="NULL",
                  accepted=False, expects=("chk_heatzone_absorption_complete",)),
    heatzone_case("heatzone: absorbed without ratio",
                  absorbed="10", remaining="90", ratio="NULL", basis="now()",
                  accepted=False, expects=("chk_heatzone_absorption_complete",)),
    heatzone_case("heatzone: negative absorbed demand",
                  absorbed="-5", remaining="105", ratio="0", basis="now()",
                  accepted=False,
                  expects=("chk_heatzone_absorption_non_negative",
                           "chk_heatzone_absorption_consistent")),
    heatzone_case("heatzone: negative remaining demand",
                  absorbed="100", remaining="-50", ratio="1", basis="now()",
                  accepted=False,
                  expects=("chk_heatzone_absorption_non_negative",
                           "chk_heatzone_absorption_consistent")),
    heatzone_case("heatzone: ratio above 1",
                  absorbed="15", remaining="0", ratio="1.5", basis="now()",
                  accepted=False,
                  expects=("chk_heatzone_absorption_ratio",
                           "chk_heatzone_absorption_consistent")),
    heatzone_case("heatzone: ratio inconsistent with demands",
                  absorbed="10", remaining="90", ratio="0.9", basis="now()",
                  accepted=False, expects=("chk_heatzone_absorption_consistent",)),
]


CASES: list[Case] = [
    *DEAL_CASES,
    *FEEDBACK_CASES,
    *COMPOSITION_CASES,
    *GATE_CASES,
    *POLICY_CASES,
    *ALERT_CASES,
    *HEATZONE_CASES,
]

_undeclared = [c.label for c in CASES if not c.accepted and not c.expects]
if _undeclared:  # a rejecting case without a named cause proves nothing
    raise SystemExit(f"cases missing an expected constraint name: {_undeclared}")


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


#: pgserver's own `psql` helper swallows the server's error text into a logged
#: message, which would leave section C unable to tell *why* a row was rejected.
#: The bundled binary is therefore invoked directly so stderr is captured.
PSQL = pathlib.Path(pgserver.__file__).parent / "pginstall" / "bin" / "psql"


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
        result = subprocess.run(
            [str(PSQL), self._uri, "-v", "ON_ERROR_STOP=1", "-q", "-X",
             "--single-transaction", "-f", str(path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return False, (result.stderr + result.stdout).strip()
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

    ok, err = run(SEED)
    print("\nfixture seed:", "OK" if ok else f"FAIL\n{err[:700]}")
    if not ok:
        server.cleanup()
        return 1

    print("\n=== C. constraint behaviour ===")
    mismatches = 0
    for case in CASES:
        accepted, err = run(case.sql())
        want = "accept" if case.accepted else "reject"
        got = "accept" if accepted else "reject"
        if accepted != case.accepted:
            mismatches += 1
            print(f"  MISMATCH {case.label:<52} expected={want} got={got}")
            if err:
                print("           " + err.replace("\n", "\n           ")[:300])
            continue
        matched = [name for name in case.expects if name in err]
        if not accepted and not matched:
            mismatches += 1
            print(f"  WRONGCAUSE {case.label:<50} expected one of {case.expects}")
            print("           " + err.replace("\n", "\n           ")[:300])
        else:
            cause = f" via {matched[0]}" if matched else ""
            print(f"  OK       {case.label:<52} {want}{cause}")
    print(f"  -> {len(CASES) - mismatches}/{len(CASES)} behaved as designed")

    server.cleanup()
    return 1 if (apply_fails or idem_fails or mismatches) else 0


if __name__ == "__main__":
    sys.exit(main())
