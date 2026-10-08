"""Fail-closed behaviour of the six live business journeys (ODP-BUSINESS-LIVE-E2E-COVERAGE-001).

OFFLINE FIXTURES ONLY. ``BusinessWeb`` below is an in-process double of the
deployed Web BFF used to prove the *runner's* decisions; it is never live
evidence. Every receipt the runner seals from it is still checked by
``verify_receipt``, and the live acceptance itself belongs to
ODP-BUSINESS-LIVE-E2E-ACCEPTANCE-001 against the real deployment.

Each test starts from a world where all six journeys pass, breaks exactly one
business fact, and asserts (a) the journey does not pass, (b) it names the
dependency an operator must repair, and (c) where the failure is a missing
admission, no business write and no worker/provider trigger left the runner.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.parse
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

import pytest

from delivery_toolchain.release import release_manifest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "delivery_toolchain/e2e/live_business_journeys.py"
GATE_TESTS = ROOT / "tests/e2e/test_live_e2e_gate.py"

# Matches tests/e2e/test_live_e2e_gate.py so the gate integration below can
# reuse that suite's passing live-deployment doubles unchanged.
SHA = "b" * 40
NOW = "2026-10-08T07:00:00Z"
WEB_ORIGIN = "https://oday-web.dev.alfloop.internal"
TENANT = "7d3c1a9e-5b2f-4c8d-9e1a-2b3c4d5e6f70"
FOREIGN_TENANT = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
AUTH_PATHS = {"/login", "/auth/session", "/auth/logout"}


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bj = _load("live_business_journeys_under_test", RUNNER)


# ---------------------------------------------------------------------------
# Offline Web BFF double (NOT live evidence)
# ---------------------------------------------------------------------------


@dataclass
class Resp:
    status: int
    payload: dict[str, Any] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    error: str = ""
    location: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


@dataclass
class Ctx:
    actor: str
    roles: frozenset[str]
    method: str
    path: str
    query: dict[str, str]
    body: dict[str, Any]
    headers: dict[str, str]
    match: re.Match[str]


ACCOUNTS: dict[str, frozenset[str]] = {
    # operations
    "ops.manager": frozenset({"operations_manager"}),
    "field.supervisor": frozenset({"regional_supervisor"}),
    # growth
    "pricing.manager": frozenset({"pricing_manager"}),
    "marketing.manager": frozenset({"marketing_manager"}),
    "ops.analyst": frozenset({"operations_manager"}),
    # expansion
    "network.planner": frozenset({"executive"}),
    "network.approver": frozenset({"executive"}),
    "expansion.analyst": frozenset({"marketing_manager"}),
    # governance
    "governance.lead": frozenset({"operations_manager"}),
    "growth.marketer": frozenset({"marketing_manager"}),
    # franchise
    "franchisee.owner": frozenset({"franchisee"}),
    "ops.regional": frozenset({"operations_manager"}),
    # intake
    "intake.staff": frozenset({"expansion_user"}),
    "pricing.analyst": frozenset({"pricing_manager"}),
}

#: journey -> slot -> account (the scope authorizes exactly these).
ACTORS: dict[str, dict[str, str]] = {
    "operations": {"primary": "ops.manager", "denied": "field.supervisor"},
    "growth": {
        "primary": "pricing.manager",
        "marketer": "marketing.manager",
        "denied": "ops.analyst",
    },
    "expansion": {
        "primary": "network.planner",
        "approver": "network.approver",
        "denied": "expansion.analyst",
    },
    "governance": {"primary": "governance.lead", "denied": "growth.marketer"},
    "franchise": {"primary": "franchisee.owner", "denied": "ops.regional"},
    "intake": {"primary": "intake.staff", "denied": "pricing.analyst"},
}


def account_id(username: str) -> str:
    return str(uuid5(NAMESPACE_DNS, username))


def password_for(username: str) -> str:
    return f"Pw!{username}#2026-q4"


def credential_env(journeys: dict[str, dict[str, str]] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for journey_id, slots in (journeys or ACTORS).items():
        spec = bj.JOURNEYS_BY_ID[journey_id]
        for slot, username in slots.items():
            prefix = spec.env_prefix(slot)
            env[f"{prefix}_USERNAME"] = username
            env[f"{prefix}_PASSWORD"] = password_for(username)
    return env


class BusinessWeb:
    """Stateful double of the deployed Web origin and its ``/api/v1`` BFF."""

    def __init__(self) -> None:
        self.release_identity = {
            "release_sha": SHA, "release_profile": "full", "release_profile_valid": True,
            "manifest_digest": DIGEST, "web_release_sha": SHA,
            "web_release_profile": "full", "web_manifest_digest": DIGEST,
        }
        self.admins: set[str] = set()
        self.account_roles = dict(ACCOUNTS)
        self.calls: list[tuple[str, str, str]] = []
        self.audit: list[dict[str, Any]] = []
        self.handlers: list[tuple[str, re.Pattern[str], Callable[[Ctx], Resp]]] = []
        self.job_status = "succeeded"
        self.open_rbac: set[str] = set()
        self.cross_tenant_leak: set[str] = set()
        self.non_durable: set[str] = set()
        self.drop_audit: set[str] = set()
        self.policy_open = False
        self._event = 0

        self.issue = {
            "id": "SO-ISS-1001",
            "storeId": "ST-0412",
            "tenantId": TENANT,
            "status": "new",
            "ownerRoleId": "opsLead", "ownerName": "Offline owner",
            "slaDueAt": NOW, "relatedGrowthId": None,
            "history": [{"status": "new", "at": "2026-10-07T09:00:00Z"}],
        }
        self.plan = {
            "plan_id": "PLN-2001",
            "tenant_id": TENANT,
            "status": "draft",
            "status_history": ["draft"],
            "items": [{"sku": "SKU-88", "price": 129}],
        }
        self.scenario: dict[str, Any] = {
            "scenario_id": "NP-SCN-31",
            "tenant_id": TENANT,
            "status": "draft",
            "status_history": ["draft"],
            "options_by_entity": {"RB-801": ["C-1", "C-2"]},
            "model_version": "sitescore-2026.09",
            "feature_version": "fs-14",
            "solver_version": "cp-sat-9.10",
            "approvals": [],
        }
        self.rebalance = {
            "id": "RB-801", "status": "avmready",
            "canonicalNetPlanScenarioIds": ["NP-SCN-31"],
            "netPlanScenarios": [], "netPlanJob": None,
        }
        self.listings = [{"id": "L-2024", "tenantId": TENANT,
                          "sourceId": "broker-desk", "sourceEvidence": []}]
        self.governance = {
            "approvals": [{"id": "GOV-APR-4001", "status": "pending", "kind": "price_exception"}],
            "decisions": [],
            "auditRows": [],
        }
        self.store = {
            "store": {"id": "ST-0412"},
            "meta": {"scope": {"storeId": "ST-0412"}},
            "reports": [],
        }
        self.report = {
            "report_id": "ALR-76",
            "campaign_id": "CMP-3001",
            "model_version": "adlift-1",
            "feature_version": "fs-14",
            "source_snapshot_ids": ["snap-201"],
            "generated_at": NOW,
        }
        self.intake = {
            "id": "LI-6001",
            "tenantId": TENANT,
            "sourceId": "broker-desk",
            "policy": "ASSISTED_ENTRY_ONLY",
            "intakeMethod": "ASSISTED_MANUAL",
            "stage": "READY",
            "version": 3,
            "auditEvents": [],
            "matchResult": {"targetListingId": "L-2024"},
        }
        self._routes()

    # -- plumbing -----------------------------------------------------------

    def route(self, method: str, pattern: str, handler: Callable[[Ctx], Resp]) -> None:
        self.handlers.append((method, re.compile(pattern), handler))

    @property
    def business_writes(self) -> list[tuple[str, str, str]]:
        return [
            call
            for call in self.calls
            if call[0] in MUTATING and urllib.parse.urlsplit(call[1]).path not in AUTH_PATHS
        ]

    def writes_to(self, fragment: str) -> list[tuple[str, str, str]]:
        return [call for call in self.business_writes if fragment in call[1]]

    def event(self, ctx: Ctx, event_type: str, journey: str, job_id: str | None = None) -> str:
        self._event += 1
        event_id = f"evt-{self._event:04d}"
        if journey not in self.drop_audit:
            self.audit.append(
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "correlation_id": ctx.headers.get("x-correlation-id"),
                    "job_id": job_id,
                }
            )
        return event_id

    def denied(self, ctx: Ctx, journey: str, roles: set[str]) -> Resp | None:
        if journey in self.open_rbac or ctx.roles & roles:
            return None
        return Resp(403, {"error": {"code": "FORBIDDEN"}})

    def request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        body: Any = None,
        headers: Any = None,
        follow_redirects: bool = True,
    ) -> Resp:
        assert authenticated is False, "the journey runner must never send an API bearer"
        assert follow_redirects is False
        headers = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
        cookie = headers.get("cookie", "")
        actor = cookie.split("odp_session=sess-", 1)[1] if "odp_session=sess-" in cookie else ""
        self.calls.append((method, path, actor))
        parts = urllib.parse.urlsplit(path)
        bare = parts.path
        body = dict(body or {})
        if bare == "/login":
            username = str(body.get("username") or "")
            if username in ACCOUNTS and body.get("password") == password_for(username):
                return Resp(
                    200,
                    {"ok": True, "subject": username, "returnTo": body.get("returnTo")},
                    cookies={"odp_session": f"sess-{username}"},
                )
            return Resp(401, {"ok": False, "error": "invalid_credentials"})
        if bare == "/auth/session":
            return Resp(200, {"subject": actor}) if actor else Resp(401, {})
        if not actor:
            return Resp(401, {"error": {"code": "unauthenticated"}})
        if method in MUTATING and not headers.get("idempotency-key"):
            return Resp(400, {"error": {"code": "IDEMPOTENCY_KEY_REQUIRED"}})
        if bare == bj.RELEASE_IDENTITY_PATH:
            return Resp(200, deepcopy(self.release_identity))
        if bare == bj.PRINCIPAL_PATH:
            roles = self.account_roles[actor] | (
                {"platform_admin"} if actor in self.admins else set()
            )
            return Resp(
                200, {"account_id": account_id(actor), "tenant_id": TENANT, "roles": sorted(roles)}
            )
        if bare == "/api/v1/operator/users":
            return Resp(200, {"users": []}) if actor in self.admins else Resp(403, {})
        query = dict(urllib.parse.parse_qsl(parts.query))
        for verb, pattern, handler in self.handlers:
            match = pattern.fullmatch(bare)
            if verb == method and match:
                ctx = Ctx(actor, ACCOUNTS[actor], method, bare, query, body, headers, match)
                result = handler(ctx)
                if result.status < 400 and bare != "/api/v1/audit/events":
                    self._event += 1
                    self.audit.append(
                        {
                            "event_id": f"authz-{self._event}",
                            "event_type": "security.authorization",
                            "outcome": "allow",
                            "actor": account_id(actor),
                            "action": "view" if method == "GET" else "create",
                            "resource": bare,
                            "correlation_id": headers.get("x-correlation-id"),
                            "metadata": {"tenant_id": TENANT, "reason": "role permits request"},
                        }
                    )
                return deepcopy(result)
        raise AssertionError(f"unrouted journey request: {method} {path}")

    # -- routes -------------------------------------------------------------

    def _routes(self) -> None:  # noqa: C901 - one table of business routes
        v1 = "/api/v1"

        def audit_events(ctx: Ctx) -> Resp:
            wanted = ctx.query.get("correlation_id")
            return Resp(200, {"events": [e for e in self.audit if e["correlation_id"] == wanted]})

        self.route("GET", f"{v1}/audit/events", audit_events)

        # operations ---------------------------------------------------------
        def read_issue(ctx: Ctx) -> Resp:
            issue_id = ctx.match.group(1)
            if issue_id == self.issue["id"] or "operations" in self.cross_tenant_leak:
                return Resp(200, {"issue": self.issue})
            return Resp(404, {"error": {"code": "NOT_FOUND"}})

        def transition(ctx: Ctx) -> Resp:
            if ctx.headers.get("x-operator-role") != "ops-lead":
                return Resp(403, {})
            refused = self.denied(ctx, "operations", {"operations_manager", "executive"})
            if refused:
                return refused
            if ctx.match.group(1) != self.issue["id"]:
                return Resp(404, {})
            action = ctx.match.group(2)
            if "operations" not in self.non_durable:
                if action == "transfer":
                    self.issue["ownerRoleId"] = ctx.body.get("targetRoleId") or self.issue["ownerRoleId"]
                    if ctx.body.get("targetOwnerName"):
                        self.issue["ownerName"] = ctx.body["targetOwnerName"].strip()
                else:
                    self.issue["status"] = f"{action}d" if action.endswith("e") else f"{action}ed"
                    self.issue["history"].append({"status": self.issue["status"], "by": ctx.actor})
            event_id = self.event(ctx, "operator.store_ops.issue_transition", "operations")
            return Resp(200, {"issue": self.issue, "auditEvent": {"eventId": event_id}})

        self.route("GET", rf"{v1}/operator/store-ops/issues/([^/]+)", read_issue)
        self.route("POST", rf"{v1}/operator/store-ops/issues/([^/]+)/([^/]+)", transition)

        # growth -------------------------------------------------------------
        def read_plan(ctx: Ctx) -> Resp:
            if ctx.match.group(1) == self.plan["plan_id"] or "growth" in self.cross_tenant_leak:
                return Resp(200, self.plan)
            return Resp(404, {})

        def adlift_report(ctx: Ctx) -> Resp:
            refused = self.denied(ctx, "growth", {"marketing_manager"})
            return refused or Resp(200, self.report)

        def price_action(ctx: Ctx) -> Resp:
            refused = self.denied(ctx, "growth", {"pricing_manager"})
            if refused:
                return refused
            if ctx.match.group(1) != self.plan["plan_id"]:
                return Resp(404, {})
            if "growth" not in self.non_durable:
                self.plan["status"] = "submitted"
                self.plan["status_history"].append("submitted")
            self.event(ctx, bj.PRICEOPS_AUDIT_EVENTS[ctx.match.group(2)], "growth")
            return Resp(200, self.plan)

        def adlift_job(ctx: Ctx) -> Resp:
            refused = self.denied(ctx, "growth", {"marketing_manager"})
            if refused:
                return refused
            event_id = self.event(ctx, "adlift.incrementality_evaluated", "growth", "ALJ-77")
            self.report = {**self.report, "report_id": "ALR-77"}
            return Resp(202, {"job_id": "ALJ-77", "status": "queued", "audit_event_id": event_id})

        def adlift_job_status(ctx: Ctx) -> Resp:
            return Resp(
                200,
                {"job_id": ctx.match.group(1), "status": self.job_status, "reports": [self.report]},
            )

        self.route("GET", rf"{v1}/priceops/plans/([^/]+)", read_plan)
        self.route("GET", rf"{v1}/adlift/reports/([^/]+)", adlift_report)
        self.route("POST", rf"{v1}/priceops/plans/([^/]+)/([^/]+)", price_action)
        self.route("POST", rf"{v1}/adlift/incrementality-jobs", adlift_job)
        self.route("GET", rf"{v1}/adlift/incrementality-jobs/([^/]+)", adlift_job_status)

        # expansion ----------------------------------------------------------
        def read_scenario(ctx: Ctx) -> Resp:
            own = ctx.match.group(1) == self.scenario["scenario_id"]
            if own or "expansion" in self.cross_tenant_leak:
                return Resp(200, self.scenario)
            return Resp(404, {})

        def scenario_step(ctx: Ctx) -> Resp:
            refused = self.denied(ctx, "expansion", {"executive"})
            if refused:
                return refused
            step = ctx.match.group(2)
            durable = "expansion" not in self.non_durable
            if step == "solve" and durable:
                self.scenario.update(
                    {
                        "status": "solved",
                        "selected_candidate_id": "C-2",
                        "solve": {
                            "result": {
                                "objective": 1.25,
                                "unmodelled_constraint_classes": ["lease_terms"],
                            },
                            "model_version": self.scenario.get("model_version"),
                        },
                    }
                )
            elif step == "submit" and durable:
                self.scenario["status"] = "submitted"
            elif step == "decide" and durable:
                self.scenario["status"] = ctx.body.get("decision")
                self.scenario["approvals"].append(
                    {
                        "approval_id": f"NP-APP-{len(self.scenario['approvals']) + 1}",
                        "scenario_id": self.scenario["scenario_id"],
                        "actor_id": ctx.body.get("actor_id"),
                        "approval_principal_id": ctx.body.get("actor_id"),
                        "approval_receipt_id": ctx.body.get("approval_receipt_id"),
                        "decision": ctx.body.get("decision"),
                        "reason": ctx.body.get("reason"), "decided_at": NOW,
                        "authentic_approval_verified": True,
                        "approved_by": ctx.actor,
                        "modelled_constraint_classes": ["CAPITAL"],
                        "unmodelled_constraint_classes": ["LEASE"],
                    }
                )
            if durable:
                self.scenario["status_history"].append(self.scenario["status"])
            event_type = {"solve": "netplan.solved", "submit": "netplan.submitted"}.get(
                step, f"netplan.{ctx.body.get('decision')}"
            )
            self.event(ctx, event_type, "expansion")
            return Resp(200, self.scenario)

        def operator_solve(ctx: Ctx) -> Resp:
            refused = self.denied(ctx, "expansion", {"executive"})
            if refused:
                return refused
            if ctx.match.group(1) != self.rebalance["id"]:
                return Resp(404, {})
            if "expansion" not in self.non_durable:
                self.scenario.update({
                    "status": "solved", "selected_candidate_id": "C-2",
                    "solve": {"solved_at": NOW, "model_version": self.scenario["model_version"],
                              "result": {"unmodelled_constraint_classes": ["LEASE"]}},
                })
                self.scenario["status_history"].append("solved")
                self.rebalance.update({
                    "status": "netplanreview", "selectedScenarioId": None,
                    "netPlanJob": {"id": self.scenario["scenario_id"], "completedAt": NOW},
                    "netPlanScenarios": [{"id": self.scenario["scenario_id"],
                                          "modelledConstraintClasses": ["CAPITAL"],
                                          "unmodelledConstraintClasses": ["LEASE"]}],
                })
            event_id = self.event(ctx, "rebalance.netplan.solved", "expansion")
            return Resp(200, {"store": self.rebalance,
                              "auditEvent": {"id": None if "expansion" in self.drop_audit else event_id},
                              "correlationId": ctx.headers.get("x-correlation-id")})

        self.route("GET", rf"{v1}/operator/network-rebalance", lambda ctx: Resp(200, {"stores": [self.rebalance]}))
        self.route("POST", rf"{v1}/operator/network-rebalance/stores/([^/]+)/netplan/solve", operator_solve)
        self.route("GET", rf"{v1}/netplan/scenarios/([^/]+)", read_scenario)
        self.route("POST", rf"{v1}/netplan/scenarios/([^/]+)/(solve|submit|decide)", scenario_step)

        # governance ---------------------------------------------------------
        def snapshot(ctx: Ctx) -> Resp:
            return Resp(200, self.governance)

        def decision(ctx: Ctx) -> Resp:
            if ctx.body.get("action") not in {"approve", "return", "reject"}:
                return Resp(422, {})
            refused = self.denied(ctx, "governance", {"operations_manager", "executive"})
            if refused:
                return refused
            approval = next(
                (a for a in self.governance["approvals"] if a["id"] == ctx.body.get("approvalId")),
                None,
            )
            if approval is None and "governance" not in self.cross_tenant_leak:
                return Resp(404, {"error": {"code": "NOT_FOUND"}})
            if approval is not None and "governance" not in self.non_durable:
                approval["status"] = {
                    "approve": "approved",
                    "return": "returned",
                    "reject": "rejected",
                }[ctx.body["action"]]
                self.governance["decisions"].append(
                    {"id": "DEC-1", "approvalId": approval["id"], "action": ctx.body.get("action")}
                )
            if "governance" not in self.drop_audit:
                self.governance["auditRows"].append(
                    {
                        "id": "AUD-GOV-1",
                        "category": "approval",
                        "correlationId": f"corr-{ctx.body.get('approvalId')}",
                    }
                )
            return Resp(
                200,
                {
                    "approvalId": ctx.body.get("approvalId"),
                    "action": ctx.body["action"],
                    "decision": {"id": "DEC-1"},
                    "correlation_id": ctx.headers.get("x-correlation-id"),
                },
            )

        self.route("GET", rf"{v1}/operator/governance/snapshot", snapshot)
        self.route("POST", rf"{v1}/operator/governance/decisions", decision)

        # franchise ----------------------------------------------------------
        def own_store(ctx: Ctx) -> Resp:
            refused = self.denied(ctx, "franchise", {"franchisee"})
            if refused:
                return refused
            own = ctx.query.get("storeId") == self.store["store"]["id"]
            if own or "franchise" in self.cross_tenant_leak:
                return Resp(200, self.store)
            return Resp(403, {"error": {"code": "STORE_SCOPE_MISMATCH"}})

        def field_report(ctx: Ctx) -> Resp:
            refused = self.denied(ctx, "franchise", {"franchisee"})
            if refused:
                return refused
            if ctx.body.get("storeId") != self.store["store"]["id"]:
                return Resp(403, {})
            report = {"reportId": f"FR-{len(self.store['reports']) + 1}", **ctx.body,
                      "subjectId": account_id(ctx.actor), "status": "received", "createdAt": NOW,
                      "correlationId": ctx.headers.get("x-correlation-id")}
            if "franchise" not in self.non_durable:
                self.store["reports"].append(report)
            audit = (
                {
                    "id": "AUD-SHELL-1",
                    "metadata": {"correlationId": ctx.headers.get("x-correlation-id")},
                }
                if "franchise" not in self.drop_audit
                else {}
            )
            return Resp(201, {"report": report, "auditEvent": audit})

        self.route("GET", rf"{v1}/operator/shell/franchisee", own_store)
        self.route("POST", rf"{v1}/operator/shell/franchisee/reports", field_report)

        # intake -------------------------------------------------------------
        def read_intake(ctx: Ctx) -> Resp:
            if ctx.match.group(1) == self.intake["id"] or "intake" in self.cross_tenant_leak:
                return Resp(200, self.intake)
            return Resp(404, {})

        def decide_intake(ctx: Ctx) -> Resp:
            if ctx.body.get("action") not in {
                "create",
                "revise",
                "duplicate",
                "quarantine",
                "reject",
            } or not ctx.body.get("reason"):
                return Resp(422, {})
            if ctx.headers.get("x-operator-role") != "expansion-staff":
                return Resp(403, {})
            refused = self.denied(ctx, "intake", {"expansion_user"})
            if refused:
                return refused
            action = ctx.body["action"]
            if "intake" not in self.non_durable:
                self.intake["stage"] = {"quarantine": "QUARANTINED", "reject": "FAILED"}.get(action, "READY")
                if action == "create":
                    target_id = "L-2030"
                    self.listings.append({"id": target_id, "tenantId": TENANT, "sourceId": self.intake["sourceId"]})
                    self.intake["matchResult"]["targetListingId"] = target_id
                elif action in {"revise", "duplicate"}:
                    target = self.listings[0]
                    marker = f"EV-{self.intake['id']}-{'DUPLICATE' if action == 'duplicate' else 'REVISION'}"
                    target["sourceEvidence"].append(marker)
                    target["status"] = "watching"
            if "intake" not in self.drop_audit:
                self.intake["auditEvents"].append(
                    {
                        "id": "AUD-INTAKE-1",
                        "targetId": self.intake["id"],
                        "action": f"intake.decide.{ctx.body['action']}",
                        "correlationId": ctx.headers.get("x-correlation-id"),
                        "metadata": {"decision": action, "stage": self.intake["stage"],
                                     "targetListingId": self.intake["matchResult"]["targetListingId"]},
                    }
                )
            return Resp(200, self.intake)

        def submit_intake(ctx: Ctx) -> Resp:
            refused = self.denied(ctx, "intake", {"expansion_user"})
            if refused:
                return refused
            if self.policy_open:
                return Resp(
                    200,
                    {
                        "id": "LI-7001",
                        "stage": "PENDING_REVIEW",
                        "policy": "SOURCE_BLOCKED",
                        "rawSnapshot": {"html": "<listing/>"},
                    },
                )
            return Resp(422, {"error": {"code": "SOURCE_BLOCKED"}})

        def search_intake(ctx: Ctx) -> Resp:
            return Resp(200, {"items": []})

        self.route(
            "GET",
            rf"{v1}/operator/network-listings",
            lambda ctx: Resp(200, {"listings": self.listings}),
        )
        self.route("GET", rf"{v1}/operator/network-listings/intake", search_intake)
        self.route("POST", rf"{v1}/operator/network-listings/intake/submit", submit_intake)
        self.route("GET", rf"{v1}/operator/network-listings/intake/([^/]+)", read_intake)
        self.route("POST", rf"{v1}/operator/network-listings/intake/([^/]+)/decide", decide_intake)


class RuntimeApi:
    """Offline double of the side-effect-free deployed identity endpoint."""

    def __init__(self, *, sha: str = SHA, profile: str = "full", valid: bool = True) -> None:
        self.sha = sha
        self.profile = profile
        self.valid = valid
        self.calls: list[str] = []

    def request(self, method: str, path: str, *, authenticated: bool = True, **_: Any) -> Resp:
        assert authenticated is False
        self.calls.append(f"{method} {path}")
        if path == "/platform/version":
            return Resp(200, {"release_sha": self.sha})
        if path == bj.RELEASE_IDENTITY_PATH:
            return Resp(200, {
                "release_sha": self.sha, "release_profile": self.profile,
                "release_profile_valid": self.valid, "manifest_digest": DIGEST,
            })
        if path == "/readiness":
            raise AssertionError("Provider-probing readiness must not be used for admission")
        raise AssertionError(f"unrouted runtime request: {method} {path}")


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


# ---------------------------------------------------------------------------
# Release manifest and scope authorization
# ---------------------------------------------------------------------------


def manifest(profile: str = "full", **extra: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "candidate_sha": SHA,
        "source_repository": "alfloop-dev/odayplus",
        "images": {"api": f"oday-api@sha256:{'1' * 64}", "web": f"oday-web@sha256:{'2' * 64}"},
        **extra,
    }
    profile_block = release_manifest.build_release_profile(profile, target_environment="dev")
    if profile_block is not None:
        document["release_profile"] = profile_block
    document["manifest_digest"] = release_manifest.compute_manifest_digest(document)
    return document


FULL_MANIFEST = manifest()
DIGEST = FULL_MANIFEST["manifest_digest"]


def journey_scopes() -> dict[str, dict[str, Any]]:
    def base(journey_id: str, records: dict[str, str], foreign: dict[str, str]) -> dict[str, Any]:
        return {
            "tenant_id": TENANT,
            "foreign_tenant_id": FOREIGN_TENANT,
            "actors": dict(ACTORS[journey_id]),
            "account_ids": {
                slot: account_id(username) for slot, username in ACTORS[journey_id].items()
            },
            "records": records,
            "foreign_records": foreign,
            "writes": {},
        }

    scopes = {
        "operations": base("operations", {"issue_id": "SO-ISS-1001"}, {"issue_id": "SO-ISS-9901"}),
        "growth": base(
            "growth",
            {"plan_id": "PLN-2001", "campaign_id": "CMP-3001"},
            {"plan_id": "PLN-9902"},
        ),
        "expansion": base(
            "expansion",
            {
                "scenario_id": "NP-SCN-31",
                "rebalance_store_id": "RB-801",
                "modelled_class": "CAPITAL",
                "unmodelled_class": "LEASE",
            },
            {"scenario_id": "NP-SCN-99"},
        ),
        "governance": base(
            "governance", {"approval_id": "GOV-APR-4001"}, {"approval_id": "GOV-APR-9904"}
        ),
        "franchise": base("franchise", {"store_id": "ST-0412"}, {"store_id": "ST-0999"}),
        "intake": base("intake", {"intake_id": "LI-6001"}, {"intake_id": "LI-9906"}),
    }
    scopes["operations"]["writes"] = {
        "transition": {"action": "triage", "body": {"note": "Field lead to inspect the chiller"}}
    }
    scopes["growth"]["writes"] = {
        "price_action": {"action": "submit", "body": {"comment": "Q4 basket price move"}},
        "adlift_job": {
            "action": "incrementality",
            "body": {
                "campaigns": [{"campaign_id": "CMP-3001", "source_snapshot_ids": ["snap-201"]}]
            },
        },
    }
    scopes["expansion"]["writes"] = {
        "solve": {"action": "solve", "body": {}},
        "submit": {"action": "submit", "body": {}},
        "decide": {"action": "approved", "body": {
            "decision": "approved", "actor_id": account_id(ACTORS["expansion"]["approver"]),
            "reason": "Offline named approval",
        }},
    }
    scopes["expansion"]["approval_ref"] = "EXP-APPROVAL-2026-118"
    scopes["governance"]["writes"] = {
        "decision": {
            "action": "approve",
            "body": {"action": "approve", "reason": "Within approved band"},
        }
    }
    scopes["governance"]["approval_ref"] = "GOV-CAB-2026-77"
    scopes["franchise"]["writes"] = {
        "report": {
            "action": "report",
            "body": {"category": "equipment", "message": "Freezer temperature alarm"},
        }
    }
    scopes["intake"]["writes"] = {
        "decide": {
            "action": "create",
            "body": {
                "action": "create",
                "reason": "Broker verified",
                "riskAcknowledged": True,
                "riskSummary": "Reviewed approved manual entry",
            },
        },
        "policy_probe": {
            "source_ref": "https://blocked-portal.example.invalid/listing-8812",
            "body": {"url": "https://blocked-portal.example.invalid/listing-8812"},
        },
    }
    return scopes


def scope_document(**journeys: Any) -> dict[str, Any]:
    scopes = journey_scopes()
    for journey_id, value in journeys.items():
        if value is None:
            scopes.pop(journey_id)
        else:
            scopes[journey_id] = value
    return {
        "kind": bj.SCOPE_KIND,
        "schema_version": bj.SCOPE_SCHEMA_VERSION,
        "release_sha": SHA,
        "manifest_digest": DIGEST,
        "authorized_by": "ops-director",
        "authorization_ref": "CAB-2026-10-08-01",
        "journeys": scopes,
    }


class OfflineUi:
    """OFFLINE ONLY; never a live browser receipt."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def available(self) -> tuple[bool, str]:
        return True, "offline double"

    def run(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return bj.UiOutcome(
            "PASSED",
            bj.LIVE_UI_COMMAND,
            0,
            "offline fixture",
        )


_DEFAULT = object()


def run(
    web: BusinessWeb | None = None,
    *,
    manifest_doc: Any = _DEFAULT,
    expected_digest: str = DIGEST,
    scope: Any = _DEFAULT,
    selection: list[str] | None = None,
    environ: dict[str, str] | None = None,
    caller_profile: str = "",
    api: Any = _DEFAULT,
    ui: Any = _DEFAULT,
) -> tuple[dict[str, Any], BusinessWeb, Any]:
    web = web or BusinessWeb()
    api = RuntimeApi() if api is _DEFAULT else api
    binding = bj.bind_release(
        FULL_MANIFEST if manifest_doc is _DEFAULT else manifest_doc,
        expected_sha=SHA,
        expected_digest=expected_digest,
        caller_profile=caller_profile,
    )
    clock = Clock()
    receipt = bj.run_journeys(
        binding=binding,
        scope_document=scope_document() if scope is _DEFAULT else scope,
        selection=selection,
        web=web,
        web_origin=WEB_ORIGIN,
        api=api,
        environ=credential_env() if environ is None else environ,
        command=[bj.RUNNER_PATH, *(f"--journey={j}" for j in selection or [])],
        now=NOW,
        run_id="journey-bbbbbbbbbbbb-1",
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        job_deadline_seconds=60.0,
        poll_interval_seconds=5.0,
        ui_driver=OfflineUi() if ui is _DEFAULT else ui,
    )
    return receipt, web, api


def blockers(receipt: dict[str, Any], journey_id: str) -> dict[str, str]:
    return {b["check"]: b["dependency"] for b in receipt["journeys"][journey_id]["blockers"]}


def reseal(receipt: dict[str, Any]) -> dict[str, Any]:
    receipt["receipt_digest"] = bj.receipt_digest(receipt)
    return receipt


def failing_checks(receipt: Any, **overrides: Any) -> set[str]:
    kwargs = {"expected_sha": SHA, "expected_digest": DIGEST, "release_profile": "full"}
    kwargs.update(overrides)
    return {c.name for c in bj.verify_receipt(receipt, **kwargs) if not c.ok}


# ---------------------------------------------------------------------------
# Happy path: six journeys, one sealed receipt
# ---------------------------------------------------------------------------


def test_all_six_journeys_pass_and_seal_a_verifiable_receipt() -> None:
    receipt, web, _ = run()

    statuses = receipt["summary"]["statuses"]
    assert statuses == {journey_id: "PASSED" for journey_id in bj.JOURNEY_IDS}, {
        j: receipt["journeys"][j]["blockers"] for j in bj.JOURNEY_IDS
    }
    assert receipt["summary"]["status"] == "PASSED"
    assert receipt["summary"]["full_acceptance_eligible"] is True
    assert receipt["command"]["exit_code"] == 0
    assert receipt["offline_fixture"] is False
    assert failing_checks(receipt) == set()
    # Real writes really happened, each with its own audit event.
    assert web.issue["status"] == "triaged"
    assert web.scenario["status"] == "approved"
    assert web.intake["stage"] == "READY"
    assert web.intake["version"] == 3
    assert web.intake["matchResult"]["targetListingId"] == "L-2030"
    assert len(web.store["reports"]) == 1


def test_receipt_binds_release_actor_selector_and_record_identity() -> None:
    receipt, _, _ = run()

    assert receipt["source_sha"] == SHA
    assert receipt["release"]["manifest_digest"] == DIGEST
    assert receipt["release"]["sealed_release_profile"] == "full"
    assert receipt["scope_authorization"]["authorization_ref"] == "CAB-2026-10-08-01"
    expansion = receipt["journeys"]["expansion"]
    assert expansion["selector"] == "/operator?ws=network"
    assert expansion["actor"]["tenant_id"] == TENANT
    assert expansion["actor"]["subjects"]["approver"] == "network.approver"
    assert expansion["actor"]["approval_ref"] == "EXP-APPROVAL-2026-118"
    assert expansion["before"]["record_id"] == expansion["after"]["record_id"] == "NP-SCN-31"
    assert {ref["write"] for ref in expansion["audit_refs"]} == {"solve", "submit", "decide"}
    growth = receipt["journeys"]["growth"]
    assert growth["job_refs"] == [
        {
            "job_id": "ALJ-77",
            "status": "succeeded",
            "result_id": "ALR-77",
            "previous_result_id": "ALR-76",
        }
    ]
    assert growth["requests"]["worker_or_provider_triggers"] == 1
    intake = receipt["journeys"]["intake"]
    assert intake["negative_probes"] == {
        "cross_tenant_denied": True,
        "wrong_role_denied": True,
        "policy_rejected": True,
    }
    assert intake["policy_outcome"]["mode"] == "refused"


def test_receipt_carries_no_password_cookie_or_token() -> None:
    receipt, _, _ = run()
    text = json.dumps(receipt)

    for username in ACCOUNTS:
        assert password_for(username) not in text
        assert f"sess-{username}" not in text
    assert bj._forbidden_keys(receipt) == []


def test_every_mutation_carries_an_idempotency_key_and_unique_correlation() -> None:
    web = BusinessWeb()
    seen: list[str] = []
    original = web.request

    def spy(method: str, path: str, **kwargs: Any) -> Resp:
        headers = kwargs.get("headers") or {}
        if method in MUTATING and path not in AUTH_PATHS:
            assert headers.get("idempotency-key", "").startswith("idem-")
            seen.append(headers["x-correlation-id"])
        return original(method, path, **kwargs)

    web.request = spy  # type: ignore[method-assign]
    run(web)
    assert seen and len(seen) == len(set(seen))


# ---------------------------------------------------------------------------
# Release binding: the sealed manifest decides, never the caller
# ---------------------------------------------------------------------------


def test_dev_admin_manifest_is_not_admitted_even_when_caller_claims_full() -> None:
    dev_manifest = manifest("dev-admin")
    receipt, web, api = run(
        manifest_doc=dev_manifest,
        expected_digest=dev_manifest["manifest_digest"],
        caller_profile="full",
    )

    assert receipt["summary"]["statuses"] == {j: "NOT_ADMITTED" for j in bj.JOURNEY_IDS}
    assert receipt["summary"]["status"] == "NOT_ADMITTED"
    assert receipt["release"]["caller_profile_override_refused"] is True
    assert receipt["release"]["full_profile_admitted"] is False
    assert receipt["command"]["exit_code"] == 1
    assert web.calls == [] and api.calls == []
    assert "business_journeys:sealed_profile" in failing_checks(reseal(receipt))


def test_dev_admin_release_reports_no_full_admission_in_the_verifier() -> None:
    receipt, _, _ = run()

    checks = bj.verify_receipt(
        receipt, expected_sha=SHA, expected_digest=DIGEST, release_profile="dev-admin"
    )
    assert [(c.name, c.ok, c.dependency) for c in checks] == [
        ("business_journeys:admission", False, "release-profile")
    ]


@pytest.mark.parametrize(
    ("manifest_doc", "expected_digest"),
    [
        (None, DIGEST),
        (manifest(candidate_sha="c" * 40), DIGEST),
        (FULL_MANIFEST, "sha256:" + "0" * 64),
        ({**FULL_MANIFEST, "images": {}}, DIGEST),
    ],
    ids=["no-manifest", "other-candidate", "other-digest", "tampered-manifest"],
)
def test_unbound_release_blocks_before_any_request(manifest_doc: Any, expected_digest: str) -> None:
    receipt, web, api = run(manifest_doc=manifest_doc, expected_digest=expected_digest)

    assert set(receipt["summary"]["statuses"].values()) == {"BLOCKED"}
    assert all("release:manifest_binding" in blockers(receipt, j) for j in bj.JOURNEY_IDS)
    assert web.calls == [] and api.calls == []


@pytest.mark.parametrize(
    "api",
    [RuntimeApi(sha="c" * 40), RuntimeApi(profile="dev-admin"), RuntimeApi(valid=False), None],
    ids=["served-sha-drift", "served-profile-drift", "served-profile-invalid", "no-api"],
)
def test_runtime_preflight_refusal_proves_zero_writes(api: Any) -> None:
    receipt, web, _ = run(api=api)

    assert set(receipt["summary"]["statuses"].values()) == {"BLOCKED"}
    assert web.calls == []
    for journey_id in bj.JOURNEY_IDS:
        requests = receipt["journeys"][journey_id]["requests"]
        assert requests["business_writes"] == 0
        assert requests["worker_or_provider_triggers"] == 0


def test_preflight_guard_refuses_a_mutation_before_writes_are_armed() -> None:
    ledger = bj.RequestLedger()
    guarded = bj.LedgerHttp(BusinessWeb(), ledger)

    with pytest.raises(bj.PreflightViolation):
        guarded.request("POST", "/api/v1/netplan/scenarios/NP-SCN-31/solve", body={})
    assert ledger.business_writes == 0


# ---------------------------------------------------------------------------
# Missing admission -> BLOCKED with the named dependency, zero writes
# ---------------------------------------------------------------------------


def _zero_writes(receipt: dict[str, Any], web: BusinessWeb, journey_id: str) -> None:
    requests = receipt["journeys"][journey_id]["requests"]
    assert requests["business_writes"] == 0
    assert requests["worker_or_provider_triggers"] == 0
    assert web.business_writes == []


def test_missing_scope_authorization_blocks_every_journey() -> None:
    receipt, web, _ = run(scope=None)

    assert set(receipt["summary"]["statuses"].values()) == {"BLOCKED"}
    assert all("scope:authorization" in blockers(receipt, j) for j in bj.JOURNEY_IDS)
    assert web.calls == []


def test_scope_bound_to_another_release_blocks() -> None:
    scope = scope_document()
    scope["release_sha"] = "c" * 40

    receipt, web, _ = run(scope=scope)
    assert set(receipt["summary"]["statuses"].values()) == {"BLOCKED"}
    assert web.calls == []


def test_missing_business_credential_blocks_with_its_env_name() -> None:
    env = credential_env()
    env.pop("ODP_LIVE_JOURNEY_GROWTH_MARKETER_PASSWORD")

    receipt, web, _ = run(selection=["growth"], environ=env)
    entry = receipt["journeys"]["growth"]
    assert entry["status"] == "BLOCKED"
    assert entry["blockers"][0]["dependency"] == "business-credential"
    assert "ODP_LIVE_JOURNEY_GROWTH_MARKETER_USERNAME" in entry["blockers"][0]["detail"]
    assert web.calls == []
    _zero_writes(receipt, web, "growth")


def test_missing_approver_credential_blocks_on_named_approval() -> None:
    env = credential_env()
    env.pop("ODP_LIVE_JOURNEY_EXPANSION_APPROVER_USERNAME")

    receipt, web, _ = run(selection=["expansion"], environ=env)
    assert receipt["journeys"]["expansion"]["status"] == "BLOCKED"
    assert set(blockers(receipt, "expansion").values()) == {"named-approval"}
    _zero_writes(receipt, web, "expansion")


def test_missing_named_approval_reference_blocks() -> None:
    scopes = journey_scopes()
    scopes["governance"].pop("approval_ref")

    receipt, web, _ = run(
        selection=["governance"], scope=scope_document(governance=scopes["governance"])
    )
    assert receipt["journeys"]["governance"]["status"] == "BLOCKED"
    assert "named-approval" in blockers(receipt, "governance").values()
    _zero_writes(receipt, web, "governance")


def test_missing_policy_probe_blocks_intake_on_source() -> None:
    scopes = journey_scopes()
    scopes["intake"]["writes"].pop("policy_probe")

    receipt, web, _ = run(selection=["intake"], scope=scope_document(intake=scopes["intake"]))
    assert receipt["journeys"]["intake"]["status"] == "BLOCKED"
    assert "source" in blockers(receipt, "intake").values()
    _zero_writes(receipt, web, "intake")


def test_scope_action_outside_the_journey_vocabulary_blocks() -> None:
    scopes = journey_scopes()
    scopes["operations"]["writes"]["transition"]["action"] = "delete"

    receipt, web, _ = run(
        selection=["operations"], scope=scope_document(operations=scopes["operations"])
    )
    assert receipt["journeys"]["operations"]["status"] == "BLOCKED"
    assert "scope-authorization" in blockers(receipt, "operations").values()
    _zero_writes(receipt, web, "operations")


def test_foreign_tenant_must_differ_from_the_authorized_tenant() -> None:
    scopes = journey_scopes()
    scopes["franchise"]["foreign_tenant_id"] = TENANT

    receipt, web, _ = run(
        selection=["franchise"], scope=scope_document(franchise=scopes["franchise"])
    )
    assert receipt["journeys"]["franchise"]["status"] == "BLOCKED"
    _zero_writes(receipt, web, "franchise")


def test_credential_for_an_unauthorized_account_blocks() -> None:
    env = credential_env()
    env["ODP_LIVE_JOURNEY_OPERATIONS_USERNAME"] = "ops.analyst"
    env["ODP_LIVE_JOURNEY_OPERATIONS_PASSWORD"] = password_for("ops.analyst")

    receipt, web, _ = run(selection=["operations"], environ=env)
    assert blockers(receipt, "operations") == {
        "preflight:primary_credential": "scope-authorization"
    }
    assert web.calls == []


def test_platform_admin_is_never_promoted_into_a_business_role() -> None:
    web = BusinessWeb()
    web.admins.add("governance.lead")

    receipt, web, _ = run(web, selection=["governance"])
    assert receipt["journeys"]["governance"]["status"] == "BLOCKED"
    assert blockers(receipt, "governance") == {"actor:primary_authoritative_roles": "business-role"}
    _zero_writes(receipt, web, "governance")


def test_wrong_password_blocks_on_business_credential() -> None:
    env = credential_env()
    env["ODP_LIVE_JOURNEY_FRANCHISE_PASSWORD"] = "wrong-password-value"

    receipt, web, _ = run(selection=["franchise"], environ=env)
    assert blockers(receipt, "franchise") == {
        "session:primary_password_login": "business-credential"
    }
    assert "wrong-password-value" not in json.dumps(receipt)
    _zero_writes(receipt, web, "franchise")


def test_missing_model_provenance_blocks_expansion_on_model() -> None:
    web = BusinessWeb()
    del web.scenario["solver_version"]

    receipt, web, _ = run(web, selection=["expansion"])
    assert receipt["journeys"]["expansion"]["status"] == "BLOCKED"
    assert blockers(receipt, "expansion") == {"read:read_scenario:model_provenance": "model"}
    _zero_writes(receipt, web, "expansion")


def test_unapproved_listing_source_blocks_intake_on_source() -> None:
    web = BusinessWeb()
    web.intake["policy"] = "SOURCE_BLOCKED"

    receipt, web, _ = run(web, selection=["intake"])
    assert receipt["journeys"]["intake"]["status"] == "BLOCKED"
    assert set(blockers(receipt, "intake").values()) == {"source"}
    _zero_writes(receipt, web, "intake")


def test_governance_record_already_decided_blocks_on_business_data() -> None:
    web = BusinessWeb()
    web.governance["approvals"][0]["status"] = "approved"

    receipt, web, _ = run(web, selection=["governance"])
    assert set(blockers(receipt, "governance").values()) == {"business-data"}
    _zero_writes(receipt, web, "governance")


def test_surrogate_record_fails_as_data_binding_before_any_write() -> None:
    web = BusinessWeb()
    web.issue["storeId"] = "seed-store-01"

    receipt, web, _ = run(web, selection=["operations"])
    assert receipt["journeys"]["operations"]["status"] == "FAILED"
    assert blockers(receipt, "operations") == {"read:read_issue:live": "data-binding"}
    _zero_writes(receipt, web, "operations")


# ---------------------------------------------------------------------------
# Live system misbehaves -> FAILED with the named dependency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("journey_id", bj.JOURNEY_IDS)
def test_served_foreign_tenant_record_fails_tenant_isolation(journey_id: str) -> None:
    web = BusinessWeb()
    web.cross_tenant_leak.add(journey_id)

    receipt, web, _ = run(web, selection=[journey_id])
    assert receipt["journeys"][journey_id]["status"] == "FAILED"
    assert blockers(receipt, journey_id) == {"negative:cross_tenant_denied": "tenant-isolation"}
    assert receipt["journeys"][journey_id]["negative_probes"]["cross_tenant_denied"] is False


@pytest.mark.parametrize("journey_id", bj.JOURNEY_IDS)
def test_unauthorized_role_allowed_to_write_fails_authorization(journey_id: str) -> None:
    web = BusinessWeb()
    web.open_rbac.add(journey_id)

    receipt, web, _ = run(web, selection=[journey_id])
    assert receipt["journeys"][journey_id]["status"] == "FAILED"
    assert blockers(receipt, journey_id) == {"negative:wrong_role_denied": "authorization"}


@pytest.mark.parametrize("journey_id", bj.JOURNEY_IDS)
def test_write_that_does_not_survive_readback_fails(journey_id: str) -> None:
    web = BusinessWeb()
    web.non_durable.add(journey_id)

    receipt, _, _ = run(web, selection=[journey_id])
    assert receipt["journeys"][journey_id]["status"] == "FAILED"
    expected = {"expansion": "write:solve", "intake": "readback:intake_decision_outcome"}.get(
        journey_id, "readback:durable_state_change"
    )
    assert expected in blockers(receipt, journey_id)


@pytest.mark.parametrize("journey_id", bj.JOURNEY_IDS)
def test_write_without_durable_audit_event_fails(journey_id: str) -> None:
    web = BusinessWeb()
    web.drop_audit.add(journey_id)

    receipt, _, _ = run(web, selection=[journey_id])
    assert receipt["journeys"][journey_id]["status"] == "FAILED"
    assert set(blockers(receipt, journey_id).values()) == {"audit"}


@pytest.mark.parametrize("status", ["failed", "queued"])
def test_job_that_never_succeeds_fails_on_worker(status: str) -> None:
    web = BusinessWeb()
    web.job_status = status

    receipt, _, _ = run(web, selection=["growth"])
    assert receipt["journeys"]["growth"]["status"] == "FAILED"
    assert blockers(receipt, "growth") == {"job:terminal_succeeded": "worker"}


def test_missing_disclosure_fails_expansion() -> None:
    web = BusinessWeb()
    original = web.handlers

    def strip_disclosure(ctx: Ctx) -> Resp:
        if ctx.match.group(1) != web.scenario["scenario_id"]:
            return Resp(404, {})
        payload = deepcopy(web.scenario)
        for approval in payload.get("approvals", []):
            approval.pop("unmodelled_constraint_classes", None)
        return Resp(200, payload)

    web.handlers = [
        (m, p, strip_disclosure if m == "GET" and "netplan" in p.pattern else h)
        for m, p, h in original
    ]
    receipt, _, _ = run(web, selection=["expansion"])
    assert receipt["journeys"]["expansion"]["status"] == "FAILED"
    assert blockers(receipt, "expansion") == {"disclosure:workspace_payload": "disclosure"}


def test_policy_blocked_source_accepted_with_retrieval_fails_intake() -> None:
    web = BusinessWeb()
    web.policy_open = True

    receipt, _, _ = run(web, selection=["intake"])
    assert receipt["journeys"]["intake"]["status"] == "FAILED"
    assert blockers(receipt, "intake") == {"policy:blocked_source_rejected": "policy"}


def test_durable_quarantine_without_retrieval_satisfies_the_policy_probe() -> None:
    web = BusinessWeb()
    quarantined = {
        "id": "LI-7001",
        "stage": "QUARANTINED",
        "policy": "SOURCE_BLOCKED",
        "rawSnapshot": None,
        "snapshotId": None,
        "parsedFields": {},
    }

    def submit(ctx: Ctx) -> Resp:
        return Resp(200, quarantined)

    def read(ctx: Ctx) -> Resp:
        if ctx.match.group(1) == "LI-7001":
            return Resp(200, quarantined)
        if ctx.match.group(1) == web.intake["id"]:
            return Resp(200, web.intake)
        return Resp(404, {})

    web.handlers = [
        (
            m,
            p,
            submit
            if p.pattern.endswith("intake/submit")
            else read
            if m == "GET" and p.pattern.endswith("intake/([^/]+)")
            else h,
        )
        for m, p, h in web.handlers
    ]
    receipt, _, _ = run(web, selection=["intake"])
    assert receipt["journeys"]["intake"]["status"] == "PASSED", blockers(receipt, "intake")
    assert receipt["journeys"]["intake"]["policy_outcome"]["mode"] == "quarantined"


# ---------------------------------------------------------------------------
# Receipt verification (what the gate trusts)
# ---------------------------------------------------------------------------


def test_receipt_altered_after_sealing_is_rejected() -> None:
    receipt, _, _ = run(selection=["operations"])
    full, _, _ = run()
    full["journeys"]["operations"]["status"] = "PASSED"
    full["summary"]["statuses"]["growth"] = "PASSED"
    full["journeys"]["growth"]["negative_probes"]["cross_tenant_denied"] = False

    assert "business_journeys:receipt_digest" in failing_checks(full)
    assert "business_journeys:complete_selection" in failing_checks(receipt)


def test_receipt_missing_one_journey_never_claims_full_acceptance() -> None:
    receipt, _, _ = run()
    receipt["journeys"].pop("franchise")
    reseal(receipt)

    assert failing_checks(receipt) == {"business_journey:franchise"}


@pytest.mark.parametrize("journey_id", bj.JOURNEY_IDS)
def test_receipt_with_one_non_passing_journey_fails(journey_id: str) -> None:
    web = BusinessWeb()
    web.drop_audit.add(journey_id)
    receipt, _, _ = run(web)

    failing = failing_checks(receipt)
    assert f"business_journey:{journey_id}" in failing
    assert "business_journeys:command_exit" in failing
    assert receipt["command"]["exit_code"] == 1


def test_offline_or_secret_bearing_receipt_is_rejected() -> None:
    receipt, _, _ = run()
    offline = reseal({**deepcopy(receipt), "offline_fixture": True})
    leaky = deepcopy(receipt)
    leaky["journeys"]["operations"]["actor"]["password"] = "<redacted>"
    reseal(leaky)

    assert failing_checks(offline) == {"business_journeys:live_receipt"}
    assert failing_checks(leaky) == {"business_journeys:no_secret_fields"}


def test_receipt_for_another_release_or_manifest_is_rejected() -> None:
    receipt, _, _ = run()

    assert failing_checks(receipt, expected_sha="c" * 40) == {"business_journeys:release_sha"}
    assert failing_checks(receipt, expected_digest="sha256:" + "0" * 64) == {
        "business_journeys:manifest_digest"
    }
    assert "business_journeys:manifest_digest" in failing_checks(receipt, expected_digest="")


def test_hand_written_receipt_claims_are_cross_checked_per_journey() -> None:
    receipt, _, _ = run()
    entry = receipt["journeys"]["operations"]
    entry["audit_refs"] = []
    entry["after"] = deepcopy(entry["before"])
    reseal(receipt)

    problems = bj.journey_receipt_problems(bj.JOURNEYS_BY_ID["operations"], entry)
    assert "record state unchanged" in problems
    assert "audit refs missing for ['transition']" in problems


def test_no_receipt_blocks_all_six_journeys() -> None:
    checks = bj.verify_receipt(
        None, expected_sha=SHA, expected_digest=DIGEST, release_profile="full"
    )

    assert not any(check.ok for check in checks)
    assert {c.name for c in checks} >= {f"business_journey:{j}" for j in bj.JOURNEY_IDS}


# ---------------------------------------------------------------------------
# Gate integration: the receipt feeds the full-acceptance claim
# ---------------------------------------------------------------------------


fixtures = _load("live_e2e_gate_fixtures_for_journeys", GATE_TESTS)


def gate_report(receipt: Any, **config: Any) -> dict[str, Any]:
    clock = fixtures.FakeClock()
    values = {"expected_manifest_digest": DIGEST, **config}
    routes = fixtures.live_routes()
    if values.get("release_profile") == "dev-admin":
        values.setdefault("expected_deployment", "dev")
    _, report = fixtures.gate.evaluate_gate(
        fixtures.config(**values),
        http=fixtures.FakeHttp(routes),
        worker_driver=fixtures.FakeWorkerDriver(),
        correlation_id=fixtures.CORRELATION_ID,
        now=fixtures.NOW,
        web_http=fixtures.passing_web_http(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        business_journey_receipt=receipt,
    )
    return report


def test_gate_claims_full_acceptance_only_with_a_verified_six_journey_receipt() -> None:
    receipt, _, _ = run()
    report = gate_report(receipt)

    assert report["ok"] is True, report["blockers"]
    assert report["full_acceptance"]["status"] == "PASSED", report["full_acceptance"]["blockers"]
    assert report["full_acceptance"]["journeys"] == {j: "PASSED" for j in bj.JOURNEY_IDS}
    assert report["release_profile"]["full_product_acceptance_claimed"] is True


def test_gate_without_receipt_keeps_runtime_ok_but_claims_no_full_acceptance() -> None:
    report = gate_report(None)

    assert report["ok"] is True
    assert report["release_profile"]["full_product_acceptance_claimed"] is False
    full = report["full_acceptance"]
    assert full["status"] == "BLOCKED"
    assert full["journeys"] == {j: "BLOCKED" for j in bj.JOURNEY_IDS}
    assert "business-journey" in full["blocking_dependencies"]
    assert all(b["next_action"] for b in full["blockers"])


def test_gate_rejects_a_receipt_for_another_manifest_digest() -> None:
    receipt, _, _ = run()
    report = gate_report(receipt, expected_manifest_digest="sha256:" + "0" * 64)

    assert report["release_profile"]["full_product_acceptance_claimed"] is False
    assert "business_journeys:manifest_digest" in {
        b["check"] for b in report["full_acceptance"]["blockers"]
    }


def test_gate_runtime_failure_withholds_full_acceptance_even_with_a_receipt() -> None:
    receipt, _, _ = run()
    report = gate_report(receipt, expected_sha="c" * 40)

    assert report["ok"] is False
    assert report["full_acceptance"]["status"] == "BLOCKED"
    assert report["release_profile"]["full_product_acceptance_claimed"] is False


def test_gate_full_acceptance_flag_controls_the_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    receipt, _, _ = run()
    claimed = gate_report(receipt)
    unclaimed = gate_report(None)

    assert fixtures.gate._report_full_acceptance(claimed, require=True) == 0
    assert fixtures.gate._report_full_acceptance(unclaimed, require=True) == 1
    assert fixtures.gate._report_full_acceptance(unclaimed, require=False) == 0
    assert "Full product acceptance: BLOCKED" in capsys.readouterr().out


def test_gate_receipt_loader_fails_closed(tmp_path: Path) -> None:
    broken = tmp_path / "receipt.json"
    broken.write_text("{not json", encoding="utf-8")

    assert fixtures.gate.load_business_journey_receipt(None)[0] is None
    assert fixtures.gate.load_business_journey_receipt(tmp_path / "absent.json")[0] is None
    payload, note = fixtures.gate.load_business_journey_receipt(broken)
    assert payload is None and "unreadable" in note


# ---------------------------------------------------------------------------
# CLI: fail closed without network when nothing is admitted
# ---------------------------------------------------------------------------


class _NoNetworkGate:
    class UrllibHttpClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def request(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the runner reached the network while not admitted")

    @staticmethod
    def _normalize_origin(url: str, *, allow_http: bool = False) -> str:
        return url.rstrip("/")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in list(credential_env()) + [
        bj.RELEASE_MANIFEST_ENV,
        bj.MANIFEST_DIGEST_ENV,
        bj.SCOPE_ENV,
        bj.RELEASE_PROFILE_ENV,
        bj.API_URL_ENV,
        bj.WEB_URL_ENV,
        bj.EXPECTED_SHA_ENV,
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bj, "_load_gate_module", lambda: _NoNetworkGate)
    return monkeypatch


def test_cli_without_inputs_writes_a_blocked_receipt(clean_env: Any, tmp_path: Path) -> None:
    output = tmp_path / "receipt.json"

    assert bj.main(["--output", str(output)]) == 1
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert set(receipt["summary"]["statuses"].values()) == {"BLOCKED"}
    assert receipt["command"]["exit_code"] == 1


def test_cli_dev_admin_manifest_with_caller_full_is_not_admitted(
    clean_env: Any, tmp_path: Path
) -> None:
    dev_manifest = manifest("dev-admin")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(dev_manifest), encoding="utf-8")
    output = tmp_path / "receipt.json"
    clean_env.setenv(bj.RELEASE_PROFILE_ENV, "full")

    code = bj.main(
        [
            "--release-manifest",
            str(manifest_path),
            "--expected-sha",
            SHA,
            "--expected-manifest-digest",
            dev_manifest["manifest_digest"],
            "--web-url",
            WEB_ORIGIN,
            "--api-url",
            "https://oday-api.dev.alfloop.internal",
            "--output",
            str(output),
        ]
    )
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert code == 1
    assert receipt["summary"]["status"] == "NOT_ADMITTED"
    assert receipt["release"]["caller_profile_override_refused"] is True


# ---------------------------------------------------------------------------
# Anti-drift: mirrors and routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["full", "dev-admin"])
def test_manifest_mirrors_match_the_release_toolchain(profile: str) -> None:
    document = manifest(profile)

    assert bj.compute_manifest_digest(document) == release_manifest.compute_manifest_digest(
        document
    )
    assert bj.sealed_release_profile(document) == release_manifest.manifest_release_profile(
        document
    )


def test_journey_specs_are_business_journeys() -> None:
    assert bj.JOURNEY_IDS == (
        "operations",
        "growth",
        "expansion",
        "governance",
        "franchise",
        "intake",
    )
    for spec in bj.JOURNEYS:
        primary = spec.actor_roles["primary"]
        assert "platform_admin" not in set().union(*spec.actor_roles.values())
        assert primary.isdisjoint(spec.actor_roles["denied"]), spec.journey_id
        assert spec.writes and spec.cross_tenant and spec.readback
        assert set(spec.actors) == set(ACTORS[spec.journey_id])


def _route_pattern(path: str) -> re.Pattern[str]:
    return re.compile(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path)))


