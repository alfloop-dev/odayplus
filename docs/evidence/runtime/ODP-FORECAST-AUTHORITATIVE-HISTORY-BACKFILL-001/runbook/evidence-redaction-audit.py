#!/usr/bin/env python3
"""Audit every committed file in this evidence directory for leaked identifiers.

Acceptance criterion 4 asks for **redacted** before/after evidence, and every
receipt here carries a `redaction` line promising counts only -- "no store,
member, order id or amount". Those are promises made by the probe that wrote
each file. Nothing had ever checked them against what landed on disk, and an
untested redaction claim is the same shape of defect as the horizon numbers this
task spent a week re-measuring: right-looking, and unguaranteed.

Direction matters here, and the obvious direction is the wrong one. Pulling
every identifier out of the database and searching the evidence for each would
mean 563 565 transaction ids searched across every file, and it would still miss
any identifier class not enumerated up front. So the audit runs the other way:
extract every identifier-SHAPED token from the committed files -- a small,
bounded set -- and ask the database what each one actually is. That is cheap,
and it classifies rather than guesses, which matters because the values at issue
are indistinguishable by shape.

They really are indistinguishable. `core.transactions.store_id` is a UUID, and
so is `data_plane.ingestion_runs.run_id`, which the redaction policy explicitly
PUBLISHES. No regex can separate a leak from a legitimately reported run id, so
resemblance is not evidence; membership in a named table is.

Classified as a LEAK:      store_id (core.stores, core.transactions)
                           tenant_id (core.tenants)
                           machine_id (core.machines)
                           transaction_id (core.transactions) -- the order id
Classified as ALLOWED:     run_id (data_plane.ingestion_runs)
Reported as UNKNOWN:       identifier-shaped tokens matching nothing above --
                           git shas, content hashes, snapshot ids, uuids in
                           prose. Counted, never assumed innocent.

The `run_id` class doubles as the audit's control. It is allowed, it is known to
be present, and it must come back NON-ZERO: a clean report from an audit that
cannot find an identifier at all would prove nothing.

The audit must not itself leak. A hit is reported as the file, the class, and a
salted 12-character fingerprint -- never the value. Fingerprints are stable
within a report, so a reviewer can see whether two files leaked the same id,
and carry nothing back to the store.

Read-only against the database and the evidence directory.

Usage:
    source /tmp/odp-forecast-dsn.env && python3 evidence-redaction-audit.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
EVIDENCE = os.path.dirname(HERE)

OUT = os.environ.get(
    "PROBE_OUT",
    "/tmp/odp-forecast-evidence-stage/evidence_redaction_audit.json",
)

# Salted so a published receipt is not a rainbow table of the tenant's store
# ids; fixed rather than random so two runs produce comparable reports.
FINGERPRINT_SALT = "odp-redaction-audit-v1|"

UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIXES = (".pyc", ".png", ".gz", ".jpg")

# (class, leaks?, table, column)
CLASSES = [
    ("store_id", True, "core.stores", "store_id"),
    ("store_id_txn", True, "core.transactions", "store_id"),
    ("tenant_id", True, "core.tenants", "tenant_id"),
    ("machine_id", True, "core.machines", "machine_id"),
    ("transaction_id", True, "core.transactions", "transaction_id"),
    ("run_id", False, "data_plane.ingestion_runs", "run_id"),
]


def _fingerprint(value: str) -> str:
    return hashlib.sha256((FINGERPRINT_SALT + value.lower()).encode()).hexdigest()[:12]


def _scan_files() -> tuple[list[str], dict[str, set[str]]]:
    """Return the scanned file list and token -> set(files) it appears in."""
    scanned: list[str] = []
    tokens: dict[str, set[str]] = defaultdict(set)
    for root, dirs, files in os.walk(EVIDENCE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if name.endswith(SKIP_SUFFIXES):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, EVIDENCE)
            scanned.append(rel)
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    blob = fh.read()
            except OSError:
                continue
            for match in UUID_RE.findall(blob):
                tokens[match.lower()].add(rel)
    return scanned, tokens


def _classify(dsn: str, tokens: list[str]) -> dict[str, set[str]]:
    """Ask the database which named identifier each token actually is."""
    found: dict[str, set[str]] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for name, _leaks, table, column in CLASSES:
            cur.execute(
                f"SELECT DISTINCT {column}::text FROM {table} "  # noqa: S608 - fixed literals above
                f"WHERE {column}::text = ANY(%s)",
                (tokens,),
            )
            found[name] = {r[0].lower() for r in cur.fetchall()}
    return found


def main() -> int:
    dsn = os.environ.get("ODP_LEGACY_DATABASE_URL")
    if not dsn:
        print("ODP_LEGACY_DATABASE_URL is not set", file=sys.stderr)
        return 2

    started = datetime.now(UTC)
    scanned, tokens = _scan_files()
    token_list = sorted(tokens)
    found = _classify(dsn, token_list) if token_list else {n: set() for n, *_ in CLASSES}

    leak_classes = [name for name, leaks, *_ in CLASSES if leaks]
    allowed_classes = [name for name, leaks, *_ in CLASSES if not leaks]

    findings: list[dict] = []
    for name in leak_classes:
        for value in sorted(found[name]):
            findings.append(
                {
                    "class": name,
                    "fingerprint": _fingerprint(value),
                    "files": sorted(tokens[value]),
                }
            )

    classified = set().union(*found.values()) if found else set()
    unknown = [t for t in token_list if t not in classified]
    allowed_found = sum(len(found[n]) for n in allowed_classes)

    receipt = {
        "artifact": "evidence_redaction_audit",
        "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
        "captured_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "what_this_is": (
            "Every identifier-shaped token in this evidence directory, classified "
            "against the database rather than by pattern. Criterion 4 asks for "
            "redacted evidence and every receipt promises counts only; this is the "
            "test of those promises instead of a restatement of them."
        ),
        "method": (
            "Tokens are extracted from the committed files (small, bounded set) and "
            "classified by membership in named tables. The reverse -- searching the "
            "evidence for 563k transaction ids -- is both slower and blind to any "
            "class not enumerated first. Store ids and the run ids the policy "
            "publishes are both UUIDs, so shape cannot separate them and membership "
            "is the only sound test."
        ),
        "scope": {
            "files_scanned": len(scanned),
            "distinct_identifier_shaped_tokens": len(token_list),
            "leak_classes": leak_classes,
            "allowed_classes": allowed_classes,
        },
        "control": {
            "what_it_proves": (
                "run ids are allowed and are known to appear in this evidence. The "
                "control must be non-zero: a clean report from an audit structurally "
                "unable to find an identifier would prove nothing."
            ),
            "allowed_identifiers_found": allowed_found,
            "control_is_meaningful": allowed_found > 0,
        },
        "findings": findings,
        "unclassified": {
            "count": len(unknown),
            "fingerprints": sorted(_fingerprint(t) for t in unknown)[:20],
            "meaning": (
                "identifier-shaped tokens matching none of the named tables -- "
                "content hashes, snapshot ids, uuids appearing in prose. Counted "
                "rather than assumed innocent."
            ),
        },
        "verdict": {
            "leaked_identifiers": len(findings),
            "redaction_holds": len(findings) == 0 and allowed_found > 0,
            "caveat": (
                "Covers UUID-shaped identifiers. core.transactions.member_id is a "
                "varchar and is unpopulated in this data, and monetary amounts are "
                "not an identifier set -- neither is testable this way, and neither "
                "is claimed."
            ),
        },
        "elapsed_seconds": round((datetime.now(UTC) - started).total_seconds(), 1),
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=False, default=str)
    print(json.dumps(receipt, indent=2, sort_keys=False, default=str))
    print(f"\nwrote {OUT}")
    return 0 if receipt["verdict"]["redaction_holds"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
