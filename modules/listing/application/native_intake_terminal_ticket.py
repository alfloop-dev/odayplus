"""Pure structural binding for a historical native Intake delivery ticket.

This is not current signature authentication and grants no authority. The
caller must supply the original durable admitted token hash and retain the
complete surrounding terminal proof, including the durable job identity,
admission, transitions, audit and outbox evidence. A caller-supplied hash
alone cannot establish that a token was ever admitted or signed.
"""

from __future__ import annotations

from hashlib import sha256
from hmac import compare_digest
from uuid import UUID

_PAYLOAD_KEYS = frozenset({"job_id", "tenant_id", "attempt_token"})
_JOB_TYPES = (
    "native-intake-start",
    "native-intake-identity",
    "native-intake-source-policy",
)


class NativeIntakeTerminalTicketConflict(ValueError):
    """The supplied historical frame does not match its explicit binding."""


def _uuid(value: object) -> None:
    if type(value) is not str or len(value) != 36:
        raise ValueError
    parsed = UUID(value)
    if not parsed.int or str(parsed) != value:
        raise ValueError


def _lower_hex64(value: object) -> bool:
    return (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def verify_native_intake_terminal_ticket(
    delivery, *, tenant: str, generation: int, job_type: str,
    attempt_token_sha256: str,
) -> None:
    """Check the actual delivery frame against explicit durable expectations.

    Return None on structural agreement; raise NativeIntakeTerminalTicketConflict
    otherwise. Owners translate this failure to their existing terminal conflict
    and still require every surrounding terminal predicate. Pass the real
    delivery and the original durable admitted hash, never a reconstructed frame
    or a hash recomputed from the delivery being checked.

    The opaque historical token is checked by its exact-byte SHA256, without
    consulting current signing keys, authenticators, policy, clocks, storage or
    caller capabilities. Key retirement/rotation therefore does not affect this
    check. It neither authenticates a current signature nor proves terminal
    success, tenant access or a new execution grant. Inputs are not modified.
    """
    try:
        if type(generation) is not int or generation < 1:
            raise ValueError
        if type(job_type) is not str or job_type not in _JOB_TYPES:
            raise ValueError
        _uuid(tenant)
        if not _lower_hex64(attempt_token_sha256):
            raise ValueError

        delivery_id, delivery_type, payload = delivery.job_id, delivery.job_type, delivery.payload
        if (type(delivery_type) is not str or delivery_type != job_type
                or type(payload) is not dict or len(payload) != 3
                or any(type(key) is not str for key in payload)
                or set(payload) != _PAYLOAD_KEYS):
            raise ValueError
        _uuid(delivery_id)
        _uuid(payload["job_id"])
        _uuid(payload["tenant_id"])
        if payload["job_id"] != delivery_id or payload["tenant_id"] != tenant:
            raise ValueError

        token = payload["attempt_token"]
        # Derive the canonical prefix from the explicit exact-int expectation.
        # Bound delivery work before slicing/hashing; never convert untrusted
        # decimal input to int. This introduces no new generation-size limit.
        prefix = str(generation) + "."
        if (type(token) is not str or len(token) != len(prefix) + 64
                or not token.startswith(prefix) or not _lower_hex64(token[len(prefix):])):
            raise ValueError
        if not compare_digest(sha256(token.encode("ascii")).hexdigest(), attempt_token_sha256):
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError):
        raise NativeIntakeTerminalTicketConflict("NATIVE_INTAKE_TERMINAL_TICKET_CONFLICT") from None
