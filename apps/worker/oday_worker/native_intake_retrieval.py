"""Reserve native retrieval jobs for one explicitly configured tenant.

    python -m apps.worker.oday_worker.native_intake_retrieval --tenant-id UUID --limit 25

Admission is durable; the worker separately proves each HTTP/storage intent.
This command enables no source, account, storage, deployment or schedule.
"""

import argparse
import json
import sys
from uuid import UUID


def main():
    parser = argparse.ArgumentParser(description="Reserve native retrieval jobs for a configured tenant partition")
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    if not args.tenant_id.int or not 1 <= args.limit <= 100:
        parser.error("A nonzero tenant UUID and limit from 1 to 100 are required")
    from apps.api.server import bootstrap_runtime

    bundle = None
    try:
        bundle = bootstrap_runtime()
        owner = bundle.canonical_intake_retrieval()
        for receipt in owner.admit_partition(str(args.tenant_id), limit=args.limit):
            print(json.dumps({"phase": "native_retrieval_admission_only", "admission_receipt": receipt}), flush=True)
    except Exception as error:
        print(json.dumps({"error_class": type(error).__name__, "admission_complete": False}), file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if bundle is not None and getattr(bundle, "engine", None) is not None:
            bundle.engine.close()


if __name__ == "__main__":
    main()
