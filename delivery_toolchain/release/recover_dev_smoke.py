"""Foreground recovery custody/budget/receipt primitives; NOT an executor.

No CLI, credential-bearing subprocess, HTTP call, secret writer or admission
shortcut. The eventual trusted coordinator must compose official API, fresh
source/admission observations, genuine writer exclusion and a reviewed native
consumer BEFORE effects. These objects cannot grant that authority or resume a
persisted execution. Do not construct them with live secrets in a worker.
"""
from __future__ import annotations

import json
import math
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from shared.identity.dev_smoke_recovery import (
    AUTHORIZATION_ID,
    RecoveryRefused,
    validate_recovery_plan,
)

# A single absolute clock, not a refreshed timeout for every phase. These are
# conservative DESIGN ceilings, not measured production observer bounds.
PHASE_CEILINGS = (
    ("preflight", 600), ("reserve", 30), ("issue", 30), ("rotate", 120),
    ("reconcile", 120), ("intent", 20), ("put", 80), ("ack", 20),
    ("gate", 540), ("fresh_consumer", 540), ("cleanup", 180), ("quarantine", 120),
)
_CEILINGS = dict(PHASE_CEILINGS)
_NORMAL = tuple(_CEILINGS)[:-1]
_OUTCOMES = frozenset({"completed", "unknown", "refused", "interrupted"})


def _uuid(value: str) -> bool:
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, TypeError, AttributeError):
        return False


class ParentMemoryCustody:
    """One random pair and one capability in the creating process only.

    No serialization or child transfer API. PID fencing denies access after fork,
    but is NOT OS memory isolation/zeroization: approved foreground runtime must
    separately disable dumps/debugging, control swap and never fork with custody.
    Losing this object means losing the credential, not permission to rotate again.
    """

    __slots__ = ("_pid", "_password", "_capability", "_attached", "_closed")

    def __init__(self) -> None:
        self._pid = os.getpid()
        # 384 random bits; existing NFKC/length/weak-list policy is unchanged.
        self._password = secrets.token_urlsafe(48)
        self._capability = ""
        self._attached = self._closed = False

    def __repr__(self) -> str:
        return "ParentMemoryCustody(<redacted>)"

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_CUSTODY_SERIALIZATION_REFUSED")

    def _check(self) -> None:
        if self._closed or os.getpid() != self._pid:
            raise RecoveryRefused("RECOVERY_CUSTODY_LOST")

    @property
    def password(self) -> str:
        self._check()
        return self._password

    @property
    def capability(self) -> str:
        self._check()
        if not self._attached:
            raise RecoveryRefused("RECOVERY_CAPABILITY_UNAVAILABLE")
        return self._capability

    def attach_capability(self, token: str) -> None:
        import re
        self._check()
        if self._attached or type(token) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise RecoveryRefused("RECOVERY_CAPABILITY_INPUT_REFUSED")
        self._capability, self._attached = token, True

    def close(self) -> None:
        # Best-effort release of references, never a zeroization claim.
        self._password = self._capability = ""
        self._closed = True


class ExclusivePhaseReceipts:
    """Create a NEW non-secret execution directory, never reopen/retry/overwrite.

    The existing parent directory is a trusted foreground runtime input, not a
    request/plan path. Exclusive creation is only a LOCAL uncertainty barrier,
    NOT exclusion of external GitHub writers. No loader/continuation API exists.
    Each allowlisted record is fsynced before control returns. On any error the
    instance is permanently poisoned, including partial write/fsync uncertainty.
    """

    def __init__(self, parent: Path, *, execution_id: str, plan_digest: str) -> None:
        from shared.identity.dev_smoke_journal import _DIGEST
        self._poisoned, self._sequence = True, 0
        self._fd = None
        try:
            if not _uuid(execution_id) or type(plan_digest) is not str or not _DIGEST.fullmatch(plan_digest):
                raise ValueError
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.mkdir(execution_id, mode=0o700, dir_fd=parent_fd)
                self._fd = os.open(execution_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            self._execution, self._digest = execution_id, plan_digest
            self._poisoned = False
        except Exception:
            self.close()
            raise RecoveryRefused("RECOVERY_RECEIPT_STORAGE_UNAVAILABLE") from None

    def append(self, *, phase: str, stage: str, outcome: str, elapsed: float,
               remaining: float, event_id: str = "", exit_code: int | None = None,
               http_status: int | None = None) -> None:
        try:
            if (self._poisoned or self._fd is None or phase not in _CEILINGS
                    or stage not in {"start", "terminal"}
                    or outcome not in ({"pending"} if stage == "start" else _OUTCOMES)
                    or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                           for v in (elapsed, remaining))
                    or (event_id != "" and not _uuid(event_id))
                    or (exit_code is not None and (type(exit_code) is not int or not -(2**31) <= exit_code < 2**31))
                    or (http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599))
                    or (stage == "start" and (event_id or exit_code is not None or http_status is not None))):
                raise ValueError
            record = {
                "schema_version": 1, "authorization_id": AUTHORIZATION_ID,
                "execution_id": self._execution, "plan_digest": self._digest,
                "sequence": self._sequence, "phase": phase, "stage": stage, "outcome": outcome,
                "elapsed_seconds": elapsed, "remaining_seconds": remaining,
                "event_id": event_id, "exit_code": exit_code, "http_status": http_status,
                "execution_authorized": False, "credential_binding_verified": False,
                "secret_values_redacted": True,
            }
            name = f"{self._sequence:03d}-{phase}-{stage}.json"
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self._fd)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(self._fd)
            self._sequence += 1
        except Exception:
            self._poisoned = True
            raise RecoveryRefused("RECOVERY_RECEIPT_STORAGE_UNAVAILABLE") from None

    def close(self) -> None:
        self._poisoned = True
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


