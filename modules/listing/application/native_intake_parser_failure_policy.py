"""Bounded typed IO observations; classification alone grants no settlement.

Only an actual synchronous operation may record these facts. The owner must
prove its closed journal, original command, current authority and both fences.
Unknown errors, SQL/root expiry and unresolved reaping have no terminal grant.
"""
from __future__ import annotations

import re

from modules.external_data.application.native_assisted_snapshot_transport import (
    NativeAssistedSnapshotProofRejected,
)
from modules.external_data.application.native_intake_process import NativeIORejected
from modules.external_data.application.native_parser_output import NativeParserOutputRejected
from modules.external_data.application.native_snapshot_transport import NativeSnapshotProofRejected

_FIELDS = {"version", "family", "code", "status"}
_FIXED = {
    "NativeParserOutputRejected": "NATIVE_PARSER_OUTPUT_REJECTED",
    "NativeAssistedSnapshotProofRejected": "NATIVE_ASSISTED_SNAPSHOT_PROOF_REJECTED",
    "NativeSnapshotProofRejected": "NATIVE_RETRIEVAL_SNAPSHOT_PROOF_REJECTED",
}
_PERMANENT = frozenset({"NATIVE_IO_PROTOCOL_REJECTED", "NATIVE_IO_LENGTH_REJECTED",
    "NATIVE_IO_PARSER_BINDING_REJECTED", "NATIVE_IO_PARSER_BUCKET_PROOF_REJECTED",
    "NATIVE_IO_PARSER_SNAPSHOT_METADATA_REJECTED", "NATIVE_IO_PARSER_OUTPUT_PROOF_REQUIRED",
    "NATIVE_IO_PARSER_OUTPUT_PROOF_REJECTED"})
_TRANSIENT = frozenset({"NATIVE_IO_DEADLINE_EXPIRED", "NATIVE_IO_PARSER_DEADLINE_EXPIRED",
    "NATIVE_IO_CONNECTION_FAILED", "NATIVE_IO_CHILD_FAILED"})


def parser_failure_observation(error):
    """No exception text, payload, credential or arbitrary attributes persist."""
    if type(error) is NativeIORejected:
        value = {"version": 1, "family": "NativeIORejected", "code": error.code, "status": error.status}
    elif type(error) in {NativeParserOutputRejected, NativeAssistedSnapshotProofRejected, NativeSnapshotProofRejected}:
        family = type(error).__name__
        value = {"version": 1, "family": family, "code": _FIXED[family], "status": None}
    else:
        return None
    return value if valid_parser_failure_observation(value, type(error).__name__) else None


def valid_parser_failure_observation(value, error_class):
    if (not isinstance(value, dict) or set(value) != _FIELDS
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["family"], str) or value["family"] != error_class
            or not isinstance(value["code"], str)
            or re.fullmatch(r"NATIVE_[A-Z0-9_]{1,89}", value["code"]) is None
            or (value["status"] is not None and (type(value["status"]) is not int or not 100 <= value["status"] <= 599))):
        return False
    if value["family"] == "NativeIORejected":
        return value["code"].startswith(("NATIVE_IO_", "NATIVE_HTTP_", "NATIVE_GCS_"))
    return value["family"] in _FIXED and value["code"] == _FIXED[value["family"]] and value["status"] is None


def parser_failure_kind(value, error_class):
    """A safe observed classification, never inferred from ERROR or a name."""
    if not valid_parser_failure_observation(value, error_class):
        return "UNRESOLVED"
    if value["family"] in _FIXED:
        return "PERMANENT"
    status, code = value["status"], value["code"]
    if status in {401, 403} or code == "NATIVE_GCS_PARSER_CREDENTIAL_UNAVAILABLE":
        return "CAPABILITY_UNAVAILABLE"
    if status in {408, 429} or (status is not None and status >= 500) or code in _TRANSIENT:
        return "TRANSIENT"
    # A status-bearing provider error is not a local parser-proof defect.
    if status is None and code in _PERMANENT:
        return "PERMANENT"
    return "UNRESOLVED"