def _iter_method_routes(router: Any, prefix: str = "") -> Any:
    """Mirror of ``shared.api.versioning._iter_router_paths`` keeping methods.

    FastAPI wraps an included router in ``_IncludedRouter`` (no own ``path``,
    the real router on ``original_router``, the prefix on ``include_context``).
    """

    for route in getattr(router, "routes", []):
        path = getattr(route, "path", "")
        if path:
            for method in getattr(route, "methods", None) or ():
                yield method, f"{prefix}{path}"
            continue
        nested = getattr(route, "original_router", None)
        if nested is None:
            continue
        context = getattr(route, "include_context", None)
        yield from _iter_method_routes(nested, f"{prefix}{getattr(context, 'prefix', '') or ''}")


def test_every_journey_path_is_routed_by_the_deployed_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """A renamed route would surface as a business failure instead of drift."""

    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    from apps.api.oday_api.main import create_app
    from shared.infrastructure.persistence.factory import _memory_bundle

    app = create_app(persistence=_memory_bundle())
    served = [(method, _route_pattern(path)) for method, path in _iter_method_routes(app.router)]
    assert len(served) > 100, "route walker no longer reaches the included routers"

    def routed(method: str, path: str) -> bool:
        concrete = re.sub(r"\{[^}]+\}", "X-1", urllib.parse.urlsplit(path).path)
        return any(m == method and p.fullmatch(concrete) for m, p in served)

    def expand(spec: Any, template: str) -> list[str]:
        """Every action the scope may authorize must hit a served route."""

        match = re.search(r"\{write\.([a-z_]+)\.action\}", template)
        if match is None:
            return [template]
        return [
            template.replace(match.group(0), action)
            for action in sorted(spec.writes_allowed[match.group(1)])
        ]

    steps: list[tuple[str, str]] = [
        ("GET", bj.USER_ADMIN_PROBE_PATH),
        ("GET", bj.PRINCIPAL_PATH),
        ("GET", bj.AUDIT_EVENTS_PATH),
    ]
    for spec in bj.JOURNEYS:
        for step in (spec.read, *spec.writes, spec.readback, spec.cross_tenant, *spec.extra_reads):
            steps.extend((step.method, path) for path in expand(spec, step.path))
        if spec.job is not None:
            steps.append(("GET", spec.job.path))
        if spec.policy is not None:
            steps.append((spec.policy.submit.method, spec.policy.submit.path))
            steps.append((spec.policy.absence.method, spec.policy.absence.path))
            steps.append(("GET", spec.policy.readback_path))
    missing = [f"{method} {path}" for method, path in steps if not routed(method, path)]
    assert missing == []


