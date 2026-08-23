"""NetPlan <- EMGI decision integration.

Provides contract: odayplus.netplan-emgi.v1
Requires contract: emgi.site-market-context.v1
Requires contract: odayplus.sitescore-v3.v1
Requires contract: odayplus.physical-feasibility.v1
Requires contract: odayplus.site-economics.v1

This module is the only place where NetPlan reads the EMGI decision stack.
It turns a versioned market context plus the three decision products into
solver-ready :class:`~modules.netplan.domain.planning.CandidateSiteInput`
options, and it refuses to do so unless the provenance chain is intact.

Two rules drive every branch below:

1. *Versioned inputs only.*  A candidate is admitted only when the market
   context, SiteScore v3, feasibility and economics documents all agree on
   the same point-in-time manifest and market-context identity.  Documents
   that merely resemble the contracts are rejected, not coerced.
2. *Fail closed.*  Anything unknown, missing, unparsable or unrecognised
   withholds the candidate from the binding plan.  Admission never falls out
   of a default value.

Admission is still not approval: the emitted document always carries
``requires_human_approval``.  Binding sign-off lives in OpsBoard
(:mod:`modules.opsboard.integrations.emgi`).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from modules.netplan.domain.planning import CandidateSiteInput
from modules.site_economics.domain.contracts import (
    CONTRACT_ID as ECONOMICS_CONTRACT_ID,
)
from modules.site_economics.domain.contracts import (
    CONTRACT_VERSION as ECONOMICS_CONTRACT_VERSION,
)
from modules.site_economics.domain.models import EconomicsDecision
from modules.site_feasibility.domain.contracts import (
    CONTRACT_ID as FEASIBILITY_CONTRACT_ID,
)
from modules.site_feasibility.domain.contracts import (
    CONTRACT_VERSION as FEASIBILITY_CONTRACT_VERSION,
)
from modules.site_feasibility.domain.models import FeasibilityDecision
from modules.sitescore.v3.domain.contracts import (
    CONTRACT_ID as SITESCORE_CONTRACT_ID,
)
from modules.sitescore.v3.domain.contracts import (
    CONTRACT_VERSION as SITESCORE_CONTRACT_VERSION,
)
from modules.sitescore.v3.domain.models import (
    DecisionReadiness,
    ScoreAvailability,
    SiteScoreDecision,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    CONTRACT_ID as MARKET_CONTEXT_CONTRACT_ID,
)
from packages.oday_data_product_contracts_client.models.site_market_context import (
    CONTRACT_VERSION as MARKET_CONTEXT_CONTRACT_VERSION,
)

CONTRACT_ID = "odayplus.netplan-emgi.v1"
CONTRACT_VERSION = "1.0.0"
CONTRACT_CATEGORY = "decision_product"

#: Bumped whenever the admission rules or the economics -> solver mapping
#: changes.  It is persisted on every emitted document so a past plan stays
#: reproducible after this file evolves.
INTEGRATION_POLICY_VERSION = "netplan-emgi-admission-v1"


class CandidateAdmission(StrEnum):
    """Whether a candidate site may enter the binding network plan."""

    ADMITTED = "ADMITTED"
    WITHHELD_PROVENANCE = "WITHHELD_PROVENANCE"
    WITHHELD_INCOMPLETE = "WITHHELD_INCOMPLETE"
    REJECTED = "REJECTED"


# Precedence used when several checks fail at once.  Provenance outranks a
# business rejection on purpose: when the chain of custody is broken we cannot
# claim the site was rejected on its merits, only that we may not act on it.
_ADMISSION_PRECEDENCE: dict[CandidateAdmission, int] = {
    CandidateAdmission.ADMITTED: 0,
    CandidateAdmission.WITHHELD_INCOMPLETE: 1,
    CandidateAdmission.REJECTED: 2,
    CandidateAdmission.WITHHELD_PROVENANCE: 3,
}


class NetPlanEmgiContractError(ValueError):
    """Raised when a netplan-emgi document violates its own contract."""


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Read either a wire mapping or a released contract document."""

    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        rendered = to_dict()
        if isinstance(rendered, Mapping):
            return rendered
    return {}


