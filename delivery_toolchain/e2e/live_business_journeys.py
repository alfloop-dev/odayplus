#!/usr/bin/env python3
"""Six real business journeys for full-profile live acceptance.

ODP-BUSINESS-LIVE-E2E-COVERAGE-001.

``check_live_e2e_gate.py`` proves the deployed *runtime* (release binding,
persistence, provider posture, models, worker, audit) and, for the dev-only
``dev-admin`` scope, the platform-administrator password session. Neither is
business acceptance: no business role signs in, no business record changes,
nothing is read back. This module is the missing half. It drives six business
journeys through the deployed Web origin exactly as signed-in business users
would -- password form, sealed session cookie, BFF proxy -- and writes one
receipt that ``check_live_e2e_gate.py`` requires before it may claim *full
product acceptance*.

The six journeys (``JOURNEY_IDS``):

* ``operations`` -- a Store Ops issue workflow transition (operations manager),
  durable readback, audit.
* ``growth`` -- a PriceOps plan lifecycle action (pricing manager) and an AdLift
  incrementality job (marketing manager) whose durable job receipt must reach
  ``succeeded``.
* ``expansion`` -- a NetPlan scenario with real candidate options and model /
  solver provenance: solve, submit, the disclosure the network workspace renders
  (modelled and *unmodelled* constraint classes such as lease/schedule facts),
  and a distinct named approver's decision carrying the approval receipt.
* ``governance`` -- a business approval decision (not user administration).
* ``franchise`` -- a franchisee reads only its own store (ABAC) and submits a
  field report; a foreign store is refused.
* ``intake`` -- an assisted listing intake from an approved source or manual
  assisted entry is decided and read back; a blocked-source submission is
  rejected by policy (refused without persistence, or durably quarantined with
  the policy recorded and no retrieval).

Every journey shares one fail-closed skeleton:

1. **Preflight before any business write or worker/provider trigger.** The
   immutable admitted manifest is loaded and its digest recomputed; the sealed
   release profile -- never the caller's ``ODP_RELEASE_PROFILE`` -- decides the
   scope, and a ``dev-admin`` manifest is ``NOT_ADMITTED`` for full acceptance.
   The served runtime must report the exact release SHA and the sealed profile.
   A scope authorization must bind this exact release and manifest digest and
   name the tenant, actors, records, allowed actions, the bodies of every write
   (authored by the named authorizer, never generated here) and, where
   required, the named approval. Each actor signs in through the Web password
   form, reads its actual account/tenant/roles from the authenticated self
   principal surface, and must be refused platform user administration, so a
   ``platform_admin`` account can never stand in for a business role. The
   authorized record is read and must be live (no fixture/seed/mock marker)
   with its provenance. Until all of that holds the HTTP wrapper refuses every
   business mutation, so a refused preflight provably sent zero business
   writes and zero worker/provider triggers; the receipt counts say so.
2. **Negative probes.** A foreign-tenant record must be refused, and a real
   account holding a role *without* the permission (``denied`` actor) must be
   refused the journey's first write while the record stays unchanged.
3. **Write -> durable readback -> audit / terminal outcome.** Each write has
   its own correlation id; the readback must show the same record in a
   different state; the audit log must hold an event for every write; async
   work must reach ``succeeded``.

A missing credential, scope, record, model, source or named approval is
``BLOCKED`` naming the dependency -- never skipped, never green. A live
misbehaviour (tenant leak, authorization bypass, lost write, missing audit) is
``FAILED``. The runner exits 0 only when every selected journey ``PASSED``.

Nothing here may run against a live environment from this task: the live
execution belongs to ODP-BUSINESS-LIVE-E2E-ACCEPTANCE-001 once its admission
actually exists. Offline regressions live in
``tests/e2e/test_live_business_journeys.py`` and are not live acceptance.

Secrets come from named environment variables and are never written: no
password, bearer token, cookie or session value appears in the receipt.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
LIVE_DATA_GATE = ROOT / "delivery_toolchain" / "e2e" / "check_live_production_data.py"
LIVE_E2E_GATE = ROOT / "delivery_toolchain" / "e2e" / "check_live_e2e_gate.py"
DEFAULT_OUTPUT = ROOT / ".odp_data" / "live-e2e-gate" / "business-journeys-receipt.json"
RUNNER_PATH = "delivery_toolchain/e2e/live_business_journeys.py"

RECEIPT_KIND = "odp.live-business-journeys"
RECEIPT_SCHEMA_VERSION = 2
SCOPE_KIND = "odp.live-business-journey-scope"
SCOPE_SCHEMA_VERSION = 2

API_URL_ENV = "ODP_LIVE_E2E_API_URL"
WEB_URL_ENV = "ODP_LIVE_E2E_WEB_URL"
EXPECTED_SHA_ENV = "ODAY_RELEASE_SHA"
MANIFEST_DIGEST_ENV = "ODP_RELEASE_MANIFEST_DIGEST"
RELEASE_MANIFEST_ENV = "ODP_RELEASE_MANIFEST_PATH"
RELEASE_PROFILE_ENV = "ODP_RELEASE_PROFILE"
API_TRANSPORT_TOKEN_ENV = "ODP_API_INVOKER_TOKEN"
SCOPE_ENV = "ODP_LIVE_JOURNEY_SCOPE_PATH"
RECEIPT_ENV = "ODP_LIVE_BUSINESS_JOURNEY_RECEIPT"

RELEASE_PROFILE_FULL = "full"
RELEASE_PROFILE_DEV_ADMIN = "dev-admin"
KNOWN_RELEASE_PROFILES = frozenset({RELEASE_PROFILE_FULL, RELEASE_PROFILE_DEV_ADMIN})

STATUS_PASSED = "PASSED"
STATUS_FAILED = "FAILED"
STATUS_BLOCKED = "BLOCKED"
STATUS_NOT_ADMITTED = "NOT_ADMITTED"

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DENIED_STATUSES = frozenset({401, 403})
#: A foreign-tenant / foreign-store record must be refused. 404 is accepted
#: because a tenant-isolated repository legitimately answers "not in your
#: tenant" as "not found"; it is never accepted for the actor's own record.
CROSS_TENANT_REFUSALS = frozenset({403, 404, 422})
AUTH_PATHS = frozenset({"/login", "/auth/logout", "/auth/session"})
#: The platform user-administration surface. A business actor must be refused
#: here; an account served it holds platform administration grants and may
#: not stand in for a business role.
USER_ADMIN_PROBE_PATH = "/api/v1/operator/users"
PRINCIPAL_PATH = "/api/v1/auth/principal"
RELEASE_IDENTITY_PATH = "/api/v1/platform/release-identity"
PRICEOPS_AUDIT_EVENTS = {
    "submit": "priceops.submitted.v1",
    "approve": "priceops.approved.v1",
    "activate": "priceops.activated.v1",
    "simulate": "priceops.simulated.v1",
    "optimize": "priceops.optimized.v1",
}
AUDIT_EVENTS_PATH = "/api/v1/audit/events"
#: ``event_type`` of the API's RBAC allow/deny decisions (shared/audit/policy.py).
SECURITY_EVENT_TYPE = "security.authorization"
#: Actor slots a journey may use. ``denied`` is always required.
ACTOR_SLOTS = ("primary", "approver", "marketer", "denied")

#: Keys a receipt must never carry, at any depth.
FORBIDDEN_RECEIPT_KEYS = frozenset(
    {
        "password",
        "token",
        "bearer",
        "cookie",
        "cookies",
        "set-cookie",
        "authorization",
        "session",
        "secret",
    }
)

DEPENDENCY_ACTIONS: Mapping[str, str] = {
    "release": (
        "Point the runner at the immutable admitted manifest of the deployed release and "
        "its digest (ODP_RELEASE_MANIFEST_PATH / ODP_RELEASE_MANIFEST_DIGEST)."
    ),
    "release-profile": (
        "Full business acceptance needs a release admitted under the full profile; a "
        "dev-admin manifest is never upgraded by the caller's ODP_RELEASE_PROFILE."
    ),
    "scope-authorization": (
        "Provide the journey scope authorization bound to this exact release SHA and "
        "manifest digest (tenant, actors, records, allowed actions, write bodies)."
    ),
    "business-credential": (
        "Provision the named business account for this journey actor and expose its "
        "password through the journey credential environment variables."
    ),
    "business-role": (
        "Sign in with an account that holds the journey's business role; platform_admin "
        "is never promoted into a business role."
    ),
    "business-data": (
        "The authorized record is missing, not live, or lacks provenance; bind the "
        "journey scope to a real record in the authorized tenant."
    ),
    "model": (
        "The journey depends on production model/solver provenance that is not present; "
        "publish and approve it before claiming the journey."
    ),
    "source": (
        "The intake journey needs an approved listing source or authorized manual "
        "assisted entry, and a blocked-source probe, in scope."
    ),
    "named-approval": (
        "The journey requires a named business approval reference and a distinct approver account."
    ),
    "tenant-isolation": (
        "A foreign-tenant or foreign-store record was served; restore tenant/ABAC "
        "binding before any release."
    ),
    "authorization": (
        "An account without the permission was allowed to write; restore RBAC before any release."
    ),
    "business-write": "The business write was refused or failed; inspect the API response.",
    "durable-readback": (
        "The write did not survive a fresh read; restore the durable repository binding."
    ),
    "audit": "No durable audit event was found for the write's correlation id.",
    "worker": "The journey's durable job never reached a successful terminal state.",
    "disclosure": (
        "The disclosure the workspace renders (modelled/unmodelled constraint classes, "
        "model provenance, approval receipt) was missing."
    ),
    "policy": "The policy rejection was not enforced, or it retrieved/persisted listing data.",
    "data-binding": "A fixture/mock/seed surrogate reached a live business response.",
    "web": "The deployed Web origin or its BFF proxy is not usable.",
}

#: Dependencies meaning "the admission is not there" (BLOCKED), as opposed to
#: "the live system misbehaved" (FAILED).
BLOCKING_DEPENDENCIES = frozenset(
    {
        "release",
        "release-profile",
        "scope-authorization",
        "business-credential",
        "business-role",
        "business-data",
        "model",
        "source",
        "named-approval",
        "web",
    }
)


def _load_surrogate_scanner() -> Callable[[Any], list[str]]:
    """Share the fixture/seed marker vocabulary with the live data gate."""

    spec = importlib.util.spec_from_file_location(
        "odp_check_live_production_data_journeys", LIVE_DATA_GATE
    )
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load surrogate marker vocabulary from {LIVE_DATA_GATE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.find_surrogate_values


find_surrogate_values = _load_surrogate_scanner()


# ---------------------------------------------------------------------------
# HTTP surface (shape-compatible with check_live_e2e_gate.HttpClient)
# ---------------------------------------------------------------------------


class Response(Protocol):
    status: int
    payload: dict[str, Any]
    cookies: dict[str, str]
    error: str

    @property
    def failed(self) -> bool: ...


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> Response: ...


class PreflightViolation(RuntimeError):
    """A business mutation was attempted before preflight completed."""


@dataclass
class RequestLedger:
    """Counts what a journey actually sent, so a receipt can prove zero writes."""

    business_writes: int = 0
    worker_triggers: int = 0
    auth_requests: int = 0
    reads: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "business_writes": self.business_writes,
            "worker_or_provider_triggers": self.worker_triggers,
            "auth_requests": self.auth_requests,
            "reads": self.reads,
        }


class LedgerHttp:
    """Wraps the Web client; refuses business mutations until preflight passed."""

    def __init__(self, inner: HttpClient, ledger: RequestLedger) -> None:
        self._inner = inner
        self._ledger = ledger
        self.writes_armed = False

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        worker_trigger: bool = False,
    ) -> Response:
        verb = method.upper()
        bare = urllib.parse.urlsplit(path).path
        if bare in AUTH_PATHS:
            self._ledger.auth_requests += 1
        elif verb in MUTATING_METHODS:
            if not self.writes_armed:
                raise PreflightViolation(f"business write before preflight: {verb} {bare}")
            self._ledger.business_writes += 1
            if worker_trigger:
                self._ledger.worker_triggers += 1
        else:
            self._ledger.reads += 1
        return self._inner.request(
            verb,
            path,
            authenticated=False,
            body=body,
            headers=headers,
            follow_redirects=False,
        )


# ---------------------------------------------------------------------------
# Journey specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One HTTP step.

    ``path`` and ``body_overrides`` values are templates over the journey
    variables: ``{record.<key>}`` / ``{foreign.<key>}`` from the scope,
    ``{write.<key>.<field>}`` from the scope's authorized writes and
    ``{captured.<name>}`` from earlier ``capture`` rules.
    """

    name: str
    method: str
    path: str
    actor: str = "primary"
    #: Key in ``scope.writes`` whose ``body`` is sent. Bodies are authored by
    #: the named scope authorizer, never generated by this runner.
    body_from: str = ""
    body_overrides: Mapping[str, str] = field(default_factory=dict)
    expect: frozenset[int] = frozenset({200})
    worker_trigger: bool = False
    idempotent: bool = False
    #: name -> dotted path in the response payload.
    capture: Mapping[str, str] = field(default_factory=dict)
    #: dotted paths that must be present and non-empty in the response.
    required: tuple[str, ...] = ()
    #: Expected platform audit event type prefix for this write. Matched only
    #: against business events in ``/api/v1/audit/events`` carrying this
    #: write's unique correlation id; ``security.authorization`` decisions never
    #: count as the business audit.
    audit_event: str = ""
    #: Where the real API records the business audit outside the platform log
    #: (the operator shell's own audit feed), the write response returns the
    #: event: ``audit_response_id`` is its id and ``audit_response_correlation``
    #: the correlation id it must carry (this write's).
    audit_response_id: str = ""
    audit_response_correlation: str = ""
    #: response path -> template the write response must echo, binding the
    #: write to the authorized record and action (e.g. ``approvalId``).
    response_bindings: Mapping[str, str] = field(default_factory=dict)
    #: response paths that must be non-empty lists (a real business payload,
    #: not an empty job).
    nonempty_lists: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobSpec:
    """Durable async work started by a write and polled to a terminal status.

    ``succeeded`` alone is not the business outcome: a worker that had nothing
    to do also succeeds. The terminal job payload must carry the business
    result (``result_required`` / ``result_nonempty`` / ``result_bindings``)
    and, after it, the durable result read (``result_read``) must serve that
    result for the authorized record with its provenance.
    """

    id_capture: str
    path: str
    status_path: str = "status"
    actor: str = "primary"
    #: paths on the terminal job payload that must be present and non-empty.
    result_required: tuple[str, ...] = ()
    #: paths on the terminal job payload that must be non-empty lists.
    result_nonempty: tuple[str, ...] = ()
    #: path on the terminal job payload -> template it must contain/equal.
    result_bindings: Mapping[str, str] = field(default_factory=dict)
    #: durable read of the produced result, after the job is terminal.
    result_read: Step | None = None
    result_read_required: tuple[str, ...] = ()
    result_read_bindings: Mapping[str, str] = field(default_factory=dict)
    #: path on the durable result whose value identifies the result record.
    result_id_path: str = ""
    #: ``captured`` name of the result id read before the write; the durable
    #: result must be a new one.
    previous_result_capture: str = ""