# Review R9-R14: offline contract/dependency proofs, never live acceptance.
def test_release_identity_has_real_versioned_alias_without_provider_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from shared.api.versioning import alias_paths, versioned_paths
    from shared.infrastructure.persistence.factory import _memory_bundle

    monkeypatch.setenv("ODAY_RELEASE_SHA", SHA)
    monkeypatch.setenv("ODP_RELEASE_PROFILE", "full")
    monkeypatch.setenv("ODP_RELEASE_MANIFEST_DIGEST", DIGEST)
    probes: list[Any] = []

    def offline_probe(**kwargs: Any) -> Any:
        probes.append(kwargs)
        raise AssertionError("release identity must not probe any provider")

    app = create_app(
        persistence=_memory_bundle(),
        external_provider_validation=lambda: None,
        external_provider_connectivity_probe=offline_probe,
    )
    assert alias_paths(app) == [path[len("/api/v1"):] for path in versioned_paths(app)]
    client = TestClient(app)
    headers = {"x-correlation-id": "offline-release-identity-alias"}
    versioned = client.get(bj.RELEASE_IDENTITY_PATH, headers=headers)
    alias = client.get("/platform/release-identity", headers=headers)
    assert versioned.status_code == alias.status_code == 200
    # The release payload includes a per-request clock, not deployment identity.
    versioned_body, alias_body = versioned.json(), alias.json()
    assert isinstance(versioned_body.pop("time"), str)
    assert isinstance(alias_body.pop("time"), str)
    assert versioned_body == alias_body
    assert versioned.json()["release_sha"] == SHA
    assert versioned.json()["manifest_digest"] == DIGEST
    assert "Deprecation" not in versioned.headers
    assert alias.headers["Deprecation"] == "true"
    assert alias.headers["Link"] == f'<{bj.RELEASE_IDENTITY_PATH}>; rel="successor-version"'
    assert "/platform/release-identity" not in app.openapi()["paths"]
    assert probes == []


