"""Typed error observations cannot turn unknown authority/reaping into failure."""
import copy

import pytest

from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_parser_output import NativeParserOutputRejected
from modules.listing.application.native_intake_parser_failure_policy import (
    parser_failure_kind,
    parser_failure_observation,
    valid_parser_failure_observation,
)
from modules.listing.application.native_intake_parser_policy import NativeParserConflict
from shared.jobs.queue import JobFenceRejectedError


@pytest.mark.parametrize("error,expected", [
    (NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED"), "PERMANENT"),
    (NativeParserOutputRejected("Secret input must never be retained"), "PERMANENT"),
    (NativeIORejected("NATIVE_IO_CONNECTION_FAILED"), "TRANSIENT"),
    (NativeIORejected("NATIVE_IO_CHILD_FAILED"), "TRANSIENT"),
    (NativeIORejected("NATIVE_IO_DEADLINE_EXPIRED"), "TRANSIENT"),
    (NativeIORejected("NATIVE_IO_PARSER_DEADLINE_EXPIRED"), "TRANSIENT"),
    (NativeIORejected("NATIVE_GCS_PROVIDER_REJECTED", status=429), "TRANSIENT"),
    (NativeIORejected("NATIVE_GCS_PROVIDER_REJECTED", status=503), "TRANSIENT"),
    (NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED", status=500), "TRANSIENT"),
    (NativeIORejected("NATIVE_GCS_PROVIDER_REJECTED", status=403), "CAPABILITY_UNAVAILABLE"),
    (NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED", status=401), "CAPABILITY_UNAVAILABLE"),
    (NativeIORejected("NATIVE_GCS_PARSER_CREDENTIAL_UNAVAILABLE"), "CAPABILITY_UNAVAILABLE"),
    (NativeIORejected("NATIVE_GCS_PROVIDER_REJECTED", status=404), "UNRESOLVED"),
    (NativeIORejected("NATIVE_IO_UNKNOWN_ERROR"), "UNRESOLVED"),
    (NativeIORejected("Bearer private-credential"), "UNRESOLVED"),
    (NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED"), "UNRESOLVED"),
    (NativeParserConflict("NATIVE_PARSER_ROOT_DEADLINE_EXPIRED"), "UNRESOLVED"),
    (JobFenceRejectedError("NATIVE_PARSER_PHYSICAL_NOT_OWNED"), "UNRESOLVED"),
    (RuntimeError("Secret input must never be retained"), "UNRESOLVED"),
])
def test_actual_typed_error_controls_classification_without_retaining_exception_text(error, expected):
    value = parser_failure_observation(error)
    assert parser_failure_kind(value, type(error).__name__) == expected
    assert "Secret input" not in str(value) and "private-credential" not in str(value)


@pytest.mark.parametrize("damage", ["boolean_version", "boolean_status", "extra_field", "family", "code", "status", "version"])
def test_forged_error_shapes_never_gain_a_permanent_failure_classification(damage):
    value = copy.deepcopy(parser_failure_observation(NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED")))
    if damage == "boolean_version":
        value["version"] = True
    elif damage == "boolean_status":
        value["status"] = False
    elif damage == "extra_field":
        value["retryable"] = False
    elif damage == "family":
        value["family"] = "NativeIOUnreaped"
    elif damage == "code":
        value["code"] = "NATIVE_IO_PROTOCOL_REJECTED\ncredential"
    elif damage == "status":
        value["status"] = 600
    else:
        value["version"] = 2
    assert not valid_parser_failure_observation(value, "NativeIORejected")
    assert parser_failure_kind(value, "NativeIORejected") == "UNRESOLVED"


def test_exception_name_spoof_and_subclass_do_not_become_fixed_runtime_evidence():
    spoof = type("NativeParserOutputRejected", (ValueError,), {})
    class Derived(NativeIORejected):
        pass
    assert parser_failure_observation(spoof("NATIVE_PARSER_OUTPUT_REJECTED")) is None
    assert parser_failure_observation(Derived("NATIVE_IO_PROTOCOL_REJECTED")) is None
