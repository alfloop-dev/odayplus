#!/usr/bin/env python3
"""One-shot: replace the raw ids already committed in the fidelity receipt.

`evidence-redaction-audit.py` found 10 tenant ids and 10 store ids published
verbatim in `eligibility_model_fidelity.json`, under
`eligible_pairs.only_in_model_sample` / `only_in_view_sample`. The probe that
writes that file is fixed, so it cannot happen again -- but the receipt on disk
was produced before the fix, and re-running the probe means another full scan of
the PG16 target while a slice and two other probes are queued against it.

The sample is illustrative rather than load-bearing (§8's finding is
`only_in_model = 0`), and a fingerprint preserves everything it was read for:
whether two rows concern the same store. So the transformation is a pure,
deterministic rewrite of those two fields, with the same salt the audit and the
probe use, recorded inside the file rather than applied silently.

Applied once and kept committed so the transformation is reproducible and
auditable rather than an unexplained diff. Re-running it is a no-op.

WHAT THIS DOES NOT DO: the raw values remain in this branch's git history, in
the commits that first added the receipt. Removing them needs a history rewrite
on a pushed branch, which is not a worker's call to make unilaterally. It is
flagged for the reviewer and the human instead.

Usage:  python3 redact-fidelity-sample.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.path.dirname(HERE)
TARGET = os.path.join(EVIDENCE, "eligibility_model_fidelity.json")

FINGERPRINT_SALT = "odp-redaction-audit-v1|"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
FIELDS = ("only_in_model_sample", "only_in_view_sample")


def _fingerprint(value: str) -> str:
    return hashlib.sha256((FINGERPRINT_SALT + value.lower()).encode()).hexdigest()[:12]


def main() -> int:
    with open(TARGET, encoding="utf-8") as fh:
        receipt = json.load(fh)

    pairs = receipt.get("eligible_pairs", {})
    replaced = 0
    for field in FIELDS:
        rows = pairs.get(field) or []
        for row in rows:
            for index, value in enumerate(row):
                if isinstance(value, str) and UUID_RE.match(value):
                    row[index] = _fingerprint(value)
                    replaced += 1

    if replaced:
        pairs["sample_identifiers"] = (
            "salted sha256 fingerprints; raw ids are not published"
        )
        receipt["redaction"] = (
            "Counts, dates and fingerprints only. The two sample fields under "
            "eligible_pairs originally carried raw tenant and store uuids; they were "
            "replaced in place by runbook/redact-fidelity-sample.py after "
            "runbook/evidence-redaction-audit.py found them, and the probe that "
            "writes this file now fingerprints them at source. The raw values remain "
            "in this branch's git history."
        )
        with open(TARGET, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, indent=2, sort_keys=False, default=str)
            fh.write("\n")

    print(f"replaced {replaced} raw identifier(s) in {os.path.basename(TARGET)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
