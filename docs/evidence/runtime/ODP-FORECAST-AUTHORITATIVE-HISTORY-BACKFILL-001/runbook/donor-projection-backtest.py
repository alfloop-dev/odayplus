"""Backtest the donor rule that the whole -b3 critical-path decision rests on.

Motivation. Criterion 3 was attributed to slice `-b3` by
`horizon_critical_path.json`, which reported that the backwards family moves
h28 from 0 to 419 while the gap-fill family moves it to 2. That number is a
PROJECTION: the not-yet-ingested dates carry no data, so the probe has to decide
which stores would be trading on them, and it decides with a donor rule --
optimistic (traded on the nearest dense landed day) and strict (traded on every
one of the nearest 7). Two later probes corroborated the *population* the rule
projects onto -- `backwards_window_store_density.json` showed the backwards
dates are dense trading days at 0.934 of a landed control, and
`backwards_window_store_mappability.json` showed 420 of the 421 always-trading
backwards stores still resolve through `core.stores`. Neither tested the RULE.
Nothing had ever compared a donor projection against an outcome, because until
now no projected date had subsequently landed.

`-s4` supplies the first blind holdout, and it is blind in the strict sense: the
critical-path probe's cached grid was fetched at 2026-07-28T19:54Z, while `-s4`
was mid-flight. It already held 2026-07-12 and 2026-07-13 -- the two partitions
`-s4` had finished -- and did not hold 2026-07-14..2026-07-17. Those dates have
since landed under the same Job. The projection for them was therefore made
without any of their data, and can now be scored against it.

Method. `pre` is that cached grid, byte-for-byte, not a reconstruction. `post`
is the grid re-fetched after `-s4` reached SUCCEEDED. The holdout is every date
`post` holds and `pre` does not. The projection is produced by IMPORTING
`apply_slices` and `dense_dates` from the probe under test and registering a
synthetic slice window over the holdout span, so the donor logic being scored is
the identical code, not a paraphrase of it.

Scored three ways:

  * per-date store sets -- recall (of the stores that actually traded, how many
    did the rule project) and precision (of the stores it projected, how many
    actually traded), plus the raw misses in each direction.
  * attestation -- the counterfactual marks projected dates ATTESTED as well as
    present. If a landed holdout date is not in fact attested, the rule is
    optimistic in a second, independent way that the store-set score cannot see.
  * end to end -- `evaluate()` over `pre + projection` against `evaluate()` over
    `post`, restricted to the same date domain, which scores the rule on the
    quantity that actually decides acceptance rather than on an intermediate.

What a pass does and does not buy. The holdout dates sit 1-5 days from their
donors and extend an island forwards; `-b1`..`-b4` sit up to two months from
theirs. This measurement therefore bounds the donor rule's error in the
FAVOURABLE regime. It can falsify the rule outright, and a clean result is a
lower bound on the error rather than a guarantee at two months' distance -- the
distance question is what the density and mappability probes address, by
measuring the backwards population directly against the source instead of
projecting it. Read the three together.

Read-only. Selects only; no writes, no DDL, no job mutation.

Usage:
    source /tmp/odp-forecast-dsn.env
    PRE_CACHE=/tmp/odp-horizon-daily-cache.pre-s4.json \
    POST_CACHE=/tmp/odp-horizon-daily-cache.post-s4.json \
    python3 donor-projection-backtest.py
"""

import importlib.util
import json
import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE_PATH = os.path.join(HERE, "horizon-critical-path-probe.py")

OUT = os.environ.get(
    "PROBE_OUT",
    "/tmp/odp-forecast-evidence-stage/donor_projection_backtest.json",
)
PRE_CACHE = os.environ.get("PRE_CACHE", "/tmp/odp-horizon-daily-cache.pre-s4.json")
POST_CACHE = os.environ.get("POST_CACHE", "/tmp/odp-horizon-daily-cache.post-s4.json")

# Synthetic slice name registered into the probe's own window table so that
# apply_slices projects exactly the holdout span.
HOLDOUT_KEY = "__backtest_holdout__"


