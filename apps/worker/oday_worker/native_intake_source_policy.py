"""Reserve native source-policy evaluation commands for one configured tenant partition.

    python -m apps.worker.oday_worker.native_intake_source_policy --tenant-id UUID --limit 25

Each printed receipt is already durable. This does not enable a deployment
schedule, retrieve content, create Listings or complete the Intake pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID


def main() -> None:
    parser = argparse.ArgumentParser(description="Reserve native source-policy evaluation; not complete Intake processing")
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    if not args.tenant_id.int or not 1 <= args.limit <= 100:
        parser.error("A nonzero tenant UUID and limit from 1 to 100 are required")
    from apps.api.server import bootstrap_runtime

    bundle = None
    try:
        bundle = bootstrap_runtime()
        owner = bundle.canonical_intake_source_policy()
        for receipt in owner.admit_partition(str(args.tenant_id), limit=args.limit):
            print(json.dumps({"phase": "native_source_policy_only", "admission_receipt": receipt}), flush=True)
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