@dataclass(frozen=True)
class PolicySpec:
    """A submission the source policy must refuse or durably quarantine."""

    submit: Step
    #: Read proving a refused submission persisted nothing (``items`` empty).
    absence: Step
    #: Response path of the created record when the policy quarantines instead.
    record_id_path: str
    stage_path: str
    quarantine_stage: str
    policy_path: str
    blocked_policies: frozenset[str]
    #: Paths that must be empty on a quarantined record (no retrieval).
    not_retrieved_paths: tuple[str, ...]
    readback_path: str


@dataclass(frozen=True)
class UiStep:
    """One browser step of the live program: ``click``, ``visible`` or ``text``.

    ``selector`` is a ``data-testid`` (template); ``text`` (template) must be
    contained in the element for ``text`` steps.
    """

    action: str
    selector: str
    text: str = ""


UI_STEP_ACTIONS = frozenset({"click", "visible", "text"})


@dataclass(frozen=True)
class UiCheckSpec:
    """A real browser assertion against the deployed Web workspace.

    It runs between two writes (``after_write``), so the journey proves the
    user actually *sees* the outcome (e.g. the NetPlan disclosure after the
    solve) before the next decision is taken. The live Playwright program
    signs in through the password form and asserts the steps against the
    deployed data; it never intercepts a request (no ``page.route``).
    """

    name: str
    after_write: str
    actor: str
    #: Workspace URL (template) the browser opens.
    url: str
    steps: tuple[UiStep, ...]


@dataclass(frozen=True)
class DomainAuditSpec:
    """A durable business audit trail served by the record's own read model.

    The governance service records its decisions in its own durable audit
    trail (``auditRows``), not in the platform audit log. The row must be new
    (absent before the write) and must name the authorized record.
    """

    write: str
    #: Path of the audit row list on the read/readback payload.
    list_path: str
    #: row field -> template the new row must carry.
    match: Mapping[str, str]
    id_field: str = "id"
    #: Label of the trail in the receipt (``event_type`` of the audit ref).
    event_type: str = ""


@dataclass(frozen=True)
class JourneySpec:
    journey_id: str
    title: str
    #: The Web workspace the user works in (the selector of record).
    selector: str
    #: actor slot -> business roles the authorized account is expected to hold.
    actor_roles: Mapping[str, frozenset[str]]
    #: X-Operator-Role persona for operator-console routes ("" for domain routes).
    operator_role: str
    required_records: tuple[str, ...]
    required_foreign: tuple[str, ...]
    #: write key -> allowed ``action`` values the scope may authorize.
    writes_allowed: Mapping[str, frozenset[str]]
    read: Step
    writes: tuple[Step, ...]
    readback: Step
    cross_tenant: Step
    record_id_path: str
    state_paths: tuple[str, ...]
    provenance_paths: tuple[str, ...]
    #: ``scope.records`` key the served record id must equal.
    record_key: str = ""
    #: Paths where the served record carries its own tenant; each must equal
    #: the authorized tenant before any write is armed.
    tenant_paths: tuple[str, ...] = ()
    #: read path -> template the served record must equal (record binding).
    record_bindings: Mapping[str, str] = field(default_factory=dict)
    #: write key -> body field that must equal the authorized action.
    action_fields: Mapping[str, str] = field(default_factory=dict)
    #: write key -> (actions, body field, minimum length): a reason the real
    #: service requires for those actions (refused as 422 otherwise).
    reason_rules: Mapping[str, tuple[frozenset[str], str, int]] = field(default_factory=dict)
    #: write key -> body fields that must be non-empty lists.
    body_nonempty: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: path -> (allowed values, dependency) the read record must satisfy.
    allowed_values: Mapping[str, tuple[frozenset[str], str]] = field(default_factory=dict)
    model_provenance_paths: tuple[str, ...] = ()
    requires_named_approval: bool = False
    job: JobSpec | None = None
    #: keys that must *exist* on the readback (an empty list is a disclosure).
    disclosure_keys: tuple[str, ...] = ()
    #: paths that must be present and non-empty on the readback.
    readback_required: tuple[str, ...] = ()
    #: readback path -> template the readback must equal.
    readback_bindings: Mapping[str, str] = field(default_factory=dict)
    domain_audit: DomainAuditSpec | None = None
    policy: PolicySpec | None = None
    extra_reads: tuple[Step, ...] = ()
    ui_checks: tuple[UiCheckSpec, ...] = ()

    @property
    def actors(self) -> tuple[str, ...]:
        used = {"primary", "denied"}
        used.update(step.actor for step in self.writes)
        used.update(step.actor for step in self.extra_reads)
        used.update(check.actor for check in self.ui_checks)
        if self.job is not None:
            used.add(self.job.actor)
        return tuple(slot for slot in ACTOR_SLOTS if slot in used)

    @property
    def granted_actors(self) -> tuple[str, ...]:
        """Actors whose business grant must be proven before writes are armed."""

        return tuple(slot for slot in self.actors if slot != "denied")

    def env_prefix(self, actor: str = "primary") -> str:
        base = f"ODP_LIVE_JOURNEY_{self.journey_id.upper()}"
        return base if actor == "primary" else f"{base}_{actor.upper()}"

    def all_paths(self) -> list[str]:
        steps = [self.read, *self.writes, self.readback, self.cross_tenant, *self.extra_reads]
        if self.policy is not None:
            steps.extend([self.policy.submit, self.policy.absence])
        paths = [step.path for step in steps]
        if self.job is not None:
            paths.append(self.job.path)
            if self.job.result_read is not None:
                paths.append(self.job.result_read.path)
        if self.policy is not None:
            paths.append(self.policy.readback_path)
        return paths


V1 = "/api/v1"


def _write(name: str, method: str, path: str, **kwargs: Any) -> Step:
    return Step(name=name, method=method, path=path, body_from=name, idempotent=True, **kwargs)


