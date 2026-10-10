"""Foreground preflight, reservation journal and memory-only Web lifecycle.

The pure validator checks a custodian's NON-SECRET proposed execution binding.
The optional PostgreSQL journal reserves that exact plan once and can quarantine
it after an uncertain result. Neither authenticates the custodian, verifies
release human approval, proves mailbox ownership, or authorizes a cloud mutation.
The Web lifecycle below requires a consumed-dev-admission observer pinned by
the trusted foreground coordinator and rechecks it before mutations. Independent
source approval is freshly observed through independently pinned GitHub PR/review/CI
roots. The canonical recorded user consent and foreground-approved scoped plan
are supported without another social approval or mailbox-deliverability ceremony.
A separately pinned GitHub custodian comment remains an optional input, not a
mandatory policy. Rollback ownership is still a coordinator duty. The optional encrypted bundle writer below
composes the same lifecycle/password in one call but cannot prove the stored
secret value. The release workflow consumes an already staged bundle through the
strict memory-only reader; it never invokes the lifecycle/writer. There is
intentionally no anonymous provisioning CLI.

Account input must come from the existing authenticated identity readback, not
caller headers or an offline receipt. Credentials/capabilities are not accepted
in a plan and remain exclusively in executor/encryption memory.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any
from uuid import UUID

from shared.audit import AuditEvent
from shared.identity.dev_smoke_journal import (
    _CUSTODIAN,
    _DIGEST,
    _PLAN_KEYS,
    _SHA,
    _USERNAME,
    CREDENTIAL_BUNDLE_SECRET_NAME,
    _aware,
    original_account_readback,
)
from shared.identity.dev_smoke_journal import (
    AUTHORIZATION_ID as AUTHORIZATION_ID,
)
from shared.identity.dev_smoke_journal import (
    PRESERVED_ACCOUNT_ID as PRESERVED_ACCOUNT_ID,
)
from shared.identity.dev_smoke_journal import (
    PRESERVED_ROLES as PRESERVED_ROLES,
)
from shared.identity.dev_smoke_journal import (
    PURPOSE as PURPOSE,
)
from shared.identity.dev_smoke_journal import (
    REPOSITORY as REPOSITORY,
)
from shared.identity.dev_smoke_journal import (
    TENANT_ID as TENANT_ID,
)
from shared.identity.dev_smoke_journal import (
    DevSmokeBindingJournal as LocalDevSmokeBindingJournal,
)
from shared.identity.dev_smoke_journal import (
    ForegroundPlan as ForegroundPlan,
)
from shared.identity.dev_smoke_journal import (
    JournalReservation as JournalReservation,
)
from shared.identity.dev_smoke_journal import (
    ProvisioningJournal as ProvisioningJournal,
)
from shared.identity.dev_smoke_journal import (
    ProvisioningRefused as ProvisioningRefused,
)
from shared.identity.dev_smoke_journal import (
    validate_foreground_plan as validate_foreground_plan,
)
from shared.identity.invitation_provenance import invitation_provenance


def verify_consumed_dev_admission(
    *, manifest: Any, registry: Any, lease: Any, public_key: Any,
    state_store: Any, release_sha: str, manifest_digest: str, task_id: str,
    consumed_by: str, component_images: Any, now: datetime,
) -> dict[str, Any]:
    """Read-only foreground check of the already-consumed exact dev admission.

    Trust roots (public key, Supervisor-owned state store, registry and consumer
    identity) MUST be obtained by the coordinator independently, not from the
    provisioning plan or a caller's `admitted=true` receipt. This does not issue,
    consume or replay a lease. It rechecks canonical admission predicates and
    observes existing consumption; it cannot prove custody/source approval,
    promotion, rollout ownership or who originally called the admission tool.
    The returned identifiers are evidence only, never execution authority.
    """
    from delivery_toolchain.release import release_lease as leases
    from delivery_toolchain.release.check_runtime_admission import registry_admission_errors
    from delivery_toolchain.release.release_manifest import (
        component_binding_errors,
        manifest_release_profile,
        validate_manifest,
        validate_release_admission,
    )

    try:
        if (not _aware(now) or not isinstance(release_sha, str) or not _SHA.fullmatch(release_sha)
                or not isinstance(manifest_digest, str) or not _DIGEST.fullmatch(manifest_digest)
                or not isinstance(task_id, str) or not leases.TASK_ID_PATTERN.fullmatch(task_id)
                or not isinstance(consumed_by, str) or not consumed_by.strip()
                or len(consumed_by) > 320 or not isinstance(state_store, leases.LeaseStateStore)
                or not isinstance(manifest, dict) or not isinstance(registry, dict)
                or not isinstance(lease, dict) or not isinstance(component_images, dict)
                or set(component_images) != {"api", "web", "worker", "scheduler"}
                or registry.get("release", {}).get("candidate_sha") != release_sha):
            raise ValueError
        # Reuse the actual release predicates, not a shape-only receipt check.
        if (validate_manifest(manifest, expected_candidate_sha=release_sha,
                              expected_digest=manifest_digest)
                or validate_release_admission(manifest, environment="dev")
                or manifest_release_profile(manifest) != "dev-admin"
                or manifest.get("external_sources_expected_enabled") != []
                or component_binding_errors(manifest, component_images)
                or registry_admission_errors(registry, release_sha=release_sha,
                                             environment="dev",
                                             expected_manifest_digest=manifest_digest)):
            raise ValueError
        if (set(lease) - set((*leases.LEASE_FIELDS, "signature"))
                or any(k not in lease for k in (*leases.LEASE_REQUIRED_FIELDS, "signature"))
                or type(lease.get("schema_version")) is not int
                or lease["schema_version"] != leases.LEASE_SCHEMA_VERSION
                or leases.signature_errors(lease, public_key=public_key)
                or leases._field_format_errors(lease)
                or leases._validity_window_errors(lease, now)
                or leases._binding_errors(
                    lease, expected_task_id=task_id, expected_candidate_sha=release_sha,
                    expected_manifest_digest=manifest_digest, expected_environment="dev",
                    expected_action="deploy",
                )
                or lease["release_id"] != manifest["release_id"]):
            raise ValueError
        # verify_lease deliberately requires ISSUED for *new* admission. Do not
        # call it with a fake issued view or consume again. Here the real store
        # must already contain the same signed lease in CONSUMED state.
        record = state_store.get(lease["lease_id"])
        if (not isinstance(record, dict) or record.get("state") != leases.STATE_CONSUMED
                or record.get("lease_id") != lease["lease_id"]
                or record.get("lease") != lease or record.get("consumed_by") != consumed_by
                or record.get("revoked_at") is not None or record.get("revoked_reason") is not None):
            raise ValueError
        consumed_at = datetime.fromisoformat(record["consumed_at"])
        issued_at = datetime.fromisoformat(lease["issued_at"])
        expires_at = datetime.fromisoformat(lease["expires_at"])
        if (not _aware(consumed_at) or not issued_at <= consumed_at <= now < expires_at):
            raise ValueError
        return {
            "stage": "consumed-dev-admission-observed", "execution_authorized": False,
            "repository": REPOSITORY, "environment": "dev", "release_profile": "dev-admin",
            "task_id": task_id, "lease_id": lease["lease_id"], "release_sha": release_sha,
            "manifest_digest": manifest_digest, "secret_values_redacted": True,
            "deployment_success": False,
        }
    except Exception:
        # Never surface a nonce, signature, arbitrary registry/state error or
        # remote exception. An unavailable/corrupt trust root is not approval.
        raise ProvisioningRefused("PROVISIONING_DEV_ADMISSION_UNVERIFIED") from None


class RecordedUserAuthorization:
    """Canonical recorded consent plus the coordinator's approved scoped plan.

    This is a trusted-foreground input, NOT authentication of an arbitrary caller.
    The recorded user reply already authorizes this operation: no new GitHub
    comment, SMTP proof or social approval is required. The foreground owner
    selects the recipient/custodian and supplies the approved NON-SECRET plan;
    its digest cannot subsequently be changed by an executor. Source/CI and
    consumed admission still have their independent observers. Never load these
    inputs from an anonymous request or let a background worker approve a plan.
    """

    def __init__(self, *, authorization: Any, approved_plan: Any) -> None:
        from copy import deepcopy

        self._authorization = deepcopy(authorization)
        self._plan = deepcopy(approved_plan)

    def observe(self, plan: Any, *, now: datetime) -> None:
        try:
            record = self._authorization
            scope = record["approved_scope"]
            if (record["schema_version"] != 1 or type(record["schema_version"]) is not int
                    or record["authorization_id"] != AUTHORIZATION_ID
                    or record["source"] != "Explicit user reply in this foreground conversation"
                    or record["user_reply"] != "同意"
                    or scope["environment"] != "dev" or scope["repository"] != REPOSITORY
                    or scope["tenant_id"] != TENANT_ID or scope["new_identity_roles"] != ["platform_admin"]
                    or scope["preserve_existing_account_id"] != PRESERVED_ACCOUNT_ID
                    or scope["preserve_existing_roles"] != sorted(PRESERVED_ROLES)
                    or any(scope[key] is not True for key in (
                        "no_iam_changes", "no_gate_relaxation", "no_business_mutation_authority",
                        "no_cross_tenant_bypass", "no_source_activation_backfill_or_fixture",
                        "no_model_promotion", "no_full_product_or_F11_self_approval"))
                    or not isinstance(plan, dict) or plan != self._plan
                    or set(plan) != _PLAN_KEYS or not all(type(v) is str for v in plan.values())
                    or not _aware(now)):
                raise ValueError
            expiry = datetime.fromisoformat(plan["expires_at"])
            if not _aware(expiry) or not now < expiry <= now + timedelta(hours=1):
                raise ValueError
        except Exception:
            raise ProvisioningRefused("PROVISIONING_CUSTODY_APPROVAL_UNVERIFIED") from None


class GitHubCustodyApprovalObserver:
    """Fresh authenticated custodian attestation, not mailbox-delivery proof.

    The trusted foreground coordinator independently pins a repository issue,
    comment ID and approved HUMAN login/numeric ID, never from the plan. That
    exact unedited comment must explicitly approve the digest of the entire
    non-secret plan and attest owner-controlled recipient custody/consent. Its
    author must match the approved custodian and the plan. No comment is posted
    by this library. A token owner, arbitrary receipt or plan boolean is NOT
    consent. The coordinator still verifies mailbox control before requesting
    this attestation, independent source approval and rollback ownership.
    """

    def __init__(
        self, *, token: str, issue_number: int, comment_id: int,
        custodian_login: str, custodian_id: int, transport: Any = None,
    ) -> None:
        import httpx

        if (not isinstance(token, str) or not token or any(c in token for c in "\r\n")
                or any(type(v) is not int or v <= 0 for v in (issue_number, comment_id, custodian_id))
                or type(custodian_login) is not str or not _CUSTODIAN.fullmatch(custodian_login)):
            raise ProvisioningRefused("PROVISIONING_CUSTODY_APPROVAL_CONFIG_INVALID")
        self._issue = issue_number
        self._comment = comment_id
        self._login = custodian_login
        self._identity = custodian_id
        self._client = httpx.Client(
            base_url="https://api.github.com", transport=transport, timeout=20,
            follow_redirects=False, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self) -> None:
        self._client.close()

    def observe(self, plan: Any, *, now: datetime) -> None:
        """Re-read exact evidence on EVERY call; never cache a passing receipt.

        Comment body is strict JSON (no Markdown wrapper), with only schema,
        authorization, full-plan digest, fixed decision/custody/consent and expiry.
        No recipient address, credentials or capability belongs in that comment.
        Deletion, edits, redirects, auth loss, stale/malformed evidence refuse.
        """
        try:
            if (not isinstance(plan, dict) or set(plan) != _PLAN_KEYS
                    or not all(type(v) is str and 0 < len(v) <= 320 for v in plan.values())
                    or not _aware(now) or plan["recipient_control"] != "owner-controlled"
                    or plan["recipient_custodian"].casefold() != self._login.casefold()):
                raise ValueError
            response = self._client.get(f"/repos/{REPOSITORY}/issues/comments/{self._comment}")
            comment = response.json()
            user = comment.get("user")
            if (response.status_code != 200 or type(comment.get("id")) is not int
                    or comment["id"] != self._comment
                    or comment.get("issue_url") != f"https://api.github.com/repos/{REPOSITORY}/issues/{self._issue}"
                    or not isinstance(user, dict) or user.get("type") != "User"
                    or type(user.get("id")) is not int or user["id"] != self._identity
                    or type(user.get("login")) is not str or user["login"].casefold() != self._login.casefold()
                    or type(comment.get("body")) is not str or len(comment["body"]) > 2048
                    or comment.get("created_at") != comment.get("updated_at")):
                raise ValueError

            def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            approval = json.loads(comment["body"], object_pairs_hook=unique)
            digest = "sha256:" + hashlib.sha256(json.dumps(
                plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode()).hexdigest()
            expected = {
                "schema_version": 1, "authorization_id": AUTHORIZATION_ID,
                "plan_digest": digest, "decision": "approve",
                "recipient_control": "owner-controlled",
                "consent": "invitation-and-dev-smoke-credential-bundle",
                "expires_at": plan["expires_at"],
            }
            if (not isinstance(approval, dict) or type(approval.get("schema_version")) is not int
                    or approval != expected):
                raise ValueError
            issued = datetime.fromisoformat(comment["created_at"])
            expiry = datetime.fromisoformat(plan["expires_at"])
            if (not _aware(issued) or not _aware(expiry)
                    or not issued <= now < expiry <= issued + timedelta(hours=1)):
                raise ValueError
        except Exception:
            # No arbitrary body, address, token, HTTP error or remote exception.
            raise ProvisioningRefused("PROVISIONING_CUSTODY_APPROVAL_UNVERIFIED") from None


class GitHubSourceApprovalObserver:
    """Fresh exact-source review/CI observation, not rollout authority.

    The coordinator independently pins the merged PR, its reviewed head, the
    canonical task-review-gate writer and required CI workflow/app/job names.
    None is selected from a plan or passing receipt. A merge SHA is accepted
    only when its entire Git tree equals the reviewed head; merge composition
    that changes that tree needs its own exact-source approval. Required CI
    must succeed on BOTH the reviewed head and candidate, without skipped jobs.
    The trusted canonical gate writer enforces assigned independent review;
    this reader does not post an approval, rerun CI or waive branch protection.
    """

    def __init__(
        self, *, token: str, pull_number: int, reviewed_head: str,
        review_writer_login: str, review_writer_id: int, workflow_id: int,
        checks_app_id: int, required_checks: tuple[str, ...], transport: Any = None,
    ) -> None:
        import httpx

        if (not isinstance(token, str) or not token or any(c in token for c in "\r\n")
                or any(type(v) is not int or v <= 0 for v in
                       (pull_number, review_writer_id, workflow_id, checks_app_id))
                or type(reviewed_head) is not str or not _SHA.fullmatch(reviewed_head)
                or type(review_writer_login) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\[\]-]{0,99}", review_writer_login)
                or type(required_checks) is not tuple or not required_checks
                or any(type(v) is not str or not v.strip() or len(v) > 100 for v in required_checks)
                or len(set(required_checks)) != len(required_checks)):
            raise ProvisioningRefused("PROVISIONING_SOURCE_APPROVAL_CONFIG_INVALID")
        self._pull = pull_number
        self._head = reviewed_head
        self._writer = review_writer_login
        self._writer_id = review_writer_id
        self._workflow = workflow_id
        self._app = checks_app_id
        self._checks = required_checks
        self._client = httpx.Client(
            base_url="https://api.github.com", transport=transport, timeout=20,
            follow_redirects=False, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str) -> Any:
        response = self._client.get(f"/repos/{REPOSITORY}/{path}")
        # A bounded single page is intentional: incomplete evidence refuses,
        # never silently picks a convenient approval/check from a partial list.
        if response.status_code != 200 or 'rel="next"' in response.headers.get("link", ""):
            raise ValueError
        return response.json()

    @staticmethod
    def _past(value: Any, now: datetime) -> datetime:
        at = datetime.fromisoformat(value)
        if not _aware(at) or at > now:
            raise ValueError
        return at

    def _ci(self, sha: str, now: datetime) -> None:
        runs = self._get(f"actions/workflows/{self._workflow}/runs?head_sha={sha}&per_page=100")
        records = runs["workflow_runs"]
        if (type(runs.get("total_count")) is not int or not isinstance(records, list)
                or not records or len(records) != runs["total_count"] or len(records) > 100
                or any(type(r.get("id")) is not int or r["id"] <= 0 for r in records)):
            raise ValueError
        # Newer pending/failed attempts supersede older success. Never search
        # for any passing run or permit a run of a different workflow/event.
        run = max(records, key=lambda r: r["id"])
        if (run.get("head_sha") != sha or run.get("workflow_id") != self._workflow
                or run.get("path") != ".github/workflows/ci.yml"
                or run.get("event") not in {"push", "pull_request", "merge_group"}
                or run.get("repository", {}).get("full_name") != REPOSITORY
                or run.get("head_repository", {}).get("full_name") != REPOSITORY
                or run.get("status") != "completed" or run.get("conclusion") != "success"
                or type(run.get("check_suite_id")) is not int or run["check_suite_id"] <= 0):
            raise ValueError
        self._past(run["updated_at"], now)
        checks = self._get(f"check-suites/{run['check_suite_id']}/check-runs?filter=latest&per_page=100")
        records = checks["check_runs"]
        if (type(checks.get("total_count")) is not int or not isinstance(records, list)
                or len(records) != checks["total_count"] or len(records) > 100):
            raise ValueError
        for name in self._checks:
            selected = [c for c in records if c.get("name") == name]
            if len(selected) != 1:
                raise ValueError
            check = selected[0]
            if (check.get("head_sha") != sha or check.get("app", {}).get("id") != self._app
                    or check.get("check_suite", {}).get("id") != run["check_suite_id"]
                    or check.get("status") != "completed" or check.get("conclusion") != "success"):
                raise ValueError
            self._past(check["completed_at"], now)

    def observe(self, *, release_sha: str, now: datetime) -> None:
        """Re-read all pinned evidence each time; missing/changed evidence refuses."""
        try:
            if type(release_sha) is not str or not _SHA.fullmatch(release_sha) or not _aware(now):
                raise ValueError
            pull = self._get(f"pulls/{self._pull}")
            if (type(pull.get("number")) is not int or pull["number"] != self._pull
                    or pull.get("merged") is not True or pull.get("state") != "closed"
                    or pull.get("draft") is not False or pull.get("merge_commit_sha") != release_sha
                    or pull.get("head", {}).get("sha") != self._head
                    or pull.get("head", {}).get("repo", {}).get("full_name") != REPOSITORY
                    or pull.get("base", {}).get("ref") != "dev"
                    or pull.get("base", {}).get("repo", {}).get("full_name") != REPOSITORY):
                raise ValueError
            self._past(pull["merged_at"], now)
            trees = []
            for sha in dict.fromkeys((self._head, release_sha)):
                commit = self._get(f"git/commits/{sha}")
                tree = commit.get("tree", {}).get("sha")
                if (commit.get("sha") != sha or type(tree) is not str or not _SHA.fullmatch(tree)):
                    raise ValueError
                trees.append(tree)
            if len(set(trees)) != 1:
                raise ValueError
            statuses = self._get(f"commits/{self._head}/statuses?per_page=100")
            if not isinstance(statuses, list) or len(statuses) >= 100:
                raise ValueError
            gates = [s for s in statuses if s.get("context") == "task-review-gate"]
            if not gates:
                raise ValueError
            times = [self._past(s["created_at"], now) for s in gates]
            latest = max(times)
            if times.count(latest) != 1:
                raise ValueError
            gate = gates[times.index(latest)]
            author = gate.get("creator", {})
            if (gate.get("state") != "success"
                    or gate.get("url") != f"https://api.github.com/repos/{REPOSITORY}/statuses/{self._head}"
                    or type(author.get("id")) is not int or author["id"] != self._writer_id
                    or type(author.get("login")) is not str
                    or author["login"].casefold() != self._writer.casefold()):
                raise ValueError
            for sha in dict.fromkeys((self._head, release_sha)):
                self._ci(sha, now)
        except Exception:
            raise ProvisioningRefused("PROVISIONING_SOURCE_APPROVAL_UNVERIFIED") from None


class ConsumedDevAdmissionObserver:
    """Pinned admission material plus fresh reads of the Supervisor-owned store.

    The coordinator must obtain these trust roots independently, never from a
    plan or a caller's passing receipt. Documents are snapshotted, not reloaded
    from the plan; lease state is read anew on EVERY observation. This object is
    not authenticated source/custody approval or a deploy/rollback capability.
    There is no callback, cached pass boolean or receipt-based alternative.
    """

    def __init__(
        self, *, manifest: Any, registry: Any, lease: Any, public_key: Any,
        state_store: Any, task_id: str, consumed_by: str, component_images: Any,
    ) -> None:
        from copy import deepcopy

        from delivery_toolchain.release.release_lease import LeaseStateStore

        if not isinstance(state_store, LeaseStateStore):
            raise ProvisioningRefused("PROVISIONING_DEV_ADMISSION_UNVERIFIED")
        try:
            self._material = deepcopy(dict(manifest=manifest, registry=registry, lease=lease,
                                           component_images=component_images))
        except Exception:
            raise ProvisioningRefused("PROVISIONING_DEV_ADMISSION_UNVERIFIED") from None
        self._key = public_key
        self._store = state_store
        self._task = task_id
        self._consumer = consumed_by

    def observe(self, *, release_sha: str, manifest_digest: str, now: datetime) -> None:
        # Discard the evidence receipt: it is not an execution authority token.
        verify_consumed_dev_admission(
            **self._material, public_key=self._key, state_store=self._store,
            task_id=self._task, consumed_by=self._consumer, release_sha=release_sha,
            manifest_digest=manifest_digest, now=now,
        )


def _journal_session(method: Any) -> Any:
    @wraps(method)
    def execute(self: Any, *args: Any, **kwargs: Any) -> Any:
        if type(self._journal) is RemoteProvisioningJournal:
            admin_password, new_password = kwargs.get("admin_password"), kwargs.get("new_password")
            if (not isinstance(admin_password, str) or not admin_password
                    or not isinstance(new_password, str) or not 12 <= len(new_password) <= 1024
                    or admin_password == new_password):
                raise ProvisioningRefused("PROVISIONING_CREDENTIAL_INPUT_INVALID")
            lifecycle = self if isinstance(self, WebInvitationExecutor) else self._lifecycle
            plan = args[0] if args else kwargs.get("plan")
            lifecycle._observe_admission(plan, release_sha=kwargs.get("release_sha", ""),
                                         manifest_digest=kwargs.get("manifest_digest", ""))
        with self._journal.session(admin_password=kwargs.get("admin_password", "")):
            return method(self, *args, **kwargs)
    return execute


class RemoteProvisioningJournal:
    """Same authenticated Web/BFF trust path; no foreground DB credentials.

    Opens one execution-owned admin session for the outermost operation; nested
    lifecycle/binding/gate calls share it. All journal writes are single-attempt.
    An uncertain reservation cannot be replayed: server DB lock/root audit is
    authoritative. A caller-provided original-account receipt is never forwarded.
    """

    PATH = "/api/v1/operator/users/dev-smoke-journal"

    def __init__(self, *, web: Any, web_origin: str) -> None:
        from urllib.parse import urlsplit
        origin = urlsplit(web_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment):
            raise ProvisioningRefused("PROVISIONING_TRANSPORT_INVALID")
        self._web, self._origin = web, web_origin
        self._cookie = ""
        self._depth = 0

    def _now(self) -> datetime:
        # Server reservation/expiry uses DB time. This time is only for fresh
        # independent source/admission observations, never accepted as DB time.
        return datetime.now(UTC)

    def _request(self, method: str, path: str, body: Any = None, *, status: int = 200) -> Any:
        try:
            headers = {"accept": "application/json", "origin": self._origin}
            if self._cookie:
                headers["cookie"] = f"__Host-oday_web_session={self._cookie}"
            response = self._web.request(method, path, authenticated=False, body=body,
                                         headers=headers, follow_redirects=False)
            if response.failed or response.status != status or not isinstance(response.payload, dict):
                raise ValueError
            return response
        except Exception:
            raise ProvisioningRefused("PROVISIONING_JOURNAL_UNAVAILABLE") from None

    @contextmanager
    def session(self, *, admin_password: str) -> Any:
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        try:
            if not isinstance(admin_password, str) or not admin_password:
                raise ValueError
            response = self._request("POST", "/login", {
                "username": "ajoe734", "password": admin_password, "returnTo": "/operator?view=admin",
            })
            cookie = response.cookies.get("__Host-oday_web_session")
            if (response.payload.get("ok") is not True or response.payload.get("subject") != "ajoe734"
                    or not isinstance(cookie, str) or not cookie or len(cookie) > 8192
                    or any(c in cookie for c in ";\r\n")):
                raise ValueError
            self._cookie, self._depth = cookie, 1
            # The server's journal context revalidates the pinned actor, active
            # role, tenant and durable session on EVERY operation.
            self.inspect()
            yield self
        except ProvisioningRefused:
            raise
        except Exception:
            raise ProvisioningRefused("PROVISIONING_JOURNAL_UNAVAILABLE") from None
        finally:
            if self._cookie:
                try:
                    response = self._request("POST", "/auth/logout")
                    if response.payload.get("ok") is not True:
                        raise ValueError
                    self._request("GET", "/auth/session", status=401)
                except Exception:
                    # Do not return successful binding/gate after logout uncertainty.
                    try:
                        reserved = self.inspect()
                        if reserved is not None and reserved.stage == "reserved":
                            self.require_recovery(reserved)
                    except Exception:
                        pass  # Existing durable root/intent remains a no-retry boundary.
                    raise ProvisioningRefused("PROVISIONING_JOURNAL_SESSION_RECOVERY_REQUIRED") from None
                finally:
                    self._cookie, self._depth = "", 0

    @staticmethod
    def _reservation(value: Any) -> JournalReservation | None:
        if value is None:
            return None
        try:
            if (not isinstance(value, dict) or set(value) != {
                    "execution_id", "plan_digest", "event_id", "stage", "authorization_id",
                    "execution_authorized", "secret_values_redacted"}
                    or value["authorization_id"] != AUTHORIZATION_ID
                    or value["execution_authorized"] is not False or value["secret_values_redacted"] is not True
                    or value["stage"] not in {"reserved", "recovery-required"}
                    or not _DIGEST.fullmatch(value["plan_digest"])
                    or any(str(UUID(value[key])) != value[key] for key in ("execution_id", "event_id"))):
                raise ValueError
            return JournalReservation(*(value[key] for key in ("execution_id", "plan_digest", "event_id", "stage")))
        except Exception:
            raise ProvisioningRefused("PROVISIONING_JOURNAL_INVALID") from None

    def _read(self, method: str, body: Any = None) -> dict[str, Any]:
        value = self._request(method, self.PATH, body).payload
        if (set(value) != {"reservation", "binding", "execution_authorized", "secret_values_redacted"}
                or value["execution_authorized"] is not False or value["secret_values_redacted"] is not True):
            raise ProvisioningRefused("PROVISIONING_JOURNAL_INVALID")
        return value

    def inspect(self) -> JournalReservation | None:
        return self._reservation(self._read("GET")["reservation"])

    def reserve(self, plan: Any, **context: Any) -> JournalReservation:
        # Never transmit a caller's account readback or any credential/capability.
        value = self._read("POST", {"action": "reserve", "plan": plan})
        result = self._reservation(value.get("reservation"))
        if result is None:
            raise ProvisioningRefused("PROVISIONING_JOURNAL_INVALID")
        return result

    def require_recovery(self, reservation: JournalReservation) -> JournalReservation:
        value = self._read("POST", {
            "action": "quarantine", "reservation": reservation.to_receipt(),
        })
        result = self._reservation(value.get("reservation"))
        if result is None:
            raise ProvisioningRefused("PROVISIONING_JOURNAL_INVALID")
        return result

    def binding_inspect(self) -> dict[str, Any] | None:
        value = self._read("GET")["binding"]
        if value is not None and (not isinstance(value, dict)
                or set(value) != DevSmokeBindingJournal._KEYS | {"audit_event_id"}
                or value.get("authorization_id") != AUTHORIZATION_ID
                or value.get("tenant_id") != TENANT_ID or value.get("execution_authorized") is not False
                or value.get("credential_binding_verified") is not False
                or value.get("secret_values_redacted") is not True
                or value.get("secret_name") != GitHubDevSecretStore.NAME
                or value.get("stage") not in {"binding-intent", "binding-acknowledged", "recovery-required"}):
            raise ProvisioningRefused("PROVISIONING_BINDING_JOURNAL_INVALID")
        return value

    def binding_append(self, metadata: dict[str, Any], stage: str) -> AuditEvent:
        value = self._read("POST", {
            "action": stage, "account_id": metadata["account_id"],
            "execution_id": metadata["execution_id"], "plan_digest": metadata["plan_digest"],
        })["binding"]
        if (not isinstance(value, dict) or set(value) != DevSmokeBindingJournal._KEYS | {"audit_event_id"}
                or {k: v for k, v in value.items() if k != "audit_event_id"} != {**metadata, "stage": stage}):
            raise ProvisioningRefused("PROVISIONING_BINDING_JOURNAL_INVALID")
        try:
            event_id = str(UUID(value["audit_event_id"]))
        except Exception:
            raise ProvisioningRefused("PROVISIONING_BINDING_JOURNAL_INVALID") from None
        return AuditEvent(event_id=event_id, event_type="release.dev_smoke.binding", actor="",
                          action="DEV_SMOKE_BINDING", resource="", outcome="success",
                          correlation_id=DevSmokeBindingJournal._CORRELATION)


class WebInvitationExecutor:
    """Execute the supported lifecycle through Web, never directly mutate SQL.

    This is a foreground library entrypoint, NOT an admission/approval service.
    The coordinator must verify promotion, approve recipient custody and
    supply independently pinned source/review/CI and admission roots plus the
    canonical recorded consent and scoped plan (or optional GitHub attestation).
    Consumed exact-dev admission and the approved plan are rechecked before side effects. A journal or
    matching string is not that proof. No workflow or CLI calls this entrypoint.

    All HTTP side effects are single-attempt. Once reserved, ANY uncertainty
    quarantines the root; never reset a password, replace a recipient, revoke
    another session, delete an account, or retry invite/accept automatically.
    The returned receipt proves lifecycle readback only, not binding or a gate.
    """

    _COOKIE = "__Host-oday_web_session"

    def __init__(
        self, *, web: Any, web_origin: str, journal: ProvisioningJournal | RemoteProvisioningJournal,
        admission: ConsumedDevAdmissionObserver, custody: RecordedUserAuthorization | GitHubCustodyApprovalObserver,
        source: GitHubSourceApprovalObserver,
    ) -> None:
        from urllib.parse import urlsplit

        origin = urlsplit(web_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment
                or type(journal) not in (ProvisioningJournal, RemoteProvisioningJournal)
                or (type(journal) is RemoteProvisioningJournal
                    and (journal._web is not web or journal._origin != web_origin))):
            raise ProvisioningRefused("PROVISIONING_TRANSPORT_INVALID")
        if type(admission) is not ConsumedDevAdmissionObserver:
            raise ProvisioningRefused("PROVISIONING_DEV_ADMISSION_UNVERIFIED")
        if type(custody) not in (RecordedUserAuthorization, GitHubCustodyApprovalObserver):
            raise ProvisioningRefused("PROVISIONING_CUSTODY_APPROVAL_UNVERIFIED")
        if type(source) is not GitHubSourceApprovalObserver:
            raise ProvisioningRefused("PROVISIONING_SOURCE_APPROVAL_UNVERIFIED")
        self._source = source
        self._web = web
        self._origin = web_origin
        self._journal = journal
        self._admission = admission
        self._custody = custody

    def _observe_admission(self, plan: Any, *, release_sha: str, manifest_digest: str) -> None:
        if (not isinstance(plan, dict) or plan.get("release_sha") != release_sha
                or plan.get("manifest_digest") != manifest_digest):
            raise ProvisioningRefused("PROVISIONING_RELEASE_MISMATCH")
        now = self._journal._now()
        self._admission.observe(release_sha=release_sha, manifest_digest=manifest_digest, now=now)
        self._custody.observe(plan, now=now)
        self._source.observe(release_sha=release_sha, now=now)

    def _request(
        self, method: str, path: str, *, cookie: str = "", body: Any = None, status: int = 200,
    ) -> Any:
        # No user bearer, actor, tenant or role headers, and no redirects.
        headers = {"accept": "application/json", "origin": self._origin}
        if cookie:
            headers["cookie"] = f"{self._COOKIE}={cookie}"
        try:
            response = self._web.request(method, path, authenticated=False, body=body,
                                         headers=headers, follow_redirects=False)
            if response.failed or response.status != status or not isinstance(response.payload, dict):
                raise ValueError("unusable response")
            return response
        except Exception:
            # Never expose upstream payload, exceptions, cookie or credentials.
            raise ProvisioningRefused("PROVISIONING_WEB_REQUEST_REFUSED") from None

    def _login(self, username: str, password: str) -> str:
        response = self._request("POST", "/login", body={
            "username": username, "password": password, "returnTo": "/operator?view=admin",
        })
        cookie = response.cookies.get(self._COOKIE)
        if (response.payload.get("ok") is not True or response.payload.get("subject") != username
                or not isinstance(cookie, str) or not cookie or len(cookie) > 8192
                or any(c in cookie for c in ";\r\n")):
            raise ProvisioningRefused("PROVISIONING_SESSION_INVALID")
        return cookie

    def _session(self, cookie: str, username: str) -> None:
        current = self._request("GET", "/auth/session", cookie=cookie).payload
        if current.get("subject") != username:
            raise ProvisioningRefused("PROVISIONING_SESSION_INVALID")

    def _users(self, cookie: str) -> list[dict[str, Any]]:
        records = self._request("GET", "/api/v1/operator/users", cookie=cookie).payload.get("users")
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise ProvisioningRefused("PROVISIONING_INVENTORY_INVALID")
        return records

    _original = staticmethod(original_account_readback)

    def _principal(self, cookie: str, account: str, roles: list[str]) -> None:
        principal = self._request("GET", "/api/v1/auth/principal", cookie=cookie).payload
        observed = principal.get("roles")
        if (principal.get("account_id") != account or principal.get("tenant_id") != TENANT_ID
                or not isinstance(observed, list) or not all(isinstance(r, str) for r in observed)
                or sorted(observed) != sorted(roles)):
            raise ProvisioningRefused("PROVISIONING_PRINCIPAL_MISMATCH")

    def _promoted_release(self, cookie: str, checked: ForegroundPlan) -> None:
        """Observe BOTH revisions through the same authenticated Web/BFF path.

        Caller tuple, independent API URL, plan/journal and offline receipts are
        not serving-revision evidence. This readback is not source approval or
        admission authority: the trusted coordinator must still verify those.
        Missing metadata and mixed/rolled-back revisions always refuse.
        """
        identity = self._request("GET", "/api/v1/platform/release-identity", cookie=cookie).payload
        expected = {
            "release_sha": checked.release_sha, "web_release_sha": checked.release_sha,
            "manifest_digest": checked.manifest_digest, "web_manifest_digest": checked.manifest_digest,
            "release_profile": "dev-admin", "web_release_profile": "dev-admin",
        }
        if (identity.get("release_profile_valid") is not True
                or any(type(identity.get(key)) is not str or identity[key] != value
                       for key, value in expected.items())):
            raise ProvisioningRefused("PROVISIONING_PROMOTED_RELEASE_MISMATCH")

    @_journal_session
    def execute(
        self, plan: Any, *, admin_password: str, new_password: str,
        release_sha: str, manifest_digest: str,
    ) -> dict[str, Any]:
        """After foreground approval/admission: invite, accept, read back, logout.

        Secrets are separate memory-only arguments, never part of plan/receipt.
        Binding must use this same new pair only after this function succeeds.
        A reserved root cannot be replayed even after successful lifecycle proof.
        """
        reservation = None
        admin_cookie = new_cookie = ""
        result = None
        failed = False
        try:
            if (not isinstance(admin_password, str) or not admin_password
                    or not isinstance(new_password, str) or not 12 <= len(new_password) <= 1024
                    or admin_password == new_password):
                raise ProvisioningRefused("PROVISIONING_CREDENTIAL_INPUT_INVALID")
            self._observe_admission(plan, release_sha=release_sha, manifest_digest=manifest_digest)
            admin_cookie = self._login("ajoe734", admin_password)
            self._session(admin_cookie, "ajoe734")
            # Fresh trusted before-state: the authoritative user inventory and
            # the verified session principal must agree on the ACTUAL roles.
            # The consent receipt's role list bounds them; it is never restored.
            records = self._users(admin_cookie)
            original = self._original(records)
            self._principal(admin_cookie, PRESERVED_ACCOUNT_ID, original["roles"])
            checked = validate_foreground_plan(
                plan, original_account=original, release_sha=release_sha,
                manifest_digest=manifest_digest, now=self._journal._now(),
            )
            if any(str(r.get("username", "")).casefold() == checked.username.casefold()
                   or str(r.get("email", "")).casefold() == checked.email.casefold() for r in records):
                raise ProvisioningRefused("PROVISIONING_ACCOUNT_EXISTS")
            # Observe the serving pair before consuming the single-use root.
            self._promoted_release(admin_cookie, checked)
            self._observe_admission(plan, release_sha=release_sha, manifest_digest=manifest_digest)
            reservation = self._journal.reserve(plan, original_account=original,
                                                release_sha=release_sha, manifest_digest=manifest_digest)
            self._observe_admission(plan, release_sha=release_sha, manifest_digest=manifest_digest)
            issued = self._request("POST", "/api/v1/operator/users/invitations", cookie=admin_cookie,
                                   body={"email": checked.email, "lifetime_seconds": 3600}, status=201).payload
            invitation = issued.get("invitation_id")
            token = issued.get("token")
            if (issued.get("status") != "invited" or issued.get("tenant_id") != TENANT_ID
                    or not isinstance(invitation, str) or str(UUID(invitation)) != invitation
                    or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)):
                raise ProvisioningRefused("PROVISIONING_INVITATION_INVALID")
            # A rollback/mixed-revision transition after issue cannot authorize
            # acceptance. Leave the invitation reserved for explicit recovery.
            self._promoted_release(admin_cookie, checked)
            self._observe_admission(plan, release_sha=release_sha, manifest_digest=manifest_digest)
            accepted = self._request("POST", "/auth/invitations", body={
                "invitation_id": invitation, "token": token, "username": checked.username,
                "password": new_password,
            }, status=201).payload
            account = accepted.get("account_id")
            if (accepted.get("status") != "accepted" or accepted.get("invitation_id") != invitation
                    or accepted.get("tenant_id") != TENANT_ID or not isinstance(account, str)
                    or str(UUID(account)) != account or account == PRESERVED_ACCOUNT_ID):
                raise ProvisioningRefused("PROVISIONING_ACCEPTANCE_INVALID")
            self._observe_admission(plan, release_sha=release_sha, manifest_digest=manifest_digest)
            new_cookie = self._login(checked.username, new_password)
            self._session(new_cookie, checked.username)
            self._principal(new_cookie, account, ["platform_admin"])
            after = self._users(new_cookie)
            if self._original(after) != original:
                raise ProvisioningRefused("PROVISIONING_ORIGINAL_ACCOUNT_CHANGED")
            own = [r for r in after if r.get("subject_id") == account and r.get("username") == checked.username
                   and r.get("email") == checked.email
                   and r.get("attributes", {}).get("identity_source") == "identity.accounts"]
            events = self._request("GET", "/api/v1/operator/users/audit-trail", cookie=new_cookie).payload.get("events")
            provenance = (invitation_provenance(own[0], events) if len(own) == 1
                          and isinstance(events, list) and all(isinstance(e, dict) for e in events) else None)
            if (provenance is None or provenance["invitation_id"] != invitation
                    or provenance["issuer_account_id"] != PRESERVED_ACCOUNT_ID
                    or provenance["issue_event_id"] != issued.get("audit_event_id")
                    or provenance["accept_event_id"] != accepted.get("audit_event_id")):
                raise ProvisioningRefused("PROVISIONING_PROVENANCE_INVALID")
            self._promoted_release(new_cookie, checked)
            self._observe_admission(plan, release_sha=release_sha, manifest_digest=manifest_digest)
            result = {**checked.to_receipt(), "stage": "web-lifecycle-verified",
                      "serving_release_observed": True, "custody_approval_observed": True,
                      "source_approval_observed": True,
                      "account_id": account, **provenance, "credential_binding_verified": False,
                      "deployment_success": False, "live_gate_passed": False}
        except Exception:
            failed = True
        finally:
            # Logout only the two sessions created here; never revoke other sessions.
            for cookie in (new_cookie, admin_cookie):
                if cookie:
                    try:
                        logout = self._request("POST", "/auth/logout", cookie=cookie).payload
                        if logout.get("ok") is not True:
                            failed = True
                        self._request("GET", "/auth/session", cookie=cookie, status=401)
                    except Exception:
                        failed = True
            if failed and reservation is not None:
                try:
                    self._journal.require_recovery(reservation)
                except Exception:
                    # Reservation itself remains the durable no-retry boundary.
                    pass
        if failed or result is None:
            raise ProvisioningRefused("PROVISIONING_LIFECYCLE_RECOVERY_REQUIRED" if reservation
                                     else "PROVISIONING_LIFECYCLE_REFUSED") from None
        return result


class GitHubDevSecretStore:
    """Pinned GitHub HTTPS API, no CLI, redirects, retries or plaintext uploads.

    The coordinator independently pins the approved human custodian's GitHub
    numeric ID/login, never deriving them from the plan. The token's /user is
    checked before preflight and again before PUT. This authenticates the token
    owner, NOT mailbox control, human consent, source approval or rollout ownership.
    The coordinator must exclude concurrent external secret writers: GitHub has
    no create-only conditional PUT. No secret value can be read back here.
    """

    NAME = CREDENTIAL_BUNDLE_SECRET_NAME

    def __init__(
        self, *, token: str, custodian_login: str, custodian_id: int, transport: Any = None,
    ) -> None:
        import httpx

        if (not isinstance(token, str) or not token or any(c in token for c in "\r\n")
                or not isinstance(custodian_login, str) or not _CUSTODIAN.fullmatch(custodian_login)
                or type(custodian_id) is not int or custodian_id <= 0):
            raise ProvisioningRefused("PROVISIONING_GITHUB_CONFIG_INVALID")
        self._custodian_login = custodian_login
        self._custodian_id = custodian_id
        self._client = httpx.Client(
            base_url="https://api.github.com", transport=transport, timeout=20,
            follow_redirects=False, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            return self._client.request(method, path, **kwargs)
        except Exception:
            raise ProvisioningRefused("PROVISIONING_GITHUB_UNCERTAIN") from None

    def _authenticate_custodian(self, expected_custodian: Any) -> None:
        """Fresh pinned token-owner evidence, never a caller approval receipt."""
        try:
            if (type(expected_custodian) is not str
                    or expected_custodian.casefold() != self._custodian_login.casefold()):
                raise ValueError
            response = self._request("GET", "/user")
            user = response.json()
            if (response.status_code != 200 or not isinstance(user, dict)
                    or user.get("type") != "User" or type(user.get("id")) is not int
                    or user["id"] != self._custodian_id or type(user.get("login")) is not str
                    or user["login"].casefold() != self._custodian_login.casefold()):
                raise ValueError
        except Exception:
            raise ProvisioningRefused("PROVISIONING_CUSTODIAN_UNVERIFIED") from None

    def prepare(self, *, expected_custodian: Any) -> tuple[str, str, str]:
        """Authenticate the pinned custodian, then observe repo/key/secret absence."""
        import base64

        try:
            self._authenticate_custodian(expected_custodian)
            response = self._request("GET", f"/repos/{REPOSITORY}")
            repo = response.json()
            if (response.status_code != 200 or repo.get("full_name") != REPOSITORY
                    or type(repo.get("id")) is not int or repo["id"] <= 0):
                raise ValueError("repository mismatch")
            path = f"/repositories/{repo['id']}/environments/dev/secrets"
            existing = self._request("GET", f"{path}/{self.NAME}")
            if existing.status_code != 404:
                raise ValueError("binding exists or absence unproven")
            response = self._request("GET", f"{path}/public-key")
            key = response.json()
            if (response.status_code != 200 or not isinstance(key.get("key_id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key["key_id"])
                    or not isinstance(key.get("key"), str)
                    or len(base64.b64decode(key["key"], validate=True)) != 32):
                raise ValueError("invalid key")
            return path, key["key_id"], key["key"]
        except Exception:
            raise ProvisioningRefused("PROVISIONING_GITHUB_PREFLIGHT_REFUSED") from None

    def write(
        self, prepared: tuple[str, str, str], bundle: dict[str, Any], *, expected_custodian: Any,
    ) -> None:
        import base64

        from nacl.public import PublicKey, SealedBox

        try:
            self._authenticate_custodian(expected_custodian)
            path, key_id, public_key = prepared
            clear = json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
            encrypted = SealedBox(PublicKey(base64.b64decode(public_key, validate=True))).encrypt(clear)
            response = self._request("PUT", f"{path}/{self.NAME}", json={
                "key_id": key_id, "encrypted_value": base64.b64encode(encrypted).decode(),
            })
            # 204 would mean an existing secret was replaced (external writer race).
            # A timeout/204/error is never inferred to be an acknowledged creation.
            if response.status_code != 201:
                raise ValueError("creation not acknowledged")
        except Exception:
            raise ProvisioningRefused("PROVISIONING_GITHUB_UNCERTAIN") from None


@dataclass(frozen=True)
class DevCredentialBundle:
    """Memory-only matched pair. Never serialize, print or pass to a subprocess."""

    username: str
    password: str = field(repr=False)
    account_id: str
    tenant_id: str
    execution_id: str


def read_dev_credential_bundle(
    raw: str, *, environment: str, release_profile: str,
) -> DevCredentialBundle | None:
    """Empty means legacy binding; any nonempty invalid bundle refuses fallback.

    This is a secret/configuration reader, NOT human approval or lifecycle proof.
    The unchanged gate must still authenticate the account and prove invitation
    provenance. The bundle is standing dev configuration, not candidate-specific.
    Neither an acknowledged upload nor a successful parse is activation evidence.
    """
    if raw == "":
        return None
    try:
        if (not isinstance(raw, str) or len(raw) > 16384
                or environment != "dev" or release_profile != "dev-admin"):
            raise ValueError

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        bundle = json.loads(raw, object_pairs_hook=unique)
        keys = {"schema_version", "authorization_id", "execution_id", "repository",
                "environment", "tenant_id", "account_id", "username", "password"}
        if (not isinstance(bundle, dict) or set(bundle) != keys
                or type(bundle["schema_version"]) is not int or bundle["schema_version"] != 1
                or not all(type(bundle[k]) is str for k in keys - {"schema_version"})
                or bundle["authorization_id"] != AUTHORIZATION_ID
                or bundle["repository"] != REPOSITORY or bundle["environment"] != "dev"
                or bundle["tenant_id"] != TENANT_ID
                or not _USERNAME.fullmatch(bundle["username"])
                or bundle["username"].casefold() == "ajoe734"
                or not 12 <= len(bundle["password"]) <= 1024):
            raise ValueError
        for key in ("account_id", "execution_id"):
            value = UUID(bundle[key])
            if value.int == 0 or str(value) != bundle[key]:
                raise ValueError
        if bundle["account_id"] == PRESERVED_ACCOUNT_ID:
            raise ValueError
        return DevCredentialBundle(bundle["username"], bundle["password"], bundle["account_id"],
                                   bundle["tenant_id"], bundle["execution_id"])
    except Exception:
        # JSON decoding/UUID errors may embed secret input: static code only.
        raise ProvisioningRefused("PROVISIONING_CREDENTIAL_BUNDLE_INVALID") from None


def credential_bundle_acknowledged(bundle: DevCredentialBundle, events: list[Any]) -> bool:
    """Authenticated tenant-audit projection only; never a local upload receipt.

    Requires reserved root + intent + durable ACK, matching execution/account and
    original tuple. Recovery/extra/ambiguous events fail closed. Standing binding
    may be used for later admitted candidates without relabelling its creation.
    """
    try:
        grouped = []
        for cls, stages in ((ProvisioningJournal, ["reserved"]),
                            (DevCredentialBundleExecutor, ["binding-intent", "binding-acknowledged"])):
            found = [e for e in events if e.get("event_type") == cls._TYPE
                     or e.get("correlation_id") == cls._CORRELATION]
            found.sort(key=lambda e: datetime.fromisoformat(e["timestamp"]))
            if len(found) != len(stages):
                return False
            for event, stage in zip(found, stages, strict=True):
                m = event["metadata"]
                if (event["event_type"] != cls._TYPE or event["actor"] != cls._ACTOR
                        or event["action"] != ("DEV_SMOKE_RESERVATION" if cls is ProvisioningJournal else "DEV_SMOKE_BINDING")
                        or event["resource"] != cls._CORRELATION or event["correlation_id"] != cls._CORRELATION
                        or event["outcome"] != "success" or not _aware(datetime.fromisoformat(event["timestamp"]))
                        or str(UUID(event["event_id"])) != event["event_id"] or UUID(event["event_id"]).int == 0
                        or set(m) != cls._KEYS or m["stage"] != stage
                        or m["authorization_id"] != AUTHORIZATION_ID or m["tenant_id"] != bundle.tenant_id
                        or m["execution_id"] != bundle.execution_id or m["execution_authorized"] is not False
                        or m["secret_values_redacted"] is not True or not _SHA.fullmatch(m["release_sha"])
                        or not _DIGEST.fullmatch(m["manifest_digest"])
                        or not _DIGEST.fullmatch(m["plan_digest"])):
                    return False
                if cls is DevCredentialBundleExecutor and (
                        m["account_id"] != bundle.account_id or m["secret_name"] != GitHubDevSecretStore.NAME
                        or m["credential_binding_verified"] is not False):
                    return False
            grouped.append(found)
        ordered = grouped[0] + grouped[1]
        if len({e["event_id"] for e in ordered}) != 3:
            return False
        base = grouped[0][0]["metadata"]
        for e in grouped[1]:
            if any(e["metadata"][k] != base[k] for k in ProvisioningJournal._KEYS - {"stage"}):
                return False
        return all(datetime.fromisoformat(a["timestamp"]) <= datetime.fromisoformat(b["timestamp"])
                   for a, b in zip(ordered, ordered[1:], strict=False))
    except Exception:
        return False


class DevSmokeBindingJournal(LocalDevSmokeBindingJournal):
    """Foreground adapter; all local ledger predicates live in shared identity."""

    def __init__(self, *, journal: ProvisioningJournal | RemoteProvisioningJournal) -> None:
        self._journal = journal

    def inspect(self) -> dict[str, Any] | None:
        if type(self._journal) is RemoteProvisioningJournal:
            return self._journal.binding_inspect()
        return super().inspect()

    def _append(self, metadata: dict[str, Any], stage: str) -> AuditEvent:
        if type(self._journal) is RemoteProvisioningJournal:
            return self._journal.binding_append(metadata, stage)
        return super()._append(metadata, stage)


class DevCredentialBundleExecutor(DevSmokeBindingJournal):
    """Foreground lifecycle + same-pair encrypted staging, NOT rollout authority.

    A SINGLE encrypted JSON secret contains the matched username/password plus
    tuple/account identifiers. It cannot mix the preserved account's password or
    optional bootstrap credential. Existing vars/secrets are untouched. The
    workflow consumes the bundle only as a matched pair; acknowledged PUT is NOT
    proof of secret value, deployment or gate success. The approved coordinator must own
    source/admission/custody checks before calling this library.

    Durable intent precedes PUT. Crash/lost reply/readback/journal failures leave
    intent or quarantine as a no-retry boundary. No password reset, secret delete,
    value retrieval, rollback, replacement or automatically repeated PUT exists.
    """

    def __init__(self, *, lifecycle: WebInvitationExecutor, store: GitHubDevSecretStore) -> None:
        if not isinstance(lifecycle, WebInvitationExecutor) or not isinstance(store, GitHubDevSecretStore):
            raise ProvisioningRefused("PROVISIONING_BINDING_CONFIG_INVALID")
        self._lifecycle = lifecycle
        self._journal = lifecycle._journal
        self._store = store

    def execute_and_check_gate(
        self, plan: Any, *, gate_config: Any, worker_job: str,
        gcp_region: str, gcp_project: str, **credentials: Any,
    ) -> dict[str, Any]:
        """Foreground-only same-process staging -> unchanged final live gate.

        The caller must already hold authenticated custody/source approval and
        exact dev admission, and own the promoted rollout's rollback boundary.
        This library does NOT promote traffic or commit deployment. It avoids
        GitHub's stale same-job secret context by passing the newly accepted pair
        directly to the canonical evaluator in memory, never to shell/env/argv.
        It does not verify GitHub's decrypted value: a later ordinary consumer
        must still do that. No caller gate callback or passing receipt is accepted.
        """
        config = self._prepare_gate(plan, gate_config=gate_config, worker_job=worker_job,
                                    gcp_region=gcp_region, gcp_project=gcp_project, **credentials)
        return self._execute_gate(plan, config=config, worker_job=worker_job,
                                  gcp_region=gcp_region, gcp_project=gcp_project, **credentials)

    def _prepare_gate(
        self, plan: Any, *, gate_config: Any, worker_job: str,
        gcp_region: str, gcp_project: str, **credentials: Any,
    ) -> Any:
        from delivery_toolchain.e2e import check_live_e2e_gate as gate

        # Validate every known gate input before login, reservation or PUT. The
        # template's standing/initial credentials are never used or inherited.
        try:
            if (type(gate_config) is not gate.GateConfig or not isinstance(plan, dict)
                    or gate_config.expected_sha != credentials["release_sha"]
                    or gate_config.expected_manifest_digest != credentials["manifest_digest"]
                    or gate_config.release_profile != "dev-admin"
                    or gate_config.expected_deployment != "dev"
                    or gate_config.allow_http is not False
                    or gate_config.external_provider_mode != "disabled"
                    or gate_config.web_url != self._lifecycle._origin
                    or any(not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", v)
                           for v in (worker_job, gcp_region, gcp_project))):
                raise ValueError
            config = replace(
                gate_config, dev_admin_username=plan["username"],
                dev_admin_password=credentials["new_password"],
                dev_admin_initial_password="", bootstrap_admin_username="",
                bootstrap_admin_password="", dev_admin_bundle_account_id="",
                dev_admin_bundle_tenant_id="", dev_admin_bundle_execution_id="",
            )
            if not all(c.ok for c in gate.validate_config(config)):
                raise ValueError
        except Exception:
            raise ProvisioningRefused("PROVISIONING_GATE_CONFIG_INVALID") from None

        return config

    @_journal_session
    def _execute_gate(self, plan: Any, *, config: Any, worker_job: str,
                      gcp_region: str, gcp_project: str, **credentials: Any) -> dict[str, Any]:
        from delivery_toolchain.e2e import check_live_e2e_gate as gate

        receipt = self.execute(plan, **credentials)
        try:
            config = replace(
                config, dev_admin_bundle_account_id=receipt["account_id"],
                dev_admin_bundle_tenant_id=TENANT_ID,
                dev_admin_bundle_execution_id=receipt["execution_id"],
            )
            correlation = f"corr-dev-smoke-{receipt['execution_id']}"
            http = gate.UrllibHttpClient(
                gate._normalize_origin(config.api_url, allow_http=False), timeout=config.timeout,
                bearer_token=config.bearer_token, operator_role=config.operator_role,
                operator_subject=config.operator_subject, operator_tenant=config.operator_tenant,
                correlation_id=correlation, transport_token=config.api_transport_token,
            )
            worker = gate.CloudRunWorkerDriver(
                job=worker_job, region=gcp_region, project=gcp_project,
                max_jobs=len(config.snapshot_provider_ids) + 4,
                timeout=max(config.worker_deadline_seconds, 60.0),
            )
            checks, report = gate.evaluate_gate(
                config, http=http, worker_driver=worker, correlation_id=correlation,
                now=self._journal._now().isoformat(),
                web_http=gate._web_client(config, correlation),
            )
            # Only the canonical evaluator's complete result counts, not an
            # acknowledged upload, point-in-time readback or truthy status value.
            self._lifecycle._observe_admission(
                plan, release_sha=config.expected_sha, manifest_digest=config.expected_manifest_digest,
            )
            passed = bool(checks) and all(c.ok is True for c in checks)
            if (type(report.get("ok")) is not bool or report["ok"] != passed
                    or report.get("expected_release_sha") != config.expected_sha
                    or report.get("expected_deployment") != "dev"):
                raise ValueError
            if not passed:
                self._journal.require_recovery(self._journal.inspect())
            return {"provisioning": {**receipt, "live_gate_passed": passed,
                                     "deployment_success": False,
                                     "credential_binding_verified": False},
                    "gate": report}
        except Exception:
            try:
                self._journal.require_recovery(self._journal.inspect())
            except Exception:
                pass  # Reserved root/binding ACK still prevent lifecycle retry.
            raise ProvisioningRefused("PROVISIONING_GATE_RECOVERY_REQUIRED") from None

    @_journal_session
    def execute(self, plan: Any, **credentials: Any) -> dict[str, Any]:
        """Same password goes to acceptance/fresh-login AND encrypted bundle.

        No caller-provided lifecycle receipt is accepted as a creation proof.
        API acknowledgement cannot verify the decrypted GitHub secret: only a
        future normally admitted consumer's unchanged live gate can do that.
        """
        reservation = None
        metadata = None
        try:
            # Fail before account mutation if a staged binding already exists,
            # journal is unavailable, repository/key is wrong, or auth is refused.
            self._lifecycle._observe_admission(
                plan, release_sha=credentials["release_sha"], manifest_digest=credentials["manifest_digest"],
            )
            if self.inspect() is not None:
                raise ProvisioningRefused("PROVISIONING_BINDING_ALREADY_ATTEMPTED")
            prepared = self._store.prepare(expected_custodian=plan.get("recipient_custodian"))
            lifecycle = self._lifecycle.execute(plan, **credentials)
            reservation = self._journal.inspect()
            if reservation is None or reservation.stage != "reserved":
                raise ProvisioningRefused("PROVISIONING_RESERVATION_MISMATCH")
            metadata = {
                "authorization_id": AUTHORIZATION_ID, "execution_id": reservation.execution_id,
                "plan_digest": reservation.plan_digest, "release_sha": lifecycle["release_sha"],
                "manifest_digest": lifecycle["manifest_digest"], "tenant_id": TENANT_ID,
                "account_id": lifecycle["account_id"], "secret_name": self._store.NAME,
                "stage": "binding-intent", "execution_authorized": False,
                "credential_binding_verified": False, "secret_values_redacted": True,
            }
            self._append(metadata, "binding-intent")
            # Contains no initial-password fallback, token, email or admin password.
            bundle = {"schema_version": 1, "authorization_id": AUTHORIZATION_ID,
                      "execution_id": reservation.execution_id, "repository": REPOSITORY,
                      "environment": "dev", "tenant_id": TENANT_ID,
                      "account_id": lifecycle["account_id"], "username": plan["username"],
                      "password": credentials["new_password"]}
            self._lifecycle._observe_admission(
                plan, release_sha=credentials["release_sha"], manifest_digest=credentials["manifest_digest"],
            )
            self._store.write(prepared, bundle, expected_custodian=plan["recipient_custodian"])
            event = self._append(metadata, "binding-acknowledged")
            return {**lifecycle, "stage": "binding-acknowledged", "binding_audit_event_id": event.event_id,
                    "binding_write_acknowledged": True, "credential_binding_verified": False,
                    "live_gate_passed": False, "deployment_success": False}
        except Exception:
            if reservation is not None:
                if metadata is not None:
                    try:
                        self._append(metadata, "recovery-required")
                    except Exception:
                        pass  # Committed intent still forbids retry even if quarantine is unavailable.
                try:
                    self._journal.require_recovery(reservation)
                except Exception:
                    pass  # Root reservation still forbids account recreation.
            raise ProvisioningRefused("PROVISIONING_BINDING_RECOVERY_REQUIRED" if reservation
                                     else "PROVISIONING_BINDING_REFUSED") from None


class ForegroundDevSmokeRollout:
    """Explicit trusted-owner invocation of the EXISTING rollback-owned shell.

    No CLI, workflow toggle, arbitrary hook or caller-passed gate receipt. The
    owner constructs the actual executor with independent review/CI, consumed
    admission and recorded-consent pins, and supplies secrets only in memory.
    An inherited socket joins the shell's promoted-before-gate boundary to that
    executor. The canonical gate runs in THIS process with the newly accepted
    pair; the shell receives only a fixed completion acknowledgement. Failure
    leaves the shell's EXIT rollback armed and the durable journal forbids retry.
    """

    def __init__(self, *, binding: DevCredentialBundleExecutor) -> None:
        if type(binding) is not DevCredentialBundleExecutor:
            raise ProvisioningRefused("PROVISIONING_ROLLOUT_CONFIG_INVALID")
        self._binding = binding

    def execute(
        self, plan: Any, *, gate_config: Any, worker_job: str,
        gcp_region: str, gcp_project: str, deploy_env: dict[str, str],
        report_path: Any, deadline_seconds: float = 7200, **credentials: Any,
    ) -> dict[str, Any]:
        import socket
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        binding = self._binding
        # Validate before launching any cloud-capable shell or opening a session.
        config = binding._prepare_gate(plan, gate_config=gate_config, worker_job=worker_job,
                                       gcp_region=gcp_region, gcp_project=gcp_project, **credentials)
        try:
            if (type(deploy_env) is not dict or not all(type(k) is str and type(v) is str
                                                      for k, v in deploy_env.items())
                    or type(deadline_seconds) not in (int, float)
                    or not 60 <= deadline_seconds <= 7200):
                raise ValueError
            expected = {
                "ODAY_RELEASE_SHA": config.expected_sha,
                "MANIFEST_DIGEST": config.expected_manifest_digest,
                "ODP_DEPLOY_ENV": "dev", "ODP_RELEASE_PROFILE": "dev-admin",
                "ODP_EXTERNAL_PROVIDER_MODE": "disabled", "GCP_REGION": gcp_region,
                "GCP_PROJECT": gcp_project, "ODP_WEB_BASE_URL": config.web_url,
            }
            if any(deploy_env.get(k) != v for k, v in expected.items()):
                raise ValueError
            # Do not inherit the foreground owner's credentials into any child.
            # This environment is explicitly supplied, never dict(os.environ).
            forbidden = {
                "ODP_DEV_SMOKE_FOREGROUND_FD", "ODP_DEV_ADMIN_CREDENTIAL_BUNDLE",
                "ODP_DEV_ADMIN_PASSWORD", "ODP_DEV_ADMIN_INITIAL_PASSWORD",
                "ODP_DEV_BOOTSTRAP_ADMIN_PASSWORD", "ODP_IDENTITY_BOOTSTRAP_SECRET",
                "GH_TOKEN", "GITHUB_TOKEN", "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS",
            }
            secrets = (credentials["admin_password"], credentials["new_password"],
                       binding._store._client.headers["Authorization"].removeprefix("Bearer "))
            if (any(deploy_env.get(k) for k in forbidden)
                    or any(k.startswith("BASH_FUNC_") for k in deploy_env)
                    or any(secret and secret in value for secret in secrets for value in deploy_env.values())):
                raise ValueError
            # The deployment entrypoint must come from the exact approved clean
            # candidate checkout, not an arbitrary script/plugin supplied by a plan.
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                                  capture_output=True, text=True).stdout.strip()
            dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                   cwd=root, check=True, capture_output=True, text=True).stdout
            if head != config.expected_sha or dirty:
                raise ValueError
            report_path = Path(report_path)
            binding._lifecycle._observe_admission(
                plan, release_sha=config.expected_sha, manifest_digest=config.expected_manifest_digest,
            )
        except Exception:
            raise ProvisioningRefused("PROVISIONING_ROLLOUT_PREFLIGHT_REFUSED") from None

        parent, child = socket.socketpair()
        process = None
        result = None
        failure = False
        try:
            env = dict(deploy_env, ODP_DEV_SMOKE_FOREGROUND_FD=str(child.fileno()),
                       LIVE_E2E_REPORT=str(report_path))
            process = subprocess.Popen(
                ["/bin/bash", str(root / "product_ops/deployment/deploy_cloud_run_waji.sh")],
                cwd=root, env=env, pass_fds=(child.fileno(),),
            )
            child.close()
            parent.settimeout(deadline_seconds)
            payload = bytearray()
            while not payload.endswith(b"\n"):
                chunk = parent.recv(4096)
                if not chunk or len(payload) + len(chunk) > 32768:
                    raise ValueError
                payload.extend(chunk)
            context = json.loads(payload)
            required = {
                **expected, "LIVE_E2E_API_URL": config.api_url,
                "LIVE_E2E_WEB_URL": config.web_url, "LIVE_E2E_DEPLOYMENT_MODE": "dev",
                "WORKER_CANDIDATE_JOB": worker_job,
            }
            required.pop("ODP_WEB_BASE_URL")
            tokens = {"ODP_OPERATOR_SMOKE_BEARER_TOKEN", "ODP_API_INVOKER_TOKEN"}
            if (type(context) is not dict or context.keys() != required.keys() | tokens
                    or any(context[k] != v for k, v in required.items())
                    or any(type(context[k]) is not str or not context[k].strip() for k in tokens)):
                raise ValueError
            # Service credentials are freshly minted by the canonical shell;
            # all origins, tuple, profile, sources and worker remain owner-pinned.
            fresh = replace(config, bearer_token=context["ODP_OPERATOR_SMOKE_BEARER_TOKEN"],
                            api_transport_token=context["ODP_API_INVOKER_TOKEN"])
            result = binding.execute_and_check_gate(
                plan, gate_config=fresh, worker_job=worker_job,
                gcp_region=gcp_region, gcp_project=gcp_project, **credentials,
            )
            # Persist only canonical secret-redacted reports, never an approval
            # input. A report-write failure also keeps deployment uncommitted.
            serialized = json.dumps(result["gate"], indent=2, sort_keys=True) + "\n"
            if any(secret and secret in serialized for secret in secrets):
                raise ValueError
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(serialized, encoding="utf-8")
            passed = (result["provisioning"]["live_gate_passed"] is True
                      and result["gate"]["ok"] is True)
            parent.sendall(b"PASS\n" if passed else b"FAIL\n")
            failure = not passed
        except Exception:
            failure = True
            try:
                parent.sendall(b"FAIL\n")
            except OSError:
                pass
        finally:
            parent.close()
            child.close()
            if process is not None:
                # The original Popen handle and exit status are the only shell
                # completion proof. No log grep or independently supplied receipt.
                try:
                    status = process.wait(timeout=deadline_seconds)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        status = process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    failure = True  # Interrupted recovery is UNKNOWN, not success.
                    status = -1
            else:
                status = -1
        if failure or status != 0 or result is None:
            raise ProvisioningRefused("PROVISIONING_ROLLOUT_RECOVERY_REQUIRED") from None
        return {**result, "rollout": {"shell_exit_code": status, "deployment_success": True,
                                    "credential_binding_verified": False,
                                    "secret_values_redacted": True}}
