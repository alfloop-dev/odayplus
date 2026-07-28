"""Validate the critical-path projection's eligibility MODEL against the real view.

Motivation. Acceptance criterion 3 is judged by
`model_ready.forecast_training_view.is_training_eligible`, but every number that
put `-b3` on the critical path (`horizon_critical_path.json`, h28 0 -> 419) was
produced by `horizon-critical-path-probe.py`, which RE-IMPLEMENTS eligibility on
the SOURCE plane -- it never ran the view. The projection therefore rests on an
unverified premise: that the re-implementation and the view agree.

Reading the view (`scripts/models/sql/model_ready_views.sql`) shows the
re-implementation is not obviously equivalent. `transaction_daily`'s
`source_run_complete` is

    bool_and( observation_time >= event_time
              AND ingested_at   >= observation_time
              AND source_run_complete )

while the model asserts only the third conjunct. The two temporal-ordering
predicates are `bool_and` over every transaction of a store-day, so a SINGLE
record with skewed clocks silently disqualifies the whole day -- the same shape
that let 4 stray lineage rows cost an entire date under Defect D. The view also
requires `cardinality(lineage_window_snapshot_ids) > 0`, the four causal
features to be non-null, and `label_maturity_time <= CURRENT_TIMESTAMP`.

This probe settles it by measurement instead of by reading: it runs BOTH methods
against the SAME PostgreSQL 16 target, so no state drift can explain a
disagreement, and compares the eligible `(tenant, store, date)` SETS -- not just
the horizon counts, which could match by coincidence. A directional set
difference also says WHICH way the model errs:

  * only_in_model -> the model is OPTIMISTIC; 419 is an over-count and the
    backwards plan may not clear criterion 3.
  * only_in_view  -> the model is CONSERVATIVE; 419 is a floor.

`exclusion_reason` is reported for every `only_in_model` pair, which names the
exact predicate that diverged rather than leaving it to inspection.

Why the PG16 target is a fair plane: it was last activated after `-s3` and holds
376 998 transactions over 2026-05-22..07-27 -- the same landed state the
critical-path probe measured. Both methods here read that one snapshot.

Read-only. Only `ON COMMIT DROP` temp relations are created and the transaction
is rolled back; nothing in `core`, `data_plane` or `model_ready` is written, and
no Kubernetes object is touched. Safe to run while a slice ingests, because it
reads the TARGET while ingestion writes the SOURCE.

Usage: source /tmp/odp-forecast-dsn.env && python3 eligibility-model-fidelity-probe.py
"""

import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

import psycopg

OUT = os.environ.get(
    "PROBE_OUT",
    "/tmp/odp-forecast-evidence/eligibility_model_fidelity.json",
)

HORIZONS = (7, 14, 28, 56, 84, 168)

# The view, as the activation's `verify` reads it. One row per (tenant, store,
# date) with the authoritative verdict and, when excluded, the reason.
VIEW_SQL = """
SELECT tenant_id::text, store_id::text, date, is_training_eligible,
       coalesce(exclusion_reason, 'ELIGIBLE')
FROM model_ready.forecast_training_view
"""

# Verbatim from horizon-critical-path-probe.py, with tenant_id added to the
# projection so both sides key on the view's own grain. Any edit here breaks the
# comparison: this must stay a copy of the model under test, not an improvement
# of it.
MODEL_DAILY_SQL = """
WITH src AS (
    SELECT
        store.tenant_id,
        txn.transaction_id,
        txn.store_id,
        (txn.event_time AT TIME ZONE 'UTC')::date AS d,
        source.source_run_complete,
        coalesce(cardinality(source.source_snapshot_ids), 0) > 0 AS has_lineage
    FROM core.transactions AS txn
    INNER JOIN core.stores AS store ON store.store_id = txn.store_id
    LEFT JOIN LATERAL (
        SELECT
            array_agg(DISTINCT lineage.source_snapshot_id::text)
                AS source_snapshot_ids,
            bool_and(
                ingestion.status = 'SUCCEEDED'
                AND ingestion.finished_at IS NOT NULL
            ) AS source_run_complete
        FROM data_plane.canonical_lineage AS lineage
        INNER JOIN data_plane.ingestion_runs AS ingestion
            ON ingestion.run_id = lineage.run_id
        WHERE lineage.tenant_id = store.tenant_id
          AND lineage.canonical_table = 'core.transactions'
          AND lineage.canonical_id = txn.transaction_id
    ) AS source ON TRUE
    WHERE txn.transaction_status = 'succeeded'
      AND txn.currency = 'TWD'
)
SELECT
    tenant_id::text,
    store_id::text,
    d,
    coalesce(bool_and(has_lineage), FALSE)
        AND coalesce(bool_and(source_run_complete), FALSE) AS attested
FROM src
GROUP BY tenant_id, store_id, d
ORDER BY tenant_id, store_id, d;
"""


def islands(dates):
    """Longest-run decomposition; mirrors the view's date - row_number()
    gaps-and-islands grouping. Shared by both sides so that any difference in
    the horizon counts is attributable to eligibility, never to island logic."""
    runs = []
    run = 0
    prev = None
    for d in sorted(dates):
        if prev is not None and d == prev + timedelta(days=1):
            run += 1
        else:
            if run:
                runs.append(run)
            run = 1
        prev = d
    if run:
        runs.append(run)
    return runs