@pytest.mark.parametrize("missing_scope", [False, True])
def test_actual_identity_preflight_never_dispatches_provider_on_refusal(
    monkeypatch: pytest.MonkeyPatch, missing_scope: bool,
) -> None:
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from shared.infrastructure.persistence.factory import _memory_bundle

    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    monkeypatch.setenv("ODAY_RELEASE_SHA", "c" * 40)
    monkeypatch.setenv("ODP_RELEASE_PROFILE", "full")
    monkeypatch.setenv("ODP_RELEASE_MANIFEST_DIGEST", DIGEST)
    probes: list[Any] = []

    def offline_probe(**kwargs: Any) -> Any:
        probes.append(kwargs)
        raise AssertionError("offline dispatch sentinel, no provider/network")

    client = TestClient(create_app(
        persistence=_memory_bundle(),
        external_provider_validation=SimpleNamespace(mode="live", ok=True, errors=()),
        external_provider_connectivity_probe=offline_probe,
    ))

    class LocalApi:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def request(self, method: str, path: str, **kwargs: Any) -> Resp:
            self.calls.append(path)
            assert path == bj.RELEASE_IDENTITY_PATH
            response = client.request(method, path)
            return Resp(response.status_code, response.json())

    api = LocalApi()
    receipt, web, _ = run(api=api, scope=None if missing_scope else scope_document(), selection=["operations"])
    assert receipt["journeys"]["operations"]["status"] == "BLOCKED"
    assert api.calls == ([] if missing_scope else [bj.RELEASE_IDENTITY_PATH])
    assert probes == []
    _zero_writes(receipt, web, "operations")
    # Calibration: this exact deployed handler really would dispatch a provider
    # through the injected offline sentinel if the old readiness URL were used.
    client.get("/readiness")
    assert len(probes) == 1