class ForegroundRecoveryBudget:
    """Fail-stop phase sequencing with durable starts and terminal receipts.

    Shape-valid plan is NOT execution approval. ``begin`` returns a maximum call
    allowance, not cancellation support: actual adapters MUST enforce an absolute
    deadline and preserve original child/HTTP completion receipts. No adapter or
    live executor is provided yet. Later GET truth cannot call begin to resume.
    Cleanup/quarantine remain finite, once-only recovery lanes after a stop.
    """

    def __init__(self, plan: dict[str, str], *, receipts: ExclusivePhaseReceipts,
                 total_seconds: float = 2400) -> None:
        now = datetime.now(UTC)
        if type(plan) is not dict:
            raise RecoveryRefused("RECOVERY_BUDGET_INVALID")
        digest = validate_recovery_plan(plan, release_sha=plan.get("release_sha"),
                                        manifest_digest=plan.get("manifest_digest"), now=now)
        if (type(receipts) is not ExclusivePhaseReceipts or receipts._poisoned
                or receipts._execution != plan["execution_id"] or receipts._digest != digest
                or type(total_seconds) not in (int, float) or not math.isfinite(total_seconds)
                or not 0 < total_seconds <= 2400):
            raise RecoveryRefused("RECOVERY_BUDGET_INVALID")
        self._start = self._last = time.monotonic()
        self._expiry = datetime.fromisoformat(plan["expires_at"])
        self._deadline = self._start + min(total_seconds, (self._expiry - now).total_seconds())
        self._receipts, self._next = receipts, 0
        self._active = None
        self._used: set[str] = set()
        self._stopped = False

    def _time(self) -> tuple[float, float]:
        now = time.monotonic()
        if not math.isfinite(now) or now < self._last:
            self._stopped = True
            raise RecoveryRefused("RECOVERY_CLOCK_UNVERIFIED")
        self._last = now
        return now - self._start, max(0.0, min(self._deadline - now,
                        (self._expiry - datetime.now(UTC)).total_seconds()))

    def begin(self, phase: str) -> float:
        try:
            elapsed, remaining = self._time()
            recovery_lane = phase in {"cleanup", "quarantine"}
            if (phase not in _CEILINGS or phase in self._used or self._active is not None
                    or (self._stopped and not recovery_lane)
                    or (not self._stopped and phase != "quarantine"
                        and (self._next >= len(_NORMAL) or phase != _NORMAL[self._next]))):
                raise ValueError
            if phase == "quarantine":
                self._stopped = True
            # Before EVERY effect, reserve all downstream ceilings, including
            # full gate/fresh consumer, cleanup and quarantine. Preflight is
            # read-only: its timeout still cannot consume recovery reserves.
            if self._stopped and recovery_lane:
                downstream = 120 if phase == "cleanup" and "quarantine" not in self._used else 0
            else:
                index = tuple(_CEILINGS).index(phase)
                downstream = sum(seconds for _, seconds in PHASE_CEILINGS[index + 1:])
            ceiling = _CEILINGS[phase]
            if phase == "preflight":
                ceiling = min(ceiling, remaining - downstream)
            if ceiling <= 0 or remaining < ceiling + downstream:
                raise ValueError
            self._used.add(phase)  # poison/replay barrier even if storage fails
            self._receipts.append(phase=phase, stage="start", outcome="pending",
                                  elapsed=elapsed, remaining=remaining)
            self._active = (phase, elapsed, ceiling)
            return float(ceiling)
        except Exception:
            self._stopped = True
            raise RecoveryRefused("RECOVERY_PHASE_START_REFUSED") from None

    def finish(self, *, outcome: str, event_id: str = "", exit_code: int | None = None,
               http_status: int | None = None) -> str:
        try:
            if self._active is None or outcome not in _OUTCOMES:
                raise ValueError
            phase, started, ceiling = self._active
            elapsed, remaining = self._time()
            # A timeout/lost original receipt is UNKNOWN, not success. PUT204
            # cannot acknowledge creation. No body/metadata-based reconstruction.
            if elapsed - started >= ceiling or remaining <= 0:
                outcome = "unknown"
            if exit_code is not None and exit_code != 0:
                outcome = "interrupted" if exit_code < 0 else "refused"
            if phase == "put" and (type(http_status) is not int or http_status != 201):
                outcome = "unknown"
            if outcome != "completed":
                self._stopped = True
            self._receipts.append(phase=phase, stage="terminal", outcome=outcome,
                                  elapsed=elapsed, remaining=remaining, event_id=event_id,
                                  exit_code=exit_code, http_status=http_status)
            self._active = None
            if outcome == "completed" and not self._stopped:
                self._next += 1
            return outcome
        except Exception:
            self._stopped = True
            self._active = None
            raise RecoveryRefused("RECOVERY_PHASE_TERMINAL_UNAVAILABLE") from None
