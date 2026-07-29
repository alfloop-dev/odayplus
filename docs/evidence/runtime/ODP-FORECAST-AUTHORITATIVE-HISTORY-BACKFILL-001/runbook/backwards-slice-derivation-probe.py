"""Record what was applied for backwards slices -b5..-b9, and PROVE the derivation.

Why this exists. Slices `-b1`..`-b4` have a committed applied-manifest receipt
(`orders_history_backfill_jobs.applied.json`); `-b5`..`-b9`, created in the
criterion-5 session, did not. The only record of what was actually applied to
the cluster was a `kubectl get -o json` dump in `/tmp`, which is neither
evidence nor survives a supervisor worktree reset, and the cluster itself is
currently unreachable (the gcloud session needs a human `gcloud auth login`), so
that dump is at present the ONLY copy in existence.

Why it diffs instead of asserting. Every backwards slice is claimed to be a
verbatim derivation of the one before it -- "same release SHA, same
digest-pinned image, same serviceAccount / securityContext / secrets / volumes;
only the name, the annotations and the two window env vars differ". That
sentence has been copied forward from receipt to receipt since `-s2` without
anything ever recomputing it, which is precisely the shape of claim this task
has twice found to be half-true (the redaction promise, and the README's
"no synthetic row anywhere" opening that became Defect F). So this probe
flattens both manifests to leaf paths and classifies EVERY difference into one
of four expected families -- name, annotation, window env var, deadline --
and reports anything else under `unexpected`. A non-empty `unexpected` means the
slice is not a verbatim derivation and the invariant sentence must be rewritten
rather than repeated.

The deadline family is expected and is not drift: `-b1`..`-b4` were patched
14400 -> 28800 live (`slice_deadline_headroom.json`), but the committed `-b4`
manifest is the snapshot taken BEFORE that patch, so a 14400 -> 28800
difference is the patch showing through, not a divergence. This is the same
trap noted in the resume recipe -- last-applied still reads 14400, so the
deadline must be set EXPLICITLY on any newly derived slice, which is what these
five did.

Criterion-5 framing. The `oday.plus/eligible-dates-after-this-slice` annotations
carry the ladder 45/51/57/63/69. Those are arithmetic on an assumed attested
span end of 2026-07-04, not measurements, and the boundary they imply is tight
in a way worth stating out loud: `criterion5_span_requirement.json` puts the
governing gate at 58 eligible dates, so `-b7` lands on 57 and misses by exactly
one day, and `-b8` is the first slice that clears it. A one-day error anywhere
in the ladder therefore changes which slice is the gate. The ladder is repeated
here as a PREDICTION with its assumptions named, not as a result.

Read-only. Reads two JSON files. No database, no cluster, no writes outside the
evidence directory.

Usage:
    python3 backwards-slice-derivation-probe.py [--live /tmp/backwards-slices-b5-b9.json]
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.path.dirname(HERE)

COMMITTED = os.path.join(EVIDENCE, "orders_history_backfill_jobs.applied.json")
REQUIREMENT = os.path.join(EVIDENCE, "criterion5_span_requirement.json")
OUT = os.path.join(EVIDENCE, "orders_history_backfill_jobs_b5_b9.applied.json")

# The attested span's upper end. Not a free parameter: 2026-07-05 and 2026-07-06
# are Defect D's permanent unattested holes, so the contiguous eligible run that
# grows downwards can never extend past 2026-07-04.
SPAN_END = date(2026, 7, 4)
PRIOR_DAY_COUNT = 28


def flatten(value, path=""):
    """Leaf paths of a nested structure, so two manifests can be compared exactly."""
    out = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(flatten(item, f"{path}/{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(flatten(item, f"{path}[{index}]"))
    else:
        out[path] = value
    return out


def classify(path: str) -> str:
    if path == "/metadata/name":
        return "name"
    if path.startswith("/metadata/annotations/") or path.startswith(
        "/spec/template/metadata/annotations/"
    ):
        return "annotation"
    if "/env[" in path:
        return "env"
    if path == "/spec/activeDeadlineSeconds":
        return "deadline"
    return "UNEXPECTED"


def ladder_entry(window_start: date, required_eligible: int) -> dict:
    attested_days = (SPAN_END - window_start).days + 1
    eligible = max(0, attested_days - PRIOR_DAY_COUNT)
    return {
        "span_start": window_start.isoformat(),
        "attested_days": attested_days,
        "eligible_dates": eligible,
        "clears_criterion_5": eligible >= required_eligible,
        "shortfall": max(0, required_eligible - eligible),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", default="/tmp/backwards-slices-b5-b9.json")
    args = parser.parse_args()

    with open(COMMITTED, encoding="utf-8") as handle:
        committed = json.load(handle)
    with open(REQUIREMENT, encoding="utf-8") as handle:
        requirement = json.load(handle)
    with open(args.live, encoding="utf-8") as handle:
        live = json.load(handle)["items"]

    required_eligible = int(requirement["minimum_eligible_dates"])
    required_days = int(requirement["translation"]["required_contiguous_attested_days"])

    reference = next(
        job for job in committed["jobs"] if job["metadata"]["name"].endswith("-b4")
    )
    base = flatten(reference)

    derivation = {}
    jobs = []
    for job in sorted(live, key=lambda item: item["metadata"]["name"]):
        name = job["metadata"]["name"]
        current = flatten(job)
        buckets: dict[str, list] = {}
        for key in sorted(set(base) | set(current)):
            if base.get(key) == current.get(key):
                continue
            buckets.setdefault(classify(key), []).append(
                {
                    "path": key,
                    "reference_b4": base.get(key),
                    "this_slice": current.get(key),
                }
            )
        derivation[name] = {
            "difference_families": sorted(buckets),
            "window": job["metadata"]["annotations"].get("oday.plus/window"),
            "suspend": job["spec"].get("suspend"),
            "active_deadline_seconds": job["spec"].get("activeDeadlineSeconds"),
            "window_env_differences": buckets.get("env", []),
            "deadline_difference": buckets.get("deadline", []),
            "annotation_difference_count": len(buckets.get("annotation", [])),
            "unexpected": buckets.get("UNEXPECTED", []),
            "verbatim_derivation": not buckets.get("UNEXPECTED"),
        }
        jobs.append(job)

    ladder = {}
    for name, entry in derivation.items():
        window = entry["window"]
        if not window:
            continue
        start = date.fromisoformat(window.split("..")[0])
        ladder[name] = {
            "window": window,
            "annotated_eligible_dates": int(
                next(
                    job for job in jobs if job["metadata"]["name"] == name
                )["metadata"]["annotations"].get(
                    "oday.plus/eligible-dates-after-this-slice", "0"
                )
            ),
            **ladder_entry(start, required_eligible),
        }
        ladder[name]["annotation_matches_recomputation"] = (
            ladder[name]["annotated_eligible_dates"] == ladder[name]["eligible_dates"]
        )

    gate = next(
        (name for name, item in sorted(ladder.items()) if item["clears_criterion_5"]),
        None,
    )

    receipt = {
        "artifact": "orders_history_backfill_jobs_b5_b9.applied",
        "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
        "captured_at": datetime.now(UTC).isoformat(),
        "purpose": (
            "Durable record of the backwards slices created for criterion 5, with "
            "their derivation from -b4 measured rather than asserted. Committed "
            "because the only prior copy was a /tmp dump and the cluster that "
            "holds the originals is currently unreachable."
        ),
        "derived_from": "oday-data-platform-orders-history-93cb9f94-b4 (itself derived from -s3, from -s2)",
        "derivation_check": {
            "method": (
                "Both manifests flattened to leaf paths; every difference classified "
                "into name / annotation / window-env / deadline. Any other path is "
                "reported under 'unexpected' and falsifies the verbatim-derivation claim."
            ),
            "expected_difference_families": ["name", "annotation", "env", "deadline"],
            "deadline_note": (
                "The committed -b4 manifest is the pre-patch snapshot (14400). The "
                "28800 on these five is the explicit deadline they were created with, "
                "not drift -- last-applied on the earlier slices still reads 14400."
            ),
            "slices_checked": len(derivation),
            "verbatim_derivations": sum(
                1 for item in derivation.values() if item["verbatim_derivation"]
            ),
            "unexpected_differences_total": sum(
                len(item["unexpected"]) for item in derivation.values()
            ),
        },
        "criterion_5_ladder": {
            "requirement": {
                "minimum_eligible_dates": required_eligible,
                "required_contiguous_attested_days": required_days,
                "source": "criterion5_span_requirement.json (imported, not restated)",
            },
            "assumptions": [
                f"attested span end fixed at {SPAN_END.isoformat()} -- 2026-07-05 and "
                "2026-07-06 are Defect D's permanent holes, and eligibility requires "
                "source_run_complete on the date itself, so the downward-growing "
                "eligible run cannot cross them",
                "every date in a slice's window lands attested and contiguous, which "
                "is what backwards_landing_validation.json tests slice by slice",
                f"prior_day_count_28 = {PRIOR_DAY_COUNT}, so N contiguous attested "
                "days yield N-28 eligible dates",
            ],
            "why_contiguity_is_required": (
                "expand_forecast_horizon_rows builds each horizon window as "
                "ordered[i:i+horizon_days] and then rejects it unless the dates equal "
                "[origin+0 .. origin+horizon_days-1] exactly, so eligible dates split "
                "across islands do not combine. The upper island 2026-07-07..07-27 is "
                "therefore not available to criterion 5 no matter how long it grows."
            ),
            "by_slice": ladder,
            "first_slice_clearing_criterion_5": gate,
            "margin_note": (
                "-b7 lands on 57 eligible dates against a requirement of 58: it misses "
                "by exactly one day. The ladder is arithmetic, so a one-day error "
                "anywhere in it changes which slice is the gate."
            ),
        },
        "open_limits": [
            "The ladder is projected, not measured. Its per-slice numbers have never "
            "been checked against landed data; backwards_landing_validation.json scores "
            "the store-count projection but not the eligible-date ladder.",
            "-b5..-b8 reach 2026-04-05, below everything ever measured upstream: the "
            "density probe's window starts 2026-04-29 and the depth probe sampled "
            "nothing before 2026-05-14. april_window_store_density_probe.pod.yaml is "
            "committed UNRUN and needs the cluster, so it is blocked with everything "
            "else on the human gcloud re-auth.",
            "-b9 (2026-03-30..2026-04-05) is a RESERVE: created and suspended, "
            "deliberately NOT in driver v5's queue.",
        ],
        "jobs": jobs,
    }

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=1, sort_keys=False)
        handle.write("\n")

    print(json.dumps(receipt["derivation_check"], indent=1))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "window"}
                      for k, v in ladder.items()}, indent=1))
    print("gate:", gate)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