@pytest.mark.parametrize("field,value", [
    ("release_sha", "c" * 40), ("release_profile", "dev-admin"),
    ("release_profile_valid", False), ("manifest_digest", "sha256:" + "d" * 64),
    ("web_release_sha", "c" * 40), ("web_release_profile", "dev-admin"),
    ("web_manifest_digest", ""),
])
def test_actual_bff_identity_drift_blocks_before_business_effects(field: str, value: Any) -> None:
    web = BusinessWeb()
    web.release_identity[field] = value
    receipt, _, _ = run(web, selection=["operations"])
    assert receipt["journeys"]["operations"]["status"] == "BLOCKED"
    assert any(path == bj.RELEASE_IDENTITY_PATH for _, path, _ in web.calls)
    _zero_writes(receipt, web, "operations")


def test_priceops_per_action_audit_matches_real_route_contract() -> None:
    from apps.api.app.routes.priceops import create_priceops_router

    router = create_priceops_router()
    endpoints = {route.path: route.endpoint for route in router.routes}
    assert set(bj.PRICEOPS_AUDIT_EVENTS) == set(bj.JOURNEYS_BY_ID["growth"].writes_allowed["price_action"])
    for action, event in bj.PRICEOPS_AUDIT_EVENTS.items():
        # Compiled, shipped endpoint contract; not strings invented by BusinessWeb.
        assert event in endpoints[f"/priceops/plans/{{plan_id}}/{action}"].__code__.co_consts


