"""Reserve native source-policy recheck commands for one configured tenant partition.

    python -m apps.worker.oday_worker.native_intake_source_policy_recheck --tenant-id UUID --limit 25

Each printed receipt is already durable. This does not enable a deployment
schedule, retrieve content, create Listings or complete the Intake pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID


def main() -> None:
    parser = argparse.ArgumentParser(description="Reserve native source-policy recheck; not complete Intake processing")
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--cursor", help="Resume the tenant-bound cursor from a prior scan receipt")
    parser.add_argument("--include-progress", action="store_true", help="Print the scan counts and continuation cursor")
    args = parser.parse_args()
    if not args.tenant_id.int or not 1 <= args.limit <= 100:
        parser.error("A nonzero tenant UUID and limit from 1 to 100 are required")
    from apps.api.server import bootstrap_runtime

    bundle = None
    try:
        bundle = bootstrap_runtime()
        owner = bundle.canonical_intake_source_policy_recheck()
        admission = {"limit": args.limit}
        if args.cursor is not None:
            admission["cursor"] = args.cursor
        for receipt in owner.admit_partition(str(args.tenant_id), **admission):
            print(json.dumps({"phase": "native_source_policy_recheck_only", "admission_receipt": receipt}), flush=True)
        progress = owner.last_partition_progress
        if args.include_progress or progress["has_more"] or progress["conflicts"]:
            print(json.dumps({"phase": "native_source_policy_recheck_scan", "scan_receipt": progress}), flush=True)
    except Exception as error:
        # A driver error may contain connection details. Emit only its class;
        # successfully printed earlier receipts remain durable, not rolled back.
        print(json.dumps({"error_class": type(error).__name__, "admission_complete": False}), file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if bundle is not None and getattr(bundle, "engine", None) is not None:
            bundle.engine.close()


if __name__ == "__main__":
    main()
