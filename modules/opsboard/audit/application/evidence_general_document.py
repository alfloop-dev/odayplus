"""Versioned fixed general evidence fields shared by retain/preview/render.

No approved-program records or caller-granted authority belong to this owner.
Recorded chain is partial: actual outcome remains unavailable, never filled.
"""
from __future__ import annotations

import json

from modules.opsboard.audit.application.evidence_projection import SafeSiteScoreSections
from modules.opsboard.audit.application.sitescore_decision_chain import (
    RecordedSiteScoreDecisionChain,
)
from modules.opsboard.audit.application.sitescore_evidence import SiteScoreEvidenceUnavailable
from shared.audit.integrity import canonical_json

LIMITS = {"content": "recorded_decision_and_audit_references_only", "source_content_included": False,
    "model_readiness_claimed": False, "subsidy_eligibility_claimed": False}

CHAIN_GENERAL_SCHEMA = "sitescore.decision-audit.recorded-partial-chain.v2"
GENERAL_SCHEMA = "sitescore.decision-audit.references.v1"
CHAIN_LIMITS = {**LIMITS, "content": "recorded_decision_audit_and_partial_chain", "full_QA03_complete": False}


def validate_general_document(value: dict) -> dict:
    include_chain = isinstance(value, dict) and value.get("schema") == CHAIN_GENERAL_SCHEMA
    expected = {"schema", "source_digest", "classification", "masking_profile", "actor_fields_masked", "sections", "limits"}
    if include_chain:
        expected.add("recorded_chain")
    if (not isinstance(value, dict) or set(value) != expected
            or value["schema"] not in (GENERAL_SCHEMA, CHAIN_GENERAL_SCHEMA) or value["classification"] != "CONFIDENTIAL"
            or value["masking_profile"] != "masked" or value["actor_fields_masked"] is not True
            or value["limits"] != (CHAIN_LIMITS if include_chain else LIMITS) or not isinstance(value["sections"], dict)):
        raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:general_format_projection")
    sections = []
    for name, rows in value["sections"].items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:general_format_projection")
        for row in rows:
            for key in ("actor", "decision_actor"):
                if key not in row:
                    continue
                actor = row[key]
                if not isinstance(actor, str) or not actor:
                    raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:general_format_mask")
                if "@" in actor:
                    local, _, domain = actor.partition("@")
                    masked = bool(domain) and (len(local) <= 1 or not (set(local[1:]) - {"*"}))
                else:
                    masked = not (set(actor) - {"*"})
                if not masked:
                    raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:general_format_mask")
        sections.append((name, tuple(tuple(row.items()) for row in rows)))
    projection = SafeSiteScoreSections(value["source_digest"], tuple(sections)).for_format("json")
    if projection["sections"] != value["sections"]:
        raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:general_format_projection")
    if include_chain:
        chain = RecordedSiteScoreDecisionChain(value["source_digest"], canonical_json(value["recorded_chain"]).encode()).document()
        rows = value["sections"].get("decision_log")
        if not rows or len(rows) != 1:
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:recorded_chain_selection")
        row = rows[0]
        human, prediction = chain["human_decision"], chain["prediction"]
        joins = {"decision_id": human["decision_id"], "decision_version": human["decision_version"],
            "approval_id": human["approval_id"], "approval_status": human["approval_status"],
            "decision_actor": human["actor"], "decided_at": human["decided_at"],
            "recorded_policy_reference": human["policy_reference"], "rationale_sha256": human["reason_sha256"],
            "report_id": prediction["report_id"], "report_version": prediction["report_version"],
            "report_sha256": prediction["report_sha256"], "source_snapshot_ids": prediction["source_snapshot_ids"],
            "recorded_model_reference": prediction["model_reference"], "recorded_feature_reference": prediction["feature_reference"],
            "recorded_network_action": chain["recommendation"]["recorded_network_action"]}
        if any(row.get(key) != item for key, item in joins.items()):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:recorded_chain_reference_binding")
    return json.loads(json.dumps(value, allow_nan=False))