@pytest.mark.parametrize("mismatch", ["foreign-scenario", "multiple-scenarios", "wrong-store", "already-solved"])
def test_operator_solve_scope_mismatch_is_refused_before_trigger(mismatch: str) -> None:
    web = BusinessWeb()
    if mismatch == "foreign-scenario":
        web.rebalance["canonicalNetPlanScenarioIds"] = ["FOREIGN-SCENARIO"]
    elif mismatch == "multiple-scenarios":
        web.rebalance["canonicalNetPlanScenarioIds"].append("SECOND-SCENARIO")
    elif mismatch == "wrong-store":
        web.scenario["options_by_entity"] = {"OTHER-STORE": ["C-1"]}
    else:
        web.scenario["status"] = "solved"
    receipt, _, _ = run(web, selection=["expansion"])
    assert receipt["journeys"]["expansion"]["status"] == "BLOCKED"
    _zero_writes(receipt, web, "expansion")


def test_stale_operator_projection_never_proceeds_to_ui_or_approval() -> None:
    web = BusinessWeb()
    web.rebalance["netPlanJob"] = {"id": web.scenario["scenario_id"], "completedAt": NOW}
    ui = OfflineUi()
    receipt, _, _ = run(web, selection=["expansion"], ui=ui)
    assert blockers(receipt, "expansion") == {"write:solve:fresh_projection": "disclosure"}
    assert not ui.calls
    assert not any(path.endswith(("/submit", "/decide")) for _, path, _ in web.business_writes)


def test_real_operator_solve_projects_this_canonical_solve_over_http(tmp_path: Path) -> None:
    helpers = _load("journey_projection_contract", ROOT / "tests/integration/test_netplan_disclosure_ui_e2e.py")
    scenario, repo, _, operator, engine = helpers._canonical_surface(
        "OFFLINE-CANONICAL-SOLVE", helpers._fully_modelled_constraints(),
        database_path=tmp_path / "offline-netplan.sqlite3",
    )
    client = helpers._mount_operator_api(operator)  # Offline router/schema/CP-SAT, not authorization evidence.
    before = client.get("/api/v1/operator/network-rebalance").json()
    store_id = next(iter(scenario.options_by_entity))
    response = client.post(
        f"/api/v1/operator/network-rebalance/stores/{store_id}/netplan/solve",
        headers={"Idempotency-Key": "offline-fresh-projection", "X-Correlation-Id": "offline-solve-corr"},
        json={"actorRoleId": "expansionManager", "actorName": "offline-only"},
    )
    assert response.status_code == 200, response.text
    store = response.json()["store"]
    solve = repo.get_solve(scenario.scenario_id)
    assert store["netPlanJob"]["id"] == scenario.scenario_id
    assert store["netPlanJob"]["completedAt"] == solve.solved_at.isoformat()
    assert store["selectedScenarioId"] is None  # Cards/disclosure render without a selection write.
    assert any(row["id"] == scenario.scenario_id for row in store["netPlanScenarios"])
    after = client.get("/api/v1/operator/network-rebalance").json()
    assert bj.dig(before, f"stores[id={store_id}].netPlanJob.completedAt") is None
    assert bj.dig(after, f"stores[id={store_id}].netPlanJob.completedAt") == store["netPlanJob"]["completedAt"]
    engine.close()


def test_missing_chromium_launch_is_blocked_before_solver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    for name in ("business-ui.live.ts", "playwright.live.config.ts"):
        path = tmp_path / "tests/e2e/live" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// offline tooling fixture\n")
    (tmp_path / "node_modules/@playwright/test").mkdir(parents=True)
    children: list[Any] = []
    monkeypatch.setenv("ODP_API_INVOKER_TOKEN", "must-not-reach-browser-probe")

    def unavailable_browser(command: Any, **kwargs: Any) -> Any:
        children.append((command, kwargs))
        return SimpleNamespace(returncode=1, stdout="", stderr="offline missing browser")

    driver = bj.PlaywrightUiDriver(root=tmp_path, runner=unavailable_browser, which=lambda name: f"/offline/{name}")
    receipt, web, _ = run(selection=["expansion"], ui=driver)
    assert receipt["journeys"]["expansion"]["status"] == "BLOCKED"
    _zero_writes(receipt, web, "expansion")
    assert len(children) == 1 and "chromium.launch" in children[0][0][-1]
    assert "ODP_API_INVOKER_TOKEN" not in children[0][1]["env"]


def test_ready_intake_creates_tenant_bound_listing_surviving_repository_restart(tmp_path: Path) -> None:
    from modules.opsboard.application.network_listings import NetworkListingService
    from shared.infrastructure.persistence.factory import _durable_bundle

    path = tmp_path / "offline-intake.sqlite3"
    bundle = _durable_bundle(path)
    repository = bundle.operator_intake_repository
    repository.save_intake({
        "id": "OFFLINE-DURABLE-INTAKE", "sourceId": "OFFLINE-SOURCE", "tenantId": TENANT,
        "heatZoneId": "HZ-01", "originalUrl": "https://example.invalid/offline",
        "stage": "READY", "version": 3, "parsedFields": {}, "auditEvents": [],
        "matchResult": {"targetListingId": "L-2024"},
    })
    service = NetworkListingService(bundle.listing_repository, repository, tenant_id=TENANT)
    before = service.get_intake("OFFLINE-DURABLE-INTAKE")
    prior_ids = {row["id"] for row in service.snapshot(tenant_id=TENANT)["listings"]}
    result = service.decide_intake(
        intake_id=before["id"], action="create", reason="Offline durable creation test",
        risk_summary="Offline fixture, no retrieval", risk_acknowledged=True,
        actor_role_id="expansionManager", actor_name="offline-only",
        idempotency_key="offline-create", correlation_id="offline-create-corr",
    )
    assert result["stage"] == before["stage"] == "READY"
    assert result["version"] == before["version"] == 3
    target_id = result["matchResult"]["targetListingId"]
    assert target_id not in prior_ids
    bundle.engine.close()
    restarted = _durable_bundle(path)
    rebuilt = NetworkListingService(restarted.listing_repository, restarted.operator_intake_repository,
                                    seed_fixtures=False, tenant_id=TENANT)
    saved = rebuilt.get_intake(before["id"])
    row = next(row for row in rebuilt.snapshot(tenant_id=TENANT)["listings"] if row["id"] == target_id)
    assert row["tenantId"] == TENANT and row["sourceId"] == before["sourceId"]
    assert saved["matchResult"]["targetListingId"] == target_id
    assert saved["auditEvents"][-1]["metadata"]["decision"] == "create"
    assert saved["auditEvents"][-1]["correlationId"] == "offline-create-corr"
    assert rebuilt.snapshot(tenant_id="22222222-2222-4222-8222-222222222222")["listings"] == []
    restarted.engine.close()


def test_receipt_without_fresh_solve_capture_is_refused_without_exception() -> None:
    receipt, _, _ = run()
    receipt["journeys"]["expansion"].pop("captured")
    assert "business_journey:expansion" in failing_checks(reseal(receipt))


# Review R1-R8: offline regressions, never live acceptance.
@pytest.mark.parametrize("journey_id", bj.JOURNEY_IDS)
def test_misbound_tenant_is_refused_before_any_business_write(journey_id: str) -> None:
    scope = scope_document()
    scope["journeys"][journey_id]["tenant_id"] = "22222222-2222-4222-8222-222222222222"
    receipt, web, _ = run(scope=scope, selection=[journey_id])
    assert receipt["journeys"][journey_id]["status"] == "BLOCKED"
    assert not web.business_writes
    assert receipt["journeys"][journey_id]["requests"]["worker_or_provider_triggers"] == 0


