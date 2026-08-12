#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical finalize-lane doctor.

The implementation lives in :mod:`finalize_lane_doctor`.  This module keeps
the original import path and status-file-only CLI available to existing
runbooks while avoiding a second copy of the diagnosis logic.
"""

from finalize_lane_doctor import (  # noqa: F401
    ALL_CATEGORIES,
    CAT_CI_FAILED,
    CAT_CI_UNRESOLVED,
    CAT_MISSING_PR,
    CAT_OWNER_UNAVAILABLE,
    CAT_STALE_BASE,
    FinalizeDiagnosisError,
    classify_stranded_task,
    diagnose,
    find_finalize_tasks,
    legacy_main,
    load_json_file,
)

main = legacy_main


if __name__ == "__main__":
    raise SystemExit(main())