def _field(value: Any, name: str) -> Any:
    mapping = _as_mapping(value)
    if name in mapping:
        return mapping[name]
    if value is None:
        return None
    return getattr(value, name, None)


def _text(value: Any) -> str | None:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _number(value: Any) -> float | None:
    """Return a finite float, or ``None`` for anything we must not guess at."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _digest(value: Any) -> str | None:
    """Prefer a document's own digest, else hash its canonical wire form."""

    declared = _text(getattr(value, "digest", None))
    if declared is not None:
        return declared
    mapping = _as_mapping(value)
    if not mapping:
        return None
    return hashlib.sha256(_canonical_json(dict(mapping)).encode("utf-8")).hexdigest()


def _context_for_site(market_context: Any, site_id: str) -> Mapping[str, Any]:
    """Unwrap the site-scoped context out of a context or context document.

    A context document is not interchangeable with any single child: falling
    back to an unmatched sibling would let another site's market decide this
    site's plan.  An unmatched lookup returns ``{}`` so the caller withholds.
    """

    context = _as_mapping(market_context)
    contexts = context.get("contexts")
    if isinstance(contexts, Sequence) and not isinstance(contexts, (str, bytes)):
        for item in contexts:
            candidate = _as_mapping(item)
            if _as_mapping(candidate.get("identity")).get("site_id") == site_id:
                return candidate
        return {}
    if _as_mapping(context.get("identity")).get("site_id") != site_id:
        return {}
    return context


def _manifest_ids(value: Any) -> set[str]:
    """Collect the manifest references one context or document level declares.

    Sibling ``contexts`` are deliberately *not* traversed: a manifest that only
    another site's context carries must not vouch for this candidate's
    point-in-time inputs.  Callers union the site-scoped context with the
    document root, which is the only level whose refs are legitimately shared.
    """

    manifest_ids: set[str] = set()

    def collect(source: Any) -> None:
        mapping = _as_mapping(source)
        for name in (
            "manifest_id",
            "product_manifest_id",
            "feature_manifest_id",
            "source_manifest_id",
            "release_id",
        ):
            candidate = _text(mapping.get(name))
            if candidate is not None:
                manifest_ids.add(candidate)
        children = mapping.get("component_manifest_refs")
        if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
            for child in children:
                collect(child)
        metadata = mapping.get("metadata")
        if metadata is not None:
            metadata_mapping = _as_mapping(metadata)
            for name in ("manifest_id", "product_manifest_id"):
                candidate = _text(metadata_mapping.get(name))
                if candidate is not None:
                    manifest_ids.add(candidate)

    collect(value)
    return manifest_ids


def _declares_contract(value: Any, contract_id: str) -> bool:
    """Check a contract id on a document or on one of its component refs.

    The generated ``emgi.site-market-context.v1`` model stores the contract id
    in ``contract_version``, and hand-built contexts carry it only on their
    component manifest refs, so all three placements are accepted.
    """

    mapping = _as_mapping(value)
    for name in ("contract_id", "contract_version"):
        if _text(mapping.get(name)) == contract_id:
            return True
    for name in ("component_manifest_refs", "contexts"):
        children = mapping.get(name)
        if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
            for child in children:
                if _declares_contract(child, contract_id):
                    return True
    return False


def _market_context_id(context: Mapping[str, Any], document: Any) -> str | None:
    """Return the identity feasibility/economics stamp as their source."""

    return (
        _text(context.get("context_id"))
        or _text(context.get("document_id"))
        or _text(_field(document, "document_id"))
    )