@pytest.mark.parametrize("journey_id", bj.JOURNEY_IDS)
def test_read_of_another_record_is_refused_before_writes(journey_id: str) -> None:
    web = BusinessWeb()
    spec = bj.JOURNEYS_BY_ID[journey_id]
    scope = scope_document()
    scope["journeys"][journey_id]["records"][spec.record_key] = "WRONG-RECORD"
    web.cross_tenant_leak.add(journey_id)
    receipt, _, _ = run(web, scope=scope, selection=[journey_id])
    assert receipt["journeys"][journey_id]["status"] == "BLOCKED"
    assert not web.business_writes


@pytest.mark.parametrize("fault", ["missing-grant", "foreign-account"])
def test_served_permission_proof_is_required_before_arming_writes(fault: str) -> None:
    web = BusinessWeb()
    original = web.request

    def tamper(method: str, path: str, **kwargs: Any) -> Resp:
        response = original(method, path, **kwargs)
        if path.startswith(bj.AUDIT_EVENTS_PATH):
            if fault == "missing-grant":
                response.payload["events"] = []
            else:
                for event in response.payload["events"]:
                    event["actor"] = account_id("other.account")
        return response

    web.request = tamper
    receipt, _, _ = run(web, selection=["operations"])
    assert receipt["journeys"]["operations"]["status"] == "BLOCKED"
    assert not web.business_writes


@pytest.mark.parametrize("body", [{}, {"campaigns": []}, {"campaigns": [{"campaign_id": "OTHER"}]}])
def test_empty_or_unauthorized_adlift_campaign_never_triggers_worker(body: dict[str, Any]) -> None:
    scope = scope_document()
    scope["journeys"]["growth"]["writes"]["adlift_job"]["body"] = body
    receipt, web, _ = run(scope=scope, selection=["growth"])
    assert receipt["journeys"]["growth"]["status"] == "BLOCKED"
    assert not web.business_writes


def test_null_initial_adlift_report_blocks_before_writes() -> None:
    web = BusinessWeb()
    web.report = {}
    receipt, _, _ = run(web, selection=["growth"])
    assert receipt["journeys"]["growth"]["status"] == "BLOCKED"
    assert not web.business_writes


@pytest.mark.parametrize(
    "fault", ["empty-result", "wrong-campaign", "empty-report", "stale-report"]
)
def test_succeeded_empty_adlift_job_is_not_business_acceptance(fault: str) -> None:
    web = BusinessWeb()
    original = web.request

    def tamper(method: str, path: str, **kwargs: Any) -> Resp:
        response = original(method, path, **kwargs)
        if method == "GET" and "incrementality-jobs/" in path:
            if fault == "empty-result":
                response.payload["reports"] = []
            elif fault == "wrong-campaign":
                response.payload["reports"][0]["campaign_id"] = "OTHER"
        if method == "GET" and "/adlift/reports/" in path and web.business_writes:
            if fault == "empty-report":
                response.payload = {}
            elif fault == "stale-report":
                response.payload["report_id"] = "ALR-76"
        return response

    web.request = tamper
    receipt, _, _ = run(web, selection=["growth"])
    assert receipt["journeys"]["growth"]["status"] == "FAILED"


def test_expansion_ui_runs_after_solve_before_submit_and_approval() -> None:
    web = BusinessWeb()

    class OrderedUi(OfflineUi):
        def run(self, **kwargs: Any) -> Any:
            assert web.scenario["status"] == "solved"
            assert not web.writes_to("/submit") and not web.writes_to("/decide")
            assert any(
                s["selector"] == "scenario-disclosure-NP-SCN-31" for s in kwargs["assertions"]
            )
            return super().run(**kwargs)

    ui = OrderedUi()
    receipt, _, _ = run(web, selection=["expansion"], ui=ui)
    assert receipt["journeys"]["expansion"]["status"] == "PASSED"
    assert len(ui.calls) == 1


def test_missing_live_browser_blocks_expansion_before_any_write() -> None:
    receipt, web, _ = run(selection=["expansion"], ui=None)
    assert receipt["journeys"]["expansion"]["status"] == "BLOCKED"
    assert not web.business_writes


def test_failed_disclosure_prevents_submit_and_approval() -> None:
    class FailedUi(OfflineUi):
        def run(self, **kwargs: Any) -> Any:
            return bj.UiOutcome("FAILED", ("npx", "playwright", "test"), 1)

    receipt, web, _ = run(selection=["expansion"], ui=FailedUi())
    assert receipt["journeys"]["expansion"]["status"] == "FAILED"
    assert not web.writes_to("/submit") and not web.writes_to("/decide")


@pytest.mark.parametrize("journey_id", bj.JOURNEY_IDS)
@pytest.mark.parametrize("fault", ["identity", "correlation", "grant"])
def test_resealed_incomplete_evidence_cannot_pass(journey_id: str, fault: str) -> None:
    receipt, _, _ = run()
    entry = receipt["journeys"][journey_id]
    if fault == "identity":
        entry["before"].pop("record_id")
        entry["after"].pop("record_id")
    elif fault == "correlation":
        entry["correlation_ids"] = {}
        for ref in entry["audit_refs"]:
            ref.pop("correlation_id")
    else:
        entry["actor"]["verified_grants"] = {}
    assert f"business_journey:{journey_id}" in failing_checks(reseal(receipt))


def test_resealed_receipt_without_ui_is_rejected() -> None:
    receipt, _, _ = run()
    receipt["journeys"]["expansion"]["ui_checks"] = []
    assert "business_journey:expansion" in failing_checks(reseal(receipt))


@pytest.mark.parametrize("action", ["approve", "return", "reject"])
def test_governance_vocabulary_matches_actual_service(action: str) -> None:
    from apps.api.app.routes.operator_modules.governance import DecisionPayload
    from modules.opsboard.application.governance import GovernanceService

    service = GovernanceService()  # explicit OFFLINE seed; not live evidence
    approval_id = service.snapshot()["approvals"][0]["id"]
    body = DecisionPayload(
        approvalId=approval_id, action=action, reason="Offline regression justification"
    )
    response = service.decide(
        approval_id=body.approvalId,
        action=body.action,
        reason=body.reason,
        correlation_id="offline-corr",
    )
    assert response["action"] == action and response["decision"]["id"]
    assert service.snapshot()["auditRows"][0]["correlationId"] == f"corr-{approval_id}"
    assert action in bj.JOURNEYS_BY_ID["governance"].writes_allowed["decision"]


@pytest.mark.parametrize("action", ["create", "revise", "duplicate", "quarantine", "reject"])
def test_intake_actual_action_vocabulary_accepts_authorized_scope(action: str) -> None:
    from modules.opsboard.application.network_listings import (
        InMemoryAssistedIntakeRepository,
        NetworkListingService,
    )

    # Actual service with explicitly offline input/repository, no retrieval.
    repository = InMemoryAssistedIntakeRepository()
    repository.save_intake(
        {
            "id": "OFFLINE-INTAKE",
            "sourceId": "OFFLINE-SOURCE",
            "tenantId": TENANT,
            "heatZoneId": "HZ-01",
            "originalUrl": "https://example.invalid/offline",
            "stage": "READY",
            "parsedFields": {},
            "auditEvents": [],
            "matchResult": {"targetListingId": "L-2024"},
        }
    )
    service = NetworkListingService(intake_repository=repository, tenant_id=TENANT)
    result = service.decide_intake(
        intake_id="OFFLINE-INTAKE",
        action=action,
        reason="Offline decision test",
        risk_summary="Offline risk disclosure",
        risk_acknowledged=True,
        actor_role_id="expansionManager",
        actor_name="offline-actor",
        idempotency_key=f"offline-{action}",
        correlation_id="offline-intake-corr",
    )
    assert result["auditEvents"][-1]["action"] == f"intake.decide.{action}"
    assert (
        service.get_intake("OFFLINE-INTAKE")["auditEvents"][-1]["correlationId"]
        == "offline-intake-corr"
    )
    scope = scope_document()
    scope["journeys"]["intake"]["writes"]["decide"]["action"] = action
    scope["journeys"]["intake"]["writes"]["decide"]["body"]["action"] = action
    if action in {"revise", "duplicate"}:
        scope["journeys"]["intake"]["records"]["target_listing_id"] = "L-2024"
        scope["journeys"]["intake"]["writes"]["decide"]["body"]["targetListingId"] = "L-2024"
    receipt, _, _ = run(selection=["intake"], scope=scope)
    assert receipt["journeys"]["intake"]["status"] == "PASSED"


def test_cli_manifest_digest_reaches_full_acceptance_verification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipt, _, _ = run()
    gate = fixtures.gate
    captured = {}

    def evaluate(config: Any, **kwargs: Any) -> Any:
        captured["digest"] = config.expected_manifest_digest
        failed = [
            c
            for c in bj.verify_receipt(
                receipt,
                expected_sha=config.expected_sha,
                expected_digest=config.expected_manifest_digest,
                release_profile=config.release_profile,
            )
            if not c.ok
        ]
        return [], {
            "ok": True,
            "blockers": [],
            "blocking_dependencies": [],
            "full_acceptance": {"status": "BLOCKED" if failed else "PASSED", "blockers": []},
            "release_profile": {"full_product_acceptance_claimed": not failed},
        }

    monkeypatch.setattr(gate, "evaluate_gate", evaluate)
    assert (
        gate.main(
            [
                "--api-url",
                "https://api.example.invalid",
                "--expected-sha",
                SHA,
                "--release-profile",
                "full",
                "--expected-manifest-digest",
                DIGEST,
                "--require-full-acceptance",
                "--output",
                str(tmp_path / "gate.json"),
            ]
        )
        == 0
    )
    assert captured["digest"] == DIGEST


@pytest.mark.parametrize("journey_id", bj.JOURNEY_IDS)
def test_wrong_actual_business_role_is_refused_with_zero_writes(journey_id: str) -> None:
    web = BusinessWeb()
    web.account_roles[ACTORS[journey_id]["primary"]] = frozenset({"data_owner"})
    receipt, _, _ = run(web, selection=[journey_id])
    assert receipt["journeys"][journey_id]["status"] == "BLOCKED"
    assert not web.business_writes


def test_principal_surface_returns_only_verified_self_context_and_refuses_spoofed_production_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from apps.api.oday_api.security.dependencies import reset_default_boundary
    from shared.infrastructure.persistence.factory import _memory_bundle

    # OFFLINE auth adapter regression. Construct a local app so readiness does
    # not mask the authentication layer being tested; no live business calls.
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    monkeypatch.delenv("ODP_PRODUCT_MODE", raising=False)
    for key in ("ODP_AUTH_ISSUER", "ODP_AUTH_AUDIENCES", "ODP_AUTH_HS256_KEYS"):
        monkeypatch.delenv(key, raising=False)
    reset_default_boundary()
    client = TestClient(create_app(persistence=_memory_bundle()))
    headers = {
        "x-subject-id": account_id("ops.manager"),
        "x-roles": "operations_manager",
        "x-tenant-id": TENANT,
    }
    response = client.get(bj.PRINCIPAL_PATH, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "account_id": account_id("ops.manager"),
        "tenant_id": TENANT,
        "roles": ["operations_manager"],
    }
    assert client.get(bj.PRINCIPAL_PATH).status_code == 401
    monkeypatch.setenv("ODP_PRODUCT_MODE", "production")
    assert client.get(bj.PRINCIPAL_PATH, headers=headers).status_code == 401
    reset_default_boundary()


def test_browser_driver_records_real_exit_without_exporting_unrelated_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    calls = []

    def child(command: Any, **kwargs: Any) -> Any:
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=1, stdout="sensitive browser output", stderr="sensitive diagnostics"
        )

    monkeypatch.setenv("ODP_API_INVOKER_TOKEN", "unrelated-secret")
    monkeypatch.setenv("ODP_LIVE_JOURNEY_GROWTH_PASSWORD", "other-password")
    driver = bj.PlaywrightUiDriver(root=tmp_path, runner=child, which=lambda name: "/usr/bin/npx")
    assert driver.available()[0] is False
    outcome = driver.run(
        check="netplan_disclosure",
        web_origin=WEB_ORIGIN,
        path="/operator?ws=network&tab=rebalance",
        credential=bj.Credential("ops.manager", "private-password"),
        assertions=[{"action": "visible", "selector": "scenario-disclosure-NP-SCN-31", "text": ""}],
    )
    assert outcome.status == "FAILED" and outcome.exit_code == 1
    assert outcome.command == bj.LIVE_UI_COMMAND
    assert "sensitive" not in outcome.detail
    assert calls[0][1]["env"]["ODP_LIVE_UI_PASSWORD"] == "private-password"
    assert "ODP_API_INVOKER_TOKEN" not in calls[0][1]["env"]
    assert "ODP_LIVE_JOURNEY_GROWTH_PASSWORD" not in calls[0][1]["env"]
    assert "private-password" not in str(calls[0][0])


