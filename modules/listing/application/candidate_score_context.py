"""Read existing current candidate inputs without issuing scoring authority.

The worker owns its transaction and publication fences. The Operator reader
uses a short transaction for observation only; its output is never a capability
or a replacement for fresh worker, source or production model admission.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from models.model_ready.contracts import MODEL_SPECS
from models.shared_ml.production_runtime import ProductionModelInputError, _validate_live_rows
from modules.listing.application.intake_score_admission import validate_score_admission
from modules.sitescore.application.candidate_features import feature_from_candidate
from modules.sitescore.domain.scoring import to_sitescore_model_row
from shared.infrastructure.persistence.assisted_listing_intake import _STATE_COLLECTIONS
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)


def _denied(code: str) -> None:
    from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied

    raise ScoreDeliveryDenied(code)


def _unavailable(code: str) -> None:
    from modules.listing.application.intake_score_delivery import ScoreDeliveryUnavailable

    raise ScoreDeliveryUnavailable(code)


def _time(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass(frozen=True)
class CurrentCandidateInputs:
    listing: Any
    address: Any
    attributes: dict[str, Any]


def read_current_candidate_inputs(
    repository: Any, store: Any, candidate: Any,
) -> CurrentCandidateInputs:
    """Read under the caller's existing same-engine tenant transaction.

    Keep the writer's partitioned advisory key, including when no metadata row
    exists yet. A plain metadata get would miss a concurrent first revocation.
    """
    listing = repository.get_listing(candidate.candidate_site.listing_id)
    address = repository.get_address(candidate.candidate_site.address_id)
    if listing is None or address is None:
        _denied("SITESCORE_CURRENT_INPUT_UNAVAILABLE")
    current = {key: value for key, value in vars(listing).items() if key != "listing_status"}
    metadata = store._read_locked(
        repository._store._collection(DurableAssistedIntakeRepository._LISTING_META),
        candidate.candidate_site.listing_id,
    )
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        _denied("SITESCORE_CURRENT_INPUT_UNAVAILABLE")
    for attribute, key in (
        ("prior_90d_cell_net_revenue", "prior90dCellNetRevenue"),
        ("prior_90d_cell_transaction_count", "prior90dCellTransactionCount"),
        ("prior_90d_cell_store_count", "prior90dCellStoreCount"),
        ("feature_snapshot_time", "featureSnapshotTime"),
        ("hard_rule_failures", "hardRuleFailures"),
    ):
        # Missing key retains the current domain verdict. Explicit null revokes
        # a hard-rule verdict; historical null PIT metadata is unmeasured.
        if key not in metadata or (metadata[key] is None and attribute != "hard_rule_failures"):
            continue
        value = metadata[key]
        if attribute == "feature_snapshot_time":
            try:
                value = _time(value)
            except (TypeError, ValueError, OverflowError) as exc:
                from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied

                raise ScoreDeliveryDenied("SITESCORE_CURRENT_INPUT_UNAVAILABLE") from exc
        current[attribute] = value
    return CurrentCandidateInputs(listing, address, current)


def validate_current_candidate_inputs(candidate: Any, current: CurrentCandidateInputs) -> None:
    """Preserve the existing worker's verdict, agreement and gate priority.

    This narrower worker check is separate from the six-field presentation Gate
    and the production feature/PIT/model contract. No hard-rule policy is run.
    """
    from modules.listing.application.intake_score_delivery import _digest

    failures = current.attributes.get("hard_rule_failures")
    if not isinstance(failures, list) or failures:
        _denied("SITESCORE_CURRENT_HARD_RULE_GATE_DENIED")
    stored = {key: value for key, value in vars(candidate.listing).items() if key != "listing_status"}
    if _digest(stored) != _digest(current.attributes) or _digest(candidate.address) != _digest(current.address):
        _denied("SITESCORE_CANDIDATE_INPUT_STALE")
    listing, address = current.listing, current.address
    if (listing.listing_status != "candidate"
            or listing.rent_amount <= 0 or listing.area_ping <= 0
            or not address.normalized_address or not address.h3_res_9
            or not address.geocode_confidence):
        _denied("SITESCORE_CURRENT_CANDIDATE_GATE_DENIED")


def admission_state(state: str, reason: str | None = None) -> dict[str, Any]:
    return {"state": state, "reasonCode": reason}


def unverified_current_admission(candidate: Any, reason: str) -> dict[str, Any]:
    def field(record: Any, name: str) -> Any:
        return record.get(name) if isinstance(record, Mapping) else getattr(record, name, None)

    site = field(candidate, "candidate_site")
    return {
        "candidateId": field(site, "candidate_site_id"),
        "listingId": field(site, "listing_id"),
        "addressId": field(site, "address_id"),
        "checkedAt": None,
        "currentInputs": admission_state("unverified", reason),
        "source": admission_state("unverified", reason),
        "pit": admission_state("unverified", reason),
        "model": admission_state("unverified", "SITESCORE_CURRENT_MODEL_ADMISSION_UNVERIFIED"),
    }


@dataclass(frozen=True)
class CandidateScoreContextRead:
    admission: dict[str, Any]
    current_draft: Any | None = None


class CandidateScoreContextReader:
    """Trusted tenant-bound current observation, without refresh or inference."""

    def __init__(self, *, tenant_id: str, repository: Any, engine: Any, store: Any) -> None:
        self.tenant_id = tenant_id
        self.repository = repository
        self.engine = engine
        self.store = store

    def _binding(self, candidate: Any) -> None:
        if (not self.tenant_id or getattr(self.repository, "tenant_id", None) != self.tenant_id
                or getattr(getattr(self.repository, "_store", None), "engine", None) is not self.engine
                or getattr(self.store, "_engine", None) is not self.engine
                or getattr(self.engine, "dialect", None) != "postgresql"):
            _unavailable("SITESCORE_CANDIDATE_BINDING_UNAVAILABLE")
        # Empty legacy tenant attributes are not a new authority. Exact scoped
        # repository ownership remains necessary; explicit foreign pins deny.
        if any(getattr(record, "tenant_id", None) not in (None, "", self.tenant_id)
               for record in (candidate.listing, candidate.address, candidate.candidate_site)):
            _denied("SITESCORE_PARENT_BINDING_DENIED")

    def _source(self, candidate: Any) -> dict[str, Any]:
        rows = self.engine.query(
            "SELECT job_id,payload FROM workflow.jobs WHERE tenant_id=? "
            "AND aggregate_type='candidate_site' AND aggregate_id=? AND job_type='intake-sitescore'",
            (self.tenant_id, candidate.candidate_site.candidate_site_id),
        )
        if len(rows) != 1:
            return admission_state("unverified", "SITESCORE_SOURCE_PARENT_UNVERIFIED")
        row = rows[0]
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                _denied("SITESCORE_PARENT_BINDING_DENIED")
        if not isinstance(payload, dict):
            _denied("SITESCORE_PARENT_BINDING_DENIED")
        promotion_id, intake_id = payload.get("promotion_decision_id"), payload.get("intake_id")
        if (not isinstance(promotion_id, str) or not promotion_id
                or not isinstance(intake_id, str) or not intake_id
                or payload.get("candidate_site_id") != candidate.candidate_site.candidate_site_id):
            _denied("SITESCORE_PARENT_BINDING_DENIED")
        # Match worker parent lock order before locking the exact job. The
        # initial tenant-qualified query only discovers references, never grants
        # admission; re-read the locked job after loading its exact parents.
        intake = self.store._read_locked(
            _STATE_COLLECTIONS["intakes"], f"{self.tenant_id}:{intake_id}",
        )
        promotion = self.store._read_locked(
            _STATE_COLLECTIONS["promotions"], f"{self.tenant_id}:{promotion_id}",
        )
        locked_rows = self.engine.query(
            "SELECT job_id,payload FROM workflow.jobs WHERE tenant_id=? "
            "AND aggregate_type='candidate_site' AND aggregate_id=? AND job_type='intake-sitescore' FOR SHARE",
            (self.tenant_id, candidate.candidate_site.candidate_site_id),
        )
        from modules.listing.application.intake_score_delivery import _digest

        if len(locked_rows) != 1 or _digest(locked_rows[0]) != _digest(row):
            _denied("SITESCORE_PARENT_BINDING_DENIED")
        if (not isinstance(promotion, dict) or not isinstance(intake, dict)
                or promotion.get("promotion_decision_id") != promotion_id
                or promotion.get("tenant_id") != self.tenant_id
                or promotion.get("intake_id") != intake_id
                or promotion.get("listing_id") != candidate.candidate_site.listing_id
                or promotion.get("candidate_site_id") != candidate.candidate_site.candidate_site_id
                or promotion.get("site_score_job_id") != str(row["job_id"])
                or intake.get("intake_id") != intake_id
                or not isinstance(intake.get("scope"), dict)
                or intake["scope"].get("tenant_id") != self.tenant_id):
            _denied("SITESCORE_PARENT_BINDING_DENIED")
        receipt = validate_score_admission(self.engine, intake, candidate)
        return {
            **admission_state("verified"),
            "intakeId": intake_id, "promotionDecisionId": promotion_id,
            "jobId": str(row["job_id"]), "sourceId": receipt["source_id"],
            "sourcePolicyVersion": receipt["source_policy_version"],
            "sourceSnapshotId": receipt["source_snapshot_id"],
            "sourcePolicySha256": receipt["source_policy_sha256"],
            "sourceSnapshotMetadataSha256": receipt["source_snapshot_metadata_sha256"],
        }

    def read(self, candidate: Any) -> CandidateScoreContextRead:
        from modules.listing.application.intake_score_delivery import (
            ScoreDeliveryDenied,
            ScoreDeliveryUnavailable,
        )

        admission = unverified_current_admission(candidate, "SITESCORE_CURRENT_CONTEXT_UNAVAILABLE")
        current_draft = None
        try:
            self._binding(candidate)
            with self.engine.lock:
                self.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (self.tenant_id,))
                try:
                    admission["source"] = self._source(candidate)
                except ScoreDeliveryDenied as exc:
                    admission["source"] = admission_state("blocked", str(exc))
                current = read_current_candidate_inputs(self.repository, self.store, candidate)
                current_draft = deepcopy(candidate)
                current_draft.listing = deepcopy(current.listing)
                current_draft.address = deepcopy(current.address)
                for key, value in current.attributes.items():
                    object.__setattr__(current_draft.listing, key, deepcopy(value))
                admission["checkedAt"] = datetime.now(UTC).isoformat()
                try:
                    validate_current_candidate_inputs(candidate, current)
                    admission["currentInputs"] = admission_state("verified")
                except ScoreDeliveryDenied as exc:
                    admission["currentInputs"] = admission_state("blocked", str(exc))
                try:
                    feature_row = to_sitescore_model_row(
                        feature_from_candidate(candidate, tenant_id=self.tenant_id),
                    )
                    spec = MODEL_SPECS["sitescore"]
                    _validate_live_rows(
                        [feature_row], service="sitescore",
                        expected_feature_schema_version=spec.feature_schema_version,
                        required_features=spec.feature_columns,
                    )
                    admission["pit"] = admission_state("verified")
                except (ValueError, ProductionModelInputError) as exc:
                    admission["pit"] = admission_state(
                        "blocked", getattr(exc, "code", "SITESCORE_FEATURE_CONTRACT_INCOMPLETE"),
                    )
        except ScoreDeliveryDenied as exc:
            admission["currentInputs"] = admission_state("blocked", str(exc))
        except ScoreDeliveryUnavailable as exc:
            admission["currentInputs"] = admission_state("unverified", str(exc))
        except Exception:
            # Observation failures must not turn historical report reads into
            # inference or a fabricated pass. No result of a failed transaction
            # is carried as a current verified read.
            admission = unverified_current_admission(candidate, "SITESCORE_CURRENT_CONTEXT_UNAVAILABLE")
            current_draft = None
        return CandidateScoreContextRead(admission, current_draft)