def load_probe():
    """Import the probe under test. Its main() is guarded, so importing runs no
    queries and mutates nothing."""
    spec = importlib.util.spec_from_file_location("critpath_probe", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_grid(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    base = defaultdict(lambda: (set(), set()))
    for store, day, attested in raw["rows"]:
        d = date.fromisoformat(day)
        present, att = base[store]
        present.add(d)
        if attested:
            att.add(d)
    return dict(base), raw["fetched_at"]


def dates_of(base):
    return {d for present, _ in base.values() for d in present}


def stores_on(base, d, attested_only=False):
    idx = 1 if attested_only else 0
    return {s for s, sets in base.items() if d in sets[idx]}


def score(actual, projected):
    hit = actual & projected
    return {
        "actual_stores": len(actual),
        "projected_stores": len(projected),
        "hit": len(hit),
        "missed_by_rule": len(actual - projected),
        "invented_by_rule": len(projected - actual),
        "recall": round(len(hit) / len(actual), 4) if actual else None,
        "precision": round(len(hit) / len(projected), 4) if projected else None,
    }


def main():
    probe = load_probe()

    pre, pre_fetched = load_grid(PRE_CACHE)
    post, post_fetched = load_grid(POST_CACHE)

    pre_dates = dates_of(pre)
    post_dates = dates_of(post)

    # The holdout is confined to -s4's own window. By the time the post grid was
    # fetched the driver had already resumed -b1, which commits as it goes, so
    # `post` also carries dates from a slice that is still RUNNING. Those are
    # partial by construction -- scoring against a half-ingested date would
    # charge the rule for stores that simply have not been read yet -- so they
    # are excluded and reported rather than silently swept into the holdout.
    s4_start, s4_end = probe.SLICE_WINDOWS["s4"]
    new_dates = post_dates - pre_dates
    holdout = sorted(d for d in new_dates if s4_start <= d < s4_end)
    excluded_in_flight = sorted(d for d in new_dates if not (s4_start <= d < s4_end))
    if not holdout:
        raise SystemExit(
            "no holdout: post grid adds no dates inside -s4's window over pre. "
            "Re-fetch post after a slice has actually landed."
        )

    # Register the holdout span as a slice window so the imported donor logic
    # projects precisely it. end is exclusive, matching the manifests.
    probe.SLICE_WINDOWS[HOLDOUT_KEY] = (holdout[0], holdout[-1] + timedelta(days=1))
    dense, per_date_stores = probe.dense_dates(pre)

    projections = {}
    for rule, strict in (("optimistic", False), ("strict", True)):
        projections[rule] = probe.apply_slices(pre, (HOLDOUT_KEY,), dense, strict)

    per_date = []
    for d in holdout:
        actual = stores_on(post, d)
        actual_attested = stores_on(post, d, attested_only=True)
        row = {
            "date": str(d),
            "landed_attested_stores": len(actual_attested),
            "landed_fully_attested": len(actual_attested) == len(actual),
            "donors_used": {
                "optimistic": [
                    str(x) for x in sorted(dense, key=lambda x: abs((x - d).days))[:1]
                ],
                "strict_count": 7,
                "max_donor_distance_days": max(
                    abs((x - d).days)
                    for x in sorted(dense, key=lambda x: abs((x - d).days))[:7]
                ),
            },
        }
        for rule in ("optimistic", "strict"):
            projected = {
                s
                for s, (present, _) in projections[rule].items()
                if d in present and d not in pre[s][0]
            }
            row[rule] = score(actual, projected)
        per_date.append(row)

    # End to end: the horizon counts the projection implied, against the ones the
    # landed data actually produces. Restricted to the pre+holdout date domain so
    # that dates landed by anything other than this slice cannot flatter either
    # side.
    domain = pre_dates | set(holdout)

    def restrict(base):
        return {
            s: ({d for d in p if d in domain}, {d for d in a if d in domain})
            for s, (p, a) in base.items()
        }

    measured_after, _ = probe.evaluate(restrict(post))
    measured_before, _ = probe.evaluate(pre)
    end_to_end = {
        "measured_before_holdout": measured_before,
        "measured_after_holdout": measured_after,
    }
    for rule in ("optimistic", "strict"):
        end_to_end[f"projected_{rule}"], _ = probe.evaluate(restrict(projections[rule]))

    attestation_gap = [
        r["date"] for r in per_date if not r["landed_fully_attested"]
    ]

    # Continuity. Per-date recall understates the risk that matters: the donor
    # rule is all-or-none per store -- a store gets every counterfactual date or
    # none of them -- while a real store can trade on three of four days and
    # break its island. h28 needs 28 CONSECUTIVE eligible dates, so the quantity
    # to score is the population that trades on EVERY holdout date, not the
    # per-date average. If the rule projected more continuous stores than exist,
    # it over-states island length even while under-stating each single day.
    actual_every = set.intersection(*(stores_on(post, d) for d in holdout))
    continuity = {
        "note": (
            "stores present on EVERY holdout date. The donor rule is all-or-none "
            "per store, so its per-date set is also its continuous set; reality "
            "churns day to day. This is the comparison island length depends on."
        ),
        "actual_stores_on_every_holdout_date": len(actual_every),
    }
    for rule in ("optimistic", "strict"):
        projected_every = set.intersection(
            *(
                {
                    s
                    for s, (present, _) in projections[rule].items()
                    if d in present and d not in pre[s][0]
                }
                for d in holdout
            )
        )
        continuity[rule] = score(actual_every, projected_every)

    totals = {}
    for rule in ("optimistic", "strict"):
        totals[rule] = {
            key: sum(r[rule][key] for r in per_date)
            for key in ("actual_stores", "projected_stores", "hit",
                        "missed_by_rule", "invented_by_rule")
        }
        totals[rule]["recall"] = round(
            totals[rule]["hit"] / totals[rule]["actual_stores"], 4
        )
        totals[rule]["precision"] = round(
            totals[rule]["hit"] / totals[rule]["projected_stores"], 4
        )

    receipt = {
        "captured_at": datetime.now(UTC).isoformat(),
        "purpose": (
            "Score the donor rule behind horizon_critical_path.json against a "
            "blind holdout. The -b3 critical-path decision rests on that rule's "
            "projection of which stores trade on not-yet-ingested dates; -s4 is "
            "the first time a projected date has subsequently landed."
        ),
        "holdout": {
            "dates": [str(d) for d in holdout],
            "why_blind": (
                "pre grid was fetched while -s4 was mid-flight, holding only the "
                "partitions it had finished by then; the holdout dates carried no "
                "data at projection time and landed afterwards under the same Job"
            ),
            "pre_fetched_at": pre_fetched,
            "post_fetched_at": post_fetched,
            "pre_span_days": len(pre_dates),
            "post_span_days": len(post_dates),
            "excluded_in_flight_dates": [str(d) for d in excluded_in_flight],
            "why_excluded": (
                "dates outside -s4's window that appeared between the two "
                "fetches belong to -b1, which was still RUNNING and commits as "
                "it goes. They are partial, so scoring against them would charge "
                "the rule for stores not yet read."
            ),
        },
        "method": {
            "donor_logic": (
                "imported from horizon-critical-path-probe.py (apply_slices, "
                "dense_dates) so the code scored is the code under test"
            ),
            "optimistic": "store traded on the nearest dense landed day",
            "strict": "store traded on every one of the nearest 7 dense landed days",
            "dense": "store count >= half the landed median",
            "regime_caveat": (
                "holdout dates sit 1-5 days from their donors and extend an "
                "island forwards; -b1..-b4 sit up to two months from theirs. This "
                "bounds the rule's error in the favourable regime -- it can "
                "falsify the rule, and a clean result is a lower bound on error "
                "rather than a guarantee at two months' distance. The distance "
                "question is measured directly, not projected, by "
                "backwards_window_store_density.json and "
                "backwards_window_store_mappability.json."
            ),
        },
        "per_date": per_date,
        "totals": totals,
        "continuity": continuity,
        "attestation": {
            "holdout_dates_not_fully_attested": attestation_gap,
            "note": (
                "the counterfactual marks projected dates attested as well as "
                "present; a landed holdout date that is not fully attested would "
                "make the rule optimistic in a way the store-set score cannot see"
            ),
        },
        "end_to_end": end_to_end,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(json.dumps({"holdout": receipt["holdout"]["dates"],
                      "continuity": continuity,
                      "totals": totals,
                      "attestation_gap": attestation_gap,
                      "end_to_end": end_to_end}, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