JOURNEYS: tuple[JourneySpec, ...] = (
    JourneySpec(
        journey_id="operations",
        title="Store Ops issue workflow transition",
        selector="/operator?ws=store",
        actor_roles={
            "primary": frozenset({"operations_manager"}),
            "denied": frozenset({"regional_supervisor", "franchisee", "pricing_manager"}),
        },
        operator_role="ops-lead",
        required_records=("issue_id",),
        required_foreign=("issue_id",),
        writes_allowed={
            "transition": frozenset(
                {"triage", "assign", "actions", "field-report", "outcome", "escalate", "transfer"}
            ),
        },
        read=Step("read_issue", "GET", f"{V1}/operator/store-ops/issues/{{record.issue_id}}"),
        writes=(
            _write(
                "transition",
                "POST",
                f"{V1}/operator/store-ops/issues/{{record.issue_id}}/{{write.transition.action}}",
                required=("issue.id", "auditEvent"),
                audit_event="operator.store_ops.issue_transition",
            ),
        ),
        readback=Step(
            "readback_issue", "GET", f"{V1}/operator/store-ops/issues/{{record.issue_id}}"
        ),
        cross_tenant=Step(
            "foreign_issue", "GET", f"{V1}/operator/store-ops/issues/{{foreign.issue_id}}"
        ),
        record_id_path="issue.id",
        record_key="issue_id",
        tenant_paths=("issue.tenantId",),
        state_paths=("issue.status", "issue.history"),
        provenance_paths=("issue.id", "issue.storeId", "issue.status"),
    ),
    JourneySpec(
        journey_id="growth",
        title="PriceOps plan action and AdLift incrementality job",
        selector="/operator?ws=growth&gtab=priceops",
        actor_roles={
            "primary": frozenset({"pricing_manager"}),
            "marketer": frozenset({"marketing_manager"}),
            "denied": frozenset({"marketing_manager", "operations_manager"}),
        },
        operator_role="",
        required_records=("plan_id", "campaign_id"),
        required_foreign=("plan_id",),
        writes_allowed={
            "price_action": frozenset({"submit", "approve", "activate", "simulate", "optimize"}),
            "adlift_job": frozenset({"incrementality"}),
        },
        read=Step("read_plan", "GET", f"{V1}/priceops/plans/{{record.plan_id}}"),
        extra_reads=(
            Step(
                "read_adlift_report",
                "GET",
                f"{V1}/adlift/reports/{{record.campaign_id}}",
                actor="marketer",
                required=(
                    "report_id",
                    "campaign_id",
                    "model_version",
                    "feature_version",
                    "source_snapshot_ids",
                ),
                response_bindings={"campaign_id": "{record.campaign_id}"},
                capture={"previous_report_id": "report_id"},
            ),
        ),
        body_nonempty={"adlift_job": ("campaigns",)},
        writes=(
            _write(
                "price_action",
                "POST",
                f"{V1}/priceops/plans/{{record.plan_id}}/{{write.price_action.action}}",
                audit_event="priceops.plan_",
            ),
            _write(
                "adlift_job",
                "POST",
                f"{V1}/adlift/incrementality-jobs",
                actor="marketer",
                expect=frozenset({200, 202}),
                worker_trigger=True,
                capture={"adlift_job_id": "job_id"},
                required=("job_id", "audit_event_id"),
                audit_event="adlift.incrementality_evaluated",
            ),
        ),
        readback=Step("readback_plan", "GET", f"{V1}/priceops/plans/{{record.plan_id}}"),
        cross_tenant=Step("foreign_plan", "GET", f"{V1}/priceops/plans/{{foreign.plan_id}}"),
        record_id_path="plan_id",
        record_key="plan_id",
        tenant_paths=("tenant_id",),
        state_paths=("status", "status_history"),
        provenance_paths=("plan_id", "tenant_id", "status", "items"),
        job=JobSpec(
            id_capture="adlift_job_id",
            path=f"{V1}/adlift/incrementality-jobs/{{captured.adlift_job_id}}",
            actor="marketer",
            result_required=("reports[campaign_id={record.campaign_id}].report_id",),
            result_nonempty=("reports",),
            result_bindings={"job_id": "{captured.adlift_job_id}"},
            result_read=Step(
                "durable_adlift_report",
                "GET",
                f"{V1}/adlift/reports/{{record.campaign_id}}",
                actor="marketer",
            ),
            result_read_required=(
                "report_id",
                "campaign_id",
                "model_version",
                "feature_version",
                "source_snapshot_ids",
                "generated_at",
            ),
            result_read_bindings={
                "campaign_id": "{record.campaign_id}",
                "report_id": "{captured.adlift_report_id}",
            },
            result_id_path="report_id",
            previous_result_capture="previous_report_id",
        ),
    ),
    JourneySpec(
        journey_id="expansion",
        title="NetPlan solve, disclosure and named approval",
        selector="/operator?ws=network",
        actor_roles={
            "primary": frozenset({"executive"}),
            "approver": frozenset({"executive"}),
            "denied": frozenset({"expansion_user", "site_reviewer"}),
        },
        operator_role="",
        required_records=(
            "scenario_id",
            "rebalance_store_id",
            "modelled_class",
            "unmodelled_class",
        ),
        required_foreign=("scenario_id",),
        writes_allowed={
            "solve": frozenset({"solve"}),
            "submit": frozenset({"submit"}),
            "decide": frozenset({"approved"}),
        },
        action_fields={"decide": "decision"},
        requires_named_approval=True,
        read=Step("read_scenario", "GET", f"{V1}/netplan/scenarios/{{record.scenario_id}}"),
        writes=(
            _write(
                "solve",
                "POST",
                f"{V1}/netplan/scenarios/{{record.scenario_id}}/solve",
                worker_trigger=True,
                audit_event="netplan.solved",
            ),
            _write(
                "submit",
                "POST",
                f"{V1}/netplan/scenarios/{{record.scenario_id}}/submit",
                audit_event="netplan.submitted",
            ),
            _write(
                "decide",
                "POST",
                f"{V1}/netplan/scenarios/{{record.scenario_id}}/decide",
                actor="approver",
                body_overrides={"approval_receipt_id": "{approval_ref}"},
                audit_event="netplan.approved",
            ),
        ),
        readback=Step("readback_scenario", "GET", f"{V1}/netplan/scenarios/{{record.scenario_id}}"),
        cross_tenant=Step(
            "foreign_scenario", "GET", f"{V1}/netplan/scenarios/{{foreign.scenario_id}}"
        ),
        record_id_path="scenario_id",
        record_key="scenario_id",
        tenant_paths=("tenant_id",),
        ui_checks=(
            UiCheckSpec(
                name="netplan_disclosure",
                after_write="solve",
                actor="primary",
                url="/operator?ws=network&tab=rebalance",
                steps=(
                    UiStep("click", "rebalance-card-{record.rebalance_store_id}"),
                    UiStep("visible", "rebalance-netplan-{record.rebalance_store_id}"),
                    UiStep("visible", "rebalance-scenario-{record.scenario_id}"),
                    UiStep("visible", "scenario-disclosure-{record.scenario_id}"),
                    UiStep(
                        "text",
                        "scenario-modelled-classes-{record.scenario_id}",
                        "{record.modelled_class}",
                    ),
                    UiStep(
                        "text",
                        "scenario-unmodelled-classes-{record.scenario_id}",
                        "{record.unmodelled_class}",
                    ),
                ),
            ),
        ),
        state_paths=("status", "status_history"),
        provenance_paths=("scenario_id", "tenant_id", "status", "options_by_entity"),
        model_provenance_paths=("model_version", "feature_version", "solver_version"),
        disclosure_keys=(
            "solve.result.unmodelled_constraint_classes",
            "approvals[decision=approved].modelled_constraint_classes",
            "approvals[decision=approved].unmodelled_constraint_classes",
        ),
        readback_required=(
            "solve.result",
            "solve.model_version",
            "selected_candidate_id",
            "approvals[decision=approved].approval_id",
        ),
    ),
    JourneySpec(
        journey_id="governance",
        title="Business approval decision (not user administration)",
        selector="/operator?ws=govern",
        actor_roles={
            "primary": frozenset({"operations_manager", "executive"}),
            "denied": frozenset({"expansion_user", "marketing_manager"}),
        },
        operator_role="ops-lead",
        required_records=("approval_id",),
        required_foreign=("approval_id",),
        writes_allowed={"decision": frozenset({"approve", "return", "reject"})},
        reason_rules={"decision": (frozenset({"return", "reject"}), "reason", 10)},
        domain_audit=DomainAuditSpec(
            write="decision",
            list_path="auditRows",
            match={"category": "approval", "correlationId": "corr-{record.approval_id}"},
            event_type="operator.governance.decision",
        ),
        action_fields={"decision": "action"},
        requires_named_approval=True,
        read=Step("read_governance", "GET", f"{V1}/operator/governance/snapshot"),
        writes=(
            _write(
                "decision",
                "POST",
                f"{V1}/operator/governance/decisions",
                body_overrides={"approvalId": "{record.approval_id}"},
                required=("approvalId", "decision.id", "correlation_id"),
                response_bindings={
                    "approvalId": "{record.approval_id}",
                    "action": "{write.decision.action}",
                },
            ),
        ),
        readback=Step("readback_governance", "GET", f"{V1}/operator/governance/snapshot"),
        cross_tenant=Step(
            "foreign_approval_decision",
            "POST",
            f"{V1}/operator/governance/decisions",
            body_from="decision",
            body_overrides={"approvalId": "{foreign.approval_id}"},
            expect=CROSS_TENANT_REFUSALS,
            idempotent=True,
        ),
        record_id_path="approvals[id={record.approval_id}].id",
        record_key="approval_id",
        state_paths=("approvals[id={record.approval_id}].status",),
        provenance_paths=(
            "approvals[id={record.approval_id}].id",
            "approvals[id={record.approval_id}].status",
        ),
        allowed_values={
            "approvals[id={record.approval_id}].status": (frozenset({"pending"}), "business-data"),
        },
        readback_required=("decisions[approvalId={record.approval_id}].id",),
    ),
    JourneySpec(
        journey_id="franchise",
        title="Franchisee own-store ABAC read and field report",
        selector="/franchisee",
        actor_roles={
            "primary": frozenset({"franchisee"}),
            "denied": frozenset({"operations_manager"}),
        },
        operator_role="",
        required_records=("store_id",),
        required_foreign=("store_id",),
        writes_allowed={"report": frozenset({"report"})},
        read=Step(
            "read_own_store", "GET", f"{V1}/operator/shell/franchisee?storeId={{record.store_id}}"
        ),
        writes=(
            _write(
                "report",
                "POST",
                f"{V1}/operator/shell/franchisee/reports",
                body_overrides={"storeId": "{record.store_id}"},
                expect=frozenset({200, 201}),
                required=("report.reportId",),
                audit_response_id="auditEvent.id",
                audit_response_correlation="auditEvent.metadata.correlationId",
                response_bindings={"report.storeId": "{record.store_id}"},
            ),
        ),
        readback=Step(
            "readback_own_store",
            "GET",
            f"{V1}/operator/shell/franchisee?storeId={{record.store_id}}",
        ),
        cross_tenant=Step(
            "foreign_store",
            "GET",
            f"{V1}/operator/shell/franchisee?storeId={{foreign.store_id}}",
        ),
        record_id_path="store.id",
        record_key="store_id",
        record_bindings={"meta.scope.storeId": "{record.store_id}"},
        state_paths=("reports",),
        provenance_paths=("store.id", "meta.scope.storeId"),
    ),
    JourneySpec(
        journey_id="intake",
        title="Assisted listing intake decision and source-policy rejection",
        selector="/operator?ws=network&tab=intake",
        actor_roles={
            "primary": frozenset({"expansion_user"}),
            "denied": frozenset({"pricing_manager", "franchisee"}),
        },
        operator_role="expansion-staff",
        required_records=("intake_id",),
        required_foreign=("intake_id",),
        writes_allowed={
            "decide": frozenset({"create", "revise", "duplicate", "quarantine", "reject"})
        },
        reason_rules={
            "decide": (
                frozenset({"create", "revise", "duplicate", "quarantine", "reject"}),
                "reason",
                1,
            )
        },
        domain_audit=DomainAuditSpec(
            write="decide",
            list_path="auditEvents",
            match={
                "targetId": "{record.intake_id}",
                "action": "intake.decide.{write.decide.action}",
                "correlationId": "{correlation.decide}",
            },
            event_type="network_listings.intake.decided",
        ),
        action_fields={"decide": "action"},
        read=Step(
            "read_intake", "GET", f"{V1}/operator/network-listings/intake/{{record.intake_id}}"
        ),
        writes=(
            _write(
                "decide",
                "POST",
                f"{V1}/operator/network-listings/intake/{{record.intake_id}}/decide",
            ),
        ),
        readback=Step(
            "readback_intake",
            "GET",
            f"{V1}/operator/network-listings/intake/{{record.intake_id}}",
        ),
        cross_tenant=Step(
            "foreign_intake",
            "GET",
            f"{V1}/operator/network-listings/intake/{{foreign.intake_id}}",
        ),
        record_id_path="id",
        record_key="intake_id",
        tenant_paths=("tenantId",),
        state_paths=("stage", "version"),
        provenance_paths=("id", "tenantId", "sourceId", "policy", "intakeMethod", "stage"),
        allowed_values={
            "policy": (frozenset({"APPROVED_RETRIEVAL", "ASSISTED_ENTRY_ONLY"}), "source"),
        },
        policy=PolicySpec(
            submit=Step(
                "policy_blocked_submission",
                "POST",
                f"{V1}/operator/network-listings/intake/submit",
                body_from="policy_probe",
                idempotent=True,
                expect=frozenset({200, 400, 403, 409, 422}),
            ),
            absence=Step(
                "policy_blocked_absence",
                "GET",
                f"{V1}/operator/network-listings/intake?search={{write.policy_probe.source_ref}}",
            ),
            record_id_path="id",
            stage_path="stage",
            quarantine_stage="QUARANTINED",
            policy_path="policy",
            blocked_policies=frozenset({"SOURCE_BLOCKED", "POLICY_UNKNOWN"}),
            not_retrieved_paths=("rawSnapshot", "snapshotId", "parsedFields"),
            readback_path=f"{V1}/operator/network-listings/intake/{{captured.policy_intake_id}}",
        ),
    ),
)

JOURNEY_IDS: tuple[str, ...] = tuple(spec.journey_id for spec in JOURNEYS)
JOURNEYS_BY_ID: Mapping[str, JourneySpec] = {spec.journey_id: spec for spec in JOURNEYS}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


_SEGMENT = re.compile(r"^([A-Za-z0-9_]+)(?:\[([A-Za-z0-9_]+)=([^\]]*)\])?$")
_MISSING = object()


def _dig(payload: Any, dotted: str) -> Any:
    current = payload
    for part in dotted.split("."):
        match = _SEGMENT.match(part)
        if match is None or not isinstance(current, dict) or match.group(1) not in current:
            return _MISSING
        current = current[match.group(1)]
        if match.group(2) is not None:
            if not isinstance(current, list):
                return _MISSING
            key, wanted = match.group(2), match.group(3)
            hits = [
                item for item in current if isinstance(item, dict) and str(item.get(key)) == wanted
            ]
            if not hits:
                return _MISSING
            current = hits[-1]
    return current


def dig(payload: Any, dotted: str) -> Any:
    """Resolve ``a.b[k=v].c`` (last list match wins); ``None`` when missing."""

    value = _dig(payload, dotted)
    return None if value is _MISSING else value


def exists(payload: Any, dotted: str) -> bool:
    return _dig(payload, dotted) is not _MISSING


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict, tuple)):
        return bool(value)
    return True


def _canonical_uuid(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _redactor(*secret_values: str) -> Callable[[Any], str]:
    secrets = sorted({value for value in secret_values if value}, key=len, reverse=True)

    def redact(value: Any) -> str:
        text = str(value)
        for secret in secrets:
            text = text.replace(secret, "<redacted>")
        return re.sub(
            r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer <redacted>", text, flags=re.IGNORECASE
        )

    return redact


def _failure_detail(response: Response, *, expected: str = "") -> str:
    if response.failed:
        return response.error
    parts = [f"status={response.status}"]
    if expected:
        parts.append(f"(expected {expected})")
    if response.payload:
        parts.append(f"body={json.dumps(response.payload, sort_keys=True)[:300]}")
    return " ".join(parts)


def _cookie_header(cookies: Mapping[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in sorted(cookies.items()) if value)


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.]+)\}")


def render(template: str, variables: Mapping[str, Any], *, quote: bool) -> str:
    """Substitute ``{a.b}`` placeholders; a missing variable raises KeyError."""

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        value = dig(variables, key)
        if not _present(value) or isinstance(value, (dict, list)):
            raise KeyError(key)
        return urllib.parse.quote(str(value), safe="") if quote else str(value)

    return _PLACEHOLDER.sub(substitute, template)


# ---------------------------------------------------------------------------
# Immutable release binding
# ---------------------------------------------------------------------------