def horizon_counts(eligible_by_store):
    streaks = {}
    for key, dates in eligible_by_store.items():
        runs = islands(dates)
        streaks[key] = max(runs) if runs else 0
    return {
        "stores_with_any_eligible_date": sum(1 for v in streaks.values() if v),
        "max_streak_days": max(streaks.values()) if streaks else 0,
        "horizon_windows": {
            f"h{h}": sum(1 for v in streaks.values() if v >= h) for h in HORIZONS
        },
    }


def model_eligible(present, attested):
    """The model's rule: the date is attested AND the store transacted on all 28
    preceding dates (the view's prior_day_count_28 = 28, counted over
    mature_daily with no attestation filter)."""
    out = []
    for d in sorted(present):
        if d not in attested:
            continue
        if sum(1 for k in range(1, 29) if (d - timedelta(days=k)) in present) == 28:
            out.append(d)
    return out


def main():
    dsn = os.environ["ODAY_DATABASE_URL"]
    started = datetime.now(UTC)
    with psycopg.connect(dsn, connect_timeout=30) as conn:
        conn.execute("SET statement_timeout = '45min'")

        # --- side A: the authoritative view -------------------------------
        view_rows = conn.execute(VIEW_SQL).fetchall()
        view_eligible = defaultdict(set)
        view_reason = {}
        reason_hist = Counter()
        for tenant, store, d, eligible, reason in view_rows:
            reason_hist[reason] += 1
            view_reason[(tenant, store, d)] = reason
            if eligible:
                view_eligible[(tenant, store)].add(d)

        # --- side B: the projection's model, same snapshot -----------------
        model_rows = conn.execute(MODEL_DAILY_SQL).fetchall()
        per_store = defaultdict(lambda: (set(), set()))
        for tenant, store, d, attested in model_rows:
            present, att = per_store[(tenant, store)]
            present.add(d)
            if attested:
                att.add(d)
        model_elig = {
            key: set(model_eligible(present, att))
            for key, (present, att) in per_store.items()
        }

        conn.rollback()

    view_pairs = {(k[0], k[1], d) for k, ds in view_eligible.items() for d in ds}
    model_pairs = {(k[0], k[1], d) for k, ds in model_elig.items() for d in ds}
    only_model = view_pairs.symmetric_difference(model_pairs) & model_pairs
    only_view = view_pairs.symmetric_difference(model_pairs) & view_pairs

    # Naming the predicate that diverged: for a pair the model calls eligible
    # and the view does not, the view's own exclusion_reason is the answer.
    only_model_reasons = Counter(
        view_reason.get(p, 'ABSENT_FROM_VIEW') for p in only_model
    )

    agrees = not only_model and not only_view
    if agrees:
        verdict = (
            "EXACT: the projection model and the view select the same eligible "
            "(tenant, store, date) set, so horizon_critical_path.json's 419 is "
            "measured on the same quantity acceptance is judged on."
        )
    elif only_model:
        verdict = (
            "MODEL OPTIMISTIC: it admits pairs the view excludes, so the "
            "projected h28 = 419 is an OVER-count and criterion 3 cannot be "
            "claimed from it without re-projection."
        )
    else:
        verdict = (
            "MODEL CONSERVATIVE: the view admits pairs the model excludes, so "
            "the projected h28 = 419 is a FLOOR."
        )

    payload = {
        "captured_at": started.isoformat(),
        "purpose": (
            "Validate the eligibility re-implementation behind "
            "horizon_critical_path.json against model_ready.forecast_training_view "
            "by running both against one PG16 snapshot."
        ),
        "method": {
            "plane": "TARGET PG16 (both sides read the same activated snapshot)",
            "view_side": "model_ready.forecast_training_view.is_training_eligible",
            "model_side": (
                "horizon-critical-path-probe.py's DAILY_SQL + "
                "prior_day_count_28 = 28, copied verbatim"
            ),
            "comparison": (
                "set equality on eligible (tenant, store, date), then horizon "
                "counts computed from each side with the SAME island function"
            ),
            "known_candidate_divergences": [
                "transaction_daily.source_run_complete also requires "
                "observation_time >= event_time AND ingested_at >= observation_time",
                "cardinality(lineage_window_snapshot_ids) > 0",
                "revenue_lag_1/7 and rolling_mean_7/28 non-null",
                "label_maturity_time <= CURRENT_TIMESTAMP",
            ],
            "read_only": True,
        },
        "snapshot": {
            "view_rows": len(view_rows),
            "model_store_days": len(model_rows),
            "view_exclusion_reasons": dict(reason_hist.most_common()),
        },
        "eligible_pairs": {
            "view": len(view_pairs),
            "model": len(model_pairs),
            "only_in_model": len(only_model),
            "only_in_view": len(only_view),
            "only_in_model_by_view_reason": dict(only_model_reasons.most_common()),
            "only_in_model_sample": [
                [t, s, d.isoformat()] for t, s, d in sorted(only_model, key=str)[:10]
            ],
            "only_in_view_sample": [
                [t, s, d.isoformat()] for t, s, d in sorted(only_view, key=str)[:10]
            ],
        },
        "horizons_from_view": horizon_counts(view_eligible),
        "horizons_from_model": horizon_counts(model_elig),
        "agrees_exactly": agrees,
        "verdict": verdict,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(json.dumps(payload["eligible_pairs"], indent=2))
    print(payload["verdict"])


if __name__ == "__main__":
    main()
