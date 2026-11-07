"""Source-only unit proofs for historical ticket structure, not terminal history.

Minimal typed frames and explicitly synthetic HMACs exercise only the pure
verifier. No fixture claims admission, terminal success or durable authority;
actual owner/SQL integration and execution belong to Root.
"""

import hashlib
import hmac
import os
from copy import deepcopy
from dataclasses import dataclass, replace
from uuid import UUID

import pytest

from modules.listing.application import native_intake_terminal_ticket as ticket

TENANT = "12345678-1234-4234-8234-123456789abc"
JOB_ID = "abcdefab-1234-4234-8234-abcdefabcdef"
OTHER_ID = "abcdefab-1234-4234-8234-abcdefabcdee"
JOB_TYPES = (
    "native-intake-start",
    "native-intake-identity",
    "native-intake-source-policy",
)
CONFLICT = "^NATIVE_INTAKE_TERMINAL_TICKET_CONFLICT$"


@dataclass(frozen=True)
class DeliveryFrame:
    job_id: object
    job_type: object
    payload: object


class IntSubclass(int):
    pass


class StringSubclass(str):
    pass


def _original(job_type=JOB_TYPES[0], generation=1):
    # Same wire construction as sign_native_intake_attempt, with a unit-only
    # key. This fixture does not invoke an authenticator or forge SQL history.
    content = f"{job_type}:{JOB_ID}:{TENANT}:{generation}"
    signature = hmac.new(b"synthetic unit-only retired key!!", content.encode(), hashlib.sha256).hexdigest()
    token = f"{generation}." + signature
    frame = DeliveryFrame(JOB_ID, job_type, {
        "job_id": JOB_ID, "tenant_id": TENANT, "attempt_token": token,
    })
    return frame, hashlib.sha256(token.encode()).hexdigest()


def _verify(frame, digest, *, tenant=TENANT, generation=1, job_type=JOB_TYPES[0]):
    return ticket.verify_native_intake_terminal_ticket(
        frame, tenant=tenant, generation=generation, job_type=job_type,
        attempt_token_sha256=digest,
    )


@pytest.mark.parametrize("job_type", JOB_TYPES)
@pytest.mark.parametrize("generation", [1, 2, 10, 2**63, 10**100])
def test_original_frame_agrees_without_changing_inputs(job_type, generation):
    frame, digest = _original(job_type, generation)
    original, payload = deepcopy(frame), frame.payload
    assert _verify(frame, digest, job_type=job_type, generation=generation) is None
    assert frame == original and frame.payload is payload


@pytest.mark.parametrize("job_type", JOB_TYPES)
@pytest.mark.parametrize("damage", ["tenant", "payload_job", "delivery_job", "delivery_type"])
def test_frame_substitution_with_original_token_and_hash_is_rejected(job_type, damage):
    frame, digest = _original(job_type)
    if damage == "tenant":
        frame = replace(frame, payload={**frame.payload, "tenant_id": OTHER_ID})
    elif damage == "payload_job":
        frame = replace(frame, payload={**frame.payload, "job_id": OTHER_ID})
    elif damage == "delivery_job":
        frame = replace(frame, job_id=OTHER_ID)
    else:
        frame = replace(frame, job_type=JOB_TYPES[(JOB_TYPES.index(job_type) + 1) % 3])
    original, payload = deepcopy(frame), frame.payload
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(frame, digest, job_type=job_type)
    assert frame == original and frame.payload is payload


@pytest.mark.parametrize("expected", [
    {"tenant": OTHER_ID}, {"generation": 2}, {"job_type": JOB_TYPES[1]},
])
def test_explicit_expectation_cannot_be_replaced_by_values_from_delivery(expected):
    frame, digest = _original()
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(frame, digest, **expected)


@pytest.mark.parametrize("key", ["job_id", "tenant_id", "attempt_token"])
def test_each_payload_key_is_required(key):
    frame, digest = _original()
    payload = {name: value for name, value in frame.payload.items() if name != key}
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(replace(frame, payload=payload), digest)


@pytest.mark.parametrize("key", ["extra", "delivery_generation", "attempt_token_sha256", 1, b"extra"])
def test_unknown_payload_keys_are_rejected(key):
    frame, digest = _original()
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(replace(frame, payload={**frame.payload, key: "untrusted"}), digest)


def test_unknown_key_cannot_replace_a_required_key_at_same_payload_size():
    frame, digest = _original()
    payload = {"job_id": JOB_ID, "tenant_id": TENANT, "token": frame.payload["attempt_token"]}
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(replace(frame, payload=payload), digest)


@pytest.mark.parametrize("payload", [None, True, [], (), "{}", b"{}", 3])
def test_payload_must_be_an_object_without_decoding_or_coercion(payload):
    frame, digest = _original()
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(replace(frame, payload=payload), digest)


@pytest.mark.parametrize("frame", [None, object(), {}, {"job_id": JOB_ID}])
def test_absent_delivery_attributes_raise_the_typed_conflict(frame):
    _, digest = _original()
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(frame, digest)


@pytest.mark.parametrize("generation", [True, False, 0, -1, 1.0, "1", None, [], {}, IntSubclass(1)])
def test_expected_generation_is_a_positive_exact_int(generation):
    frame, digest = _original()
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(frame, digest, generation=generation)


@pytest.mark.parametrize("prefix", ["", "0", "-1", "+1", "01", " 1", "1 ", "1.0", "1e0", "1_0", "١", "１"])
def test_noncanonical_generation_is_rejected_even_with_matching_hash(prefix):
    frame, _ = _original()
    token = prefix + "." + "a" * 64
    digest = hashlib.sha256(token.encode()).hexdigest()
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(replace(frame, payload={**frame.payload, "attempt_token": token}), digest)


