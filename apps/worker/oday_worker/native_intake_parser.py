"""Admit native parser commands for one explicitly configured source partition.

    python -m apps.worker.oday_worker.native_intake_parser --tenant-id UUID --source-id TEXT

Each printed admission receipt is already durable. Admission does not activate
a source, execute the parser, create Listings or complete the Intake pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from uuid import UUID


def _tenant_id(value: str) -> UUID:
    try:
        if type(value) is not str:
            raise ValueError
        tenant = UUID(value)
        if not tenant.int:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("A nonzero tenant UUID is required") from None
    return tenant


def _source_id(value: str) -> str:
    if (type(value) is not str or not value or value != value.strip()
            or len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise argparse.ArgumentTypeError("A valid source ID is required")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise argparse.ArgumentTypeError("A valid source ID is required") from None
    return value


def _limit(value: str) -> int:
    try:
        if type(value) is not str:
            raise ValueError
        limit = int(value)
        if not 1 <= limit <= 100:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("An integer limit from 1 to 100 is required") from None
    return limit


def _match_completed(bundle, args):
    """Recover pending MATCHING inputs through the existing case command."""
    from apps.api.oday_api.security.dependencies import default_boundary
    from modules.opsboard.auth import Credentials

    boundary = default_boundary()
    credentials = Credentials(bearer_token=os.environ.get("ODP_NATIVE_INTAKE_MATCHER_BEARER_TOKEN"))
    yield from bundle.canonical_match_cases().match_partition(str(args.tenant_id), source_id=args.source_id,
        policy_version_id=args.matching_policy_version_id, boundary=boundary, credentials=credentials, limit=args.limit)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Admit native parser commands using an explicit startup source-to-release binding",
        allow_abbrev=False,
    )
    parser.add_argument("--tenant-id", type=_tenant_id, required=True)
    parser.add_argument("--source-id", type=_source_id, required=True)
    parser.add_argument("--limit", type=_limit, default=25)
    parser.add_argument("--cursor", help="Resume the partition-bound cursor from a prior scan receipt")
    parser.add_argument("--include-progress", action="store_true", help="Print scan counts and the continuation cursor")
    parser.add_argument("--match-completed", action="store_true", help="Propose cases for completed parser inputs instead of admitting parsers")
    parser.add_argument("--matching-policy-version-id", help="Explicit approved matching authority version; required with --match-completed")
    args = parser.parse_args(argv)
    if args.match_completed and (not args.matching_policy_version_id or args.cursor or args.include_progress):
        parser.error("--match-completed requires --matching-policy-version-id and does not accept parser cursor/progress options")
    if not args.match_completed and args.matching_policy_version_id:
        parser.error("--matching-policy-version-id requires --match-completed")

    bundle = None
    error_class = None
    try:
        if not args.match_completed:
            from modules.external_data.application.native_parser_deployments import (
                native_parser_deployments,
            )

            release_id = native_parser_deployments().release_for_source(args.source_id)

        from apps.api.server import bootstrap_runtime

        bundle = bootstrap_runtime()
        if args.match_completed:
            for receipt in _match_completed(bundle, args):
                print(json.dumps({"phase": "native_matching_proposal_only", "matching_case_receipt": receipt}), flush=True)
        else:
            owner = bundle.canonical_intake_parser()
            admission = {"source_id": args.source_id, "release_id": release_id, "limit": args.limit}
            if args.cursor is not None:
                admission["cursor"] = args.cursor
            for receipt in owner.admit_partition(str(args.tenant_id), **admission):
                print(json.dumps({"phase": "native_parser_admission_only", "admission_receipt": receipt}), flush=True)
            progress = owner.last_partition_progress
            if args.include_progress or progress["has_more"] or progress["conflicts"]:
                print(json.dumps({"phase": "native_parser_scan", "scan_receipt": progress}), flush=True)
    except Exception as error:
        # Retain only the class, never configuration, paths or driver messages.
        # Earlier yielded receipts are durable even if this scan later fails.
        error_class = type(error).__name__
    finally:
        try:
            if bundle is not None:
                engine = getattr(bundle, "engine", None)
                if engine is not None:
                    engine.close()
        except Exception as error:
            # Closing is also a driver boundary. Keep the first failure when
            # both admission and cleanup fail, without leaking either message.
            if error_class is None:
                error_class = type(error).__name__

    if error_class is not None:
        print(json.dumps({"error_class": error_class, "admission_complete": False}), file=sys.stderr, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