def _market_context_digest(context: Mapping[str, Any], document: Any) -> str | None:
    """Return the market context hash the decision chain is pinned to.

    A producer-declared ``sha256``/``digest`` wins; otherwise the canonical
    content hash of the site-scoped context is used, so the evidence still
    identifies the exact payload the plan was derived from.
    """

    for source in (context, _as_mapping(document)):
        for name in ("sha256", "digest"):
            candidate = _text(source.get(name))
            if candidate is not None:
                return candidate
        metadata = _as_mapping(source.get("metadata"))
        for name in ("sha256", "digest"):
            candidate = _text(metadata.get(name))
            if candidate is not None:
                return candidate
    return _digest(context) or _digest(document)


def _source_market_context_id(document: Any) -> str | None:
    """Read the producer-stamped market-context reference from a decision doc."""

    mapping = _as_mapping(document)
    direct = _text(mapping.get("source_market_context_id"))
    if direct is not None:
        return direct
    return _text(_as_mapping(mapping.get("metadata")).get("source_market_context_id"))


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """One versioned document that a candidate decision was derived from."""

    label: str
    contract_id: str
    contract_version: str
    document_id: str | None = None
    sha256: str | None = None

    @property
    def snapshot_id(self) -> str:
        return f"{self.contract_id}:{self.document_id or 'unidentified'}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "document_id": self.document_id,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """Persisted evidence and policy versions behind one candidate decision.

    ``policy_versions`` is stored as ordered pairs rather than a dict so the
    evidence stays frozen, hashable and byte-stable across serialisations.
    """

    manifest_id: str | None
    market_context_id: str | None
    market_context_sha256: str | None
    policy_versions: tuple[tuple[str, str], ...] = ()
    refs: tuple[EvidenceRef, ...] = ()

    @property
    def snapshot_ids(self) -> tuple[str, ...]:
        return tuple(sorted({ref.snapshot_id for ref in self.refs}))

    def policy_version(self, label: str) -> str | None:
        for name, version in self.policy_versions:
            if name == label:
                return version
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "market_context_id": self.market_context_id,
            "market_context_sha256": self.market_context_sha256,
            "policy_versions": dict(self.policy_versions),
            "refs": [ref.to_dict() for ref in self.refs],
            "snapshot_ids": list(self.snapshot_ids),
        }


@dataclass(frozen=True, slots=True)
class NetPlanEmgiPolicy:
    """Versioned mapping from site economics onto NetPlan solver inputs."""

    #: Horizon used to turn a monthly EBITDA into the solver's planning-period
    #: gross margin.  It is persisted so a plan can be re-derived later.
    gross_margin_horizon_months: int = 12
    #: Risk added per economics risk flag, on top of ``1 - confidence_score``.
    risk_per_flag: float = 0.05
    default_capacity_delta: int = 1
    policy_version: str = INTEGRATION_POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "gross_margin_horizon_months": self.gross_margin_horizon_months,
            "risk_per_flag": self.risk_per_flag,
            "default_capacity_delta": self.default_capacity_delta,
            "policy_version": self.policy_version,
        }


DEFAULT_POLICY = NetPlanEmgiPolicy()


@dataclass(frozen=True, slots=True)
class CandidateDecisionRequest:
    """The per-site decision stack handed to the integration."""

    site_id: str
    sitescore_doc: Any
    feasibility_doc: Any
    economics_doc: Any
    capacity_delta: int | None = None


@dataclass(frozen=True, slots=True)
class NetPlanCandidateDecision:
    """A candidate site's admission verdict plus its evidence trail."""

    candidate_site_id: str
    admission: CandidateAdmission
    evidence: DecisionEvidence
    reasons: tuple[str, ...] = ()
    candidate_input: CandidateSiteInput | None = None
    #: Admission is a machine gate, never a sign-off.  OpsBoard owns the
    #: binding decision, so this stays true even for admitted candidates.
    requires_human_approval: bool = True

    @property
    def is_admitted(self) -> bool:
        return self.admission == CandidateAdmission.ADMITTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_site_id": self.candidate_site_id,
            "admission": self.admission.value,
            "reasons": list(self.reasons),
            "requires_human_approval": self.requires_human_approval,
            "evidence": self.evidence.to_dict(),
            "candidate_input": (
                {
                    "candidate_site_id": self.candidate_input.candidate_site_id,
                    "expected_gross_margin": self.candidate_input.expected_gross_margin,
                    "open_cost": self.candidate_input.open_cost,
                    "risk_score": self.candidate_input.risk_score,
                    "capacity_delta": self.candidate_input.capacity_delta,
                    "source_snapshot_ids": list(self.candidate_input.source_snapshot_ids),
                }
                if self.candidate_input is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class NetPlanEmgiDocument:
    """Root canonical document for contract ``odayplus.netplan-emgi.v1``."""

    document_id: str
    tenant_id: str
    scenario_key: str
    manifest_id: str
    candidates: tuple[NetPlanCandidateDecision, ...]
    policy: NetPlanEmgiPolicy = DEFAULT_POLICY
    contract_id: str = CONTRACT_ID
    contract_version: str = CONTRACT_VERSION
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    requires_human_approval: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return netplan_emgi_document_digest(self)

    @property
    def admitted_candidates(self) -> tuple[NetPlanCandidateDecision, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.is_admitted)

    def candidate_site_inputs(self) -> tuple[CandidateSiteInput, ...]:
        """Solver inputs for the admitted candidates only."""

        return tuple(
            candidate.candidate_input
            for candidate in self.admitted_candidates
            if candidate.candidate_input is not None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "document_id": self.document_id,
            "tenant_id": self.tenant_id,
            "scenario_key": self.scenario_key,
            "manifest_id": self.manifest_id,
            "generated_at": self.generated_at,
            "requires_human_approval": self.requires_human_approval,
            "policy": self.policy.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


def netplan_emgi_document_digest(doc: NetPlanEmgiDocument | Mapping[str, Any]) -> str:
    """Canonical SHA256 of a netplan-emgi document, in object or wire form.

    OpsBoard pins an approval to this digest, so it must be computable from a
    stored payload and not only from a live document object.
    """

    data = doc.to_dict() if isinstance(doc, NetPlanEmgiDocument) else dict(doc)
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def validate_netplan_emgi_document(doc: NetPlanEmgiDocument | Mapping[str, Any]) -> None:
    """Validate the wire shape of the netplan-emgi product contract."""

    data = doc.to_dict() if isinstance(doc, NetPlanEmgiDocument) else doc
    if not isinstance(data, Mapping):
        raise NetPlanEmgiContractError("netplan-emgi document must be a mapping")
    if data.get("contract_id") != CONTRACT_ID:
        raise NetPlanEmgiContractError(
            f"Invalid contract_id: expected '{CONTRACT_ID}', got '{data.get('contract_id')}'"
        )
    if data.get("contract_version") != CONTRACT_VERSION:
        raise NetPlanEmgiContractError(
            f"Invalid contract_version: expected '{CONTRACT_VERSION}', "
            f"got '{data.get('contract_version')}'"
        )
    for name in ("document_id", "tenant_id", "scenario_key", "manifest_id", "generated_at"):
        if _text(data.get(name)) is None:
            raise NetPlanEmgiContractError(f"{name} is required")
    if data.get("requires_human_approval") is not True:
        raise NetPlanEmgiContractError(
            "requires_human_approval must stay true; OpsBoard owns the binding decision"
        )
    policy = data.get("policy")
    if not isinstance(policy, Mapping) or _text(policy.get("policy_version")) is None:
        raise NetPlanEmgiContractError("policy.policy_version is required")
    candidates = data.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise NetPlanEmgiContractError("candidates must be an array")

    allowed = {admission.value for admission in CandidateAdmission}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise NetPlanEmgiContractError("each candidate must be a mapping")
        if _text(candidate.get("candidate_site_id")) is None:
            raise NetPlanEmgiContractError("candidate_site_id is required")
        admission = candidate.get("admission")
        if admission not in allowed:
            raise NetPlanEmgiContractError(f"Invalid candidate admission: {admission!r}")
        evidence = candidate.get("evidence")
        if not isinstance(evidence, Mapping):
            raise NetPlanEmgiContractError("candidate evidence is required")
        if not evidence.get("policy_versions"):
            raise NetPlanEmgiContractError("candidate evidence must persist policy versions")
        refs = evidence.get("refs")
        if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or not refs:
            raise NetPlanEmgiContractError("candidate evidence must persist document refs")
        if admission == CandidateAdmission.ADMITTED.value:
            if candidate.get("candidate_input") is None:
                raise NetPlanEmgiContractError("an admitted candidate must carry solver inputs")
            if _text(evidence.get("manifest_id")) is None:
                raise NetPlanEmgiContractError(
                    "an admitted candidate must carry its point-in-time manifest"
                )
        elif candidate.get("candidate_input") is not None:
            raise NetPlanEmgiContractError("a withheld candidate must not carry solver inputs")


class _Verdict:
    """Accumulates admission downgrades and their reasons for one candidate."""

    def __init__(self) -> None:
        self.admission = CandidateAdmission.ADMITTED
        self._reasons: list[str] = []

    def downgrade(self, admission: CandidateAdmission, reason: str) -> None:
        if _ADMISSION_PRECEDENCE[admission] > _ADMISSION_PRECEDENCE[self.admission]:
            self.admission = admission
        if reason not in self._reasons:
            self._reasons.append(reason)

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(self._reasons)


class NetPlanEmgiIntegrationService:
    """Builds NetPlan solver inputs from the versioned EMGI decision stack."""

    def __init__(
        self,
        policy: NetPlanEmgiPolicy | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.policy = policy or DEFAULT_POLICY
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: uuid4().hex)

    def build_candidate(
        self,
        *,
        site_id: str,
        manifest_id: str,
        market_context: Any,
        sitescore_doc: Any,
        feasibility_doc: Any,
        economics_doc: Any,
        capacity_delta: int | None = None,
    ) -> NetPlanCandidateDecision:
        """Return the admission verdict and evidence for one candidate site."""

        verdict = _Verdict()
        expected_manifest_id = _text(manifest_id)
        if expected_manifest_id is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE, "Evaluation manifest is missing."
            )

        context = _context_for_site(market_context, site_id)
        if not context:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                "Market context does not cover this candidate site.",
            )
        elif not _declares_contract(market_context, MARKET_CONTEXT_CONTRACT_ID):
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                f"Market context does not declare {MARKET_CONTEXT_CONTRACT_ID}.",
            )

        context_manifest_ids = _manifest_ids(context) | _manifest_ids(market_context)
        if expected_manifest_id is not None and expected_manifest_id not in context_manifest_ids:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                "Market context manifest does not match the evaluation manifest.",
            )

        market_context_id = _market_context_id(context, market_context)
        if market_context_id is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE, "Market context identity is missing."
            )

        self._check_feasibility(feasibility_doc, market_context_id, verdict)
        self._check_economics(economics_doc, market_context_id, verdict)
        self._check_sitescore(sitescore_doc, site_id, expected_manifest_id, verdict)

        evidence = self._build_evidence(
            manifest_id=expected_manifest_id,
            market_context=market_context,
            context=context,
            market_context_id=market_context_id,
            sitescore_doc=sitescore_doc,
            feasibility_doc=feasibility_doc,
            economics_doc=economics_doc,
        )
        if evidence.policy_version("feasibility_gate") is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                "Feasibility policy version is missing from the evidence chain.",
            )
        if evidence.policy_version("economics_engine") is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                "Economics engine version is missing from the evidence chain.",
            )

        candidate_input: CandidateSiteInput | None = None
        if verdict.admission == CandidateAdmission.ADMITTED:
            candidate_input = self._build_candidate_input(
                site_id=site_id,
                economics_doc=economics_doc,
                evidence=evidence,
                capacity_delta=capacity_delta,
                verdict=verdict,
            )

        return NetPlanCandidateDecision(
            candidate_site_id=site_id,
            admission=verdict.admission,
            evidence=evidence,
            reasons=verdict.reasons,
            candidate_input=candidate_input,
        )

    def build_plan_document(
        self,
        *,
        tenant_id: str,
        scenario_key: str,
        manifest_id: str,
        market_context: Any,
        requests: Sequence[CandidateDecisionRequest],
        metadata: Mapping[str, Any] | None = None,
    ) -> NetPlanEmgiDocument:
        """Build the plan-level product OpsBoard reviews and approves."""

        candidates = tuple(
            self.build_candidate(
                site_id=request.site_id,
                manifest_id=manifest_id,
                market_context=market_context,
                sitescore_doc=request.sitescore_doc,
                feasibility_doc=request.feasibility_doc,
                economics_doc=request.economics_doc,
                capacity_delta=request.capacity_delta,
            )
            for request in requests
        )
        document = NetPlanEmgiDocument(
            document_id=f"netplan-emgi-{self._id_factory()}",
            tenant_id=tenant_id,
            scenario_key=scenario_key,
            manifest_id=manifest_id,
            candidates=candidates,
            policy=self.policy,
            generated_at=self._clock().isoformat(),
            metadata=dict(metadata or {}),
        )
        # Validate at the producer boundary so OpsBoard never has to review a
        # document that only resembles the netplan-emgi contract.
        validate_netplan_emgi_document(document)
        return document

    def _check_feasibility(
        self, document: Any, market_context_id: str | None, verdict: _Verdict
    ) -> None:
        if not self._check_contract(
            "Feasibility",
            document,
            FEASIBILITY_CONTRACT_ID,
            FEASIBILITY_CONTRACT_VERSION,
            verdict,
        ):
            return
        source_id = _source_market_context_id(document)
        if source_id is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                "Feasibility document does not reference a market context.",
            )
        elif market_context_id is not None and source_id != market_context_id:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                "Feasibility source market context mismatch.",
            )

        recommendation = _text(_field(_field(document, "decision"), "recommendation"))
        if recommendation is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE, "Feasibility decision is missing."
            )
        elif recommendation == FeasibilityDecision.INFEASIBLE.value:
            verdict.downgrade(CandidateAdmission.REJECTED, "Site is physically infeasible.")
        elif recommendation == FeasibilityDecision.UNKNOWN_REQUIRES_SURVEY.value:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                "Feasibility is unknown and requires a survey.",
            )
        elif recommendation == FeasibilityDecision.CONDITIONAL.value:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                "Feasibility is conditional and requires resolution.",
            )
        elif recommendation != FeasibilityDecision.FEASIBLE.value:
            # An unrecognised value is not a pass: newer producers may add
            # decisions this policy version has never been reviewed against.
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                f"Feasibility decision is unrecognized: {recommendation}.",
            )

    def _check_economics(
        self, document: Any, market_context_id: str | None, verdict: _Verdict
    ) -> None:
        if not self._check_contract(
            "Economics",
            document,
            ECONOMICS_CONTRACT_ID,
            ECONOMICS_CONTRACT_VERSION,
            verdict,
        ):
            return
        source_id = _source_market_context_id(document)
        if source_id is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                "Economics document does not reference a market context.",
            )
        elif market_context_id is not None and source_id != market_context_id:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                "Economics source market context mismatch.",
            )

        recommendation = _text(_field(_field(document, "decision"), "recommendation"))
        if recommendation is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE, "Economics decision is missing."
            )
        elif recommendation == EconomicsDecision.REJECT.value:
            verdict.downgrade(CandidateAdmission.REJECTED, "Economics rejected the site.")
        elif recommendation == EconomicsDecision.CONDITIONAL_GO.value:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                "Economics is conditional and requires resolution.",
            )
        elif recommendation == EconomicsDecision.INVESTIGATE.value:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE, "Economics requires investigation."
            )
        elif recommendation != EconomicsDecision.GO.value:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                f"Economics decision is unrecognized: {recommendation}.",
            )

    def _check_sitescore(
        self,
        document: Any,
        site_id: str,
        expected_manifest_id: str | None,
        verdict: _Verdict,
    ) -> None:
        if not self._check_contract(
            "SiteScore v3",
            document,
            SITESCORE_CONTRACT_ID,
            SITESCORE_CONTRACT_VERSION,
            verdict,
        ):
            return
        if _text(_field(document, "site_id")) != site_id:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                "SiteScore v3 document belongs to another site.",
            )
        manifest_id = _text(_field(document, "manifest_id"))
        if manifest_id is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE, "SiteScore v3 manifest is missing."
            )
        elif expected_manifest_id is not None and manifest_id != expected_manifest_id:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE, "SiteScore v3 manifest mismatch."
            )

        assessment = _field(document, "assessment")
        readiness = _text(_field(assessment, "readiness"))
        availability = _text(_field(assessment, "availability"))
        decision = _text(_field(assessment, "decision"))

        if readiness != DecisionReadiness.READY.value:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                f"SiteScore v3 decision readiness is {readiness or 'missing'}.",
            )
        if availability != ScoreAvailability.AVAILABLE.value:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                f"SiteScore v3 score availability is {availability or 'missing'}.",
            )
        if decision == SiteScoreDecision.NO_GO.value:
            verdict.downgrade(CandidateAdmission.REJECTED, "SiteScore v3 returned NO_GO.")
        elif decision != SiteScoreDecision.GO.value:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                f"SiteScore v3 decision is {decision or 'missing'}.",
            )

    def _check_contract(
        self,
        label: str,
        document: Any,
        contract_id: str,
        contract_version: str,
        verdict: _Verdict,
    ) -> bool:
        """Reject anything that does not declare the exact expected contract."""

        if document is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE, f"{label} document is missing."
            )
            return False
        actual_id = _text(_field(document, "contract_id"))
        actual_version = _text(_field(document, "contract_version"))
        if actual_id != contract_id:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                f"{label} document declares contract {actual_id or 'none'}, "
                f"expected {contract_id}.",
            )
            return False
        if actual_version != contract_version:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_PROVENANCE,
                f"{label} document declares version {actual_version or 'none'}, "
                f"expected {contract_version}.",
            )
            return False
        return True

    def _build_evidence(
        self,
        *,
        manifest_id: str | None,
        market_context: Any,
        context: Mapping[str, Any],
        market_context_id: str | None,
        sitescore_doc: Any,
        feasibility_doc: Any,
        economics_doc: Any,
    ) -> DecisionEvidence:
        policy_versions: list[tuple[str, str]] = [
            ("netplan_emgi_integration", self.policy.policy_version)
        ]
        feasibility_policy = _text(
            _as_mapping(_field(feasibility_doc, "metadata")).get("policy_version")
        )
        if feasibility_policy is not None:
            policy_versions.append(("feasibility_gate", feasibility_policy))
        economics_engine = _text(_field(economics_doc, "engine_version"))
        if economics_engine is not None:
            policy_versions.append(("economics_engine", economics_engine))
        sitescore_version = _text(_field(sitescore_doc, "contract_version"))
        if sitescore_version is not None:
            policy_versions.append(("sitescore_contract", sitescore_version))
        product_version = _text(_field(market_context, "product_version")) or _text(
            context.get("product_version")
        )
        if product_version is not None:
            policy_versions.append(("market_context_product", product_version))

        market_context_digest = _market_context_digest(context, market_context)
        refs = (
            EvidenceRef(
                label="market_context",
                contract_id=MARKET_CONTEXT_CONTRACT_ID,
                contract_version=MARKET_CONTEXT_CONTRACT_VERSION,
                document_id=market_context_id,
                sha256=market_context_digest,
            ),
            self._evidence_ref("sitescore_v3", sitescore_doc, SITESCORE_CONTRACT_ID),
            self._evidence_ref("feasibility", feasibility_doc, FEASIBILITY_CONTRACT_ID),
            self._evidence_ref("economics", economics_doc, ECONOMICS_CONTRACT_ID),
        )
        return DecisionEvidence(
            manifest_id=manifest_id,
            market_context_id=market_context_id,
            market_context_sha256=market_context_digest,
            policy_versions=tuple(policy_versions),
            refs=refs,
        )

    @staticmethod
    def _evidence_ref(label: str, document: Any, fallback_contract_id: str) -> EvidenceRef:
        return EvidenceRef(
            label=label,
            contract_id=_text(_field(document, "contract_id")) or fallback_contract_id,
            contract_version=_text(_field(document, "contract_version")) or "unknown",
            document_id=_text(_field(document, "document_id")),
            sha256=_digest(document),
        )

    def _build_candidate_input(
        self,
        *,
        site_id: str,
        economics_doc: Any,
        evidence: DecisionEvidence,
        capacity_delta: int | None,
        verdict: _Verdict,
    ) -> CandidateSiteInput | None:
        """Map site economics onto the solver's candidate option.

        Every input is read explicitly; a missing or non-finite figure
        withholds the candidate instead of silently defaulting to a value that
        would make the site look cheap or safe.
        """

        metrics = _field(economics_doc, "metrics")
        monthly_ebitda = _number(_field(metrics, "average_monthly_ebitda"))
        open_cost = _number(_field(metrics, "total_initial_cash_outlay"))
        decision = _field(economics_doc, "decision")
        confidence = _number(_field(decision, "confidence_score"))

        if monthly_ebitda is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                "Economics metrics are missing an average monthly EBITDA.",
            )
        if open_cost is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                "Economics metrics are missing a total initial cash outlay.",
            )
        if confidence is None:
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                "Economics decision is missing a confidence score.",
            )
        elif not 0.0 <= confidence <= 1.0:
            # Out of range is corrupt input; clamping it would quietly turn a
            # nonsense confidence into a zero-risk candidate.
            verdict.downgrade(
                CandidateAdmission.WITHHELD_INCOMPLETE,
                f"Economics confidence score is out of range: {confidence}.",
            )
            confidence = None
        if monthly_ebitda is None or open_cost is None or confidence is None:
            return None

        risk_flags = _field(decision, "risk_flags") or ()
        if isinstance(risk_flags, (str, bytes)) or not isinstance(risk_flags, Sequence):
            risk_flags = ()
        risk_score = _clamp(_clamp(1.0 - confidence) + self.policy.risk_per_flag * len(risk_flags))

        return CandidateSiteInput(
            candidate_site_id=site_id,
            expected_gross_margin=round(
                monthly_ebitda * self.policy.gross_margin_horizon_months, 4
            ),
            open_cost=round(open_cost, 4),
            risk_score=round(risk_score, 6),
            capacity_delta=(
                capacity_delta if capacity_delta is not None else self.policy.default_capacity_delta
            ),
            # The solver keeps these on the chosen action, so the executed plan
            # can be traced back to the exact documents that justified it.
            source_snapshot_ids=evidence.snapshot_ids,
        )


__all__ = [
    "CONTRACT_CATEGORY",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "DEFAULT_POLICY",
    "INTEGRATION_POLICY_VERSION",
    "MARKET_CONTEXT_CONTRACT_ID",
    "MARKET_CONTEXT_CONTRACT_VERSION",
    "CandidateAdmission",
    "CandidateDecisionRequest",
    "DecisionEvidence",
    "EvidenceRef",
    "NetPlanCandidateDecision",
    "NetPlanEmgiContractError",
    "NetPlanEmgiDocument",
    "NetPlanEmgiIntegrationService",
    "NetPlanEmgiPolicy",
    "netplan_emgi_document_digest",
    "validate_netplan_emgi_document",
]