@pytest.mark.parametrize("token", [
    "", "1", "1.", "1." + "a" * 63, "1." + "a" * 65,
    "1." + "A" * 64, "1." + "g" * 64, "1." + "é" * 64,
    "1." + "a" * 63 + "\n", "1." + "a" * 63 + "\x00",
    "1." + "a" * 64 + "\n", "1." + "a" * 64 + ".extra",
])
def test_malformed_token_is_rejected_even_with_matching_hash(token):
    frame, _ = _original()
    digest = hashlib.sha256(token.encode()).hexdigest()
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(replace(frame, payload={**frame.payload, "attempt_token": token}), digest)


@pytest.mark.parametrize("token", [None, True, 1, b"1." + b"a" * 64, [], {}, StringSubclass("1." + "a" * 64)])
def test_token_must_be_an_exact_string(token):
    frame, digest = _original()
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(replace(frame, payload={**frame.payload, "attempt_token": token}), digest)


@pytest.mark.parametrize("job_type", JOB_TYPES)
def test_token_substitution_keeps_original_durable_hash(job_type):
    frame, digest = _original(job_type)
    token = frame.payload["attempt_token"]
    replacement = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(replace(frame, payload={**frame.payload, "attempt_token": replacement}), digest, job_type=job_type)


@pytest.mark.parametrize("digest", [
    None, True, 1, b"a" * 64, [], {}, "", "a" * 63, "a" * 65,
    "A" * 64, "g" * 64, "é" * 64, "a" * 63 + "\n", "a" * 64 + "\n",
    StringSubclass("a" * 64),
])
def test_admitted_digest_must_be_canonical_lowercase_sha256(digest):
    frame, _ = _original()
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(frame, digest)


@pytest.mark.parametrize("field", ["job", "tenant"])
@pytest.mark.parametrize("value", [
    None, True, 1, [], {}, UUID(JOB_ID), JOB_ID.encode(), "",
    "00000000-0000-0000-0000-000000000000", JOB_ID.upper(), JOB_ID.replace("-", ""),
    "{" + JOB_ID + "}", "urn:uuid:" + JOB_ID, " " + JOB_ID, JOB_ID + "\n",
    JOB_ID[:-1] + "z", StringSubclass(JOB_ID),
])
def test_agreeing_uuid_fields_still_require_nonzero_canonical_strings(field, value):
    frame, digest = _original()
    expected = {}
    if field == "tenant":
        expected["tenant"] = value
        frame = replace(frame, payload={**frame.payload, "tenant_id": value})
    else:
        frame = replace(frame, job_id=value, payload={**frame.payload, "job_id": value})
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(frame, digest, **expected)


@pytest.mark.parametrize("value", [None, True, 1, [], {}, "", "native-intake-parser", "native-intake-start ",
                                      JOB_TYPES[0].encode(), StringSubclass(JOB_TYPES[0])])
@pytest.mark.parametrize("field", ["expected", "delivery", "both"])
def test_job_type_requires_an_explicit_supported_native_string(field, value):
    frame, digest = _original()
    if field in {"delivery", "both"}:
        frame = replace(frame, job_type=value)
    expected = {"job_type": value} if field in {"expected", "both"} else {}
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(frame, digest, **expected)


@pytest.mark.parametrize("matches", [True, False])
def test_hash_binding_uses_constant_time_digest_comparison(monkeypatch, matches):
    frame, digest = _original()
    supplied = digest if matches else ("0" if digest[0] != "0" else "1") + digest[1:]
    compare, calls = ticket.compare_digest, []

    def observed(actual, expected):
        calls.append((actual, expected))
        return compare(actual, expected)

    monkeypatch.setattr(ticket, "compare_digest", observed)
    if matches:
        assert _verify(frame, supplied) is None
    else:
        with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
            _verify(frame, supplied)
    assert calls == [(digest, supplied)]


@pytest.mark.parametrize("part", ["generation", "signature"])
def test_oversized_token_is_rejected_before_hashing(monkeypatch, part):
    frame, digest = _original()
    token = "9" * 100_000 + "." + "a" * 64 if part == "generation" else "1." + "a" * 100_000

    def forbidden(*args, **kwargs):
        raise AssertionError("Oversized delivery input must be rejected before hashing")

    monkeypatch.setattr(ticket, "sha256", forbidden)
    with pytest.raises(ticket.NativeIntakeTerminalTicketConflict, match=CONFLICT):
        _verify(replace(frame, payload={**frame.payload, "attempt_token": token}), digest)


@pytest.mark.parametrize("job_type", JOB_TYPES)
@pytest.mark.parametrize("key_state", ["removed", "rotated"])
def test_historical_frame_is_independent_of_retired_keys_and_current_environment(monkeypatch, job_type, key_state):
    frame, digest = _original(job_type)
    for owner in ("START", "IDENTITY", "SOURCE_POLICY"):
        key_env = f"ODP_NATIVE_INTAKE_{owner}_SIGNING_KEY"
        if key_state == "removed":
            monkeypatch.delenv(key_env, raising=False)
        else:
            monkeypatch.setenv(key_env, "different synthetic current key!!")
        monkeypatch.delenv(f"ODP_NATIVE_INTAKE_{owner}_POLICY_JSON", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Historical ticket structure must not consult environment or sign")

    # Scope these traps to the pure call so pytest's own environment reads
    # remain unaffected. The original token/hash were already constructed.
    with monkeypatch.context() as guard:
        guard.setattr(type(os.environ), "__getitem__", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(hmac, "new", forbidden)
        assert _verify(frame, digest, job_type=job_type) is None