def compute_manifest_digest(manifest: Mapping[str, Any]) -> str:
    """Mirror of ``release_manifest.compute_manifest_digest``.

    Pinned rather than imported so the acceptance program never trusts code
    from the artefact it judges; the anti-drift suite binds the two.
    """

    payload = {key: value for key, value in manifest.items() if key != "manifest_digest"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sealed_release_profile(manifest: Mapping[str, Any]) -> str:
    """Mirror of ``release_manifest.manifest_release_profile``."""

    if manifest.get("release_profile") is None:
        return RELEASE_PROFILE_FULL
    profile = manifest["release_profile"]
    return str(profile.get("name") or "") if isinstance(profile, dict) else ""


@dataclass(frozen=True)
class ReleaseBinding:
    release_sha: str
    manifest_digest: str
    profile: str
    caller_profile: str
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def full_admitted(self) -> bool:
        return self.valid and self.profile == RELEASE_PROFILE_FULL

    @property
    def caller_override_refused(self) -> bool:
        return bool(self.caller_profile) and self.caller_profile != self.profile

    def as_dict(self) -> dict[str, Any]:
        return {
            "release_sha": self.release_sha,
            "manifest_digest": self.manifest_digest,
            "sealed_release_profile": self.profile,
            "caller_release_profile": self.caller_profile or None,
            "caller_profile_override_refused": self.caller_override_refused,
            "full_profile_admitted": self.full_admitted,
            "errors": list(self.errors),
        }


def bind_release(
    manifest: Mapping[str, Any] | None,
    *,
    expected_sha: str,
    expected_digest: str,
    caller_profile: str = "",
    load_error: str = "",
) -> ReleaseBinding:
    """Bind the run to the sealed manifest; the caller's profile never wins."""

    errors: list[str] = []
    expected_sha = expected_sha.strip().lower()
    expected_digest = expected_digest.strip().lower()
    caller = caller_profile.strip().lower()
    if not SHA_PATTERN.fullmatch(expected_sha):
        errors.append("missing/invalid 40-hex expected release SHA")
    if not DIGEST_PATTERN.fullmatch(expected_digest):
        errors.append(f"missing/invalid expected manifest digest ({MANIFEST_DIGEST_ENV})")
    if load_error:
        errors.append(load_error)
    if not isinstance(manifest, Mapping):
        if not load_error:
            errors.append(f"no immutable release manifest was provided ({RELEASE_MANIFEST_ENV})")
        return ReleaseBinding(expected_sha, "", "", caller, tuple(errors))

    computed = compute_manifest_digest(manifest)
    if str(manifest.get("manifest_digest") or "") != computed:
        errors.append("manifest_digest does not match the manifest's canonical payload")
    if DIGEST_PATTERN.fullmatch(expected_digest) and computed != expected_digest:
        errors.append("manifest digest differs from the deployed release's admitted digest")
    candidate = str(manifest.get("candidate_sha") or "").strip().lower()
    if candidate != expected_sha:
        errors.append(
            f"manifest candidate_sha={candidate or '<missing>'} is not the expected release SHA"
        )
    profile = sealed_release_profile(manifest)
    if profile not in KNOWN_RELEASE_PROFILES:
        errors.append(f"manifest seals an unknown release profile {profile!r}")
    return ReleaseBinding(expected_sha, computed, profile, caller, tuple(errors))


def load_json_file(path: Path | None, *, label: str) -> tuple[dict[str, Any] | None, str]:
    if path is None:
        return None, ""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{label} cannot be read: {type(exc).__name__}"
    if not isinstance(payload, dict):
        return None, f"{label} is not a JSON object"
    return payload, ""


# ---------------------------------------------------------------------------
# Scope authorization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JourneyScope:
    journey_id: str
    tenant_id: str
    actors: Mapping[str, str]
    account_ids: Mapping[str, str]
    records: Mapping[str, str]
    foreign_tenant_id: str
    foreign: Mapping[str, str]
    writes: Mapping[str, Mapping[str, Any]]
    approval_ref: str = ""

    def as_receipt(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "actors": dict(self.actors),
            "account_ids": dict(self.account_ids),
            "records": dict(self.records),
            "foreign_tenant_id": self.foreign_tenant_id,
            "foreign_records": dict(self.foreign),
            "authorized_actions": {
                key: str(_as_dict(value).get("action") or "") for key, value in self.writes.items()
            },
            "approval_ref": self.approval_ref or None,
        }


def parse_scope(
    document: Mapping[str, Any] | None, *, binding: ReleaseBinding
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Validate the top-level authorization; per-journey checks run later."""

    if not isinstance(document, Mapping):
        return {}, {}, [f"no journey scope authorization ({SCOPE_ENV})"]
    errors: list[str] = []
    if document.get("kind") != SCOPE_KIND:
        errors.append(f"scope kind must be {SCOPE_KIND!r}")
    if document.get("schema_version") != SCOPE_SCHEMA_VERSION:
        errors.append(f"scope schema_version must be {SCOPE_SCHEMA_VERSION}")
    if str(document.get("release_sha") or "").lower() != binding.release_sha:
        errors.append("scope authorization is not bound to the expected release SHA")
    if not binding.manifest_digest or document.get("manifest_digest") != binding.manifest_digest:
        errors.append("scope authorization is not bound to the admitted manifest digest")
    for key in ("authorized_by", "authorization_ref"):
        if not str(document.get(key) or "").strip():
            errors.append(f"scope authorization is missing {key}")
    journeys = document.get("journeys")
    if not isinstance(journeys, Mapping):
        errors.append("scope authorization has no journeys")
        journeys = {}
    header = {
        "authorized_by": str(document.get("authorized_by") or "") or None,
        "authorization_ref": str(document.get("authorization_ref") or "") or None,
    }
    return dict(journeys), header, errors


def journey_scope(
    spec: JourneySpec, entry: Any
) -> tuple[JourneyScope | None, list[tuple[str, str]]]:
    """Return the journey's scope, or ``(dependency, reason)`` blockers."""

    if not isinstance(entry, Mapping):
        return None, [("scope-authorization", f"no scope entry for journey {spec.journey_id}")]
    blockers: list[tuple[str, str]] = []
    tenant = _canonical_uuid(entry.get("tenant_id"))
    foreign_tenant = _canonical_uuid(entry.get("foreign_tenant_id"))
    if tenant is None:
        blockers.append(("scope-authorization", "tenant_id must be a tenant UUID"))
    if foreign_tenant is None or foreign_tenant == tenant:
        blockers.append(
            ("scope-authorization", "foreign_tenant_id must be a different tenant UUID")
        )
    actors = {
        str(k): str(v).strip()
        for k, v in _as_dict(entry.get("actors")).items()
        if isinstance(v, str) and v.strip()
    }
    account_ids = {
        str(k): _canonical_uuid(v) for k, v in _as_dict(entry.get("account_ids")).items()
    }
    for slot in spec.actors:
        if not account_ids.get(slot):
            blockers.append(
                (
                    "scope-authorization",
                    f"account_ids.{slot} must bind the authoritative account UUID",
                )
            )
        if slot not in actors:
            dependency = "named-approval" if slot == "approver" else "scope-authorization"
            blockers.append((dependency, f"actors.{slot} (the authorized account) is required"))
    subjects = [actors[slot] for slot in spec.actors if slot in actors]
    if len(set(subjects)) != len(subjects):
        blockers.append(("scope-authorization", "every journey actor must be a distinct account"))
    records = {str(k): str(v) for k, v in _as_dict(entry.get("records")).items() if _present(v)}
    for key in spec.required_records:
        if key not in records:
            blockers.append(("business-data", f"records.{key} is not authorized in scope"))
    foreign = {
        str(k): str(v) for k, v in _as_dict(entry.get("foreign_records")).items() if _present(v)
    }
    for key in spec.required_foreign:
        if key not in foreign:
            blockers.append(("scope-authorization", f"foreign_records.{key} is required"))
        elif records.get(key) == foreign[key]:
            blockers.append(
                ("scope-authorization", f"foreign_records.{key} must differ from records.{key}")
            )
    writes = {str(k): v for k, v in _as_dict(entry.get("writes")).items() if isinstance(v, Mapping)}
    for key, allowed in spec.writes_allowed.items():
        write = _as_dict(writes.get(key))
        action = str(write.get("action") or "")
        if action not in allowed:
            blockers.append(
                (
                    "scope-authorization",
                    f"writes.{key}.action={action or '<missing>'} not in {sorted(allowed)}",
                )
            )
        body = write.get("body")
        if not isinstance(body, Mapping):
            blockers.append(("scope-authorization", f"writes.{key}.body is required"))
        elif key in spec.action_fields and str(body.get(spec.action_fields[key]) or "") != action:
            blockers.append(
                (
                    "scope-authorization",
                    f"writes.{key}.body.{spec.action_fields[key]} must equal the authorized action",
                )
            )
        if isinstance(body, Mapping):
            for path in spec.body_nonempty.get(key, ()):
                value = dig(body, path)
                if not isinstance(value, list) or not value:
                    blockers.append(
                        (
                            "scope-authorization",
                            f"writes.{key}.body.{path} must be a non-empty list",
                        )
                    )
            if key in spec.reason_rules:
                actions, path, minimum = spec.reason_rules[key]
                if action in actions and len(str(dig(body, path) or "").strip()) < minimum:
                    blockers.append(
                        (
                            "scope-authorization",
                            f"writes.{key}.body.{path} needs at least {minimum} characters",
                        )
                    )
            if spec.journey_id == "intake" and key == "decide":
                if (
                    body.get("riskAcknowledged") is not True
                    or not str(body.get("riskSummary") or "").strip()
                ):
                    blockers.append(
                        (
                            "scope-authorization",
                            "intake decision requires authored riskSummary and riskAcknowledged=true",
                        )
                    )
            if spec.journey_id == "growth" and key == "adlift_job":
                campaigns = body.get("campaigns")
                if (
                    not isinstance(campaigns, list)
                    or len(campaigns) != 1
                    or not isinstance(campaigns[0], Mapping)
                    or str(campaigns[0].get("campaign_id") or "") != records.get("campaign_id")
                ):
                    blockers.append(
                        (
                            "scope-authorization",
                            "adlift campaigns must contain only the authorized campaign_id",
                        )
                    )
                elif not _present(campaigns[0].get("source_snapshot_ids")):
                    blockers.append(
                        (
                            "business-data",
                            "authorized campaign needs source_snapshot_ids provenance",
                        )
                    )
    if spec.policy is not None:
        probe = _as_dict(writes.get("policy_probe"))
        probe_body = _as_dict(probe.get("body"))
        url = str(probe_body.get("url") or "")
        if not url.startswith("https://") or not str(probe.get("source_ref") or ""):
            blockers.append(
                (
                    "source",
                    "writes.policy_probe needs an authorized HTTPS url and canonical source_ref",
                )
            )
        decision = _as_dict(writes.get("decide"))
        if decision.get("action") in {"revise", "duplicate"}:
            target = records.get("target_listing_id")
            if not target or _as_dict(decision.get("body")).get("targetListingId") != target:
                blockers.append(
                    (
                        "scope-authorization",
                        "revise/duplicate need a scoped target_listing_id and matching body.targetListingId",
                    )
                )
    approval_ref = str(entry.get("approval_ref") or "").strip()
    if spec.requires_named_approval and not approval_ref:
        blockers.append(("named-approval", "approval_ref (named business approval) is required"))
    if blockers:
        return None, blockers
    return (
        JourneyScope(
            journey_id=spec.journey_id,
            tenant_id=str(tenant),
            actors=actors,
            account_ids={k: str(v) for k, v in account_ids.items()},
            records=records,
            foreign_tenant_id=str(foreign_tenant),
            foreign=foreign,
            writes=writes,
            approval_ref=approval_ref,
        ),
        [],
    )


# ---------------------------------------------------------------------------
# Journey execution
# ---------------------------------------------------------------------------


@dataclass
class JourneyResult:
    journey_id: str
    selector: str
    status: str = STATUS_BLOCKED
    checks: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[dict[str, str]] = field(default_factory=list)
    actor: dict[str, Any] = field(default_factory=dict)
    scope: dict[str, Any] = field(default_factory=dict)
    preflight_passed: bool = False
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    correlation_ids: dict[str, str] = field(default_factory=dict)
    audit_refs: list[dict[str, Any]] = field(default_factory=list)
    job_refs: list[dict[str, Any]] = field(default_factory=list)
    negative_probes: dict[str, bool] = field(default_factory=dict)
    policy_outcome: dict[str, Any] | None = None
    #: actor slot -> the served authorization decision proving its grant.
    grants: dict[str, dict[str, Any]] = field(default_factory=dict)
    ui_checks: list[dict[str, Any]] = field(default_factory=list)
    ledger: RequestLedger = field(default_factory=RequestLedger)

    def check(self, ok: bool, name: str, detail: str, dependency: str) -> bool:
        self.checks.append(
            {"name": name, "ok": bool(ok), "detail": detail, "dependency": dependency}
        )
        if not ok:
            self.blockers.append({"check": name, "dependency": dependency, "detail": detail})
        return bool(ok)

    def settle(self) -> None:
        if not self.blockers:
            self.status = STATUS_PASSED
        elif any(b["dependency"] not in BLOCKING_DEPENDENCIES for b in self.blockers):
            self.status = STATUS_FAILED
        else:
            self.status = STATUS_BLOCKED

    def as_dict(self, redact: Callable[[Any], str]) -> dict[str, Any]:
        return {
            "journey": self.journey_id,
            "selector": self.selector,
            "status": self.status,
            "preflight_passed": self.preflight_passed,
            "actor": self.actor,
            "scope": self.scope,
            "before": self.before,
            "after": self.after,
            "correlation_ids": dict(self.correlation_ids),
            "audit_refs": list(self.audit_refs),
            "job_refs": list(self.job_refs),
            "negative_probes": dict(self.negative_probes),
            "policy_outcome": self.policy_outcome,
            "ui_checks": [
                {**check, "detail": redact(check.get("detail", ""))} for check in self.ui_checks
            ],
            "requests": self.ledger.as_dict(),
            "checks": [{**check, "detail": redact(check["detail"])} for check in self.checks],
            "blockers": [
                {
                    **blocker,
                    "detail": redact(blocker["detail"]),
                    "next_action": DEPENDENCY_ACTIONS.get(
                        blocker["dependency"], "Investigate the journey."
                    ),
                }
                for blocker in self.blockers
            ],
        }


@dataclass(frozen=True)
class Credential:
    username: str
    password: str

    @property
    def configured(self) -> bool:
        return bool(self.username) and bool(self.password)


def credential_from_env(spec: JourneySpec, actor: str, environ: Mapping[str, str]) -> Credential:
    prefix = spec.env_prefix(actor)
    return Credential(
        username=environ.get(f"{prefix}_USERNAME", "").strip(),
        password=environ.get(f"{prefix}_PASSWORD", ""),
    )


# ---------------------------------------------------------------------------
# Browser assertions (live Playwright program)
# ---------------------------------------------------------------------------

#: The live Playwright program and its config. The config has no webServer,
#: no injected role/tenant headers and no retries; the program signs in
#: through the password form and never intercepts a request.
LIVE_UI_CONFIG = "tests/e2e/live/playwright.live.config.ts"
LIVE_UI_PROGRAM = "tests/e2e/live/business-ui.live.ts"
LIVE_UI_COMMAND = (
    "npx",
    "--no-install",
    "playwright",
    "test",
    "--config",
    LIVE_UI_CONFIG,
    "--reporter=line",
)


@dataclass(frozen=True)
class UiOutcome:
    """What one browser assertion run proved (no secret is ever held)."""

    status: str
    command: tuple[str, ...]
    exit_code: int | None
    detail: str = ""


class UiDriver(Protocol):
    def available(self) -> tuple[bool, str]: ...

    def run(
        self,
        *,
        check: str,
        web_origin: str,
        path: str,
        credential: Credential,
        assertions: Sequence[Mapping[str, str]],
    ) -> UiOutcome: ...


class PlaywrightUiDriver:
    """Runs ``LIVE_UI_PROGRAM`` against the deployed Web origin.

    The credential reaches the child only through its environment; the
    recorded command carries no secret. A missing toolchain is ``BLOCKED``
    (``web``) and is detected before any write is armed.
    """

    def __init__(
        self,
        *,
        root: Path = ROOT,
        timeout_seconds: float = 300.0,
        runner: Callable[..., Any] | None = None,
        which: Callable[[str], str | None] | None = None,
    ) -> None:
        import shutil
        import subprocess

        self.root = root
        self.timeout_seconds = timeout_seconds
        self._run = runner or subprocess.run
        self._which = which or shutil.which

    def command(self) -> tuple[str, ...]:
        return LIVE_UI_COMMAND

    def available(self) -> tuple[bool, str]:
        if self._which("npx") is None:
            return False, "npx is not on PATH; the live Playwright program cannot run"
        for relative in (LIVE_UI_CONFIG, LIVE_UI_PROGRAM):
            if not (self.root / relative).is_file():
                return False, f"{relative} is missing"
        if not (self.root / "node_modules" / "@playwright" / "test").is_dir():
            return False, "@playwright/test is not installed (npm ci)"
        node = self._which("node")
        if node is None:
            return False, "node is not on PATH"
        # Launch and close a local blank browser, before writes are armed.
        # Package presence alone does not prove Chromium/binary dependencies.
        environment = {
            key: value for key, value in os.environ.items()
            if key in {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL",
                       "XDG_CACHE_HOME", "PLAYWRIGHT_BROWSERS_PATH"}
        }
        try:
            completed = self._run(
                [node, "-e", "require('@playwright/test').chromium.launch({headless:true})"
                 ".then(b=>b.close()).catch(()=>process.exit(1))"],
                cwd=str(self.root), env=environment, capture_output=True,
                text=True, timeout=30.0, check=False,
            )
        except Exception:
            return False, "local Chromium preflight could not launch"
        if completed.returncode != 0:
            return False, "local Chromium executable/dependencies are unavailable"
        return True, "local Chromium launch/close verified"

    def run(
        self,
        *,
        check: str,
        web_origin: str,
        path: str,
        credential: Credential,
        assertions: Sequence[Mapping[str, str]],
    ) -> UiOutcome:
        command = self.command()
        # No unrelated account, cloud, API or package-registry credential may
        # leak into the browser child. Dependencies must already be installed.
        allowed_env = {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "TMPDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "CI",
            "NODE_ENV",
            "XDG_CACHE_HOME",
            "PLAYWRIGHT_BROWSERS_PATH",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed_env}
        env.update(
            {
                "ODP_LIVE_UI_BASE_URL": web_origin,
                "ODP_LIVE_UI_CHECK": check,
                "ODP_LIVE_UI_PATH": path,
                "ODP_LIVE_UI_USERNAME": credential.username,
                "ODP_LIVE_UI_PASSWORD": credential.password,
                "ODP_LIVE_UI_ASSERTIONS": json.dumps(list(assertions), sort_keys=True),
            }
        )
        try:
            completed = self._run(
                list(command),
                cwd=str(self.root),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 - any spawn failure is BLOCKED
            return UiOutcome(STATUS_BLOCKED, command, None, f"{type(exc).__name__}: {exc}")
        # Browser diagnostics can contain form values/URLs. Never copy them
        # into the durable receipt, even if the configured password is redacted.
        status = STATUS_PASSED if completed.returncode == 0 else STATUS_FAILED
        return UiOutcome(status, command, int(completed.returncode), "live browser finished")


class JourneyRunner:
    """Execute one journey against the deployed Web origin."""

    def __init__(
        self,
        spec: JourneySpec,
        *,
        web: HttpClient,
        web_origin: str,
        scope: JourneyScope,
        binding: ReleaseBinding,
        credentials: Mapping[str, Credential],
        run_id: str,
        result: JourneyResult,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        job_deadline_seconds: float = 600.0,
        poll_interval_seconds: float = 10.0,
        ui_driver: UiDriver | None = None,
    ) -> None:
        self.spec = spec
        self.scope = scope
        self.binding = binding
        self.credentials = credentials
        self.run_id = run_id
        self.result = result
        self.http = LedgerHttp(web, result.ledger)
        self.origin = web_origin
        self.cookies: dict[str, dict[str, str]] = {}
        self.captured: dict[str, Any] = {}
        self.monotonic = monotonic
        self.sleep = sleep
        self.job_deadline_seconds = job_deadline_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.ui_driver = ui_driver
        self._sequence = 0
        #: actor slot -> correlation id of an authorized read it performed.
        self.grant_reads: dict[str, str] = {}
        self.before_payload: dict[str, Any] = {}
        self.after_payload: dict[str, Any] = {}
        self.write_responses: dict[str, dict[str, Any]] = {}

    # -- plumbing ---------------------------------------------------------

    def _correlation(self, label: str) -> str:
        self._sequence += 1
        return f"{self.run_id}-{self.spec.journey_id}-{self._sequence:02d}-{label}"

    def variables(self) -> dict[str, Any]:
        return {
            "record": dict(self.scope.records),
            "foreign": dict(self.scope.foreign),
            "write": {
                key: {
                    **_as_dict(_as_dict(value).get("body")),
                    **{k: v for k, v in _as_dict(value).items() if k != "body"},
                }
                for key, value in self.scope.writes.items()
            },
            "captured": dict(self.captured),
            "correlation": dict(self.result.correlation_ids),
            "approval_ref": self.scope.approval_ref,
            "tenant_id": self.scope.tenant_id,
        }

    def path_of(self, dotted: str) -> str:
        return render(dotted, self.variables(), quote=False)

    def send(
        self, step: Step, *, actor: str | None = None, label: str = ""
    ) -> tuple[Response, str]:
        acting = actor or step.actor
        correlation_id = self._correlation(label or step.name)
        variables = self.variables()
        path = render(step.path, variables, quote=True)
        headers = {
            "accept": "application/json",
            "x-correlation-id": correlation_id,
            "origin": self.origin,
            "cookie": _cookie_header(self.cookies.get(acting, {})),
        }
        if self.spec.operator_role:
            headers["x-operator-role"] = self.spec.operator_role
        body: dict[str, Any] | None = None
        if step.body_from:
            body = dict(_as_dict(_as_dict(self.scope.writes.get(step.body_from)).get("body")))
        if step.body_overrides:
            body = dict(body or {})
            for key, template in step.body_overrides.items():
                body[key] = render(template, variables, quote=False)
        if body is not None:
            headers["content-type"] = "application/json"
        if step.idempotent and step.method.upper() in MUTATING_METHODS:
            headers["idempotency-key"] = f"idem-{correlation_id}"
        response = self.http.request(
            step.method, path, headers=headers, body=body, worker_trigger=step.worker_trigger
        )
        return response, correlation_id

    def identity(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        record_id = dig(payload, self.path_of(self.spec.record_id_path))
        if not _present(record_id):
            return None
        return copy.deepcopy(
            {
                "record_id": record_id,
                "state": {path: dig(payload, self.path_of(path)) for path in self.spec.state_paths},
            }
        )

    def _mismatches(self, payload: Any, bindings: Mapping[str, str]) -> list[str]:
        """``path=served (expected wanted)`` for every binding that does not hold."""

        mismatched: list[str] = []
        for path, template in bindings.items():
            served = dig(payload, self.path_of(path))
            wanted = self.path_of(template)
            if isinstance(served, list):
                ok = wanted in {str(item) for item in served}
            else:
                ok = _present(served) and str(served) == wanted
            if not ok:
                mismatched.append(f"{path}={served!r} (expected {wanted!r})")
        return mismatched

    def _missing(self, payload: Any, paths: Sequence[str]) -> list[str]:
        return [path for path in paths if not _present(dig(payload, self.path_of(path)))]

    def _empty_lists(self, payload: Any, paths: Sequence[str]) -> list[str]:
        empty: list[str] = []
        for path in paths:
            value = dig(payload, self.path_of(path))
            if not isinstance(value, list) or not value:
                empty.append(path)
        return empty

    # -- preflight --------------------------------------------------------

    def sign_in(self, actor: str) -> bool:
        credential = self.credentials[actor]
        login = self.http.request(
            "POST",
            "/login",
            headers={"accept": "application/json", "origin": self.origin},
            body={
                "username": credential.username,
                "password": credential.password,
                "returnTo": self.spec.selector,
            },
        )
        cookies = {name: value for name, value in login.cookies.items() if value}
        signed_in = (
            not login.failed
            and login.status == 200
            and login.payload.get("ok") is True
            and login.payload.get("subject") == credential.username
            and bool(cookies)
        )
        if not self.result.check(
            signed_in,
            f"session:{actor}_password_login",
            f"status={login.status} sessionCookie={'issued' if cookies else 'missing'}",
            "business-credential",
        ):
            return False
        self.cookies[actor] = cookies
        session = self.http.request(
            "GET",
            "/auth/session",
            headers={"accept": "application/json", "cookie": _cookie_header(cookies)},
        )
        if not self.result.check(
            not session.failed
            and session.status == 200
            and session.payload.get("subject") == credential.username,
            f"session:{actor}_resolves_account",
            f"status={session.status}",
            "business-credential",
        ):
            return False
        principal = self.http.request(
            "GET",
            PRINCIPAL_PATH,
            headers={"accept": "application/json", "cookie": _cookie_header(cookies)},
        )
        roles = principal.payload.get("roles")
        role_values = (
            set(roles)
            if isinstance(roles, list) and all(isinstance(role, str) for role in roles)
            else set()
        )
        if not self.result.check(
            not principal.failed
            and principal.status == 200
            and principal.payload.get("account_id") == self.scope.account_ids.get(actor)
            and _canonical_uuid(principal.payload.get("tenant_id")) == self.scope.tenant_id
            and bool(role_values & self.spec.actor_roles.get(actor, frozenset()))
            and "platform_admin" not in role_values,
            f"actor:{actor}_authoritative_roles",
            f"status={principal.status} actualRoles={sorted(role_values)} account/tenant bound to scope",
            "business-role",
        ):
            return False
        self.result.actor["verified_business_roles"][actor] = sorted(role_values)
        probe = self.http.request(
            "GET",
            USER_ADMIN_PROBE_PATH,
            headers={"accept": "application/json", "cookie": _cookie_header(cookies)},
        )
        return self.result.check(
            not probe.failed and probe.status in DENIED_STATUSES,
            f"actor:{actor}_not_platform_admin",
            (
                f"{USER_ADMIN_PROBE_PATH} status={probe.status} (expected 401/403; an account "
                "served user administration is not a business actor)"
            ),
            "business-role",
        )

    def read_record(self) -> dict[str, Any] | None:
        response, correlation_id = self.send(self.spec.read)
        if not self.result.check(
            not response.failed and response.status in self.spec.read.expect,
            f"read:{self.spec.read.name}",
            _failure_detail(response, expected="200"),
            "business-data",
        ):
            return None
        self.grant_reads.setdefault(self.spec.read.actor, correlation_id)
        payload = response.payload
        markers = find_surrogate_values(payload)
        if not self.result.check(
            not markers,
            f"read:{self.spec.read.name}:live",
            "none" if not markers else f"surrogatePaths={markers[:5]}",
            "data-binding",
        ):
            return None
        missing = self._missing(payload, self.spec.provenance_paths)
        if not self.result.check(
            not missing,
            f"read:{self.spec.read.name}:provenance",
            "complete" if not missing else f"missing={missing}",
            "business-data",
        ):
            return None
        if not self.bind_record(payload):
            return None
        for path, (allowed, dependency) in self.spec.allowed_values.items():
            value = dig(payload, self.path_of(path))
            if not self.result.check(
                str(value) in allowed,
                f"read:{self.spec.read.name}:{path.split('.')[-1]}",
                f"{path}={value!r} allowed={sorted(allowed)}",
                dependency,
            ):
                return None
        model_missing = [
            path for path in self.spec.model_provenance_paths if not _present(dig(payload, path))
        ]
        if not self.result.check(
            not model_missing,
            f"read:{self.spec.read.name}:model_provenance",
            "complete" if not model_missing else f"missing={model_missing}",
            "model",
        ):
            return None
        if self.spec.journey_id == "intake" and _as_dict(self.scope.writes.get("decide")).get(
            "action"
        ) in {"revise", "duplicate"}:
            target_response, _ = self.send(
                Step("read_target_listing", "GET", f"{V1}/operator/network-listings")
            )
            target = dig(
                target_response.payload, self.path_of("listings[id={record.target_listing_id}]")
            )
            if not self.result.check(
                not target_response.failed
                and target_response.status == 200
                and dig(target, "id") == self.scope.records.get("target_listing_id")
                and _canonical_uuid(dig(target, "tenantId")) == self.scope.tenant_id
                and not find_surrogate_values(target),
                "read:target_listing_binding",
                "target listing must exist in the authorized tenant",
                "business-data",
            ):
                return None
        if (
            self.spec.journey_id == "intake"
            and _as_dict(self.scope.writes.get("decide")).get("action") == "duplicate"
        ):
            if not self.result.check(
                dig(payload, "matchResult.targetListingId")
                == self.scope.records.get("target_listing_id"),
                "read:duplicate_target_binding",
                "duplicate target must match the authorized existing listing",
                "scope-authorization",
            ):
                return None
        for extra in self.spec.extra_reads:
            extra_response, extra_correlation = self.send(extra)
            extra_markers = (
                find_surrogate_values(extra_response.payload) if not extra_response.failed else []
            )
            if not self.result.check(
                not extra_response.failed
                and extra_response.status in extra.expect
                and not extra_markers
                and not self._missing(extra_response.payload, extra.required)
                and not self._mismatches(extra_response.payload, extra.response_bindings),
                f"read:{extra.name}",
                _failure_detail(extra_response, expected="200 live")
                + (f" surrogatePaths={extra_markers[:5]}" if extra_markers else ""),
                "data-binding" if extra_markers else "business-data",
            ):
                return None
            self.grant_reads.setdefault(extra.actor, extra_correlation)
            for name, dotted in extra.capture.items():
                # Optional pre-write state (e.g. the latest report before the job).
                self.captured[name] = dig(extra_response.payload, self.path_of(dotted))
        return payload

    def bind_record(self, payload: Mapping[str, Any]) -> bool:
        """The served record must be the authorized record, in the authorized tenant."""

        name = self.spec.read.name
        if self.spec.record_key:
            served = dig(payload, self.path_of(self.spec.record_id_path))
            wanted = self.scope.records.get(self.spec.record_key, "")
            if not self.result.check(
                _present(served) and str(served) == wanted,
                f"read:{name}:record_binding",
                f"{self.spec.record_id_path}={served!r} authorized={wanted!r}",
                "business-data",
            ):
                return False
        mismatched = self._mismatches(payload, self.spec.record_bindings)
        if not self.result.check(
            not mismatched,
            f"read:{name}:scope_binding",
            "bound" if not mismatched else f"mismatched={mismatched}",
            "business-data",
        ):
            return False
        for path in self.spec.tenant_paths:
            raw = dig(payload, self.path_of(path))
            served_tenant = _canonical_uuid(raw)
            if not self.result.check(
                served_tenant is not None and served_tenant == self.scope.tenant_id,
                f"read:{name}:tenant_binding",
                f"{path}={raw!r} authorizedTenant={self.scope.tenant_id}",
                "scope-authorization",
            ):
                return False
        return True

    def audit_events(
        self, actor: str, correlation_id: str
    ) -> tuple[Response, list[dict[str, Any]]]:
        path = f"{AUDIT_EVENTS_PATH}?{urllib.parse.urlencode({'correlation_id': correlation_id})}"
        response, _ = self.send(Step("audit_events", "GET", path, actor=actor), label="audit")
        events = response.payload.get("events") if not response.failed else None
        rows = [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []
        return response, [row for row in rows if row.get("correlation_id") == correlation_id]

    def authorization_event(self, actor: str, correlation_id: str) -> dict[str, Any] | None:
        """The served RBAC decision (``security.authorization`` allow) for a request.

        It is the API's own record of the authenticated principal's tenant and
        of the grant that admitted the request -- not the role the scope
        expects the account to hold.
        """

        _, events = self.audit_events(actor, correlation_id)
        for event in events:
            outcome = str(event.get("outcome") or event.get("result") or "").lower()
            if (
                event.get("event_type") == SECURITY_EVENT_TYPE
                and outcome == "allow"
                and _present(event.get("event_id"))
            ):
                metadata = _as_dict(event.get("metadata"))
                tenant = metadata.get("tenant_id")
                return {
                    "correlation_id": correlation_id,
                    "event_id": event.get("event_id"),
                    "outcome": outcome,
                    "action": event.get("action"),
                    "resource": event.get("resource"),
                    "decision_reason": metadata.get("reason"),
                    "policy_id": metadata.get("policy_id"),
                    "tenant_id": _canonical_uuid(tenant) or (str(tenant) if tenant else None),
                    "account_ref": event.get("actor"),
                }
        return None

    def prove_grants(self) -> bool:
        """Every acting account must be *served* a grant in the authorized tenant."""

        for slot in self.spec.granted_actors:
            correlation_id = self.grant_reads.get(slot)
            if correlation_id is None:
                response, correlation_id = self.send(
                    self.spec.read, actor=slot, label=f"grant-{slot}"
                )
                if not self.result.check(
                    not response.failed and response.status in self.spec.read.expect,
                    f"grant:{slot}_authorized_read",
                    _failure_detail(response, expected="200"),
                    "business-role",
                ):
                    return False
            evidence = self.authorization_event(slot, correlation_id)
            if not self.result.check(
                evidence is not None,
                f"grant:{slot}_served_decision",
                (
                    "allow decision served"
                    if evidence is not None
                    else f"no {SECURITY_EVENT_TYPE} allow event for correlation {correlation_id}"
                ),
                "business-role",
            ):
                return False
            assert evidence is not None
            if not self.result.check(
                evidence["tenant_id"] == self.scope.tenant_id
                and evidence["account_ref"] == self.scope.account_ids.get(slot),
                f"grant:{slot}_tenant_binding",
                f"servedTenant={evidence['tenant_id']!r} authorizedTenant={self.scope.tenant_id}",
                "scope-authorization",
            ):
                return False
            self.result.grants[slot] = evidence
        return True

    def ui_ready(self) -> bool:
        if not self.spec.ui_checks:
            return True
        if self.ui_driver is None:
            ready, detail = False, "no live browser driver configured"
        else:
            ready, detail = self.ui_driver.available()
        return self.result.check(ready, "ui:live_browser", detail, "web")

    # -- negative probes --------------------------------------------------

    def probe_cross_tenant(self) -> bool:
        response, _ = self.send(self.spec.cross_tenant, label="cross-tenant")
        refused = not response.failed and response.status in CROSS_TENANT_REFUSALS
        self.result.negative_probes["cross_tenant_denied"] = refused
        return self.result.check(
            refused,
            "negative:cross_tenant_denied",
            _failure_detail(response, expected="403/404/422 for a foreign-tenant record"),
            "tenant-isolation",
        )

    def probe_denied_role(self, before: Mapping[str, Any]) -> bool:
        response, _ = self.send(self.spec.writes[0], actor="denied", label="denied-role")
        refused = not response.failed and response.status in DENIED_STATUSES
        readback, _ = self.send(self.spec.readback, label="denied-role-readback")
        unchanged = (
            not readback.failed
            and readback.status == 200
            and self.identity(readback.payload) == before
        )
        self.result.negative_probes["wrong_role_denied"] = refused and unchanged
        return self.result.check(
            refused and unchanged,
            "negative:wrong_role_denied",
            f"status={response.status} (expected 401/403) recordUnchanged={unchanged}",
            "authorization",
        )

    # -- business path ----------------------------------------------------

    def perform_writes(self) -> bool:
        for step in self.spec.writes:
            response, correlation_id = self.send(step)
            self.result.correlation_ids[step.name] = correlation_id
            payload = response.payload if not response.failed else {}
            missing = self._missing(payload, step.required)
            empty = self._empty_lists(payload, step.nonempty_lists)
            mismatched = self._mismatches(payload, step.response_bindings)
            problems = (
                (f" missing={missing}" if missing else "")
                + (f" emptyLists={empty}" if empty else "")
                + (f" mismatched={mismatched}" if mismatched else "")
            )
            if not self.result.check(
                not response.failed
                and response.status in step.expect
                and not missing
                and not empty
                and not mismatched,
                f"write:{step.name}",
                _failure_detail(response, expected=str(sorted(step.expect))) + problems,
                "business-write",
            ):
                return False
            for name, dotted in step.capture.items():
                value = dig(payload, self.path_of(dotted))
                if not self.result.check(
                    _present(value),
                    f"write:{step.name}:capture:{name}",
                    f"{dotted}={'present' if _present(value) else '<missing>'}",
                    "business-write",
                ):
                    return False
                self.captured[name] = value
            self.write_responses[step.name] = copy.deepcopy(payload)
            for check in self.spec.ui_checks:
                if check.after_write == step.name and not self.run_ui_check(check):
                    return False
        return True

    def run_ui_check(self, check: UiCheckSpec) -> bool:
        variables = self.variables()
        path = render(check.url, variables, quote=True)
        steps = [
            {
                "action": step.action,
                "selector": render(step.selector, variables, quote=False),
                "text": render(step.text, variables, quote=False) if step.text else "",
            }
            for step in check.steps
        ]
        assert self.ui_driver is not None  # ui_ready() proved it before arming
        outcome = self.ui_driver.run(
            check=check.name,
            web_origin=self.origin,
            path=path,
            credential=self.credentials[check.actor],
            assertions=steps,
        )
        passed = outcome.status == STATUS_PASSED and outcome.exit_code == 0
        self.result.ui_checks.append(
            {
                "name": check.name,
                "after_write": check.after_write,
                "actor": check.actor,
                "subject": self.scope.actors.get(check.actor),
                "url": path,
                "steps": steps,
                "program": LIVE_UI_PROGRAM,
                "command": list(outcome.command),
                "exit_code": outcome.exit_code,
                "status": STATUS_PASSED if passed else outcome.status,
                "detail": outcome.detail[-300:],
            }
        )
        return self.result.check(
            passed,
            f"ui:{check.name}",
            f"status={outcome.status} exit={outcome.exit_code} {outcome.detail[-200:]}",
            "web" if outcome.status == STATUS_BLOCKED else "disclosure",
        )

    def await_job(self) -> bool:
        job = self.spec.job
        if job is None:
            return True
        deadline = self.monotonic() + self.job_deadline_seconds
        status = ""
        payload: dict[str, Any] = {}
        while True:
            response, _ = self.send(
                Step("job_status", "GET", job.path, actor=job.actor), label="job"
            )
            payload = response.payload if not response.failed else {}
            status = str(dig(payload, job.status_path) or "").lower()
            if status in {"succeeded", "failed", "cancelled"} or self.monotonic() >= deadline:
                break
            self.sleep(self.poll_interval_seconds)
        ref: dict[str, Any] = {
            "job_id": self.captured.get(job.id_capture),
            "status": status or None,
        }
        self.result.job_refs.append(ref)
        if not self.result.check(
            status == "succeeded",
            "job:terminal_succeeded",
            f"status={status or '<missing>'}",
            "worker",
        ):
            return False
        missing = self._missing(payload, job.result_required)
        empty = self._empty_lists(payload, job.result_nonempty)
        mismatched = self._mismatches(payload, job.result_bindings)
        if not self.result.check(
            not missing and not empty and not mismatched,
            "job:business_result",
            (
                "result present"
                if not (missing or empty or mismatched)
                else f"missing={missing} emptyLists={empty} mismatched={mismatched}"
            ),
            "worker",
        ):
            return False
        if job.result_read is None:
            return True
        report = dig(payload, self.path_of("reports[campaign_id={record.campaign_id}]"))
        self.captured["adlift_report_id"] = dig(report, "report_id")
        result, _ = self.send(job.result_read, label="job-result")
        result_payload = result.payload if not result.failed else {}
        markers = find_surrogate_values(result_payload)
        missing = self._missing(result_payload, job.result_read_required)
        mismatched = self._mismatches(result_payload, job.result_read_bindings)
        result_id = dig(result_payload, job.result_id_path) if job.result_id_path else None
        ref["result_id"] = result_id
        ref["previous_result_id"] = (
            self.captured.get(job.previous_result_capture) if job.previous_result_capture else None
        )
        fresh = _present(result_id) and result_id != ref["previous_result_id"]
        return self.result.check(
            not result.failed
            and result.status == 200
            and not markers
            and not missing
            and not mismatched
            and fresh,
            "job:durable_result",
            (
                f"status={result.status} missing={missing} mismatched={mismatched} "
                f"newResult={fresh} surrogatePaths={markers[:5] if markers else 'none'}"
            ),
            "data-binding" if markers else "durable-readback",
        )

    def readback(self, before: Mapping[str, Any]) -> bool:
        response, _ = self.send(self.spec.readback)
        payload = response.payload if not response.failed else {}
        self.after_payload = copy.deepcopy(payload)
        after = self.identity(payload)
        self.result.after = after
        same = after is not None and after["record_id"] == before["record_id"]
        changed = same and after is not None and after["state"] != before["state"]
        markers = find_surrogate_values(payload)
        if not self.result.check(
            not response.failed and response.status == 200 and changed and not markers,
            "readback:durable_state_change",
            (
                f"status={response.status} sameRecord={same} stateChanged={changed} "
                f"surrogatePaths={markers[:5] if markers else 'none'}"
            ),
            "durable-readback",
        ):
            return False
        missing = self._missing(payload, self.spec.readback_required)
        mismatched = self._mismatches(payload, self.spec.readback_bindings)
        undisclosed = [
            path for path in self.spec.disclosure_keys if not exists(payload, self.path_of(path))
        ]
        self.result.check(
            not missing and not mismatched,
            "readback:outcome_receipt",
            (
                "complete"
                if not (missing or mismatched)
                else f"missing={missing} mismatched={mismatched}"
            ),
            "durable-readback",
        )
        if self.spec.disclosure_keys:
            self.result.check(
                not undisclosed,
                "disclosure:workspace_payload",
                "complete" if not undisclosed else f"missing={undisclosed}",
                "disclosure",
            )
        return not missing and not mismatched and not undisclosed

    def _domain_audit_row(self, audit: DomainAuditSpec) -> dict[str, Any] | None:
        """The new row of the record's own durable audit trail for this write."""

        before_rows = dig(self.before_payload, self.path_of(audit.list_path))
        after_rows = dig(self.after_payload, self.path_of(audit.list_path))
        seen = {
            str(row.get(audit.id_field))
            for row in (before_rows if isinstance(before_rows, list) else [])
            if isinstance(row, dict)
        }
        wanted = {key: self.path_of(template) for key, template in audit.match.items()}
        for row in after_rows if isinstance(after_rows, list) else []:
            if (
                isinstance(row, dict)
                and _present(row.get(audit.id_field))
                and str(row.get(audit.id_field)) not in seen
                and all(str(row.get(key)) == value for key, value in wanted.items())
            ):
                return row
        return None

    def verify_audit(self) -> bool:
        ok = True
        domain = {audit.write: audit for audit in self._domain_audits()}
        for step in self.spec.writes:
            correlation_id = self.result.correlation_ids.get(step.name, "")
            response, events = self.audit_events(step.actor, correlation_id)
            authorization = next(
                (
                    event
                    for event in events
                    if event.get("event_type") == SECURITY_EVENT_TYPE
                    and str(event.get("outcome") or event.get("result") or "").lower() == "allow"
                    and _present(event.get("event_id"))
                    and event.get("actor") == self.scope.account_ids.get(step.actor)
                    and _canonical_uuid(_as_dict(event.get("metadata")).get("tenant_id"))
                    == self.scope.tenant_id
                ),
                None,
            )
            event_id: Any = None
            event_type: Any = None
            source = ""
            if step.audit_response_id:
                written = self.write_responses.get(step.name, {})
                served_id = dig(written, self.path_of(step.audit_response_id))
                served_correlation = dig(written, self.path_of(step.audit_response_correlation))
                if _present(served_id) and served_correlation == correlation_id:
                    event_id, event_type = served_id, step.audit_event or "write-response"
                source = "write-response"
            elif step.name in domain:
                row = self._domain_audit_row(domain[step.name])
                if row is not None:
                    event_id = row.get(domain[step.name].id_field)
                    event_type = domain[step.name].event_type or str(row.get("action") or "")
                source = "durable-readback"
            else:
                business = [
                    event
                    for event in events
                    if event.get("event_type") != SECURITY_EVENT_TYPE
                    and _present(event.get("event_id"))
                    and bool(step.audit_event)
                    and (
                        event.get("event_type") == PRICEOPS_AUDIT_EVENTS.get(
                            _as_dict(self.scope.writes.get("price_action")).get("action")
                        ) if self.spec.journey_id == "growth" and step.name == "price_action"
                        else str(event.get("event_type") or "").startswith(step.audit_event)
                    )
                ]
                if business:
                    event_id, event_type = business[0]["event_id"], business[0].get("event_type")
                source = "platform"
            if _present(event_id) and authorization is not None:
                self.result.audit_refs.append(
                    {
                        "write": step.name,
                        "correlation_id": correlation_id,
                        "event_id": event_id,
                        "event_type": event_type,
                        "source": source,
                        "authorization_event_id": authorization["event_id"],
                    }
                )
            ok = (
                self.result.check(
                    _present(event_id) and authorization is not None,
                    f"audit:{step.name}",
                    (
                        f"status={response.status} source={source} "
                        f"businessEvent={'found' if _present(event_id) else 'missing'} "
                        f"authorizationEvent={'found' if authorization else 'missing'}"
                    ),
                    "audit",
                )
                and ok
            )
        return ok

    def _domain_audits(self) -> tuple[DomainAuditSpec, ...]:
        return (self.spec.domain_audit,) if self.spec.domain_audit is not None else ()

    def verify_policy(self) -> bool:
        policy = self.spec.policy
        if policy is None:
            return True
        response, correlation_id = self.send(policy.submit, label="policy")
        outcome: dict[str, Any] = {"status": response.status, "correlation_id": correlation_id}
        proven = False
        if not response.failed and response.status in DENIED_STATUSES | {400, 409, 422}:
            absence, _ = self.send(policy.absence, label="policy-absence")
            items = absence.payload.get("items") if not absence.failed else None
            code = dig(response.payload, "error.code") or dig(response.payload, "detail.code")
            proven = (
                code in policy.blocked_policies
                and not absence.failed
                and absence.status == 200
                and isinstance(items, list)
                and not items
            )
            outcome.update({"mode": "refused", "nothing_persisted": proven})
        elif not response.failed and response.status == 200:
            record_id = dig(response.payload, policy.record_id_path)
            self.captured["policy_intake_id"] = record_id
            quarantined = (
                dig(response.payload, policy.stage_path) == policy.quarantine_stage
                and str(dig(response.payload, policy.policy_path)) in policy.blocked_policies
                and not any(_present(dig(response.payload, p)) for p in policy.not_retrieved_paths)
            )
            durable = False
            if _present(record_id):
                readback, _ = self.send(
                    Step("policy_readback", "GET", policy.readback_path), label="policy-readback"
                )
                durable = (
                    not readback.failed
                    and readback.status == 200
                    and dig(readback.payload, policy.record_id_path) == record_id
                    and dig(readback.payload, policy.stage_path) == policy.quarantine_stage
                    and str(dig(readback.payload, policy.policy_path)) in policy.blocked_policies
                    and not any(
                        _present(dig(readback.payload, p)) for p in policy.not_retrieved_paths
                    )
                )
            proven = quarantined and durable
            outcome.update(
                {
                    "mode": "quarantined",
                    "record_id": record_id,
                    "policy": dig(response.payload, policy.policy_path),
                    "durable": durable,
                }
            )
        self.result.policy_outcome = outcome
        self.result.negative_probes["policy_rejected"] = proven
        return self.result.check(
            proven,
            "policy:blocked_source_rejected",
            json.dumps(outcome, sort_keys=True, default=str),
            "policy",
        )

    def run(self) -> None:
        try:
            self._run()
        except PreflightViolation as exc:  # pragma: no cover - programming error guard
            self.result.check(False, "preflight:violation", str(exc), "release")
        except KeyError as exc:
            self.result.check(
                False,
                "scope:variable",
                f"journey variable {exc} is not authorized in scope",
                "scope-authorization",
            )
        finally:
            self.http.writes_armed = False
            self.result.settle()

    def _run(self) -> None:
        self.result.actor = {
            "tenant_id": self.scope.tenant_id,
            "subjects": dict(self.scope.actors),
            "account_ids": dict(self.scope.account_ids),
            "expected_business_roles": {
                slot: sorted(self.spec.actor_roles.get(slot, frozenset()))
                for slot in self.spec.actors
            },
            # Filled only from served authorization decisions (prove_grants).
            "verified_grants": self.result.grants,
            "verified_business_roles": {},
            "operator_role": self.spec.operator_role or None,
            "approval_ref": self.scope.approval_ref or None,
        }
        for actor in self.spec.actors:
            if not self.sign_in(actor):
                return
        # Authenticate through the real BFF, then bind BOTH its upstream and
        # the Web revision before even reading the authorized business record.
        response, _ = self.send(Step("bff_release_identity", "GET", RELEASE_IDENTITY_PATH))
        errors = release_identity_failures(response.payload, self.binding, include_web=True)
        if not self.result.check(
            not response.failed and response.status == 200 and not errors,
            "runtime:bff_release_binding", "; ".join(detail for _, _, detail in errors)
            or f"status={response.status}", "release",
        ):
            return
        payload = self.read_record()
        if payload is None:
            return
        before = self.identity(payload)
        if not self.result.check(
            before is not None,
            "read:record_identity",
            f"{self.spec.record_id_path}={'present' if before else '<missing>'}",
            "business-data",
        ):
            return
        assert before is not None
        self.result.before = before
        self.before_payload = copy.deepcopy(payload)
        if not self.prove_grants() or not self.ui_ready():
            return
        # Every preflight fact is proven: only now may a mutation leave.
        self.result.preflight_passed = True
        self.http.writes_armed = True
        if not self.probe_cross_tenant() or not self.probe_denied_role(before):
            return
        if not self.perform_writes() or not self.await_job():
            return
        if not self.readback(before):
            return
        self.verify_audit()
        self.verify_policy()


# ---------------------------------------------------------------------------
# Runtime preflight (anonymous API reads only)
# ---------------------------------------------------------------------------


def release_identity_failures(
    payload: Mapping[str, Any], binding: ReleaseBinding, *, include_web: bool = False
) -> list[tuple[str, str, str]]:
    """Compare side-effect-free, server-owned identity with the admitted artifact."""
    wanted: dict[str, Any] = {
        "release_sha": binding.release_sha,
        "release_profile": binding.profile,
        "release_profile_valid": True,
        "manifest_digest": binding.manifest_digest,
    }
    if include_web:
        wanted.update({
            "web_release_sha": binding.release_sha,
            "web_release_profile": binding.profile,
            "web_manifest_digest": binding.manifest_digest,
        })
    return [
        (f"runtime:{key}", "release", f"{key} is missing or differs from admitted release")
        for key, expected in wanted.items() if payload.get(key) != expected
    ]


def runtime_preflight(
    api: HttpClient | None, binding: ReleaseBinding
) -> list[tuple[str, str, str]]:
    """No readiness calls: that endpoint may trigger real provider probes."""
    if api is None:
        return [("runtime:api_origin", "release", f"no usable deployed API origin ({API_URL_ENV})")]
    response = api.request("GET", RELEASE_IDENTITY_PATH, authenticated=False)
    if response.failed or response.status != 200:
        return [("runtime:release_identity", "release", "read-only runtime identity unavailable")]
    return release_identity_failures(response.payload, binding)


# ---------------------------------------------------------------------------
# Orchestration and receipt
# ---------------------------------------------------------------------------


def select_journeys(selection: Sequence[str] | None) -> tuple[list[JourneySpec], list[str]]:
    if not selection:
        return list(JOURNEYS), []
    unknown = [value for value in selection if value not in JOURNEYS_BY_ID]
    wanted = set(selection)
    return [spec for spec in JOURNEYS if spec.journey_id in wanted], unknown


def receipt_digest(receipt: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_journeys(
    *,
    binding: ReleaseBinding,
    scope_document: Mapping[str, Any] | None,
    selection: Sequence[str] | None,
    web: HttpClient | None,
    web_origin: str,
    api: HttpClient | None,
    environ: Mapping[str, str],
    command: Sequence[str],
    now: str,
    run_id: str | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    job_deadline_seconds: float = 600.0,
    poll_interval_seconds: float = 10.0,
    ui_driver: UiDriver | None = None,
) -> dict[str, Any]:
    """Run the selected journeys and return the sealed receipt."""

    run_id = run_id or f"journey-{binding.release_sha[:12] or 'unbound'}-{uuid4().hex[:8]}"
    specs, unknown = select_journeys(selection)
    redact = _redactor(
        *(
            credential_from_env(spec, slot, environ).password
            for spec in JOURNEYS
            for slot in ACTOR_SLOTS
        )
    )

    shared: list[tuple[str, str, str]] = []
    if unknown:
        shared.append(("selection", "scope-authorization", f"unknown journeys={unknown}"))
    for error in binding.errors:
        shared.append(("release:manifest_binding", "release", error))
    admitted = binding.full_admitted
    scope_journeys: dict[str, Any] = {}
    scope_header: dict[str, Any] = {}
    if admitted:
        scope_journeys, scope_header, scope_errors = parse_scope(scope_document, binding=binding)
        shared.extend(("scope:authorization", "scope-authorization", e) for e in scope_errors)
        if not shared:
            shared.extend(runtime_preflight(api, binding))
        if web is None:
            shared.append(("web:origin", "web", f"no usable deployed Web origin ({WEB_URL_ENV})"))

    results: list[JourneyResult] = []
    for spec in specs:
        result = JourneyResult(journey_id=spec.journey_id, selector=spec.selector)
        results.append(result)
        if binding.valid and not admitted:
            result.check(
                False,
                "preflight:release_profile_admission",
                (
                    f"sealed release profile {binding.profile!r} carries no full business "
                    "acceptance admission"
                    + (
                        f"; caller {RELEASE_PROFILE_ENV}={binding.caller_profile!r} refused"
                        if binding.caller_override_refused
                        else ""
                    )
                ),
                "release-profile",
            )
            result.status = STATUS_NOT_ADMITTED
            continue
        for name, dependency, detail in shared:
            result.check(False, name, detail, dependency)
        if result.blockers:
            result.settle()
            continue
        scope, scope_blockers = journey_scope(spec, scope_journeys.get(spec.journey_id))
        for dependency, detail in scope_blockers:
            result.check(False, f"preflight:{dependency}", detail, dependency)
        credentials = {slot: credential_from_env(spec, slot, environ) for slot in spec.actors}
        for slot, credential in credentials.items():
            if not credential.configured:
                prefix = spec.env_prefix(slot)
                dependency = "named-approval" if slot == "approver" else "business-credential"
                result.check(
                    False,
                    f"preflight:{slot}_credential",
                    f"missing {prefix}_USERNAME/{prefix}_PASSWORD",
                    dependency,
                )
            elif scope is not None and credential.username != scope.actors.get(slot):
                result.check(
                    False,
                    f"preflight:{slot}_credential",
                    f"{slot} credential is not the account the scope authorizes",
                    "scope-authorization",
                )
        if scope is not None:
            result.scope = scope.as_receipt()
        if result.blockers or scope is None or web is None:
            result.settle()
            continue
        JourneyRunner(
            spec,
            web=web,
            web_origin=web_origin,
            scope=scope,
            binding=binding,
            credentials=credentials,
            run_id=run_id,
            result=result,
            monotonic=monotonic,
            sleep=sleep,
            job_deadline_seconds=job_deadline_seconds,
            poll_interval_seconds=poll_interval_seconds,
            ui_driver=ui_driver,
        ).run()

    statuses = {result.journey_id: result.status for result in results}
    complete = [result.journey_id for result in results] == list(JOURNEY_IDS)
    all_passed = (
        bool(results) and not unknown and all(s == STATUS_PASSED for s in statuses.values())
    )
    if all_passed:
        overall = STATUS_PASSED
    elif results and all(s == STATUS_NOT_ADMITTED for s in statuses.values()):
        overall = STATUS_NOT_ADMITTED
    elif any(s == STATUS_FAILED for s in statuses.values()):
        overall = STATUS_FAILED
    else:
        overall = STATUS_BLOCKED
    receipt: dict[str, Any] = {
        "kind": RECEIPT_KIND,
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "generated_at": now,
        "run_id": run_id,
        "source_sha": binding.release_sha,
        "release": binding.as_dict(),
        "scope_authorization": scope_header,
        "selection": [spec.journey_id for spec in specs],
        "complete_selection": complete,
        "command": {
            "argv": [redact(part) for part in command],
            "exit_code": 0 if all_passed else 1,
        },
        "journeys": {result.journey_id: result.as_dict(redact) for result in results},
        "summary": {
            "status": overall,
            "statuses": statuses,
            "full_acceptance_eligible": all_passed and complete and binding.full_admitted,
        },
        "offline_fixture": False,
        "secret_values_redacted": True,
    }
    receipt["receipt_digest"] = receipt_digest(receipt)
    return receipt


# ---------------------------------------------------------------------------
# Receipt verification (used by check_live_e2e_gate.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReceiptCheck:
    ok: bool
    name: str
    detail: str
    dependency: str = "business-journey"


def _forbidden_keys(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if str(key).lower() in FORBIDDEN_RECEIPT_KEYS:
                found.append(here)
            found.extend(_forbidden_keys(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_keys(item, f"{path}[{index}]"))
    return found


def journey_receipt_problems(spec: JourneySpec, entry: Mapping[str, Any]) -> list[str]:
    if not entry:
        return ["journey receipt missing"]
    problems: list[str] = []
    if entry.get("status") != STATUS_PASSED:
        problems.append(f"status={entry.get('status')}")
    if entry.get("journey") != spec.journey_id or entry.get("selector") != spec.selector:
        problems.append(f"selector={entry.get('selector')}")
    if entry.get("preflight_passed") is not True:
        problems.append("preflight not passed")
    actor = _as_dict(entry.get("actor"))
    subjects = _as_dict(actor.get("subjects"))
    if _canonical_uuid(actor.get("tenant_id")) is None:
        problems.append("actor tenant missing")
    missing_actors = [slot for slot in spec.actors if not _present(subjects.get(slot))]
    if missing_actors:
        problems.append(f"actor subjects missing for {missing_actors}")
    verified_roles = _as_dict(actor.get("verified_business_roles"))
    for slot in spec.actors:
        values = verified_roles.get(slot)
        roles = (
            set(values)
            if isinstance(values, list) and all(isinstance(value, str) for value in values)
            else set()
        )
        if (
            not roles.intersection(spec.actor_roles.get(slot, frozenset()))
            or "platform_admin" in roles
        ):
            problems.append(f"authoritative business roles missing for {slot}")
    if spec.requires_named_approval and not _present(actor.get("approval_ref")):
        problems.append("named approval reference missing")
    before = _as_dict(entry.get("before"))
    after = _as_dict(entry.get("after"))
    scope = _as_dict(entry.get("scope"))
    records = _as_dict(scope.get("records"))
    expected_record = records.get(spec.record_key)
    if (
        not before
        or not after
        or not _present(before.get("record_id"))
        or not _present(after.get("record_id"))
        or not _present(expected_record)
        or str(before.get("record_id")) != str(expected_record)
        or before.get("record_id") != after.get("record_id")
    ):
        problems.append("before/after record identity missing or different")
    elif before.get("state") == after.get("state"):
        problems.append("record state unchanged")
    correlation_ids = _as_dict(entry.get("correlation_ids"))
    write_correlations = [correlation_ids.get(step.name) for step in spec.writes]
    if not all(_present(value) for value in write_correlations) or len(
        set(str(value) for value in write_correlations)
    ) != len(spec.writes):
        problems.append("write correlation ids missing or reused")
    audited = {
        ref.get("write")
        for ref in entry.get("audit_refs") or []
        if isinstance(ref, dict)
        and _present(ref.get("event_id"))
        and _present(ref.get("authorization_event_id"))
        and _present(ref.get("correlation_id"))
        and _present(correlation_ids.get(ref.get("write")))
        and ref.get("correlation_id") == correlation_ids.get(ref.get("write"))
    }
    missing_audit = [step.name for step in spec.writes if step.name not in audited]
    if missing_audit:
        problems.append(f"audit refs missing for {missing_audit}")
    if spec.job is not None and not any(
        isinstance(ref, dict)
        and ref.get("status") == "succeeded"
        and _present(ref.get("job_id"))
        and _present(ref.get("result_id"))
        and ref.get("result_id") != ref.get("previous_result_id")
        for ref in entry.get("job_refs") or []
    ):
        problems.append("durable job reference missing or not succeeded")
    ui_rows = entry.get("ui_checks") or []
    for check in spec.ui_checks:
        variables = {"record": records}
        expected_steps = (
            [
                {
                    "action": step.action,
                    "selector": render(step.selector, variables, quote=False),
                    "text": render(step.text, variables, quote=False) if step.text else "",
                }
                for step in check.steps
            ]
            if all(key in records for key in spec.required_records)
            else []
        )
        if not any(
            isinstance(row, dict)
            and row.get("name") == check.name
            and row.get("after_write") == check.after_write
            and row.get("program") == LIVE_UI_PROGRAM
            and row.get("status") == STATUS_PASSED
            and row.get("exit_code") == 0
            and row.get("command") == list(LIVE_UI_COMMAND)
            and bool(expected_steps)
            and row.get("steps") == expected_steps
            for row in ui_rows
        ):
            problems.append(f"live UI check missing for {check.name}")
    grants = _as_dict(actor.get("verified_grants"))
    for slot in spec.granted_actors:
        grant = _as_dict(grants.get(slot))
        if (
            not _present(grant.get("event_id"))
            or not _present(grant.get("correlation_id"))
            or grant.get("outcome") != "allow"
            or grant.get("tenant_id") != actor.get("tenant_id")
            or not _canonical_uuid(grant.get("account_ref"))
            or grant.get("account_ref") != _as_dict(scope.get("account_ids")).get(slot)
        ):
            problems.append(f"served business grant missing for {slot}")
    probes = _as_dict(entry.get("negative_probes"))
    for probe in ("cross_tenant_denied", "wrong_role_denied"):
        if probes.get(probe) is not True:
            problems.append(f"negative probe {probe} not proven")
    if spec.policy is not None and probes.get("policy_rejected") is not True:
        problems.append("policy rejection not proven")
    writes = _as_dict(entry.get("requests")).get("business_writes")
    if not isinstance(writes, int) or isinstance(writes, bool) or writes < len(spec.writes):
        problems.append("business write count below the journey's writes")
    if entry.get("blockers"):
        problems.append("journey recorded blockers")
    return problems


def verify_receipt(
    receipt: Mapping[str, Any] | None,
    *,
    expected_sha: str,
    expected_digest: str,
    release_profile: str,
) -> list[ReceiptCheck]:
    """One check per requirement; every one must hold for full acceptance."""

    checks: list[ReceiptCheck] = []

    def add(ok: bool, name: str, detail: str, dependency: str = "business-journey") -> None:
        checks.append(ReceiptCheck(bool(ok), name, detail, dependency))

    if release_profile != RELEASE_PROFILE_FULL:
        add(
            False,
            "business_journeys:admission",
            f"release profile {release_profile!r} has no full business acceptance admission",
            "release-profile",
        )
        return checks
    if not isinstance(receipt, Mapping):
        add(False, "business_journeys:receipt", "no six-journey business acceptance receipt")
        for journey_id in JOURNEY_IDS:
            add(False, f"business_journey:{journey_id}", "BLOCKED: no receipt")
        return checks

    add(
        receipt.get("kind") == RECEIPT_KIND
        and receipt.get("schema_version") == RECEIPT_SCHEMA_VERSION,
        "business_journeys:schema",
        f"kind={receipt.get('kind')} schema_version={receipt.get('schema_version')}",
    )
    add(
        receipt.get("offline_fixture") is False,
        "business_journeys:live_receipt",
        f"offline_fixture={receipt.get('offline_fixture')}",
        "data-binding",
    )
    sealed = receipt.get("receipt_digest") == receipt_digest(receipt)
    add(
        sealed,
        "business_journeys:receipt_digest",
        "digest recomputed" if sealed else "receipt was altered after sealing",
    )
    release = _as_dict(receipt.get("release"))
    expected_sha = expected_sha.strip().lower()
    expected_digest = expected_digest.strip().lower()
    add(
        bool(SHA_PATTERN.fullmatch(expected_sha))
        and receipt.get("source_sha") == expected_sha
        and release.get("release_sha") == expected_sha,
        "business_journeys:release_sha",
        (
            f"expected={expected_sha or '<missing>'} source={receipt.get('source_sha')} "
            f"release={release.get('release_sha')}"
        ),
        "release",
    )
    add(
        bool(DIGEST_PATTERN.fullmatch(expected_digest))
        and release.get("manifest_digest") == expected_digest,
        "business_journeys:manifest_digest",
        f"expected={expected_digest or '<missing>'} receipt={release.get('manifest_digest')}",
        "release",
    )
    add(
        release.get("sealed_release_profile") == RELEASE_PROFILE_FULL
        and release.get("full_profile_admitted") is True
        and not release.get("errors"),
        "business_journeys:sealed_profile",
        (
            f"sealed={release.get('sealed_release_profile')} "
            f"admitted={release.get('full_profile_admitted')}"
        ),
        "release-profile",
    )
    command = _as_dict(receipt.get("command"))
    argv = command.get("argv")
    add(
        isinstance(argv, list)
        and bool(argv)
        and str(argv[0]).endswith(RUNNER_PATH)
        and command.get("exit_code") == 0,
        "business_journeys:command_exit",
        f"argv0={argv[0] if isinstance(argv, list) and argv else '<missing>'} "
        f"exit_code={command.get('exit_code')}",
    )
    summary = _as_dict(receipt.get("summary"))
    add(
        receipt.get("selection") == list(JOURNEY_IDS)
        and receipt.get("complete_selection") is True
        and summary.get("status") == STATUS_PASSED
        and summary.get("full_acceptance_eligible") is True,
        "business_journeys:complete_selection",
        (
            f"selection={receipt.get('selection')} status={summary.get('status')} "
            f"eligible={summary.get('full_acceptance_eligible')}"
        ),
    )
    forbidden = _forbidden_keys(receipt)
    add(
        not forbidden,
        "business_journeys:no_secret_fields",
        "none" if not forbidden else f"paths={forbidden[:5]}",
        "data-binding",
    )
    journeys = _as_dict(receipt.get("journeys"))
    for journey_id in JOURNEY_IDS:
        entry = _as_dict(journeys.get(journey_id))
        problems = journey_receipt_problems(JOURNEYS_BY_ID[journey_id], entry)
        add(
            not problems,
            f"business_journey:{journey_id}",
            f"status={entry.get('status') or 'BLOCKED: missing'}"
            + (f" problems={problems[:6]}" if problems else ""),
        )
    return checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_gate_module() -> Any:
    spec = importlib.util.spec_from_file_location("odp_check_live_e2e_gate_journeys", LIVE_E2E_GATE)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise RuntimeError(f"cannot load {LIVE_E2E_GATE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--api-url", default=os.environ.get(API_URL_ENV, ""))
    parser.add_argument("--web-url", default=os.environ.get(WEB_URL_ENV, ""))
    parser.add_argument("--expected-sha", default=os.environ.get(EXPECTED_SHA_ENV, ""))
    parser.add_argument(
        "--release-manifest",
        type=Path,
        default=_env_path(RELEASE_MANIFEST_ENV),
        help="Immutable admitted manifest of the deployed release.",
    )
    parser.add_argument(
        "--expected-manifest-digest", default=os.environ.get(MANIFEST_DIGEST_ENV, "")
    )
    parser.add_argument(
        "--scope",
        type=Path,
        default=_env_path(SCOPE_ENV),
        help="Journey scope authorization bound to this release.",
    )
    parser.add_argument(
        "--journey",
        action="append",
        choices=list(JOURNEY_IDS),
        help="Journey selector (repeatable); default runs all six.",
    )
    parser.add_argument("--job-deadline-seconds", type=float, default=600.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-http",
        action="store_true",
        help="Permit HTTP only for an explicitly controlled non-production target.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(raw_argv)
    gate = _load_gate_module()
    manifest, manifest_error = load_json_file(args.release_manifest, label="release manifest")
    binding = bind_release(
        manifest,
        expected_sha=args.expected_sha,
        expected_digest=args.expected_manifest_digest,
        caller_profile=os.environ.get(RELEASE_PROFILE_ENV, ""),
        load_error=manifest_error,
    )
    scope_document, _ = load_json_file(args.scope, label="journey scope")
    run_id = f"journey-{binding.release_sha[:12] or 'unbound'}-{int(time.time())}"

    def client(url: str, transport: str = "") -> tuple[Any, str]:
        if not url:
            return None, ""
        try:
            base = gate._normalize_origin(url, allow_http=args.allow_http)
        except ValueError:
            return None, ""
        return (
            gate.UrllibHttpClient(
                base,
                timeout=args.timeout,
                bearer_token="",
                operator_role="",
                operator_subject="",
                operator_tenant="",
                correlation_id=run_id,
                transport_token=transport,
            ),
            base,
        )

    web, web_base = client(args.web_url)
    api, _ = client(args.api_url, os.environ.get(API_TRANSPORT_TOKEN_ENV, "").strip())
    origin = ""
    if web_base:
        parsed = urllib.parse.urlsplit(web_base)
        origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))

    receipt = run_journeys(
        binding=binding,
        scope_document=scope_document,
        selection=args.journey,
        web=web,
        web_origin=origin,
        api=api,
        environ=os.environ,
        command=[RUNNER_PATH, *raw_argv],
        now=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        run_id=run_id,
        job_deadline_seconds=args.job_deadline_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        ui_driver=PlaywrightUiDriver(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = receipt["summary"]
    print(f"Business journeys: {summary['status']} statuses={summary['statuses']}")
    for journey_id, entry in receipt["journeys"].items():
        for blocker in entry["blockers"]:
            print(
                f"  - {journey_id} {blocker['check']} [{blocker['dependency']}]: {blocker['detail']}"
            )
    print(f"receipt={args.output}")
    return int(receipt["command"]["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
