"""Reserve native exact URL identity commands for one configured tenant partition.

    python -m apps.worker.oday_worker.native_intake_identity --tenant-id UUID --limit 25

Each printed receipt is already durable. This does not enable a deployment
schedule, retrieve content, create Listings or complete the Intake pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID


def main() -> None:
    parser = argparse.ArgumentParser(description="Reserve native URL identity checks; not complete Intake processing")
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--match-cases", action="store_true", help="Confirm exact matching cases and admit the existing identity executor")
    parser.add_argument("--source-id", help="Explicit matching source partition")
    parser.add_argument("--matching-policy-version-id", help="Explicit current DATA_OWNER source-use authority")
    args = parser.parse_args()
    if not args.tenant_id.int or not 1 <= args.limit <= 100:
        parser.error("A nonzero tenant UUID and limit from 1 to 100 are required")
    if args.match_cases and (not args.source_id or not args.matching_policy_version_id):
        parser.error("--match-cases requires --source-id and --matching-policy-version-id")
    if not args.match_cases and (args.source_id or args.matching_policy_version_id):
        parser.error("Matching source/policy options require --match-cases")
    from apps.api.server import bootstrap_runtime

    bundle = None
    try:
        bundle = bootstrap_runtime()
        if args.match_cases:
            from apps.api.oday_api.security.dependencies import default_boundary

            owner = bundle.canonical_intake_identity(identity_service_boundary=default_boundary)
            for receipt in owner.confirm_exact_partition(str(args.tenant_id), source_id=args.source_id,
                    policy_version_id=args.matching_policy_version_id, replay_store=bundle.assisted_intake_store, limit=args.limit):
                print(json.dumps({"phase": "native_exact_identity_admitted", "admission_receipt": receipt}), flush=True)
        else:
            owner = bundle.canonical_intake_identity()
            for receipt in owner.admit_partition(str(args.tenant_id), limit=args.limit):
                print(json.dumps({"phase": "native_url_identity_only", "admission_receipt": receipt}), flush=True)
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