def test_self_principal_uses_identity_store_roles_not_token_or_browser_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from apps.api.oday_api.security import dependencies
    from modules.opsboard.auth import AuthBoundaryConfig, AuthenticationBoundary
    from shared.auth import Role, Scope
    from shared.identity import Account, RevocationReason
    from shared.infrastructure.persistence.factory import _memory_bundle

    helpers = _load(
        "journey_auth_contract_helpers", ROOT / "tests/contract/test_api_trust_contract.py"
    )
    bundle = _memory_bundle()
    account = UUID(account_id("ops.manager"))
    bundle.identity_store.save_account(
        Account(
            account_id=account,
            tenant_id=UUID(TENANT),
            username="ops.manager",
            email="offline@example.invalid",
            status="active",
        )
    )
    bundle.identity_store.set_account_roles(account, [Role.OPERATIONS_MANAGER])
    bundle.identity_store.set_account_scope(account, Scope(tenant_id=TENANT))
    session = bundle.session_service.create_session(account_id=account, provider="local_password")
    token = helpers.make_local_jwt(
        sub=str(account),
        sid=str(session.session_id),
        tenant_id=TENANT,
        extra_claims={"roles": ["platform_admin"]},
    )
    boundary = AuthenticationBoundary(
        AuthBoundaryConfig(
            audiences=frozenset({helpers.AUDIENCE}),
            local_issuer=helpers.LOCAL_ISSUER,
            local_signing_keys={"local-k1": helpers.LOCAL_KEY},
            local_audiences=frozenset({helpers.AUDIENCE}),
            identity_store=bundle.identity_store,
            session_service=bundle.session_service,
        )
    )
    monkeypatch.setenv("NODE_ENV", "test")
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    monkeypatch.delenv("ODP_PRODUCT_MODE", raising=False)
    client = TestClient(create_app(persistence=bundle))
    monkeypatch.setattr(dependencies, "default_boundary", lambda: boundary)
    monkeypatch.setenv("ODP_PRODUCT_MODE", "production")
    headers = {
        "authorization": f"Bearer {token}",
        "x-roles": "platform_admin",
        "x-tenant-id": FOREIGN_TENANT,
    }
    response = client.get(bj.PRINCIPAL_PATH, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "account_id": str(account),
        "tenant_id": TENANT,
        "roles": ["operations_manager"],
    }
    bundle.identity_store.set_account_roles(account, [Role.DATA_OWNER])
    assert client.get(bj.PRINCIPAL_PATH, headers=headers).json()["roles"] == ["data_owner"]
    bundle.session_service.revoke_session(session.session_id, RevocationReason.ADMIN_REVOKE)
    assert client.get(bj.PRINCIPAL_PATH, headers=headers).status_code == 401


# R15-R17: bound actor/write outcomes, with explicit offline real-service proofs.
@pytest.mark.parametrize("fault", ["primary", "missing", "duplicate_account", "comment", "receipt"])
def test_netplan_decision_actor_scope_blocks_before_any_write(fault: str) -> None:
    scope = scope_document()
    entry = scope["journeys"]["expansion"]
    body = entry["writes"]["decide"]["body"]
    if fault == "primary":
        body["actor_id"] = entry["account_ids"]["primary"]
    elif fault == "missing":
        body.pop("actor_id")
    elif fault == "duplicate_account":
        entry["account_ids"]["approver"] = entry["account_ids"]["primary"]
        body["actor_id"] = entry["account_ids"]["approver"]
    elif fault == "comment":
        body["comment"] = "Not a NetPlanDecisionPayload field"
    else:
        body["approval_receipt_id"] = "another-authority-receipt"
    receipt, web, _ = run(scope=scope, selection=["expansion"])
    assert receipt["journeys"]["expansion"]["status"] == "BLOCKED"
    _zero_writes(receipt, web, "expansion")


def test_netplan_positive_scope_uses_actual_decision_payload() -> None:
    from apps.api.app.routes.netplan import NetPlanDecisionPayload

    entry = journey_scopes()["expansion"]
    body = NetPlanDecisionPayload.model_validate({
        **entry["writes"]["decide"]["body"], "approval_receipt_id": entry["approval_ref"],
    })
    assert body.actor_id == entry["account_ids"]["approver"] != entry["account_ids"]["primary"]
    assert body.reason
    receipt, _, _ = run(selection=["expansion"])
    result = receipt["journeys"]["expansion"]
    assert result["status"] == "PASSED"
    approval = result["after"]["state"]["approvals"][0]
    assert approval["approval_id"] == result["captured"]["approval_id"]
    assert approval["actor_id"] == approval["approval_principal_id"] == body.actor_id
    assert approval["approval_receipt_id"] == body.approval_receipt_id


@pytest.mark.parametrize("field", ["actor_id", "approval_principal_id", "approval_receipt_id", "approval_id", "reason", "authentic_approval_verified"])
def test_netplan_durable_approval_must_match_this_named_write(field: str) -> None:
    class MisboundApproval(BusinessWeb):
        def request(self, method: str, path: str, **kwargs: Any) -> Resp:
            response = super().request(method, path, **kwargs)
            if method == "POST" and path.endswith("/decide") and response.status == 200:
                row = self.scenario["approvals"][-1]
                row[field] = False if field == "authentic_approval_verified" else "another-value"
            return response

    receipt, _, _ = run(web=MisboundApproval(), selection=["expansion"])
    assert receipt["journeys"]["expansion"]["status"] == "FAILED"


@pytest.mark.parametrize("fault", ["lost_with_concurrent", "storeId", "subjectId", "category", "message", "correlationId", "reportId", "duplicate"])
def test_franchise_requires_the_exact_new_durable_report(fault: str) -> None:
    class MisboundReport(BusinessWeb):
        def request(self, method: str, path: str, **kwargs: Any) -> Resp:
            response = super().request(method, path, **kwargs)
            if method == "POST" and path.endswith("/franchisee/reports") and response.status < 400:
                report = self.store["reports"][-1]
                if fault == "lost_with_concurrent":
                    self.store["reports"] = [{**report, "reportId": "unrelated-concurrent-report"}]
                elif fault == "duplicate":
                    self.store["reports"].append(deepcopy(report))
                else:
                    report[fault] = "unrelated-value"
            return response

    receipt, _, _ = run(web=MisboundReport(), selection=["franchise"])
    assert receipt["journeys"]["franchise"]["status"] == "FAILED"


@pytest.mark.parametrize("journey_id,capture", [("expansion", "approval_id"), ("franchise", "report_id")])
def test_resealed_receipt_requires_written_record_capture(journey_id: str, capture: str) -> None:
    receipt, _, _ = run()
    receipt["journeys"][journey_id]["captured"].pop(capture)
    assert f"business_journey:{journey_id}" in failing_checks(reseal(receipt))


@pytest.mark.parametrize("journey_id,field", [("expansion", "actor_id"), ("expansion", "approval_principal_id"), ("expansion", "approval_receipt_id"), ("franchise", "subjectId"), ("franchise", "storeId"), ("franchise", "message"), ("franchise", "correlationId")])
def test_resealed_receipt_requires_scoped_durable_outcome(journey_id: str, field: str) -> None:
    receipt, _, _ = run()
    key = "approvals" if journey_id == "expansion" else "reports"
    receipt["journeys"][journey_id]["after"]["state"][key][0][field] = "misbound"
    assert f"business_journey:{journey_id}" in failing_checks(reseal(receipt))


@pytest.mark.parametrize("body", [{"targetOwnerName": "New offline owner"}, {"targetRoleId": "facilitiesLead"}])
def test_real_storeops_transfer_is_the_durable_authorized_outcome(body: dict[str, str]) -> None:
    from modules.opsboard.application.store_ops import StoreOpsService

    service = StoreOpsService()  # OFFLINE real in-memory domain implementation
    original = service.snapshot()["issues"][0]

    class RealTransferWeb(BusinessWeb):
        def __init__(self) -> None:
            super().__init__()
            self.issue = {**original, "tenantId": TENANT}

        def request(self, method: str, path: str, **kwargs: Any) -> Resp:
            response = super().request(method, path, **kwargs)
            if method == "POST" and path.endswith("/transfer") and response.status == 200:
                written = service.transition_issue(
                    issue_id=original["id"], action_type="transfer", payload=kwargs["body"],
                    correlation_id=kwargs["headers"]["x-correlation-id"],
                )
                self.issue = {**written["issue"], "tenantId": TENANT}
                response.payload["issue"] = deepcopy(self.issue)
            return response

    scope = scope_document()
    entry = scope["journeys"]["operations"]
    entry["records"]["issue_id"] = original["id"]
    entry["writes"]["transition"] = {"action": "transfer", "body": body}
    receipt, _, _ = run(web=RealTransferWeb(), scope=scope, selection=["operations"])
    result = receipt["journeys"]["operations"]
    assert result["status"] == "PASSED"
    actual = service.get_issue(original["id"])
    assert actual["status"] == original["status"]
    assert actual.get("history") == original.get("history")
    assert (actual["ownerRoleId"], actual["ownerName"]) != (original["ownerRoleId"], original["ownerName"])
    for owner_field in ("ownerRoleId", "ownerName"):
        assert result["after"]["state"][f"issue.{owner_field}"] == actual[owner_field]


def test_noop_transfer_blocks_before_writes_and_wrong_owner_is_not_success() -> None:
    scope = scope_document()
    entry = scope["journeys"]["operations"]
    entry["writes"]["transition"] = {"action": "transfer", "body": {"targetRoleId": "opsLead"}}
    receipt, web, _ = run(scope=scope, selection=["operations"])
    assert receipt["journeys"]["operations"]["status"] == "BLOCKED"
    _zero_writes(receipt, web, "operations")
    entry["writes"]["transition"]["body"] = {"targetOwnerName": "Authorized offline owner"}

    class WrongOwner(BusinessWeb):
        def request(self, method: str, path: str, **kwargs: Any) -> Resp:
            response = super().request(method, path, **kwargs)
            if method == "POST" and path.endswith("/transfer") and response.status == 200:
                self.issue["ownerName"] = "Unrelated concurrent owner"
            return response

    receipt, _, _ = run(web=WrongOwner(), scope=scope, selection=["operations"])
    assert receipt["journeys"]["operations"]["status"] == "FAILED"


def test_runner_reads_this_real_shell_report_after_repository_restart() -> None:
    from modules.opsboard.application.shell import InMemoryShellRepository, ShellService

    repo = InMemoryShellRepository()
    service = ShellService(repository=repo)  # OFFLINE domain producer/consumer

    class RealReportWeb(BusinessWeb):
        def request(self, method: str, path: str, **kwargs: Any) -> Resp:
            response = super().request(method, path, **kwargs)
            if method == "POST" and path.endswith("/franchisee/reports") and response.status < 400:
                body = kwargs["body"]
                response = Resp(200, service.franchisee_report(
                    subject_id=account_id(ACTORS["franchise"]["primary"]), store_id=body["storeId"],
                    category=body["category"], message=body["message"],
                    correlation_id=kwargs["headers"]["x-correlation-id"],
                    idempotency_key=kwargs["headers"]["idempotency-key"],
                ))
                restarted = ShellService(repository=repo)
                self.store["reports"] = restarted.get_franchisee_view(
                    subject_id=account_id(ACTORS["franchise"]["primary"]), store_id=body["storeId"],
                )["reports"]
            return response

    receipt, _, _ = run(web=RealReportWeb(), selection=["franchise"])
    result = receipt["journeys"]["franchise"]
    assert result["status"] == "PASSED"
    assert result["captured"]["report_id"] == result["after"]["state"]["reports"][0]["reportId"]


def test_franchise_contract_matches_real_shell_service() -> None:
    from modules.opsboard.application.shell import ShellService

    service = ShellService()  # explicit OFFLINE in-memory implementation
    response = service.get_franchisee_view(subject_id="offline-owner", store_id="ST-0412")
    spec = bj.JOURNEYS_BY_ID["franchise"]
    assert bj.dig(response, spec.record_id_path) == "ST-0412"
    assert bj.dig(response, "meta.scope.storeId") == "ST-0412"
    written = service.franchisee_report(
        subject_id="offline-owner",
        store_id="ST-0412",
        category="equipment",
        message="Offline alarm regression",
        idempotency_key="offline-report",
        correlation_id="offline-report-corr",
    )
    assert written["auditEvent"]["metadata"]["correlationId"] == "offline-report-corr"
    assert (
        service.get_franchisee_view(subject_id="offline-owner", store_id="ST-0412")["reports"][0][
            "reportId"
        ]
        == written["report"]["reportId"]
    )
