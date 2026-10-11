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

from delivery_toolchain.release.provision_dev_smoke import GitHubSourceApprovalObserver
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
               http_status: int | None = None, operation: str = "") -> None:
        try:
            if (operation not in {"", "original-login", "target-login", "original-logout", "target-logout",
                                 "original-observe", "target-observe",
                                 "gate-json-login", "gate-form-login", "gate-invalid-json-login",
                                 "gate-invalid-form-login", "gate-session-logout",
                                 "preflight-checkout", "preflight-source", "preflight-admission",
                                 "preflight-secret-metadata"}
                    or self._poisoned or self._fd is None or phase not in _CEILINGS
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
                "operation": operation,
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


def _bounded_observer_child(args: tuple[str, ...], *, cwd: Path, deadline: float,
                            input_bytes: bytes | None = None) -> bytes:
    """Internal NON-SECRET exec adapter; no shell/inherited env/FDs.

    Callers supply fixed read-only git or canonical manifest-validation commands.
    Optional stdin is ONLY the non-secret deployed manifest, never custody input. Reserve
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
            or not 2 < deadline - time.monotonic() <= 2400
            or (input_bytes is not None and (type(input_bytes) is not bytes or len(input_bytes) > 2_000_000))):
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
            }, stdin=subprocess.DEVNULL if input_bytes is None else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True,
        )
        written = 0
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE)
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
                if key.fileobj is process.stdin:
                    written += os.write(key.fd, input_bytes[written:written + 8192])
                    if written == len(input_bytes):
                        selector.unregister(process.stdin)
                        process.stdin.close()
                    continue
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
            for stream in (process.stdin, process.stdout, process.stderr):
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


def _absolute_observer_deadline(deadline: float):
    """Parent-memory, main-thread deadline guard; no credential-bearing child.

    A missed deadline/OS-stuck call is refusal, not hard-real-time cancellation
    proof. Never replace another runtime's timer or extend the absolute budget.
    """
    import signal
    import threading
    from contextlib import contextmanager

    @contextmanager
    def guard():
        if (threading.current_thread() is not threading.main_thread()
                or not hasattr(signal, "setitimer") or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)):
            raise RecoveryRefused("RECOVERY_SOURCE_DEADLINE_UNAVAILABLE")
        remaining = _http_remaining(deadline)
        previous = signal.getsignal(signal.SIGALRM)

        def expired(_signum, _frame):
            raise RecoveryRefused("RECOVERY_SOURCE_DEADLINE_EXCEEDED")

        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        try:
            yield
            _http_remaining(deadline)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    return guard()


class TrustedGitHubSourceObserver(GitHubSourceApprovalObserver):
    """Fresh canonical whole-tree/review/latest reviewed+candidate CI predicates.

    Independently pinned trust roots ONLY, never plan flags/receipts. Reuses the
    existing source observer's PR/merge/review-writer/latest-run/check predicates,
    replacing ONLY its read transport with bounded parent-memory HTTPS and one
    absolute phase deadline. No gh/subprocess, env/proxy, retry or redirects.
    Independently pinned full tree must match both remote commits, not just each
    other. Any uncertainty poisons this observer; later GET cannot resume it.

    This is source evidence, NOT current admission, writer exclusion, custody
    approval, actual serving identity or execution authority. Main-thread signal
    support and OS custody controls remain prerequisites; no production bounds
    or hard-real-time termination are claimed. Coordinator must persist starts
    and terminal classifications and reobserve before effects.
    """

    def __init__(self, *, token: str, pull_number: int, reviewed_head: str,
                 release_sha: str, reviewed_tree: str, review_writer_login: str,
                 review_writer_id: int, workflow_id: int, checks_app_id: int,
                 required_checks: tuple[str, ...]) -> None:
        import httpx

        from shared.identity.dev_smoke_journal import _SHA
        self._pid, self._stopped, self._deadline = os.getpid(), True, None
        try:
            if (type(token) is not str or not 0 < len(token) <= 8192
                    or any(ord(c) <= 32 or ord(c) >= 127 for c in token)
                    or any(type(v) is not str or not _SHA.fullmatch(v) for v in (release_sha, reviewed_tree))):
                raise ValueError
            super().__init__(token=token, pull_number=pull_number, reviewed_head=reviewed_head,
                review_writer_login=review_writer_login, review_writer_id=review_writer_id,
                workflow_id=workflow_id, checks_app_id=checks_app_id, required_checks=required_checks)
            headers = dict(self._client.headers)
            self._client.close()
            headers["Accept-Encoding"] = "identity"
            self._client = httpx.Client(base_url="https://api.github.com", headers=headers,
                timeout=20, follow_redirects=False, trust_env=False)
            self._sha, self._tree, self._stopped = release_sha, reviewed_tree, False
        except Exception:
            raise RecoveryRefused("RECOVERY_SOURCE_CONFIG_INVALID") from None

    def __repr__(self) -> str:
        return "TrustedGitHubSourceObserver(<redacted>)"

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_SOURCE_SERIALIZATION_REFUSED")

    def _get(self, path: str):
        import re

        from shared.identity.dev_smoke_journal import REPOSITORY
        # All routes are canonical source reads. No public arbitrary-path API,
        # secret endpoint, write, injected callback or list pagination fallback.
        fixed = {f"pulls/{self._pull}", f"commits/{self._head}/statuses?per_page=100"}
        for sha in {self._sha, self._head}:
            fixed.update({f"git/commits/{sha}",
                          f"actions/workflows/{self._workflow}/runs?head_sha={sha}&per_page=100"})
        if (self._deadline is None or self._stopped or os.getpid() != self._pid
                or (path not in fixed and not re.fullmatch(
                    r"check-suites/[1-9][0-9]*/check-runs\?filter=latest&per_page=100", path))):
            raise ValueError
        remaining = _http_remaining(self._deadline)
        with self._client.stream("GET", f"/repos/{REPOSITORY}/{path}", timeout=min(20, remaining)) as response:
            if (response.status_code != 200 or "next" in response.links
                    or response.headers.get("content-type", "").split(";", 1)[0] != "application/json"
                    or response.headers.get("content-encoding", "identity") != "identity"):
                raise ValueError
            clear = bytearray()
            for chunk in response.iter_raw():
                _http_remaining(self._deadline)
                if len(clear) + len(chunk) > 65536:
                    raise ValueError
                clear.extend(chunk)

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            value = json.loads(clear, object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            _http_remaining(self._deadline)
            if path.startswith("git/commits/") and value.get("tree", {}).get("sha") != self._tree:
                raise ValueError
            return value

    def observe(self, *, release_sha: str, deadline: float) -> dict:
        try:
            if (self._stopped or self._deadline is not None or os.getpid() != self._pid
                    or release_sha != self._sha or type(deadline) not in (int, float)
                    or not math.isfinite(deadline) or not 0 < deadline - time.monotonic() <= 2400):
                raise ValueError
            self._deadline = deadline
            with _absolute_observer_deadline(deadline):
                super().observe(release_sha=release_sha, now=datetime.now(UTC))
            return {"stage": "remote-source-observed", "release_sha": self._sha,
                    "reviewed_head": self._head, "reviewed_tree": self._tree,
                    "execution_authorized": False, "credential_binding_verified": False,
                    "secret_values_redacted": True}
        except Exception:
            self._stopped = True
            raise RecoveryRefused("RECOVERY_SOURCE_APPROVAL_UNVERIFIED") from None
        finally:
            self._deadline = None

    def close(self) -> None:
        self._stopped = True
        super().close()


class TrustedGitHubAdmissionObserver:
    """Bounded parent-memory read of CURRENT ordinary dev deployment history.

    Reuses canonical live-step and manifest admission predicates and preserves
    newest-changing-attempt/restoration selection, including failed-after-live
    refusal. No gh, lease replay, dispatch, cached pass or injected proof reader.
    Every call rereads history, jobs, environment proof and retained artifacts.
    The foreground independently pins the artifact download host (exact trusted
    GitHub storage hostname); ONE signed redirect is allowed only for ZIP GET,
    without Authorization. It is never a generic authenticated redirect policy.

    History beyond the finite byte/request/page budget is a pre-effect blocker.
    This is admission evidence only, NOT actual serving identity, source approval,
    writer exclusion, custody or permission. Main-thread deadline/runtime custody
    prerequisites match the source observer; production bounds remain unmeasured.
    """

    def __init__(self, *, token: str, artifact_host: str) -> None:
        import re

        import httpx
        self._pid, self._stopped, self._deadline = os.getpid(), True, None
        try:
            if (type(token) is not str or not 0 < len(token) <= 8192
                    or any(ord(c) <= 32 or ord(c) >= 127 for c in token)
                    or type(artifact_host) is not str or not re.fullmatch(
                        r"[a-z0-9-]+\.(?:blob\.core\.windows\.net|actions\.githubusercontent\.com)", artifact_host)):
                raise ValueError
            self._host = artifact_host
            self._client = httpx.Client(base_url="https://api.github.com", trust_env=False,
                follow_redirects=False, timeout=20, headers={
                    "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                    "Accept-Encoding": "identity", "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "odayplus-dev-smoke-recovery",
                })
            self._download = httpx.Client(trust_env=False, follow_redirects=False,
                timeout=20, headers={"Accept-Encoding": "identity"})
            self._stopped = False
        except Exception:
            raise RecoveryRefused("RECOVERY_ADMISSION_CONFIG_INVALID") from None

    def __repr__(self) -> str:
        return "TrustedGitHubAdmissionObserver(<redacted>)"

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_ADMISSION_SERIALIZATION_REFUSED")

    @staticmethod
    def _json(clear: bytes):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        return json.loads(clear, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))

    def _body(self, response, *, limit: int) -> bytes:
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ValueError
        clear = bytearray()
        for chunk in response.iter_raw():
            _http_remaining(self._deadline)
            self._bytes += len(chunk)
            if len(clear) + len(chunk) > limit or self._bytes > 4_194_304:
                raise ValueError
            clear.extend(chunk)
        _http_remaining(self._deadline)
        return bytes(clear)

    def _get(self, path: str, *, raw: bool = False):
        import re
        from urllib.parse import urlsplit

        from shared.identity.dev_smoke_journal import REPOSITORY
        routes = (
            r"actions/workflows/deploy-dev\.yml/runs\?event=workflow_dispatch&per_page=100&page=[1-9][0-9]?",
            r"actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*/jobs\?per_page=100",
            r"actions/runs/[1-9][0-9]*/artifacts\?per_page=100",
            r"deployments\?environment=dev&sha=[0-9a-f]{40}&per_page=100&page=[1-9][0-9]?",
            r"deployments/[1-9][0-9]*/statuses\?per_page=100&page=[1-9][0-9]?",
        )
        zip_route = bool(re.fullmatch(r"actions/artifacts/[1-9][0-9]*/zip", path))
        if (self._stopped or self._deadline is None or os.getpid() != self._pid
                or raw != zip_route or (not raw and not any(re.fullmatch(r, path) for r in routes))):
            raise ValueError
        self._requests += 1
        if self._requests > 256:
            raise ValueError
        with self._client.stream("GET", f"/repos/{REPOSITORY}/{path}",
                timeout=min(20, _http_remaining(self._deadline))) as response:
            if raw:
                if response.status_code != 302:
                    raise ValueError
                url = response.headers.get("location", "")
                parsed = urlsplit(url)
                if (len(url) > 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in url)
                        or parsed.scheme != "https" or parsed.hostname != self._host
                        or parsed.netloc != self._host or parsed.username or parsed.password
                        or parsed.fragment or not parsed.path.startswith("/") or not parsed.query):
                    raise ValueError
                # Signed URL is memory-only. No API token, proxy, cookies, second
                # redirect, retry, disk ZIP, extraction or secret-value endpoint.
                self._download.cookies.clear()
                with self._download.stream("GET", url,
                        timeout=min(20, _http_remaining(self._deadline))) as artifact:
                    if artifact.status_code != 200 or artifact.headers.get("set-cookie"):
                        raise ValueError
                    return self._body(artifact, limit=2_097_152)
            if (response.status_code != 200 or response.headers.get("set-cookie")
                    or response.headers.get("content-type", "").split(";", 1)[0] != "application/json"):
                raise ValueError
            value = self._json(self._body(response, limit=65_536))
            if "/jobs?" in path or "/artifacts?" in path:
                key = "jobs" if "/jobs?" in path else "artifacts"
                if (type(value) is not dict or type(value.get(key)) is not list
                        or type(value.get("total_count")) is not int
                        or value["total_count"] != len(value[key]) or len(value[key]) > 100
                        or "next" in response.links):
                    raise ValueError
            else:
                records = value.get("workflow_runs") if type(value) is dict else value
                if (type(records) is not list or len(records) > 100
                        or (len(records) < 100 and "next" in response.links)):
                    raise ValueError
            return value

    def _paged(self, path: str) -> list:
        records = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 21):
            batch = self._get(f"{path}{separator}per_page=100&page={page}")
            if type(batch) is not list or any(type(r) is not dict for r in batch):
                raise ValueError
            records.extend(batch)
            if len(batch) < 100:
                return records
        raise ValueError

    def _environment(self, run: dict) -> bool:
        import re
        from urllib.parse import urlsplit

        from shared.identity.dev_smoke_journal import REPOSITORY
        for deployment in self._paged(f"deployments?environment=dev&sha={run['head_sha']}"):
            if type(deployment.get("id")) is not int or deployment["id"] <= 0:
                raise ValueError
            for status in self._paged(f"deployments/{deployment['id']}/statuses"):
                if status.get("state") != "success":
                    continue
                for url_field in ("log_url", "target_url"):
                    url = urlsplit(status.get(url_field) or "")
                    if (url.scheme == "https" and url.netloc == "github.com" and not url.fragment
                            and re.fullmatch(f"/{REPOSITORY}/actions/runs/{run['id']}(?:/job/[1-9][0-9]*)?",
                                             url.path)):
                        return True
        return False

    def _archive(self, run: dict, *, name: str, filename: str, limit: int) -> list:
        import zipfile
        artifacts = self._get(f"actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
        matches = [a for a in artifacts if a.get("name") == name]
        if not matches or any(a.get("expired") is not False for a in matches):
            raise ValueError
        values = []
        for match in matches:
            if type(match.get("id")) is not int or match["id"] <= 0:
                raise ValueError
            archive = self._get(f"actions/artifacts/{match['id']}/zip", raw=True)
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                entries = zipped.infolist()
                if (len(entries) != 1 or entries[0].filename != filename
                        or entries[0].file_size > limit or entries[0].flag_bits & 1):
                    raise ValueError
                values.append(self._json(zipped.read(entries[0])))
            _http_remaining(self._deadline)
        return values

    def _restored(self, run: dict, attempt: int):
        import re

        from delivery_toolchain.release import automatic_dev as auto
        values = self._archive(run, name=f"{auto.RESTORE_ARTIFACT}-{attempt}",
                               filename=auto.RESTORE_FILE, limit=100_000)
        if len(values) != 1:
            raise ValueError
        receipt = values[0]
        title = run["display_title"].split()
        live = receipt.get("live_release")
        if (receipt.get("kind") != auto.RESTORE_KIND or receipt.get("environment") != "dev"
                or type(receipt.get("scheduler_baseline_sha256")) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", receipt["scheduler_baseline_sha256"])
                or len(title) != 5 or receipt.get("failed_candidate_sha") != title[4]
                or str(receipt.get("run_id")) != str(run["id"])
                or str(receipt.get("run_attempt")) != str(attempt)
                or not (live is None or (type(live) is str and re.fullmatch(r"[0-9a-f]{16}", live)
                                        and live != title[4][:16]))):
            raise ValueError
        return live

    def _previous(self):
        from delivery_toolchain.release import automatic_dev as auto
        from shared.identity.dev_smoke_journal import _SHA, REPOSITORY
        events, seen, total = [], set(), None
        for page in range(1, 11):
            result = self._get("actions/workflows/deploy-dev.yml/runs?event=workflow_dispatch"
                               f"&per_page=100&page={page}")
            runs = result["workflow_runs"]
            count = result.get("total_count")
            if (type(count) is not int or count < 0 or (total is not None and count != total)):
                raise ValueError
            total = count
            for run in runs:
                if type(run) is not dict or type(run.get("id")) is not int or run["id"] in seen:
                    raise ValueError
                seen.add(run["id"])
                if (run.get("path") != auto.WORKFLOW or run.get("event") != "workflow_dispatch"
                        or run.get("repository", {}).get("full_name") != REPOSITORY
                        or run.get("head_repository", {}).get("full_name") != REPOSITORY):
                    continue
                title = str(run.get("display_title", "")).split()
                if title[:3] != ["Runtime", "Release", "dev"]:
                    continue
                if (type(run.get("id")) is not int or run["id"] <= 0
                        or type(run.get("run_attempt")) is not int or not 1 <= run["run_attempt"] <= 100
                        or type(run.get("head_sha")) is not str or not _SHA.fullmatch(run["head_sha"])):
                    raise ValueError
                attempts = []
                for attempt in range(run["run_attempt"], 0, -1):
                    jobs = self._get(f"actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100")["jobs"]
                    selected = [j for j in jobs if j.get("name") == auto.DEPLOY_JOB]
                    if len(selected) > 1:
                        raise ValueError
                    if not selected or selected[0].get("conclusion") == "skipped":
                        continue
                    job = selected[0]
                    if job.get("conclusion") == "success":
                        kind, at = "complete", (job.get("completed_at") or job.get("started_at")
                                                or run.get("updated_at") or run.get("created_at"))
                    else:
                        kind, at = auto.live_step_outcome(job)
                        if kind == "not_started":
                            continue
                    restored = self._restored(run, attempt) if kind == "readback" else None
                    kind = "restored" if kind == "readback" else kind
                    when = GitHubSourceApprovalObserver._past(at, datetime.now(UTC))
                    attempts.append((when, run["id"], attempt, kind, restored, run, title))
                if attempts and attempts[0][3] == "complete" and not self._environment(run):
                    raise ValueError
                events.extend(attempts)
            if len(runs) < 100:
                break
        else:
            raise ValueError
        if len(seen) != total or not events:
            raise ValueError  # incomplete history/no current release, NOT first-release authority
        events.sort(key=lambda item: item[:3], reverse=True)
        if len({item[:3] for item in events}) != len(events):
            raise ValueError
        newest = events[0]
        if newest[3] == "restored":
            if newest[4] is None:
                raise ValueError
            newest = next(item for item in events[1:] if item[3] == "complete"
                          and len(item[6]) == 5 and item[6][4][:16] == newest[4])
            if not self._environment(newest[5]):
                raise ValueError
        elif newest[3] != "complete":
            raise ValueError  # failed-after-commit/uncertain NEVER selects older success
        return newest[5], newest[6], newest[2]

    def _validate_manifest(self, manifest: dict, *, release_sha: str, manifest_digest: str) -> None:
        """Canonical predicates invoke Git internally: isolate NON-SECRET work.

        Fixed command/clean source belongs to the independently reviewed checkout.
        No token/cookie/password, inherited env, gh, observer callback or arbitrary
        validator script. Missing candidate object refuses BEFORE canonical code
        could fetch; group deadline/cancel/reap applies to nested Git reads too.
        """
        import sys
        code = (
            "import json, subprocess, sys\n"
            "from delivery_toolchain.release.release_manifest import "
            "validate_manifest, validate_release_admission, manifest_release_profile\n"
            "m=json.loads(sys.stdin.buffer.read(2000001))\n"
            "sha,digest=sys.argv[1:]\n"
            "subprocess.run(['/usr/bin/git','cat-file','-e',sha+'^{commit}'],check=True,"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            "errors=validate_manifest(m,expected_candidate_sha=sha,expected_digest=digest)\n"
            "errors+=validate_release_admission(m,environment='dev')\n"
            "sys.exit(1 if errors or manifest_release_profile(m)!='dev-admin' or "
            "m.get('external_sources_expected_enabled')!=[] else 0)\n"
        )
        _bounded_observer_child((sys.executable, "-c", code, release_sha, manifest_digest),
            cwd=Path(__file__).resolve().parents[2], deadline=self._deadline,
            input_bytes=json.dumps(manifest, separators=(",", ":"), allow_nan=False).encode())

    def observe(self, *, release_sha: str, manifest_digest: str, deadline: float) -> dict:
        from delivery_toolchain.release import automatic_dev as auto
        from shared.identity.dev_smoke_journal import _DIGEST, _SHA
        try:
            if (self._stopped or self._deadline is not None or os.getpid() != self._pid
                    or os.environ.get("GITHUB_RUN_ID") or type(release_sha) is not str
                    or not _SHA.fullmatch(release_sha) or type(manifest_digest) is not str
                    or not _DIGEST.fullmatch(manifest_digest) or type(deadline) not in (int, float)
                    or not math.isfinite(deadline) or not 0 < deadline - time.monotonic() <= 2400):
                raise ValueError
            self._deadline, self._bytes, self._requests = deadline, 0, 0
            with _absolute_observer_deadline(deadline):
                run, title, attempt = self._previous()
                manifests = self._archive(run, name=auto.DEPLOYED_ARTIFACT,
                                          filename="RELEASE_MANIFEST.json", limit=2_000_000)
                manifest = manifests[0]
                if (any(other != manifest for other in manifests[1:]) or len(title) != 5
                        or title[3] not in {"deploy", "auto"} or title[4] != manifest.get("candidate_sha")):
                    raise ValueError
                self._validate_manifest(manifest, release_sha=release_sha, manifest_digest=manifest_digest)
            return {"stage": "current-dev-admission-observed", "release_sha": release_sha,
                    "manifest_digest": manifest_digest, "run_id": run["id"], "run_attempt": attempt,
                    "execution_authorized": False, "credential_binding_verified": False,
                    "secret_values_redacted": True}
        except Exception:
            self._stopped = True
            raise RecoveryRefused("RECOVERY_DEV_ADMISSION_UNVERIFIED") from None
        finally:
            self._deadline = None

    def close(self) -> None:
        self._stopped = True
        self._client.close()
        self._download.close()


class RecoveryHTTPRefused(RecoveryRefused):
    """Only original HTTP status, if received, and fail-stop classification.

    An HTTP error/malformed response to POST is UNKNOWN, not rollback evidence.
    No upstream body, cookie, capability or exception text belongs in receipts.
    """

    def __init__(self, *, outcome: str, http_status: int | None, cookie: str = "") -> None:
        super().__init__("RECOVERY_HTTPS_ATTEMPT_REFUSED")
        self.outcome, self.http_status = outcome, http_status
        # Only a newly issued, validated own cookie, retained for cleanup even
        # when the login body is lost. Never a receipt/report/serialization field.
        self._cookie = cookie

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_HTTP_SERIALIZATION_REFUSED")


@dataclass(frozen=True)
class RecoveryHTTPResult:
    """Private in-process response, NOT an audit/phase receipt or permission.

    May contain the once-issued capability. No pickle/report/child transport.
    Caller must validate action-specific lineage before recording completion.
    """

    http_status: int
    payload: dict = field(repr=False)
    cookie: str = field(default="", repr=False)

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

    def __init__(self, sock, deadline: float, max_bytes: int = 65536) -> None:
        self._sock, self._deadline, self._size = sock, deadline, 0
        self._max_bytes = max_bytes
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
        if self._size > self._max_bytes:
            raise ValueError
        return size


class _DeadlineSocket:
    def __init__(self, sock, deadline: float, max_bytes: int = 65536) -> None:
        self._sock, self._deadline = sock, deadline
        self._max_bytes = max_bytes

    def sendall(self, data) -> None:
        # socket.sendall's timeout bounds the entire send, not each fragment.
        self._sock.settimeout(_http_remaining(self._deadline))
        self._sock.sendall(data)

    def makefile(self, mode: str):
        if mode != "rb":
            raise ValueError
        return io.BufferedReader(_DeadlineReader(self._sock, self._deadline, self._max_bytes))

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
        return self._execute_request(method=method, cookie=cookie, deadline=deadline,
                                     body=body, path=self.PATH, statuses={200, 201})

    def _execute_request(self, *, method: str, cookie: str, deadline: float,
                         body: dict | None, path: str, statuses: set[int],
                         cookie_mode: str = "none") -> RecoveryHTTPResult:
        # Private common framing engine, not an arbitrary-path public API.
        import http.client
        import socket

        if self._used:
            raise RecoveryHTTPRefused(outcome="refused", http_status=None)
        self._used = True  # even validation/connect failure cannot be retried
        status = None
        raw = tls = connection = response = None
        submitted = False
        issued_cookie = ""
        try:
            if (method not in {"GET", "POST"} or type(cookie) is not str
                    or not (0 <= len(cookie) <= 8192 if cookie_mode == "login" else 0 < len(cookie) <= 8192)
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
            headers = {"Accept": "application/json", "Origin": self._origin, "Connection": "close"}
            if cookie:
                headers["Cookie"] = f"__Host-oday_web_session={cookie}"
            if encoded is not None:
                headers["Content-Type"] = "application/json"
            submitted = True  # a partial send may have committed; never reconstruct
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            status = response.status
            # Parse the cookie independently BEFORE body/framing validation, so
            # a real own session with a lost body remains eligible for cleanup.
            cookies = response.headers.get_all("Set-Cookie", [])
            if cookie_mode == "login" and status == 200:
                from http.cookies import SimpleCookie
                if len(cookies) != 1 or len(cookies[0]) > 9000:
                    raise ValueError
                parts = cookies[0].split(";")
                keys = [part.strip().split("=", 1)[0].lower() for part in parts]
                if (keys[0] != "__host-oday_web_session" or len(set(keys)) != len(keys)
                        or not set(keys[1:]) <= {"path", "secure", "httponly", "samesite", "max-age", "expires"}
                        or any(ord(c) < 32 or ord(c) >= 127 for c in cookies[0])):
                    raise ValueError
                parsed = SimpleCookie()
                parsed.load(cookies[0])
                if set(parsed) != {"__Host-oday_web_session"}:
                    raise ValueError
                item = parsed["__Host-oday_web_session"]
                value = item.value
                if (not item["secure"] or not item["httponly"] or item["path"] != "/"
                        or item["domain"] or item["samesite"].lower() != "lax"
                        or not 0 < len(value) <= 8192
                        or any(ord(c) <= 32 or ord(c) >= 127 or c in ';"\\,' for c in value)):
                    raise ValueError
                issued_cookie = value
            elif cookies and cookie_mode != "logout":
                raise ValueError  # no session replacement during own GETs
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
                    or status not in statuses):
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
            return RecoveryHTTPResult(status, payload, issued_cookie)
        except Exception:
            raise RecoveryHTTPRefused(outcome="unknown" if submitted and method == "POST" else "refused",
                                      http_status=status, cookie=issued_cookie) from None
        finally:
            for resource in (response, connection, tls, raw):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass  # no cleanup exception/body may escape redaction


class RecoveryObservationHTTPSAttempt(RecoveryHTTPSAttempt):
    """One fixed read-only Web/BFF observation; bounded, no pagination/retry.

    Complete bounded user/tenant-audit inventories only. Over-limit history is
    a pre-effect blocker, not permission to filter away provenance/uncertainty.
    Uses the same no-store/duplicate-key/TLS/absolute deadline transport.
    """

    def observe(self, operation: str, *, cookie: str, deadline: float) -> RecoveryHTTPResult:
        paths = {
            "release": "/api/v1/platform/release-identity",
            "users": "/api/v1/operator/users",
            "audit": "/api/v1/operator/users/audit-trail",
            "old-journal": "/api/v1/operator/users/dev-smoke-journal",
            "recovery": self.PATH,
        }
        if operation not in paths:
            self._used = True
            raise RecoveryHTTPRefused(outcome="refused", http_status=None)
        return self._execute_request(method="GET", cookie=cookie, deadline=deadline,
                                     body=None, path=paths[operation], statuses={200})


def _validate_account_observations(payloads: dict, *, plan: dict, actions) -> tuple[dict, dict]:
    """Private predicate over freshly authenticated observations, NOT receipts.

    Never exports account/audit contents or credentials. Server domain checks
    private credential preservation; public snapshot equality cannot prove it.
    """
    from shared.identity import dev_smoke_recovery as domain
    from shared.identity.dev_smoke_journal import (
        _DIGEST,
        _SHA,
        PRESERVED_ACCOUNT_ID,
        DevSmokeBindingJournal,
        ProvisioningJournal,
    )
    from shared.identity.invitation_provenance import invitation_provenance

    identity = payloads["release"]
    expected = {
        "release_sha": plan["release_sha"], "web_release_sha": plan["release_sha"],
        "manifest_digest": plan["manifest_digest"], "web_manifest_digest": plan["manifest_digest"],
        "release_profile": "dev-admin", "web_release_profile": "dev-admin",
    }
    if identity.get("release_profile_valid") is not True or any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError

    def inventory(operation: str, key: str) -> list:
        payload = payloads[operation]
        records = payload.get(key)
        if (set(payload) != {key, "count", "correlation_id"} or type(records) is not list
                or any(type(r) is not dict for r in records) or type(payload["count"]) is not int
                or payload["count"] != len(records)):
            raise ValueError
        return records

    users, events = inventory("users", "users"), inventory("audit", "events")
    if (any(not _uuid(r.get("subject_id")) for r in users)
            or len({r["subject_id"] for r in users}) != len(users)
            or any(not _uuid(e.get("event_id")) for e in events)
            or len({e["event_id"] for e in events}) != len(events)):
        raise ValueError
    by_id = {r["subject_id"]: r for r in users}
    original, target = by_id[PRESERVED_ACCOUNT_ID], by_id[TARGET_ACCOUNT_ID]
    fixed_scope = {"tenant_id": TENANT_ID, "clearance": "CONFIDENTIAL", **{
        axis: [] for axis in ("brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules")
    }}
    for record, username, roles in ((original, "ajoe734", ["auditor", "platform_admin"]),
                                    (target, "odp-dev-smoke", ["platform_admin"])):
        if (record.get("username") != username or record.get("status") != "active"
                or record.get("roles") != roles or record.get("scope") != fixed_scope
                or record.get("attributes", {}).get("identity_source") != "identity.accounts"
                or type(record.get("email")) is not str or not record["email"]
                or type(record.get("name")) is not str or not record["name"]):
            raise ValueError
    if invitation_provenance(target, events) != {
        "invitation_id": domain.INVITATION_ID, "issue_event_id": domain.ISSUE_EVENT_ID,
        "accept_event_id": domain.ACCEPT_EVENT_ID, "issuer_account_id": PRESERVED_ACCOUNT_ID,
    }:
        raise ValueError

    def group(kind, correlation, actor, action, keys, stages):
        found = [e for e in events if e.get("event_type") == kind or e.get("correlation_id") == correlation]
        found.sort(key=lambda e: datetime.fromisoformat(e["timestamp"]))
        if len(found) != len(stages):
            raise ValueError
        for event, stage in zip(found, stages, strict=True):
            meta = event["metadata"]
            at = datetime.fromisoformat(event["timestamp"])
            if (event.get("event_type") != kind or event.get("correlation_id") != correlation
                    or event.get("resource") != correlation or event.get("actor") != actor
                    or event.get("action") != action or event.get("outcome") != "success"
                    or at.tzinfo is None or at.utcoffset() is None or at > datetime.now(UTC)
                    or type(meta) is not dict or set(meta) != keys or meta["stage"] != stage
                    or meta["tenant_id"] != TENANT_ID or meta["execution_authorized"] is not False
                    or meta["secret_values_redacted"] is not True):
                raise ValueError
        return found

    old = group(ProvisioningJournal._TYPE, ProvisioningJournal._CORRELATION, ProvisioningJournal._ACTOR,
                "DEV_SMOKE_RESERVATION", ProvisioningJournal._KEYS, ["reserved", "recovery-required"])
    base = old[0]["metadata"]
    if (old[1]["event_id"] != domain.OLD_QUARANTINE_EVENT_ID
            or base["authorization_id"] != ProvisioningJournal._CORRELATION.split(":", 1)[1]
            or base["execution_id"] != domain.OLD_EXECUTION_ID or base["plan_digest"] != domain.OLD_PLAN_DIGEST
            or not _SHA.fullmatch(base["release_sha"]) or not _DIGEST.fullmatch(base["manifest_digest"])
            or old[1]["metadata"] != {**base, "stage": "recovery-required"}
            or any(e.get("event_type") == DevSmokeBindingJournal._TYPE
                   or e.get("correlation_id") == DevSmokeBindingJournal._CORRELATION for e in events)):
        raise ValueError
    journal = payloads["old-journal"]
    root = journal.get("reservation")
    if (set(journal) != {"reservation", "binding", "execution_authorized", "secret_values_redacted"}
            or journal["binding"] is not None or journal["execution_authorized"] is not False
            or journal["secret_values_redacted"] is not True
            or root != {"execution_id": domain.OLD_EXECUTION_ID, "plan_digest": domain.OLD_PLAN_DIGEST,
                        "event_id": domain.OLD_QUARANTINE_EVENT_ID, "stage": "recovery-required",
                        "authorization_id": base["authorization_id"], "execution_authorized": False,
                        "secret_values_redacted": True}
            or root["execution_authorized"] is not False or root["secret_values_redacted"] is not True):
        raise ValueError
    historical = [old[0], next(e for e in events if e["event_id"] == domain.ISSUE_EVENT_ID),
                  next(e for e in events if e["event_id"] == domain.ACCEPT_EVENT_ID), old[1]]
    historical_times = [datetime.fromisoformat(e["timestamp"]) for e in historical]
    if not all(a <= b for a, b in zip(historical_times, historical_times[1:], strict=False)):
        raise ValueError
    current = payloads["recovery"]
    if (set(current) != {"reservation", "binding", "execution_authorized", "secret_values_redacted"}
            or current["binding"] is not None or current["execution_authorized"] is not False
            or current["secret_values_redacted"] is not True):
        raise ValueError
    if actions is None:
        if current["reservation"] is not None or any(
            e.get("event_type") in {domain.RecoveryJournal._TYPE, domain.RecoveryCapabilityService._CAP_TYPE,
                                   domain.RECOVERY_BINDING_TYPE}
            or e.get("correlation_id") in {domain.RecoveryJournal._CORRELATION,
                domain.RecoveryCapabilityService._CAP_CORRELATION, domain.RECOVERY_BINDING_CORRELATION} for e in events
        ):
            raise ValueError  # ANY prior pending/unknown execution bars a fresh one
    else:
        if current["reservation"] != actions._root:
            raise ValueError
        roots = group(domain.RecoveryJournal._TYPE, domain.RecoveryJournal._CORRELATION,
                PRESERVED_ACCOUNT_ID, domain.RecoveryJournal._ACTION, domain.RecoveryJournal._KEYS, ["reserved"])
        meta = roots[0]["metadata"]
        if (roots[0]["event_id"] != actions._root["event_id"] or meta["plan_digest"] != actions._digest
                or any(meta[k] != v for k, v in plan.items()) or not _uuid(meta["issuer_session_id"])
                or meta["credential_binding_verified"] is not False):
            raise ValueError
        import re
        if (validate_recovery_plan(plan, release_sha=plan["release_sha"], manifest_digest=plan["manifest_digest"],
                now=datetime.fromisoformat(roots[0]["timestamp"])) != actions._digest
                or any(type(meta[k]) is not str or not _DIGEST.fullmatch(meta[k])
                       for k in ("original_identity_digest", "target_identity_digest"))
                or any(type(meta[k]) is not str or not re.fullmatch(r"[1-9][0-9]{0,18}", meta[k])
                       for k in ("original_credential_revision", "target_credential_revision"))
                or any(datetime.fromisoformat(meta[k]).tzinfo is None for k in (
                    "original_credential_updated_at", "target_credential_updated_at"))):
            raise ValueError
        caps = group(domain.RecoveryCapabilityService._CAP_TYPE, domain.RecoveryCapabilityService._CAP_CORRELATION,
                PRESERVED_ACCOUNT_ID, "DEV_SMOKE_RECOVERY_CAPABILITY", domain.RecoveryCapabilityService._CAP_KEYS,
                ["issued", "rotated"])
        issued, rotated = caps
        expires = datetime.fromisoformat(issued["metadata"]["capability_expires_at"])
        issue_time = datetime.fromisoformat(issued["timestamp"])
        count = rotated["metadata"]["revoked_target_sessions"]
        cap_base = {**plan, "plan_digest": actions._digest, "root_event_id": roots[0]["event_id"],
                    "issuer_session_id": meta["issuer_session_id"], "capability_id": actions._capability_id,
                    "capability_expires_at": issued["metadata"]["capability_expires_at"], "issue_event_id": "",
                    "revoked_target_sessions": 0, "stage": "issued", "execution_authorized": False,
                    "credential_binding_verified": False, "secret_values_redacted": True}
        if (issued["event_id"] != actions._issue_event_id or rotated["event_id"] != actions._rotation_event_id
                or issued["metadata"] != cap_base or issued["metadata"]["credential_binding_verified"] is not False
                or type(issued["metadata"]["revoked_target_sessions"]) is not int
                or rotated["metadata"] != {**cap_base, "stage": "rotated", "issue_event_id": issued["event_id"],
                                          "revoked_target_sessions": count}
                or rotated["metadata"]["credential_binding_verified"] is not False
                or type(count) is not int or not 0 <= count < 2**63
                or not 0 < (expires - issue_time).total_seconds() <= 600
                or not datetime.fromisoformat(rotated["timestamp"]) < expires <= datetime.fromisoformat(plan["expires_at"])
                or any(e.get("event_type") == domain.RECOVERY_BINDING_TYPE
                       or e.get("correlation_id") == domain.RECOVERY_BINDING_CORRELATION for e in events)):
            raise ValueError
        ordered = [old[0], next(e for e in events if e["event_id"] == domain.ISSUE_EVENT_ID),
                   next(e for e in events if e["event_id"] == domain.ACCEPT_EVENT_ID), old[1], roots[0], *caps]
        times = [datetime.fromisoformat(e["timestamp"]) for e in ordered]
        if not all(a <= b for a, b in zip(times, times[1:], strict=False)):
            raise ValueError
    # Keep safe public identity facts in parent memory ONLY; never serialize the
    # returned private snapshot. Compare ALL accounts, not only the target.
    keys = ("subject_id", "username", "email", "name", "roles", "scope", "status", "updated_by", "attributes")
    snapshot = {sid: {key: record[key] for key in keys} for sid, record in by_id.items()}
    return snapshot, {"stage": "account-preflight-observed" if actions is None else "rotation-reconciled",
        "execution_id": plan["execution_id"], "release_sha": plan["release_sha"],
        "manifest_digest": plan["manifest_digest"], "old_quarantine_event_id": domain.OLD_QUARANTINE_EVENT_ID,
        "original_incident_session_cleanup": "unknown", "execution_authorized": False,
        "credential_binding_verified": False, "secret_values_redacted": True}


class OriginalPasswordMemory:
    """Already-approved foreground memory input, NOT custody approval/ingress.

    Never loads from environment, argv, files or a worker handoff. The trusted
    foreground still owns authorization and OS custody; reference release is
    not zeroization. Fork/pickle/reuse after close refuse without echoing input.
    """

    __slots__ = ("_pid", "_password", "_closed")

    def __init__(self, password: str) -> None:
        if type(password) is not str or not 0 < len(password) <= 1024:
            raise RecoveryRefused("RECOVERY_ORIGINAL_MEMORY_INVALID")
        self._pid, self._password, self._closed = os.getpid(), password, False

    def __repr__(self) -> str:
        return "OriginalPasswordMemory(<redacted>)"

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_CUSTODY_SERIALIZATION_REFUSED")

    @property
    def password(self) -> str:
        if self._closed or os.getpid() != self._pid:
            raise RecoveryRefused("RECOVERY_CUSTODY_LOST")
        return self._password

    def close(self) -> None:
        self._password, self._closed = "", True


class OwnedSessionHTTPSAttempt(RecoveryHTTPSAttempt):
    """Fixed Web login/session/principal/logout only; one request, no retries.

    Uses the SAME absolute TLS/framing engine, but cannot change the recovery
    route's required cookie/status predicates. No supplied path/actor/tenant.
    """

    def execute_session(self, operation: str, *, cookie: str, deadline: float,
                        username: str = "", password: str = "") -> RecoveryHTTPResult:
        try:
            if (operation not in {"login", "session", "principal", "logout", "revoked"}
                    or (operation == "login" and (cookie != "" or username not in {"ajoe734", "odp-dev-smoke"}
                        or type(password) is not str or not 0 < len(password) <= 1024))
                    or (operation != "login" and (username or password))):
                raise ValueError
            path = {"login": "/login", "session": "/auth/session", "principal": "/api/v1/auth/principal",
                    "logout": "/auth/logout", "revoked": "/auth/session"}[operation]
            method = "POST" if operation in {"login", "logout"} else "GET"
            body = ({"username": username, "password": password, "returnTo": "/operator?view=admin"}
                    if operation == "login" else {} if operation == "logout" else None)
            return self._execute_request(method=method, cookie=cookie, deadline=deadline, body=body,
                    path=path, statuses={401} if operation == "revoked" else {200},
                    cookie_mode=operation if operation in {"login", "logout"} else "none")
        except RecoveryHTTPRefused:
            raise
        except Exception:
            self._used = True
            raise RecoveryHTTPRefused(outcome="refused", http_status=None) from None


class ForegroundOwnedWebSessions:
    """Own only two fresh sessions created here, not pre-existing/incident ones.

    One original login during preflight, one target login during reconciliation.
    Each login/own-logout has an fsynced sub-operation start/terminal inside the
    coordinator's active phase. Missing login reply means UNKNOWN even if no
    cookie was recovered; it never permits another login or blanket revocation.
    A valid newly issued cookie survives malformed/lost body for cleanup only.
    No credentials are accepted from a plan or copied to children/environment.

    This is a session primitive, NOT full source/admission/account/provenance
    preflight, gate, writer exclusion, fresh-job dispatch or authority. Caller
    must quarantine BEFORE own cleanup, while the original cookie is retained.
    """

    def __init__(self, *, original: OriginalPasswordMemory, target: ParentMemoryCustody,
                 budget: ForegroundRecoveryBudget, origin: str, address: str,
                 ca_pem: str | None = None) -> None:
        try:
            if (type(original) is not OriginalPasswordMemory or type(target) is not ParentMemoryCustody
                    or type(budget) is not ForegroundRecoveryBudget or budget._stopped):
                raise ValueError
            if original.password == target.password:
                raise ValueError
            OwnedSessionHTTPSAttempt(origin=origin, address=address, ca_pem=ca_pem)
        except Exception:
            raise RecoveryRefused("RECOVERY_OWNED_SESSION_CONFIG_INVALID") from None
        self._pid, self._original, self._target, self._budget = os.getpid(), original, target, budget
        self._origin, self._address, self._ca = origin, address, ca_pem
        self._cookies = {"original": "", "target": ""}
        self._verified: set[str] = set()
        self._attempted: set[str] = set()
        self._stopped = self._closed = self._actions_created = False
        self._unknown_login = False
        self._baseline = None
        self._observation_plan = None

    def __repr__(self) -> str:
        return "ForegroundOwnedWebSessions(<redacted>)"

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_CUSTODY_SERIALIZATION_REFUSED")

    def _request(self, operation: str, *, cookie: str = "", username: str = "", password: str = ""):
        attempt = OwnedSessionHTTPSAttempt(origin=self._origin, address=self._address, ca_pem=self._ca)
        return attempt.execute_session(operation, cookie=cookie, deadline=self._budget.call_deadline,
                                       username=username, password=password)

    def _operation(self, role: str, action: str, perform) -> None:
        operation = f"{role}-{action}"
        started, terminal = False, False
        status, outcome = None, "refused"
        phase = "cleanup" if action == "logout" else "preflight" if role == "original" else "reconcile"
        try:
            if (self._closed or os.getpid() != self._pid or operation in self._attempted
                    or (self._stopped and action != "logout") or self._budget._active is None
                    or self._budget._active[0] != phase):
                raise ValueError
            self._attempted.add(operation)
            elapsed, remaining = self._budget._time()
            self._budget._receipts.append(phase=phase, operation=operation, stage="start",
                                         outcome="pending", elapsed=elapsed, remaining=remaining)
            started = True
            status = perform()
            _http_remaining(self._budget.call_deadline)
            elapsed, remaining = self._budget._time()
            self._budget._receipts.append(phase=phase, operation=operation, stage="terminal",
                    outcome="completed", elapsed=elapsed, remaining=remaining, http_status=status)
            terminal = True
            return
        except Exception as exc:
            self._stopped = self._budget._stopped = True
            if isinstance(exc, RecoveryHTTPRefused):
                status, outcome = exc.http_status, exc.outcome
                if action == "login" and exc._cookie:
                    self._cookies[role] = exc._cookie  # cleanup ONLY, never validated login
            elif started:
                outcome = "unknown"
            if action == "login" and started:
                self._unknown_login = True
                if self._cookies[role]:
                    outcome = "unknown"  # failed GET cannot relabel created session as no effect
            if started and not terminal:
                try:
                    elapsed, remaining = self._budget._time()
                    self._budget._receipts.append(phase=phase, operation=operation, stage="terminal",
                            outcome=outcome, elapsed=elapsed, remaining=remaining, http_status=status)
                except Exception:
                    outcome = "unknown"
            if action in {"login", "observe"} and self._budget._active is not None:
                try:
                    self._budget.finish(outcome="unknown", http_status=status)
                except Exception:
                    pass  # local no-retry barrier remains; persistence is unknown
            raise RecoveryActionRefused(outcome=outcome, http_status=status) from None

    def login(self, role: str) -> None:
        if role not in {"original", "target"}:
            self._stopped = self._budget._stopped = True
            raise RecoveryActionRefused(outcome="refused", http_status=None)

        def perform():
            from shared.identity.dev_smoke_journal import PRESERVED_ACCOUNT_ID
            username = "ajoe734" if role == "original" else "odp-dev-smoke"
            password = self._original.password if role == "original" else self._target.password
            result = self._request("login", username=username, password=password)
            if result.cookie:
                self._cookies[role] = result.cookie  # retain BEFORE any further validation
            if (not result.cookie or set(result.payload) != {"ok", "subject", "returnTo"}
                    or result.payload != {"ok": True, "subject": username, "returnTo": "/operator?view=admin"}
                    or result.payload["ok"] is not True):
                raise ValueError
            session = self._request("session", cookie=result.cookie).payload
            if (set(session) != {"subject", "expiresAt"} or session["subject"] != username
                    or type(session["expiresAt"]) is not int or session["expiresAt"] <= datetime.now(UTC).timestamp()):
                raise ValueError
            principal = self._request("principal", cookie=result.cookie).payload
            roles = ["auditor", "platform_admin"] if role == "original" else ["platform_admin"]
            if (principal.get("account_id") != (PRESERVED_ACCOUNT_ID if role == "original" else TARGET_ACCOUNT_ID)
                    or principal.get("tenant_id") != TENANT_ID or type(principal.get("roles")) is not list
                    or any(type(r) is not str for r in principal["roles"]) or sorted(principal["roles"]) != roles):
                raise ValueError
            return 200

        self._operation(role, "login", perform)
        self._verified.add(role)  # never usable BEFORE durable terminal

    def observe_accounts(self, plan: dict[str, str], *, actions=None) -> dict:
        """Fresh public preservation/lineage reads; NOT source/admission authority.

        Preflight owns the before-state, reconciliation compares it after the
        SAME-pair target login. All reads use the retained original session. No
        caller baseline, cookies, passing receipt or unknown-effect resume.
        Domain binding separately fences non-public credential revisions/times.
        """
        from copy import deepcopy
        role = "original" if actions is None else "target"
        safe = {}
        baseline = None

        def perform():
            nonlocal safe, baseline
            if ("original" not in self._verified or (role == "target" and "target" not in self._verified)
                    or (role == "original" and self._baseline is not None)
                    or (role == "target" and (self._baseline is None or plan != self._observation_plan
                        or type(actions) is not SameSessionRecoveryActions or actions._stopped
                        or actions._cookie != self._cookies["original"] or actions._plan != plan
                        or actions._budget is not self._budget or actions._custody is not self._target
                        or not actions._rotation_event_id))):
                raise ValueError
            validate_recovery_plan(plan, release_sha=plan.get("release_sha"),
                    manifest_digest=plan.get("manifest_digest"), now=datetime.now(UTC))
            payloads = {}
            for operation in ("release", "users", "audit", "old-journal", "recovery"):
                attempt = RecoveryObservationHTTPSAttempt(origin=self._origin, address=self._address, ca_pem=self._ca)
                payloads[operation] = attempt.observe(operation, cookie=self._cookies["original"],
                                                deadline=self._budget.call_deadline).payload
            baseline, safe = _validate_account_observations(payloads, plan=plan, actions=actions)
            if role == "target" and baseline != self._baseline:
                raise ValueError
            return 200

        self._operation(role, "observe", perform)
        if role == "original":
            self._baseline, self._observation_plan = deepcopy(baseline), deepcopy(plan)
        return safe  # only after durable terminal; never includes inventory/audit

    def recovery_actions(self, plan: dict[str, str]):
        # Primitive composition only, not proof that full preflight was performed.
        if (self._closed or self._stopped or os.getpid() != self._pid or self._actions_created
                or "original" not in self._verified):
            raise RecoveryRefused("RECOVERY_OWNED_SESSION_UNAVAILABLE")
        self._actions_created = True
        _ = self._original.password  # retained foreground custody must still be live
        return SameSessionRecoveryActions(plan, budget=self._budget, custody=self._target,
                origin=self._origin, address=self._address, ca_pem=self._ca, cookie=self._cookies["original"])

    def cleanup(self, *, gate_sessions=None) -> dict:
        # One aggregate reserved cleanup phase; each owned logout still gets its
        # own durable start. A missing cookie cannot establish cleanup of an
        # unknown login. Keep trying only OTHER known own cookies, never retry.
        if self._closed or os.getpid() != self._pid:
            raise RecoveryRefused("RECOVERY_OWNED_SESSION_UNAVAILABLE")
        if gate_sessions is not None and (type(gate_sessions) is not ForegroundSamePairGate
                or gate_sessions._sessions is not self or gate_sessions._budget is not self._budget):
            raise RecoveryRefused("RECOVERY_GATE_OWNER_MISMATCH")
        self._budget.begin("cleanup")
        failed = self._unknown_login
        try:
            if gate_sessions is not None:
                failed = not gate_sessions._cleanup_known_sessions() or failed
            for role in ("target", "original"):
                cookie = self._cookies[role]
                if not cookie:
                    continue
                def perform(cookie=cookie):
                    result = self._request("logout", cookie=cookie)
                    if result.payload.get("ok") is not True:
                        raise ValueError
                    revoked = self._request("revoked", cookie=cookie)  # ORIGINAL cookie, not the deletion value
                    if revoked.payload.get("error", {}).get("code") != "WEB_SESSION_REQUIRED":
                        raise ValueError
                    return 401
                try:
                    self._operation(role, "logout", perform)
                except RecoveryActionRefused:
                    failed = True
            outcome = self._budget.finish(outcome="unknown" if failed else "completed")
        finally:
            self.close()
        if outcome != "completed":
            raise RecoveryActionRefused(outcome="unknown", http_status=None)
        return {"own_session_cleanup": "completed", "original_incident_session_cleanup": "unknown",
                "execution_authorized": False, "credential_binding_verified": False, "secret_values_redacted": True}

    def close(self) -> None:
        self._closed = self._stopped = True
        self._cookies = {"original": "", "target": ""}  # NOT logout/zeroization proof


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
        self._stopped = self._quarantined = self._acknowledged = False
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
        safe = self._call("ack", {"action": "binding-acknowledged", "reservation": dict(self._root),
                                 "intent_event_id": self._intent["audit_event_id"], "put_status": 201},
                          lambda payload: self._binding_reply(payload, stage="binding-acknowledged"))
        self._acknowledged = True
        return safe

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


class _ForegroundGateHTTPSClient:
    """Canonical gate protocol over pinned numeric TLS; no proxy/DNS/redirect.

    Only canonical dev-admin paths/methods are permitted. Application and private
    API transport credentials stay in this process; Web gets neither. Every read
    shares the owner's absolute phase deadline, bounded wire/body/request totals.
    This is NOT a standalone CLI, worker trigger or consumer of GitHub plaintext.
    """

    def __init__(self, owner, *, api: bool, origin: str, address: str,
                 ca_pem: str | None = None) -> None:
        self._owner, self._api = owner, api
        self._transport = RecoveryHTTPSAttempt(origin=origin, address=address, ca_pem=ca_pem)

    def request(self, method, path, *, authenticated=True, body=None, headers=None,
                follow_redirects=True):
        # No following at all, including same-origin redirects. The gate's form
        # journey verifies actual303+Location without forwarding a credential.
        return self._owner._request(self, method, path, authenticated=authenticated,
                                    body=body, headers=headers)

    def _roundtrip(self, method, path, *, authenticated, body, headers):
        import http.client
        import socket
        from urllib.parse import urlencode

        from delivery_toolchain.e2e import check_live_e2e_gate as gate

        response = connection = raw = tls = None
        owner, config, transport = self._owner, self._owner._config, self._transport
        try:
            path = gate._safe_request_path(path)
            if (method not in {"GET", "POST"} or (method == "GET" and body is not None)
                    or (method == "POST" and path not in {
                        "/api/v1/jobs", "/api/v1/operator/users", "/login", "/auth/logout"})
                    or (self._api and path in {"/login", "/auth/logout", "/api/v1/operator/users"})
                    or (not self._api and method == "POST" and path == "/api/v1/jobs")):
                raise ValueError
            request_headers = {"accept": "application/json", "x-correlation-id": owner._correlation,
                               "connection": "close"}
            supplied = {} if headers is None else dict(headers)
            if (any(type(k) is not str or k != k.lower() for k in supplied)
                    or set(supplied) - {"accept", "x-correlation-id", "origin", "cookie", "content-type",
                                       "idempotency-key", "x-operator-role"}
                    or any(type(v) is not str or len(v) > 9000 or any(ord(c) < 32 or ord(c) >= 127 for c in v)
                           for v in supplied.values())
                    or ("origin" in supplied and supplied["origin"] != config.web_url)
                    or ("cookie" in supplied and self._api)):
                raise ValueError
            request_headers.update(supplied)
            if self._api:
                request_headers["x-serverless-authorization"] = f"Bearer {config.api_transport_token}"
                if authenticated:
                    request_headers.update(authorization=f"Bearer {config.bearer_token}")
                    request_headers["x-operator-role"] = config.operator_role
                    request_headers["x-subject-id"] = config.operator_subject
                    if config.operator_tenant:
                        request_headers["x-tenant-id"] = config.operator_tenant
            encoded = None
            if body is not None:
                if type(body) is not dict:
                    raise ValueError
                if request_headers.get("content-type") == "application/x-www-form-urlencoded":
                    encoded = urlencode(body).encode()
                else:
                    encoded = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
                    request_headers["content-type"] = "application/json"
                if len(encoded) > 16384:
                    raise ValueError
            deadline = owner._budget.call_deadline
            raw = socket.socket(socket.AF_INET6 if transport._ipv6 else socket.AF_INET, socket.SOCK_STREAM)
            raw.settimeout(_http_remaining(deadline))
            raw.connect((transport._address, transport._port))
            raw.settimeout(_http_remaining(deadline))
            tls = transport._context.wrap_socket(raw, server_hostname=transport._host)
            connection = http.client.HTTPConnection(transport._host, transport._port)
            connection.auto_open = 0
            connection.sock = _DeadlineSocket(tls, deadline, 524288)
            connection.request(method, path, body=encoded, headers=request_headers)
            response = connection.getresponse()
            # Retain a bounded newly issued own cookie BEFORE body decoding.
            # An ambiguous/malformed login remains UNKNOWN, never a login retry.
            pairs = gate._set_cookie_pairs(response.headers)
            cookies = {name: value for name, value in pairs if name}
            if not self._api and path == "/login":
                if len(pairs) > 1:
                    # Multiple raw Set-Cookie fields: the name->value view is
                    # lossy. Retain EVERY distinct safely-known session value for
                    # once-only bounded cleanup, then stop as UNKNOWN.
                    for name, value in pairs:
                        if name == "__Host-oday_web_session" and value and value not in owner._cookies:
                            try:
                                owner._retain_cookies({name: value})
                            except ValueError:
                                pass
                    owner._unknown_login = True
                    raise ValueError
                owner._retain_cookies(cookies)
                if cookies:
                    from http.cookies import SimpleCookie
                    values = response.headers.get_all("Set-Cookie", [])
                    if len(values) != 1:
                        raise ValueError
                    parsed = SimpleCookie()
                    parsed.load(values[0])
                    item = parsed.get("__Host-oday_web_session")
                    if (set(parsed) != {"__Host-oday_web_session"} or item is None or not item["secure"]
                            or not item["httponly"] or item["path"] != "/" or item["domain"]
                            or item["samesite"].lower() != "lax"):
                        raise ValueError
            lengths = response.headers.get_all("Content-Length", [])
            transfers = response.headers.get_all("Transfer-Encoding", [])
            limit = 491520 if supplied.get("accept") == "text/html" else 49152
            if (response.headers.defects or len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers)
                    or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit()
                                     or int(lengths[0]) > limit))
                    or (transfers and transfers[0].lower() != "chunked")
                    or len(response.headers.get_all("Location", [])) > 1
                    or len(response.headers.get_all("Set-Cookie", [])) > 1):
                raise ValueError
            clear = response.read(limit + 1)
            owner._bytes += len(clear)
            byte_limit = 524288 if owner._budget._active[0] == "cleanup" else 4194304
            if (len(clear) > limit or owner._bytes > byte_limit
                    or (lengths and len(clear) != int(lengths[0])) or response.read(1)):
                raise ValueError
            payload = {}
            if clear and response.headers.get_content_type() == "application/json":
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
            elif clear and supplied.get("accept") != "text/html":
                raise ValueError
            _http_remaining(deadline)
            return gate.HttpResponse(response.status, payload, location=response.headers.get("Location", ""),
                                     cookies=cookies)
        except Exception:
            # Includes transport/header/body/timer failures. Do not return an
            # error response that would permit later probes after uncertainty.
            raise RecoveryRefused("RECOVERY_GATE_TRANSPORT_STOPPED") from None
        finally:
            for resource in (response, connection, tls, raw):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass


class ForegroundSamePairGate:
    """Full canonical in-parent gate and failure cleanup coordinator increment.

    This does NOT supply effect authority, writer exclusion, measured production
    bounds or normally authorized fresh-job dispatch. Construct/validate BEFORE
    reservation, then execute only after this process's real201/durable ACK. No
    supplied gate callback/report or passed boolean is accepted. Success proves
    same-pair gate only; decrypted binding/full-product acceptance stay false.

    Any failed/uncertain gate stops, attempts quarantine using the retained SAME
    original cookie BEFORE remaining own logouts, then cleans only known sessions
    once under the aggregate reserved cleanup phase. Unknown login/quarantine or
    logout cannot be relabelled successful. Old interrupted original cleanup stays
    unknown. The canonical default (non-recovery) gate remains unchanged.
    """

    def __init__(self, plan: dict, *, sessions: ForegroundOwnedWebSessions,
                 gate_config, api_address: str, api_ca_pem: str | None = None) -> None:
        from dataclasses import replace

        from delivery_toolchain.e2e import check_live_e2e_gate as gate
        try:
            if (type(sessions) is not ForegroundOwnedWebSessions or sessions._closed or sessions._stopped
                    or type(gate_config) is not gate.GateConfig or type(plan) is not dict
                    or gate_config.web_url != sessions._origin
                    or gate_config.expected_sha != plan["release_sha"]
                    or gate_config.expected_manifest_digest != plan["manifest_digest"]
                    or gate_config.expected_deployment != "dev" or gate_config.release_profile != "dev-admin"
                    or gate_config.allow_http is not False or gate_config.external_provider_mode != "disabled"
                    or gate_config.required_provider_ids != () or gate_config.operator_role != "platform_admin"
                    or any(getattr(gate_config, k) for k in (
                        "dev_admin_username", "dev_admin_password", "dev_admin_initial_password",
                        "bootstrap_admin_username", "bootstrap_admin_password", "dev_admin_bundle_account_id",
                        "dev_admin_bundle_tenant_id", "dev_admin_bundle_execution_id", "dev_admin_bundle_recovery_lineage"))
                    or gate_config.dev_admin_bundle_schema_version != 1):
                raise ValueError
            digest = validate_recovery_plan(plan, release_sha=plan["release_sha"],
                    manifest_digest=plan["manifest_digest"], now=datetime.now(UTC))
            if digest != sessions._budget._receipts._digest:
                raise ValueError
            checked = replace(gate_config, dev_admin_username="odp-dev-smoke",
                              dev_admin_password=sessions._target.password)
            if not all(c.ok is True for c in gate.validate_config(checked)):
                raise ValueError
            self._budget, self._sessions, self._plan = sessions._budget, sessions, dict(plan)
            self._config = gate_config
            self._api = _ForegroundGateHTTPSClient(self, api=True, origin=gate_config.api_url,
                                                   address=api_address, ca_pem=api_ca_pem)
            self._web = _ForegroundGateHTTPSClient(self, api=False, origin=sessions._origin,
                                                   address=sessions._address, ca_pem=sessions._ca)
        except Exception:
            raise RecoveryRefused("RECOVERY_GATE_CONFIG_INVALID") from None
        self._pid, self._used, self._stopped = os.getpid(), False, False
        self._correlation = f"corr-recovery-gate-{plan['execution_id']}"
        self._logins: set[str] = set()
        self._cookies: dict[str, dict] = {}
        self._unknown_login = False
        self._bytes = self._requests = 0
        self._cleanup_outcome = self._quarantine_outcome = "not-attempted"

    def __repr__(self) -> str:
        return "ForegroundSamePairGate(<redacted>)"

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_CUSTODY_SERIALIZATION_REFUSED")

    def _retain_cookies(self, cookies) -> None:
        for name, value in cookies.items():
            if (name != "__Host-oday_web_session" or type(value) is not str or not 0 < len(value) <= 8192
                    or any(ord(c) <= 32 or ord(c) >= 127 or c in ';"\\,' for c in value)
                    or value in self._cookies or len(self._cookies) >= 4):
                self._unknown_login = True
                raise ValueError
            self._cookies[value] = {"attempted": False, "acknowledged": False, "verified": False}

    def _login_reply_valid(self, operation: str, result) -> bool:
        """Exact canonical reply per gate login operation, else UNKNOWN.

        Positive replies must carry the ONE cookie retained from this reply;
        diagnostic replies must carry none. Status/body/location must match
        the canonical evaluator's own success predicate for that operation.
        """
        from urllib.parse import parse_qs, urlsplit

        from delivery_toolchain.e2e import check_live_e2e_gate as gate
        try:
            if result.failed:
                return False
            cookies = {name: value for name, value in result.cookies.items() if value}
            issued = cookies.get("__Host-oday_web_session", "")
            single = set(cookies) == {"__Host-oday_web_session"} and issued in self._cookies
            origin = gate._origin(self._sessions._origin)
            if operation == "gate-json-login":
                return (result.status == 200 and single and result.payload.get("ok") is True
                        and result.payload.get("subject") == "odp-dev-smoke")
            if operation == "gate-form-login":
                destination = urlsplit(result.location)
                return (result.status == 303 and single and gate._origin(result.location) == origin
                        and destination.path == "/operator" and destination.query == "view=admin"
                        and not destination.fragment)
            if operation == "gate-invalid-json-login":
                return (result.status == 401 and not cookies
                        and gate._payload_mentions(result, "AUTH_INVALID_CREDENTIALS"))
            if operation == "gate-invalid-form-login":
                # Canonical check_live_e2e_gate predicate is the COMPLETE query:
                # exactly error + the evaluator's returnTo. Missing/wrong returnTo,
                # extra (even blank) params or a fragment are malformed: UNKNOWN.
                failure = urlsplit(result.location)
                return (result.status == 303 and not cookies and gate._origin(result.location) == origin
                        and failure.path == "/login" and not failure.fragment
                        and parse_qs(failure.query, keep_blank_values=True, strict_parsing=True)
                        == {"error": ["AUTH_INVALID_CREDENTIALS"], "returnTo": ["/operator?view=admin"]})
        except Exception:
            return False
        return False

    def _request(self, client, method, path, *, authenticated, body, headers):
        supplied = {} if headers is None else dict(headers)
        raw_cookie = supplied.get("cookie", "")
        cookie = raw_cookie.removeprefix("__Host-oday_web_session=") if type(raw_cookie) is str else ""
        login = not client._api and method == "POST" and path == "/login"
        logout = not client._api and method == "POST" and path == "/auth/logout"
        operation, started, status = "", False, None
        try:
            # Exactly ONE session cookie pair or none: an extra/duplicate/ambiguous
            # Cookie header could bypass per-cookie logout/revocation fencing.
            if raw_cookie and (type(raw_cookie) is not str or cookie == raw_cookie or not cookie
                               or any(c in cookie for c in ";, \t")):
                raise ValueError
            cleanup = self._budget._active is not None and self._budget._active[0] == "cleanup"
            if os.getpid() != self._pid or self._requests >= (16 if cleanup else 256) or (self._stopped and not cleanup):
                raise ValueError
            self._requests += 1
            if login:
                form = supplied.get("content-type") == "application/x-www-form-urlencoded"
                valid = type(body) is dict and body.get("username") == "odp-dev-smoke" and body.get("password") == self._sessions._target.password
                operation = "gate-" + ("" if valid else "invalid-") + ("form" if form else "json") + "-login"
                if operation in self._logins or self._budget._active[0] != "gate":
                    raise ValueError
                self._logins.add(operation)
            elif logout:
                if cookie not in self._cookies or self._cookies[cookie]["attempted"]:
                    raise ValueError
                self._cookies[cookie]["attempted"] = True  # no repeat even if receipt/transport fails
                operation = "gate-session-logout"
            if operation:
                elapsed, remaining = self._budget._time()
                self._budget._receipts.append(phase=self._budget._active[0], operation=operation,
                    stage="start", outcome="pending", elapsed=elapsed, remaining=remaining)
                started = True
            result = client._roundtrip(method, path, authenticated=authenticated, body=body, headers=supplied)
            status = result.status
            if login and not self._login_reply_valid(operation, result):
                # Positive AND diagnostic logins: anything but the exact canonical
                # reply (including a complete 200/500 without retained cookie,
                # or a diagnostic reply carrying one) is UNKNOWN. Raise BEFORE a
                # completed receipt or any further transport; an acquired cookie
                # stays retained only for once-only bounded cleanup.
                raise ValueError
            if logout:
                acknowledged = (status == 200 and not result.failed and result.payload.get("ok") is True
                                and result.cookies.get("__Host-oday_web_session", "missing") == "")
                self._cookies[cookie]["acknowledged"] = acknowledged
                if not acknowledged:
                    # Complete-but-invalid logout reply is NOT a completed cleanup:
                    # terminal UNKNOWN receipt, stop before any further login.
                    raise ValueError
            elif not client._api and cookie in self._cookies and self._cookies[cookie]["attempted"]:
                # Post-logout revocation/replay proof with the SAME cookie. Any
                # failed/non-denied read stops NOW (quarantine then once-only
                # cleanup); the evaluator's later form logins never reach transport.
                denied = {401} if path == "/auth/session" else {401, 403}
                if (result.failed or status not in denied or method != "GET"
                        or not self._cookies[cookie]["acknowledged"]):
                    # Earlier session401 cannot outweigh a later live replay.
                    self._cookies[cookie]["verified"] = False
                    raise ValueError
                if path == "/auth/session":
                    self._cookies[cookie]["verified"] = True
            if operation:
                elapsed, remaining = self._budget._time()
                self._budget._receipts.append(phase=self._budget._active[0], operation=operation,
                    stage="terminal", outcome="completed", elapsed=elapsed, remaining=remaining, http_status=status)
            return result
        except Exception:
            if login and started:
                self._unknown_login = True
            if not client._api and cookie in self._cookies and self._cookies[cookie]["attempted"]:
                self._cookies[cookie]["verified"] = False  # lost/failed revocation proof
            if operation and started:
                try:
                    elapsed, remaining = self._budget._time()
                    self._budget._receipts.append(phase=self._budget._active[0], operation=operation,
                        stage="terminal", outcome="unknown", elapsed=elapsed, remaining=remaining, http_status=status)
                except Exception:
                    pass
            self._stopped = True
            raise RecoveryRefused("RECOVERY_GATE_REQUEST_STOPPED") from None

    @staticmethod
    def _before_cleanup(checks) -> None:
        if not checks or any(check.ok is not True for check in checks):
            raise RecoveryRefused("RECOVERY_GATE_FAILED_BEFORE_CLEANUP")

    def execute(self, actions: SameSessionRecoveryActions) -> dict:
        from dataclasses import replace

        from delivery_toolchain.e2e import check_live_e2e_gate as gate
        from delivery_toolchain.release.provision_dev_smoke import read_dev_credential_bundle
        started = False
        try:
            if (self._used or self._stopped or os.getpid() != self._pid
                    or type(actions) is not SameSessionRecoveryActions or actions._stopped
                    or actions._budget is not self._budget or actions._custody is not self._sessions._target
                    or actions._cookie != self._sessions._cookies["original"] or actions._plan != self._plan
                    or not actions._acknowledged or actions._put_status != 201
                    or self._sessions._baseline is None or self._sessions._observation_plan != self._plan
                    or self._sessions._verified != {"original", "target"}):
                raise ValueError
            self._used = True
            bundle = read_dev_credential_bundle(json.dumps(actions._bundle()), environment="dev", release_profile="dev-admin")
            self._config = replace(self._config, dev_admin_username=bundle.username, dev_admin_password=bundle.password,
                dev_admin_bundle_account_id=bundle.account_id, dev_admin_bundle_tenant_id=bundle.tenant_id,
                dev_admin_bundle_execution_id=bundle.execution_id, dev_admin_bundle_schema_version=bundle.schema_version,
                dev_admin_bundle_recovery_lineage=bundle.recovery_lineage)
            self._budget.begin("gate")
            started = True
            # All work including canonical scheduled-worker polling inherits ONE
            # clock. No gcloud/CLI/credential-bearing subprocess or timer reset.
            with _absolute_observer_deadline(self._budget.call_deadline):
                checks, report = gate.evaluate_gate(self._config, http=self._api, web_http=self._web,
                    worker_driver=gate.ScheduledWorkerDriver(), correlation_id=self._correlation,
                    now=datetime.now(UTC).isoformat(), session_cleanup_barrier=self._before_cleanup)
            required = {"admin:credential_bundle_account_bound", "admin:credential_bundle_durable_acknowledgement",
                        "session:logout", "session:revoked_session_refused", "admin:logout_revokes_admin_api",
                        "session:html_form_logout", "session:html_form_revoked_session_refused"}
            if (not checks or any(c.ok is not True for c in checks) or report.get("ok") is not True
                    or not required <= {c.name for c in checks if c.ok is True}
                    or report.get("expected_release_sha") != self._plan["release_sha"]
                    or report.get("expected_deployment") != "dev" or self._unknown_login
                    or len(self._cookies) != 2 or not all(v["verified"] for v in self._cookies.values())):
                raise ValueError
            if self._budget.finish(outcome="completed") != "completed":
                raise ValueError
            started = False
            return {"stage": "same-pair-canonical-gate-completed", "release_sha": self._plan["release_sha"],
                    "manifest_digest": self._plan["manifest_digest"], "execution_id": self._plan["execution_id"],
                    "gate_passed": True, "checks_count": len(checks), "gate_own_session_cleanup": "completed",
                    "credential_binding_verified": False, "execution_authorized": False,
                    "full_product_acceptance": False, "original_incident_session_cleanup": "unknown",
                    "secret_values_redacted": True}
        except Exception:
            self._used = self._stopped = True
            if started and self._budget._active is not None:
                try:
                    self._budget.finish(outcome="unknown")
                except Exception:
                    pass
            self._budget._stopped = True
            self.stop_and_cleanup(actions)
            raise RecoveryActionRefused(outcome="unknown", http_status=None) from None

    def stop_and_cleanup(self, actions: SameSessionRecoveryActions) -> dict:
        """No new login/readback/retry: exact known root quarantine BEFORE logout.

        May be called by the future fresh-consumer coordinator on its failure.
        If quarantine is unavailable, record UNKNOWN and still try other known
        own sessions once. Never infer a committed root from GET or fake an ACK.
        """
        self._stopped = self._budget._stopped = True
        if self._quarantine_outcome != "not-attempted" or self._cleanup_outcome != "not-attempted":
            return self._failure_receipt()
        if (type(actions) is not SameSessionRecoveryActions or actions._budget is not self._budget
                or actions._custody is not self._sessions._target
                or actions._plan != self._plan or actions._cookie != self._sessions._cookies["original"]):
            self._quarantine_outcome = self._cleanup_outcome = "unknown"
            return self._failure_receipt()
        self._quarantine_outcome = "unknown"
        try:
            actions.quarantine()
            self._quarantine_outcome = "completed"
        except Exception:
            pass
        self._cleanup_outcome = "unknown"
        try:
            self._sessions.cleanup(gate_sessions=self)
            self._cleanup_outcome = "completed"
        except Exception:
            pass
        finally:
            actions.close()
        return self._failure_receipt()

    def _failure_receipt(self) -> dict:
        return {"stage": "recovery-required", "quarantine": self._quarantine_outcome,
                "own_session_cleanup": self._cleanup_outcome, "original_incident_session_cleanup": "unknown",
                "execution_authorized": False, "credential_binding_verified": False, "secret_values_redacted": True}

    def _cleanup_known_sessions(self) -> bool:
        if self._budget._active is None or self._budget._active[0] != "cleanup":
            return False
        # Reserve separate bounded cleanup I/O as well as time. Exhausted gate
        # totals may not prevent the once-only known-cookie cleanup lane.
        self._requests = self._bytes = 0
        failed = self._unknown_login
        for cookie, state in self._cookies.items():
            if state["attempted"]:
                failed = not state["verified"] or failed
                continue  # unknown/failed logout is never retried
            try:
                result = self._web.request("POST", "/auth/logout", authenticated=False, body={},
                    headers={"cookie": f"__Host-oday_web_session={cookie}", "origin": self._sessions._origin})
                if result.failed or result.status != 200 or result.payload.get("ok") is not True:
                    raise ValueError
                revoked = self._web.request("GET", "/auth/session", authenticated=False,
                    headers={"cookie": f"__Host-oday_web_session={cookie}"})
                if revoked.failed or revoked.status != 401 or not state["verified"]:
                    raise ValueError
            except Exception:
                failed = True
        return not failed


class ForegroundRecoveryPreflight:
    """Concrete bounded preflight-to-HOLD composition; NEVER effect authority.

    Uses the actual typed observers, owned Web session and canonical gate config,
    not callbacks, caller baselines, exclusion booleans or supplied pass receipts.
    Ownership of these instances/custody transfers on successful construction.
    Even successful observations MUST stop before reservation/issuance/rotation:
    genuine external writer exclusion is not implemented by this repository.
    Native app-credential/custody viability, measured production bounds and the
    exact operational plan are also separate blockers. No execution/resume API.

    Only the fresh original session created here is cleaned, once, within the
    aggregate reserved cleanup phase. There is no new root to quarantine. A lost
    login/cleanup/receipt remains UNKNOWN; the old incident is never changed.
    This is trusted-foreground source, not a worker/live invocation permission.
    """

    def __init__(self, plan: dict, *, checkout: TrustedCheckoutObserver,
                 source: TrustedGitHubSourceObserver, admission: TrustedGitHubAdmissionObserver,
                 secret: RecoveryGitHubSecretAttempt, gate: ForegroundSamePairGate) -> None:
        try:
            if (type(plan) is not dict or type(checkout) is not TrustedCheckoutObserver
                    or type(source) is not TrustedGitHubSourceObserver
                    or type(admission) is not TrustedGitHubAdmissionObserver
                    or type(secret) is not RecoveryGitHubSecretAttempt
                    or type(gate) is not ForegroundSamePairGate or gate._plan != plan
                    or gate._used or gate._stopped or gate._sessions._attempted
                    or gate._budget._used or gate._budget._active is not None or gate._budget._stopped
                    or source._stopped or admission._stopped or secret._prepare_used or secret._put_used
                    or checkout._sha != plan["release_sha"] or source._sha != checkout._sha
                    or source._tree != checkout._tree or secret._login.casefold() != plan["custodian"].casefold()
                    # Admission's fixed canonical-validator child uses THIS
                    # checkout; a separately verified path cannot vouch for it.
                    or checkout._checkout.resolve() != Path(__file__).resolve().parents[2]):
                raise ValueError
            digest = validate_recovery_plan(plan, release_sha=plan["release_sha"],
                    manifest_digest=plan["manifest_digest"], now=datetime.now(UTC))
            if digest != gate._budget._receipts._digest:
                raise ValueError
            _ = gate._sessions._original.password
            _ = gate._sessions._target.password
        except Exception:
            raise RecoveryRefused("RECOVERY_PREFLIGHT_CONFIG_INVALID") from None
        self._pid, self._used = os.getpid(), False
        self._plan, self._checkout, self._source = dict(plan), checkout, source
        self._admission, self._secret, self._gate = admission, secret, gate
        self._sessions, self._budget = gate._sessions, gate._budget

    def __repr__(self) -> str:
        return "ForegroundRecoveryPreflight(<redacted>)"

    def __reduce_ex__(self, protocol: int):
        raise RecoveryRefused("RECOVERY_CUSTODY_SERIALIZATION_REFUSED")

    def _observe(self, operation: str) -> dict:
        """Fixed observer dispatch with durable starts; no injected callbacks."""
        started = False
        try:
            elapsed, remaining = self._budget._time()
            self._budget._receipts.append(phase="preflight", operation=operation, stage="start",
                                         outcome="pending", elapsed=elapsed, remaining=remaining)
            started = True
            deadline = self._budget.call_deadline
            if operation == "preflight-checkout":
                result = self._checkout.observe(deadline=deadline)
            elif operation == "preflight-source":
                result = self._source.observe(release_sha=self._plan["release_sha"], deadline=deadline)
            elif operation == "preflight-admission":
                result = self._admission.observe(release_sha=self._plan["release_sha"],
                        manifest_digest=self._plan["manifest_digest"], deadline=deadline)
            elif operation == "preflight-secret-metadata":
                result = self._secret.prepare(deadline=deadline)
            else:
                raise ValueError
            _http_remaining(self._budget.call_deadline)
            elapsed, remaining = self._budget._time()
            self._budget._receipts.append(phase="preflight", operation=operation, stage="terminal",
                                         outcome="completed", elapsed=elapsed, remaining=remaining)
            return result  # identifiers only; NOT authority or cached pass
        except Exception as exc:
            if started:
                try:
                    elapsed, remaining = self._budget._time()
                    self._budget._receipts.append(phase="preflight", operation=operation, stage="terminal",
                        outcome=exc.outcome if isinstance(exc, ObserverRefused) else "unknown",
                        exit_code=exc.exit_code if isinstance(exc, ObserverRefused) else None,
                        elapsed=elapsed, remaining=remaining)
                except Exception:
                    pass  # receipt loss is never a pass or continuation input
            raise RecoveryRefused("RECOVERY_PREFLIGHT_OBSERVATION_STOPPED") from None

    def observe_and_hold(self) -> dict:
        """One fresh read sequence and own cleanup; NO reserve, capability or PUT.

        Source/current admission are read both before and after account/absence
        observations. A successful cached first read cannot excuse later drift.
        Nothing turns an absent bundle or matching source into writer exclusion.
        """
        if self._used or os.getpid() != self._pid:
            raise RecoveryRefused("RECOVERY_PREFLIGHT_ALREADY_STOPPED")
        self._used = True  # even a failed phase start may never be replayed
        preflight, cleanup = "unknown", "unknown"
        try:
            self._budget.begin("preflight")
            self._observe("preflight-checkout")
            self._observe("preflight-source")
            admission_before = self._observe("preflight-admission")
            self._sessions.login("original")
            self._sessions.observe_accounts(self._plan)
            self._observe("preflight-secret-metadata")
            self._observe("preflight-checkout")
            self._observe("preflight-source")
            admission_after = self._observe("preflight-admission")
            if admission_after != admission_before:
                raise ValueError  # even a same-SHA replacement needs a fresh plan
            preflight = self._budget.finish(outcome="completed")
        except Exception:
            if self._budget._active is not None:
                try:
                    preflight = self._budget.finish(outcome="unknown")
                except Exception:
                    pass
        finally:
            # Unconditional effect barrier: there is deliberately no caller-
            # supplied exclusion/approval/bounds switch to release it.
            self._budget._stopped = True
            self._gate._stopped = True
            try:
                self._sessions.cleanup(gate_sessions=self._gate)
                cleanup = "completed"
            except Exception:
                pass
            finally:
                self._sessions.close()
                self._sessions._original.close()
                self._sessions._target.close()
                for observer in (self._source, self._admission, self._secret):
                    try:
                        observer.close()
                    except Exception:
                        pass
        return {"stage": "preflight-held", "execution_id": self._plan["execution_id"],
                "preflight": preflight, "own_session_cleanup": cleanup,
                "blocking_gaps": ["external-writer-exclusion-unavailable",
                                  "native-application-credential-unimplemented",
                                  "native-custody-amendment-unapproved",
                                  "production-bounds-unmeasured", "operational-plan-unreviewed"],
                "execution_authorized": False, "credential_binding_verified": False,
                "full_product_acceptance": False, "secret_values_redacted": True,
                "original_incident_session_cleanup": "unknown"}
