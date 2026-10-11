"""Foreground recovery/staging primitives, NOT a complete recovery executor.

No CLI, credential-bearing subprocess or admission shortcut. One-use HTTPS and
sealed staging primitives are not a coordinator. The eventual coordinator must
compose fresh source/admission/account/session observations, genuine writer
exclusion and a reviewed native consumer BEFORE effects. These objects cannot
grant that authority or resume a persisted execution. No live worker use.
"""
from __future__ import annotations

import io
import json
import math
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from shared.identity.dev_smoke_recovery import (
    AUTHORIZATION_ID,
    TARGET_ACCOUNT_ID,
    TENANT_ID,
    RecoveryRefused,
    RecoveryReservation,
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
    allowance, not cancellation support: adapters MUST enforce its absolute
    call_deadline and preserve original child/HTTP completion receipts. The local
    checkout observer below is NOT a live recovery executor. GET cannot resume.
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

    @property
    def call_deadline(self) -> float:
        """Active phase's absolute monotonic limit, never a refreshed allowance.

        An adapter still owns cancellation and original completion proof. Reading
        this property cannot advance a phase or restore stopped authority.
        """
        if self._active is None or (self._stopped and self._active[0] not in {"cleanup", "quarantine"}):
            raise RecoveryRefused("RECOVERY_PHASE_DEADLINE_UNAVAILABLE")
        elapsed, remaining = self._time()
        _, started, ceiling = self._active
        allowance = min(remaining, started + ceiling - elapsed)
        if allowance <= 0:
            self._stopped = True
            raise RecoveryRefused("RECOVERY_PHASE_DEADLINE_UNAVAILABLE")
        return self._start + elapsed + allowance

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


class ObserverRefused(RecoveryRefused):
    """Static refusal and the ORIGINAL child's terminal classification only.

    None means terminal status is unknown, never exit success. No stdout, stderr,
    command, path or exception text is exposed. Later readback cannot resume.
    """

    def __init__(self, *, outcome: str, exit_code: int | None) -> None:
        super().__init__("RECOVERY_CHECKOUT_OBSERVER_REFUSED")
        self.outcome, self.exit_code = outcome, exit_code


def _bounded_observer_child(args: tuple[str, ...], *, cwd: Path, deadline: float) -> bytes:
    """Internal NON-SECRET exec adapter; no shell/inherited env/input/FDs.

    Production caller below supplies ONLY fixed read-only git commands. Reserve
    two seconds INSIDE the absolute deadline for group cancellation and reaping
    the original Popen handle. Output is bounded in memory and never logged.
    A start/kill/wait failure is unknown, not an invented completion receipt.
    Exec may transiently use fork; this is not OS memory isolation. Approved
    runtime custody/dump/debug/swap controls remain a separate prerequisite.
    """
    import selectors
    import signal
    import subprocess

    if (type(deadline) not in (int, float) or not math.isfinite(deadline)
            or not 2 < deadline - time.monotonic() <= 2400):
        raise ObserverRefused(outcome="refused", exit_code=None)
    process = None
    selector = selectors.DefaultSelector()
    output = bytearray()
    size = 0
    outcome = "unknown"
    try:
        process = subprocess.Popen(
            args, cwd=cwd, env={
                "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
            }, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, start_new_session=True,
        )
        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        work_deadline = deadline - 2
        while selector.get_map():
            remaining = work_deadline - time.monotonic()
            if remaining <= 0:
                outcome = "interrupted"
                raise ValueError
            for key, _ in selector.select(timeout=remaining):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                size += len(chunk)
                if size > 65536:
                    outcome = "refused"
                    raise ValueError
                if key.fileobj is process.stdout:
                    output.extend(chunk)
        remaining = work_deadline - time.monotonic()
        if remaining <= 0:
            outcome = "interrupted"
            raise ValueError
        status = process.wait(timeout=remaining)
        if status != 0:
            outcome = "interrupted" if status < 0 else "refused"
            raise ValueError
        return bytes(output)
    except Exception:
        # Cancel the whole process group, including children holding the pipe
        # open after git exits. Never infer completion from logs/process names.
        status = None
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                outcome = "unknown"
            try:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    status = process.wait(timeout=remaining)
                else:
                    status = process.poll()  # same original handle; no waiting extension
            except Exception:
                status = None
        if status is None:
            outcome = "unknown"
        raise ObserverRefused(outcome=outcome, exit_code=status) from None
    finally:
        selector.close()
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


class TrustedCheckoutObserver:
    """Re-read clean exact HEAD and independently pinned FULL reviewed Git tree.

    Local immutable-checkout predicate only, NOT GitHub review/CI, current dev
    admission, actual Web/API identity, human consent or execution authority.
    No commands/callbacks/approval booleans are loaded from a recovery plan.
    Trusted foreground pins the checkout and reviewed tree independently; every
    call re-reads git. Unknown/untracked changes, mismatch or incomplete/bounded
    output refuse. Git configuration capable of executing fsmonitor is disabled.
    """

    def __init__(self, checkout: Path, *, release_sha: str, reviewed_tree: str) -> None:
        from shared.identity.dev_smoke_journal import _SHA
        if (type(release_sha) is not str or not _SHA.fullmatch(release_sha)
                or type(reviewed_tree) is not str or not _SHA.fullmatch(reviewed_tree)):
            raise RecoveryRefused("RECOVERY_CHECKOUT_CONFIG_INVALID")
        self._checkout, self._sha, self._tree = Path(checkout), release_sha, reviewed_tree

    def observe(self, *, deadline: float) -> dict[str, str | bool]:
        def git(*args: str) -> bytes:
            return _bounded_observer_child((
                "/usr/bin/git", "-c", "core.fsmonitor=false", "-c", "gc.auto=0",
                "-c", "maintenance.auto=false", *args,
            ), cwd=self._checkout, deadline=deadline)

        try:
            # One deadline for all commands, not one full timeout per command.
            before = git("rev-parse", "--verify", "HEAD").decode("ascii").strip()
            tree = git("rev-parse", "--verify", "HEAD^{tree}").decode("ascii").strip()
            dirty = git("status", "--porcelain=v1", "--untracked-files=normal",
                        "--ignore-submodules=none")
            after = git("rev-parse", "--verify", "HEAD").decode("ascii").strip()
            if before != self._sha or after != before or tree != self._tree or dirty:
                raise ValueError
        except ObserverRefused:
            raise
        except Exception:
            # All children might have exited zero, but mismatch is NOT passing
            # source proof. No arbitrary path/status/output reaches a receipt.
            raise ObserverRefused(outcome="refused", exit_code=0) from None
        return {
            "stage": "local-checkout-observed", "release_sha": before,
            "reviewed_tree": tree, "execution_authorized": False,
            "credential_binding_verified": False, "secret_values_redacted": True,
        }


class RecoveryHTTPRefused(RecoveryRefused):
    """Only original HTTP status, if received, and fail-stop classification.

    An HTTP error/malformed response to POST is UNKNOWN, not rollback evidence.
    No upstream body, cookie, capability or exception text belongs in receipts.
    """

    def __init__(self, *, outcome: str, http_status: int | None) -> None:
        super().__init__("RECOVERY_HTTPS_ATTEMPT_REFUSED")
        self.outcome, self.http_status = outcome, http_status


@dataclass(frozen=True)
class RecoveryHTTPResult:
    """Private in-process response, NOT an audit/phase receipt or permission.

    May contain the once-issued capability. No pickle/report/child transport.
    Caller must validate action-specific lineage before recording completion.
    """

    http_status: int
    payload: dict = field(repr=False)

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_HTTP_SERIALIZATION_REFUSED")


def _http_remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise TimeoutError
    return remaining


class _DeadlineReader(io.RawIOBase):
    """Bound aggregate header/framing/body bytes and EVERY underlying TLS read.

    Recomputing the same absolute remaining time prevents a slow dribble from
    refreshing read timeouts. Buffered parsing cannot bypass the aggregate cap.
    """

    def __init__(self, sock, deadline: float) -> None:
        self._sock, self._deadline, self._size = sock, deadline, 0
        self._stream = sock.makefile("rb", buffering=0)

    def close(self) -> None:
        self._stream.close()
        super().close()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        self._sock.settimeout(_http_remaining(self._deadline))
        size = self._stream.readinto(buffer)
        self._size += size
        if self._size > 65536:
            raise ValueError
        return size


class _DeadlineSocket:
    def __init__(self, sock, deadline: float) -> None:
        self._sock, self._deadline = sock, deadline

    def sendall(self, data) -> None:
        # socket.sendall's timeout bounds the entire send, not each fragment.
        self._sock.settimeout(_http_remaining(self._deadline))
        self._sock.sendall(data)

    def makefile(self, mode: str):
        if mode != "rb":
            raise ValueError
        return io.BufferedReader(_DeadlineReader(self._sock, self._deadline))

    def close(self) -> None:
        self._sock.close()


class RecoveryHTTPSAttempt:
    """One HTTPS request to the FIXED official recovery route, foreground only.

    The trusted runtime independently pins HTTPS origin and numeric connection
    address (not a plan/redirect/DNS result from untrusted evidence). Certificate
    chain AND origin hostname are verified with SNI; no proxy/env, DNS, retry,
    redirect, subprocess, or remote response logging. Public CA PEM may extend
    normal trust; it cannot disable certificate/hostname verification.

    This is NOT a session owner/preflight/execution entrypoint. The caller must
    retain the SAME approved original-admin cookie in memory, persist a durable
    phase start and pass budget.call_deadline. Server checks the persisted issuer
    session on every call. New instances cannot confer replay/continuation rights.
    Writer exclusion/native-consumer/coordinator blockers remain BEFORE effects.
    """

    PATH = "/api/v1/operator/users/dev-smoke-recovery"

    def __init__(self, *, origin: str, address: str, ca_pem: str | None = None) -> None:
        import ipaddress
        import ssl
        from urllib.parse import urlsplit

        self._used = False
        try:
            parsed = urlsplit(origin)
            if (type(origin) is not str or parsed.scheme != "https" or not parsed.hostname
                    or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                    or any(ord(c) <= 32 or ord(c) >= 127 for c in origin) or not parsed.hostname.isascii()
                    or type(address) is not str or "%" in address):
                raise ValueError
            ip = ipaddress.ip_address(address)  # NEVER resolve DNS inside a phase
            self._address, self._ipv6 = str(ip), ip.version == 6
            self._origin = origin
            self._host, self._port = parsed.hostname, parsed.port or 443
            if ca_pem is not None and type(ca_pem) is not str:
                raise ValueError
            self._context = ssl.create_default_context()
            if ca_pem is not None:
                self._context.load_verify_locations(cadata=ca_pem)
            self._context.minimum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            raise RecoveryRefused("RECOVERY_HTTPS_CONFIG_INVALID") from None

    def execute(self, *, method: str, cookie: str, deadline: float,
                body: dict | None = None) -> RecoveryHTTPResult:
        import http.client
        import socket

        if self._used:
            raise RecoveryHTTPRefused(outcome="refused", http_status=None)
        self._used = True  # even validation/connect failure cannot be retried
        status = None
        raw = tls = connection = response = None
        submitted = False
        try:
            if (method not in {"GET", "POST"} or type(cookie) is not str or not 0 < len(cookie) <= 8192
                    or any(ord(c) <= 32 or ord(c) >= 127 or c in ';"\\,' for c in cookie)
                    or type(deadline) not in (int, float) or not math.isfinite(deadline)
                    or not 0 < deadline - time.monotonic() <= 2400
                    or (method == "GET" and body is not None)
                    or (method == "POST" and type(body) is not dict)):
                raise ValueError
            encoded = None if body is None else json.dumps(body, separators=(",", ":"),
                                                         allow_nan=False).encode("utf-8")
            if encoded is not None and len(encoded) > 8192:
                raise ValueError
            # No getaddrinfo/create_connection: numeric IPv4/IPv6 connect only.
            raw = socket.socket(socket.AF_INET6 if self._ipv6 else socket.AF_INET, socket.SOCK_STREAM)
            raw.settimeout(_http_remaining(deadline))
            raw.connect((self._address, self._port))
            raw.settimeout(_http_remaining(deadline))
            tls = self._context.wrap_socket(raw, server_hostname=self._host)
            connection = http.client.HTTPConnection(self._host, self._port)
            connection.auto_open = 0  # never reconnect after a closed/lost socket
            connection.sock = _DeadlineSocket(tls, deadline)
            # Preserve the exact independently pinned origin for BFF CSRF.
            headers = {"Accept": "application/json", "Origin": self._origin,
                       "Cookie": f"__Host-oday_web_session={cookie}", "Connection": "close"}
            if encoded is not None:
                headers["Content-Type"] = "application/json"
            submitted = True  # a partial send may have committed; never reconstruct
            connection.request(method, self.PATH, body=encoded, headers=headers)
            response = connection.getresponse()
            status = response.status
            lengths = response.headers.get_all("Content-Length", [])
            transfers = response.headers.get_all("Transfer-Encoding", [])
            if (len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers)
                    or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit()
                                     or int(lengths[0]) > 16384))
                    or (transfers and transfers[0].lower() != "chunked")
                    or response.headers.defects
                    or len(response.headers.get_all("Content-Type", [])) != 1
                    or response.headers.get_content_type() != "application/json"
                    or response.headers.get_all("Cache-Control", []) != ["no-store"]
                    or status not in {200, 201}):
                raise ValueError
            clear = response.read(16385)
            if len(clear) > 16384 or (lengths and len(clear) != int(lengths[0])):
                raise ValueError
            # read(amt) may return a truncated chunk without noticing premature
            # EOF. Require the framing terminator too; still under SAME deadline.
            if response.read(1):
                raise ValueError

            def unique(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError
                    value[key] = item
                return value

            payload = json.loads(clear, object_pairs_hook=unique,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if type(payload) is not dict:
                raise ValueError
            _http_remaining(deadline)  # parsing/EOF is not permission beyond expiry
            return RecoveryHTTPResult(status, payload)
        except Exception:
            raise RecoveryHTTPRefused(outcome="unknown" if submitted and method == "POST" else "refused",
                                      http_status=status) from None
        finally:
            for resource in (response, connection, tls, raw):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass  # no cleanup exception/body may escape redaction


class RecoveryActionRefused(RecoveryRefused):
    """Safe action classification only; no response/input/exception serialization."""

    def __init__(self, *, outcome: str, http_status: int | None) -> None:
        super().__init__("RECOVERY_ACTION_STOPPED")
        self.outcome, self.http_status = outcome, http_status


class SameSessionRecoveryActions:
    """Once-only recovery/binding actions over the SAME memory cookie.

    NOT a session owner or execution coordinator. Trusted foreground must first
    complete real preflight, independently reviewed authority/exclusion/consumer
    gates, and persist its preflight terminal through the supplied budget. No
    login, GET/readback/resume or credential-bearing child exists. ACK requires
    the concrete store's original201 plus durable terminal, not a supplied status.
    A lost reserve reply leaves no locally proven root: quarantine completion is
    UNKNOWN until separately authorized reconciliation, never a reserve retry.
    """

    def __init__(self, plan: dict[str, str], *, budget: ForegroundRecoveryBudget,
                 custody: ParentMemoryCustody, origin: str, address: str,
                 cookie: str, ca_pem: str | None = None) -> None:
        try:
            digest = validate_recovery_plan(plan, release_sha=plan.get("release_sha"),
                    manifest_digest=plan.get("manifest_digest"), now=datetime.now(UTC))
            if (type(budget) is not ForegroundRecoveryBudget or budget._stopped
                    or budget._receipts._execution != plan["execution_id"]
                    or budget._receipts._digest != digest
                    or type(custody) is not ParentMemoryCustody or custody._attached
                    or type(cookie) is not str or not 0 < len(cookie) <= 8192
                    or any(ord(c) <= 32 or ord(c) >= 127 or c in ';"\\,' for c in cookie)):
                raise ValueError
            custody._check()
            # Validate independently pinned transport config without any call.
            RecoveryHTTPSAttempt(origin=origin, address=address, ca_pem=ca_pem)
        except Exception:
            raise RecoveryRefused("RECOVERY_ACTION_CONFIG_INVALID") from None
        self._pid = os.getpid()
        self._plan, self._digest = dict(plan), digest
        self._budget, self._custody = budget, custody
        self._origin, self._address, self._ca, self._cookie = origin, address, ca_pem, cookie
        self._root = None
        self._capability_id = self._issue_event_id = self._rotation_event_id = ""
        self._intent = None
        self._put_status = None
        self._stopped = self._quarantined = False
        self._attempted: set[str] = set()
        self._events: set[str] = set()

    def __repr__(self) -> str:
        return "SameSessionRecoveryActions(<redacted>)"

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_ACTION_SERIALIZATION_REFUSED")

    def _call(self, phase: str, body: dict, validate) -> dict:
        status, outcome, event_id = None, "refused", ""
        started = False
        try:
            if (os.getpid() != self._pid or phase in self._attempted
                    or self._quarantined or (self._stopped and phase != "quarantine")):
                raise ValueError
            self._attempted.add(phase)
            self._budget.begin(phase)  # durable start BEFORE any network access
            started = True
            if phase != "quarantine":
                self._custody._check()
            attempt = RecoveryHTTPSAttempt(origin=self._origin, address=self._address, ca_pem=self._ca)
            result = attempt.execute(method="POST", cookie=self._cookie,
                                     deadline=self._budget.call_deadline, body=body)
            status, outcome = result.http_status, "unknown"
            # Mounted official actions return200, NOT arbitrary200/201 success.
            if status != 200:
                raise ValueError
            safe, event_id = validate(result.payload)
            if not _uuid(event_id) or event_id in self._events:
                raise ValueError
            # Parsing/validation may not outlive the same absolute phase limit.
            _http_remaining(self._budget.call_deadline)
            outcome = self._budget.finish(outcome="completed", event_id=event_id, http_status=status)
            started = False
            if outcome != "completed":
                raise ValueError
            self._events.add(event_id)
            return safe
        except Exception as exc:
            self._stopped = True
            if isinstance(exc, RecoveryHTTPRefused):
                outcome, status = exc.outcome, exc.http_status
            if started:
                try:
                    outcome = self._budget.finish(outcome=outcome, http_status=status)
                except Exception:
                    outcome = "unknown"  # missing durable terminal is NOT completion
            # No credential/body/remote exception chain escapes the adapter.
            raise RecoveryActionRefused(outcome=outcome, http_status=status) from None

    def _reservation_reply(self, payload: dict, *, stage: str) -> tuple[dict, str]:
        if set(payload) != {"reservation"} or type(payload["reservation"]) is not dict:
            raise ValueError
        receipt = payload["reservation"]
        event_id = receipt.get("event_id")
        expected = RecoveryReservation(self._plan["execution_id"], self._digest, event_id, stage).to_receipt()
        if (set(receipt) != set(expected) or receipt != expected
                or receipt["execution_authorized"] is not False
                or receipt["credential_binding_verified"] is not False
                or receipt["secret_values_redacted"] is not True or not _uuid(event_id)):
            raise ValueError
        return dict(receipt), event_id

    def reserve(self) -> dict:
        safe = self._call("reserve", {"action": "reserve", "plan": dict(self._plan)},
                          lambda payload: self._reservation_reply(payload, stage="reserved"))
        self._root = dict(safe)  # only after validated response AND durable completion
        return safe

    def issue(self) -> dict:
        def validate(payload):
            import re
            if (set(payload) != {"capability_id", "execution_id", "expires_at", "audit_event_id", "token"}
                    or payload["execution_id"] != self._plan["execution_id"]
                    or not _uuid(payload["capability_id"]) or not _uuid(payload["audit_event_id"])
                    or payload["capability_id"] in self._events
                    or payload["capability_id"] in {payload["audit_event_id"], self._plan["execution_id"]}
                    or type(payload["token"]) is not str
                    or not re.fullmatch(r"[A-Za-z0-9_-]{43}", payload["token"])
                    or type(payload["expires_at"]) is not str):
                raise ValueError
            expires = datetime.fromisoformat(payload["expires_at"])
            now = datetime.now(UTC)
            if (expires.tzinfo is None or expires.utcoffset() is None
                    or not 0 < (expires - now).total_seconds() <= 600
                    or expires > datetime.fromisoformat(self._plan["expires_at"])):
                raise ValueError
            return {k: v for k, v in payload.items() if k != "token"}, payload["audit_event_id"]

        if self._root is None:
            self._stopped = True
            raise RecoveryActionRefused(outcome="refused", http_status=None)
        # Retain bearer BEFORE durable completion. If terminal persistence fails,
        # memory survives but the stopped adapter can only quarantine, not rotate.
        def retain(payload):
            safe, event_id = validate(payload)
            self._custody.attach_capability(payload["token"])
            return safe, event_id
        safe = self._call("issue", {"action": "issue", "reservation": dict(self._root),
                                   "lifetime_seconds": 600}, retain)
        self._capability_id, self._issue_event_id = safe["capability_id"], safe["audit_event_id"]
        return safe

    def rotate(self) -> dict:
        def validate(payload):
            expected = {
                "status": "rotated", "account_id": TARGET_ACCOUNT_ID, "tenant_id": TENANT_ID,
                "execution_id": self._plan["execution_id"], "capability_id": self._capability_id,
                "issue_event_id": self._issue_event_id, "rotation_event_id": payload.get("rotation_event_id"),
                "revoked_target_sessions": payload.get("revoked_target_sessions"),
                "credential_binding_verified": False, "execution_authorized": False,
                "secret_values_redacted": True,
            }
            count = payload.get("revoked_target_sessions")
            if (set(payload) != set(expected) or payload != expected
                    or payload["execution_authorized"] is not False
                    or payload["credential_binding_verified"] is not False
                    or payload["secret_values_redacted"] is not True
                    or type(count) is not int or not 0 <= count < 2**63):
                raise ValueError
            return dict(payload), payload["rotation_event_id"]

        try:
            if self._root is None or not self._capability_id:
                raise ValueError
            body = {"action": "consume", "reservation": dict(self._root),
                    "capability_id": self._capability_id, "token": self._custody.capability,
                    "password": self._custody.password}
        except Exception:
            self._stopped = True
            raise RecoveryActionRefused(outcome="refused", http_status=None) from None
        safe = self._call("rotate", body, validate)
        self._rotation_event_id = safe["rotation_event_id"]
        return safe

    def _binding_reply(self, payload: dict, *, stage: str) -> tuple[dict, str]:
        from shared.identity.dev_smoke_journal import CREDENTIAL_BUNDLE_SECRET_NAME
        if set(payload) != {"binding"} or type(payload["binding"]) is not dict:
            raise ValueError
        value = payload["binding"]
        expected = {
            **self._plan, "plan_digest": self._digest, "root_event_id": self._root["event_id"],
            "issuer_session_id": value.get("issuer_session_id"),
            "capability_id": self._capability_id, "capability_issue_event_id": self._issue_event_id,
            "rotation_event_id": self._rotation_event_id, "secret_name": CREDENTIAL_BUNDLE_SECRET_NAME,
            "stage": stage, "put_status": 0 if stage == "binding-intent" else 201,
            "execution_authorized": False, "credential_binding_verified": False,
            "secret_values_redacted": True, "audit_event_id": value.get("audit_event_id"),
        }
        if (set(value) != set(expected) or value != expected
                or not _uuid(value["issuer_session_id"]) or type(value["put_status"]) is not int
                or value["execution_authorized"] is not False
                or value["credential_binding_verified"] is not False
                or value["secret_values_redacted"] is not True
                or (self._intent is not None and any(value[k] != self._intent[k]
                    for k in set(expected) - {"stage", "put_status", "audit_event_id"}))):
            raise ValueError
        return dict(value), value["audit_event_id"]

    def binding_intent(self) -> dict:
        # Budget requires completed reconciliation; that is NOT independent
        # proof of preflight/session ownership or permission to bypass exclusion.
        if not self._rotation_event_id or self._root is None:
            self._stopped = True
            raise RecoveryActionRefused(outcome="refused", http_status=None)
        safe = self._call("intent", {"action": "binding-intent", "reservation": dict(self._root)},
                          lambda payload: self._binding_reply(payload, stage="binding-intent"))
        self._intent = dict(safe)
        return safe

    def binding_acknowledge(self) -> dict:
        # No public status argument. Only the concrete single-attempt store below
        # can supply original201 after durable PUT completion in this process.
        if self._intent is None or self._put_status != 201:
            self._stopped = True
            raise RecoveryActionRefused(outcome="refused", http_status=None)
        return self._call("ack", {"action": "binding-acknowledged", "reservation": dict(self._root),
                                 "intent_event_id": self._intent["audit_event_id"], "put_status": 201},
                          lambda payload: self._binding_reply(payload, stage="binding-acknowledged"))

    def _bundle(self) -> dict:
        from shared.identity.dev_smoke_journal import REPOSITORY
        from shared.identity.dev_smoke_recovery import recovery_bundle_lineage
        if (self._intent is None or self._stopped or self._quarantined or os.getpid() != self._pid):
            raise RecoveryRefused("RECOVERY_BINDING_INPUT_REFUSED")
        lineage = {
            "plan_digest": self._digest, "root_event_id": self._root["event_id"],
            "capability_id": self._capability_id, "capability_issue_event_id": self._issue_event_id,
            "rotation_event_id": self._rotation_event_id,
            "binding_intent_event_id": self._intent["audit_event_id"],
            **{k: self._plan[k] for k in ("old_execution_id", "old_plan_digest", "old_quarantine_event_id",
                                         "invitation_id", "issue_event_id", "accept_event_id")},
        }
        recovery_bundle_lineage(lineage)
        return {"schema_version": 2, "authorization_id": AUTHORIZATION_ID,
                "execution_id": self._plan["execution_id"], "repository": REPOSITORY,
                "environment": "dev", "tenant_id": TENANT_ID, "account_id": TARGET_ACCOUNT_ID,
                "username": "odp-dev-smoke", "password": self._custody.password,
                "recovery_lineage": lineage}

    def quarantine(self) -> dict:
        # May record uncertainty after custody loss; uses ONLY retained own cookie
        # and confirmed root. No new session/GET/blanket revocation is introduced.
        self._stopped = True
        if self._root is None:
            raise RecoveryActionRefused(outcome="unknown", http_status=None)
        safe = self._call("quarantine", {"action": "quarantine", "reservation": dict(self._root)},
                          lambda payload: self._reservation_reply(payload, stage="recovery-required"))
        self._quarantined = True
        return safe

    def close(self) -> None:
        self._stopped = True
        self._cookie = ""  # NOT logout proof or provable memory zeroization


class RecoveryGitHubSecretAttempt:
    """Pinned bounded HTTPS staging primitive, NOT execution/exclusion authority.

    Trusted foreground independently pins human custodian, repository numeric ID,
    dev public key and numeric api.github.com address. Every observation freshly
    authenticates /user, repository, absence and exact key. This cannot exclude
    another API writer: the full coordinator MUST refuse BEFORE rotation until
    genuine external exclusion is established. No CLI/callback/boolean lock or
    writer-exclusion receipt is accepted or invented here.

    One prepare, ONE sealed-box PUT, no value GET/delete/retry/overwrite. The
    same-session actions own the retained pair/intent and original201-to-ACK
    fence. Native custody approval, source/admission/preflight and session/gate
    coordinator remain separate BEFORE-effects prerequisites.
    """

    def __init__(self, *, token: str, custodian_login: str, custodian_id: int,
                 repository_id: int, key_id: str, public_key: str, address: str,
                 ca_pem: str | None = None) -> None:
        import base64
        import re

        from shared.identity.dev_smoke_journal import _CUSTODIAN, CREDENTIAL_BUNDLE_SECRET_NAME
        self._pid, self._prepare_used, self._put_used, self._prepared = os.getpid(), False, False, False
        try:
            if (type(token) is not str or not 1 <= len(token) <= 8192
                    or any(ord(c) <= 32 or ord(c) >= 127 for c in token)
                    or type(custodian_login) is not str or not _CUSTODIAN.fullmatch(custodian_login)
                    or any(type(v) is not int or not 0 < v < 2**63 for v in (custodian_id, repository_id))
                    or type(key_id) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key_id)
                    or type(public_key) is not str
                    or len(base64.b64decode(public_key, validate=True)) != 32):
                raise ValueError
            self._transport = RecoveryHTTPSAttempt(origin="https://api.github.com", address=address,
                                                    ca_pem=ca_pem)
        except Exception:
            raise RecoveryRefused("RECOVERY_SECRET_CONFIG_INVALID") from None
        self._token, self._login, self._identity, self._repo = token, custodian_login, custodian_id, repository_id
        self._key_id, self._public_key = key_id, public_key
        self._path = f"/repositories/{repository_id}/environments/dev/secrets"
        self._secret_path = f"{self._path}/{CREDENTIAL_BUNDLE_SECRET_NAME}"

    def __repr__(self) -> str:
        return "RecoveryGitHubSecretAttempt(<redacted>)"

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_SECRET_SERIALIZATION_REFUSED")

    def _request(self, method: str, path: str, *, deadline: float, body: dict | None = None) -> RecoveryHTTPResult:
        """Internal fixed-path request; same absolute TLS/read/framing limits.

        GET of the named secret is metadata-only (404 required); public GitHub
        API has NO plaintext value retrieval. No user-supplied route is exported.
        Empty201 is accepted only after complete framing/EOF, never status alone.
        """
        import http.client
        import socket

        from shared.identity.dev_smoke_journal import REPOSITORY
        status, submitted = None, False
        raw = tls = connection = response = None
        try:
            if (os.getpid() != self._pid or not self._token
                    or type(deadline) not in (int, float) or not math.isfinite(deadline)
                    or not 0 < deadline - time.monotonic() <= 2400
                    or (method, path) not in {
                        ("GET", "/user"), ("GET", f"/repos/{REPOSITORY}"),
                        ("GET", self._secret_path), ("GET", f"{self._path}/public-key"),
                        ("PUT", self._secret_path),
                    } or (method == "GET" and body is not None)
                    or (method == "PUT" and (type(body) is not dict
                        or set(body) != {"key_id", "encrypted_value"}))):
                raise ValueError
            encoded = None if body is None else json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
            if encoded is not None and len(encoded) > 16384:
                raise ValueError
            transport = self._transport
            raw = socket.socket(socket.AF_INET6 if transport._ipv6 else socket.AF_INET, socket.SOCK_STREAM)
            raw.settimeout(_http_remaining(deadline))
            raw.connect((transport._address, transport._port))
            raw.settimeout(_http_remaining(deadline))
            tls = transport._context.wrap_socket(raw, server_hostname=transport._host)
            connection = http.client.HTTPConnection(transport._host, transport._port)
            connection.auto_open = 0
            connection.sock = _DeadlineSocket(tls, deadline)
            headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {self._token}",
                       "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "odayplus-dev-smoke-recovery",
                       "Connection": "close"}
            if encoded is not None:
                headers["Content-Type"] = "application/json"
            submitted = True
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            status = response.status
            lengths = response.headers.get_all("Content-Length", [])
            transfers = response.headers.get_all("Transfer-Encoding", [])
            if (len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers)
                    or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit() or int(lengths[0]) > 16384))
                    or (transfers and transfers[0].lower() != "chunked") or response.headers.defects
                    or status not in {200, 201, 404}):
                raise ValueError
            clear = response.read(16385)
            if (len(clear) > 16384 or (lengths and len(clear) != int(lengths[0])) or response.read(1)):
                raise ValueError
            payload = {}
            if clear:
                if (len(response.headers.get_all("Content-Type", [])) != 1
                        or response.headers.get_content_type() != "application/json"):
                    raise ValueError
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError
                        result[key] = value
                    return result
                payload = json.loads(clear, object_pairs_hook=unique,
                                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if type(payload) is not dict:
                    raise ValueError
            _http_remaining(deadline)
            return RecoveryHTTPResult(status, payload)
        except Exception:
            raise RecoveryHTTPRefused(outcome="unknown" if submitted and method == "PUT" else "refused",
                                      http_status=status) from None
        finally:
            for resource in (response, connection, tls, raw):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

    def _observe(self, *, deadline: float) -> None:
        from shared.identity.dev_smoke_journal import REPOSITORY
        user = self._request("GET", "/user", deadline=deadline)
        if (user.http_status != 200 or user.payload.get("type") != "User"
                or type(user.payload.get("id")) is not int or user.payload["id"] != self._identity
                or type(user.payload.get("login")) is not str
                or user.payload["login"].casefold() != self._login.casefold()):
            raise ValueError
        repo = self._request("GET", f"/repos/{REPOSITORY}", deadline=deadline)
        if (repo.http_status != 200 or repo.payload.get("full_name") != REPOSITORY
                or type(repo.payload.get("id")) is not int or repo.payload["id"] != self._repo):
            raise ValueError
        absent = self._request("GET", self._secret_path, deadline=deadline)
        if absent.http_status != 404:
            raise ValueError  # absence is necessary, NOT exclusion
        key = self._request("GET", f"{self._path}/public-key", deadline=deadline)
        if (key.http_status != 200 or key.payload.get("key_id") != self._key_id
                or key.payload.get("key") != self._public_key):
            raise ValueError
        _http_remaining(deadline)

    def prepare(self, *, deadline: float) -> dict:
        try:
            if self._prepare_used or self._put_used:
                raise ValueError
            self._prepare_used = True
            self._observe(deadline=deadline)
            self._prepared = True
        except Exception:
            self._prepared = False
            self._put_used = True
            raise RecoveryRefused("RECOVERY_SECRET_PREFLIGHT_REFUSED") from None
        return {"stage": "secret-metadata-observed", "execution_authorized": False,
                "credential_binding_verified": False, "secret_values_redacted": True}

    def put(self, actions: SameSessionRecoveryActions) -> dict:
        """Durable intent -> fresh pins/absence -> one sealed PUT -> terminal.

        No caller-supplied bundle, success201 or callback. A terminal-fsync failure
        AFTER real201 poisons ACK too. This is not an exclusion-enforcing complete
        coordinator and must never be invoked live without its missing preflight.
        """
        import base64

        from nacl.public import PublicKey, SealedBox

        started, outcome, status = False, "refused", None
        try:
            if (self._put_used or not self._prepared or os.getpid() != self._pid
                    or type(actions) is not SameSessionRecoveryActions or actions._stopped
                    or actions._quarantined or actions._intent is None
                    or actions._plan["custodian"].casefold() != self._login.casefold()
                    or "put" in actions._attempted):
                raise ValueError
            self._put_used = True
            actions._attempted.add("put")
            actions._budget.begin("put")
            started = True
            clear = json.dumps(actions._bundle(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            encrypted = SealedBox(PublicKey(base64.b64decode(self._public_key, validate=True))).encrypt(clear)
            del clear  # best-effort reference release, NOT zeroization
            self._observe(deadline=actions._budget.call_deadline)
            reply = self._request("PUT", self._secret_path, deadline=actions._budget.call_deadline,
                                  body={"key_id": self._key_id, "encrypted_value": base64.b64encode(encrypted).decode()})
            status, outcome = reply.http_status, "unknown"
            if status != 201 or reply.payload:
                raise ValueError
            outcome = actions._budget.finish(outcome="completed", http_status=status)
            started = False
            if outcome != "completed":
                raise ValueError
            actions._put_status = status  # original201 AND durable completion; never from GET
            return {"stage": "encrypted-put-acknowledged", "http_status": status,
                    "execution_authorized": False, "credential_binding_verified": False,
                    "secret_values_redacted": True}
        except Exception as exc:
            self._put_used = True
            if isinstance(exc, RecoveryHTTPRefused):
                outcome, status = exc.outcome, exc.http_status
            if type(actions) is SameSessionRecoveryActions:
                actions._stopped = True
                if started:
                    try:
                        outcome = actions._budget.finish(outcome=outcome, http_status=status)
                    except Exception:
                        outcome = "unknown"
            raise RecoveryActionRefused(outcome=outcome, http_status=status) from None

    def close(self) -> None:
        self._token = ""
        self._put_used = self._prepare_used = True
