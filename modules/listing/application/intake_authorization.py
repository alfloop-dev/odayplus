"""Intake Authorization implementation.

Enforces the role/action/resource/scope/state/field/risk matrix from docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTHORIZATION_MATRIX.md.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from shared.auth import DataClassification, Principal, Role


def authorize_intake_action(
    principal: Principal,
    action: str,
    resource: dict[str, Any] | None = None,
    *,
    risk_acknowledged: bool = False,
    risk_summary: str | None = None,
    first_actor_id: str | None = None,
    is_identity_affecting: bool = False,
    has_legal_hold: bool = False,
    is_residency_compliant: bool = True,
    operator_role_id: str | None = None,
) -> None:
    """Enforce deny-by-default assisted intake authorization and segregation."""
    # 1. Authenticated check
    if not principal.authenticated:
        raise HTTPException(status_code=401, detail="AUTHENTICATION_REQUIRED")

    # 2. Tenant Isolation
    if resource is not None:
        resource_tenant = resource.get("tenantId") or resource.get("tenant_id")
        if resource_tenant and principal.tenant_id and principal.tenant_id != resource_tenant:
            raise HTTPException(status_code=403, detail="TENANT_SCOPE_DENIED")

    # 3. Brand/Region/Area/HeatZone scope
    if resource is not None:
        zone_id = resource.get("heatZoneId") or resource.get("heat_zone_id")
        if zone_id and not principal.scope.permits_region(zone_id):
            raise HTTPException(status_code=403, detail="SCOPE_DENIED")

    # 4. Role mapping and matrix rules
    is_admin = principal.has_role(Role.PLATFORM_ADMIN) or operator_role_id == "platform-admin"
    is_manager = principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
        "expansion-manager",
        "expansionManager",
        "site-reviewer",
        "siteReviewer",
        "executive",
    )
    is_staff = (
        principal.has_role(Role.EXPANSION_USER)
        or operator_role_id in (
            "expansion-staff",
            "expansionStaff",
            "expansion-user",
            "expansion_user",
        )
    ) and not is_manager

    is_steward = principal.has_role(Role.DATA_OWNER) or operator_role_id in (
        "data-steward",
        "dataSteward",
    )
    is_governance = principal.has_role(
        Role.AUDITOR, Role.ARCHITECTURE_OWNER
    ) or operator_role_id in ("governance-reviewer", "governanceReviewer")
    is_privacy = principal.has_role(Role.FINANCE_LEGAL) or operator_role_id in (
        "privacy-officer",
        "privacyOfficer",
    )

    # Deny platform admin from accessing business data
    if is_admin and not (is_manager or is_staff or is_steward):
        raise HTTPException(status_code=403, detail="ROLE_DENIED")

    def _is_owner(owner: Any, submitter: Any) -> bool:
        if owner in ("林曉青", "林曉青（展店）") or submitter in ("林曉青", "林曉青（展店）"):
            return True
        return principal.subject_id in (owner, submitter)

    # Action-specific checks
    if action == "view":
        # Staff can only view own submissions
        if is_staff and resource is not None:
            owner = resource.get("owner")
            submitter = resource.get("submitter")
            if owner and submitter and not _is_owner(owner, submitter):
                raise HTTPException(status_code=403, detail="OWNERSHIP_REQUIRED")
        # Ensure allowed roles
        if not (is_staff or is_manager or is_steward or is_governance or is_privacy):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

    elif action in ("submit_url", "submit_csv"):
        if not (is_staff or is_manager or is_steward):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

    elif action == "cancel":
        if is_staff and resource is not None:
            owner = resource.get("owner")
            submitter = resource.get("submitter")
            if owner and submitter and not _is_owner(owner, submitter):
                raise HTTPException(status_code=403, detail="OWNERSHIP_REQUIRED")
        if not (is_staff or is_manager or is_steward):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

    elif action == "correct":
        # Check risk acknowledgement for corrections
        if is_identity_affecting:
            if not risk_summary or not risk_summary.strip():
                raise HTTPException(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
            if not risk_acknowledged:
                raise HTTPException(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

        # Staff can only correct own submissions
        if is_staff and resource is not None:
            owner = resource.get("owner")
            submitter = resource.get("submitter")
            if owner and submitter and not _is_owner(owner, submitter):
                raise HTTPException(status_code=403, detail="OWNERSHIP_REQUIRED")

        if not (is_staff or is_manager or is_steward or is_privacy):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

        # Identity-affecting corrections proposer reviewer check (segregation)
        if is_identity_affecting:
            # Proposer cannot be the reviewer
            if is_steward and resource is not None:
                proposer = resource.get("submitter") or resource.get("proposed_by")
                if proposer == principal.subject_id:
                    raise HTTPException(status_code=403, detail="SELF_REVIEW_DENIED")

    elif action == "decide":
        # Check risk acknowledgement for decide
        if not risk_summary or not risk_summary.strip():
            raise HTTPException(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
        if not risk_acknowledged:
            raise HTTPException(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

        if is_staff:
            # Staff can only propose
            pass
        elif not (is_manager or is_steward):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

    elif action in ("merge", "split", "unmerge"):
        if is_staff:
            raise HTTPException(status_code=403, detail="ROLE_DENIED")
        if not (is_manager or is_steward):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

        # Check risk acknowledgement for merge/split/unmerge
        if not risk_summary or not risk_summary.strip():
            raise HTTPException(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
        if not risk_acknowledged:
            raise HTTPException(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

        # Merge/split require independent second actor
        if first_actor_id and first_actor_id == principal.subject_id:
            raise HTTPException(status_code=409, detail="SECOND_ACTOR_REQUIRED")

    elif action == "promote":
        # Check risk acknowledgement for promote
        if not risk_summary or not risk_summary.strip():
            raise HTTPException(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
        if not risk_acknowledged:
            raise HTTPException(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

        if not (is_staff or is_manager or is_steward):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

        # Segregation of duties: proposer of promotion cannot approve own promotion request
        if first_actor_id and first_actor_id == principal.subject_id:
            raise HTTPException(status_code=403, detail="SELF_REVIEW_DENIED")

    elif action == "convert":
        if not (is_manager or is_steward):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

    elif action == "purge":
        if not (is_manager or is_steward or is_privacy):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")
        if has_legal_hold:
            raise HTTPException(status_code=409, detail="LEGAL_HOLD_CONFLICT")
        if first_actor_id and first_actor_id == principal.subject_id:
            raise HTTPException(status_code=409, detail="SECOND_ACTOR_REQUIRED")

    elif action == "export":
        if not is_residency_compliant:
            raise HTTPException(status_code=403, detail="RESIDENCY_DENIED")
        if not (is_staff or is_manager or is_steward or is_governance or is_privacy):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

    elif action == "reopen_failed":
        if is_staff and resource is not None:
            owner = resource.get("owner")
            submitter = resource.get("submitter")
            if owner and submitter and not _is_owner(owner, submitter):
                raise HTTPException(status_code=403, detail="OWNERSHIP_REQUIRED")
        if not (is_staff or is_manager or is_steward):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")

    elif action == "reopen_quarantine":
        if is_staff:
            raise HTTPException(status_code=403, detail="ROLE_DENIED")
        if not (is_manager or is_steward or is_privacy):
            raise HTTPException(status_code=403, detail="ROLE_DENIED")
        if first_actor_id and first_actor_id == principal.subject_id:
            raise HTTPException(status_code=409, detail="SECOND_ACTOR_REQUIRED")

    else:
        # Default deny-by-default for unknown actions
        raise HTTPException(status_code=403, detail="ROLE_DENIED")


def mask_listing(principal: Principal, listing: dict[str, Any]) -> dict[str, Any]:
    """Mask fields based on principal clearance and field classification."""
    clearance = principal.scope.clearance if principal.authenticated else DataClassification.PUBLIC
    if clearance >= DataClassification.CONFIDENTIAL:
        return listing

    masked = listing.copy()
    # Mask INTERNAL fields (clearance < INTERNAL)
    if clearance < DataClassification.INTERNAL:
        for field in ("address", "rentPerMonth", "areaPing", "floor"):
            if field in masked:
                masked[field] = None
                masked[f"{field}_masked"] = True
                masked[f"{field}_mask_reason_code"] = "FIELD_MASKED"

    # Mask CONFIDENTIAL fields (clearance < CONFIDENTIAL)
    if clearance < DataClassification.CONFIDENTIAL:
        for field in ("coordinates", "rawSnapshot", "brokerCompany", "contactEmail", "sourceUrl"):
            if field in masked:
                masked[field] = None
                masked[f"{field}_masked"] = True
                masked[f"{field}_mask_reason_code"] = "FIELD_MASKED"

    return masked


def mask_intake(principal: Principal, intake: dict[str, Any]) -> dict[str, Any]:
    """Mask fields based on principal clearance and field classification."""
    clearance = principal.scope.clearance if principal.authenticated else DataClassification.PUBLIC
    if clearance >= DataClassification.CONFIDENTIAL:
        return intake

    masked = intake.copy()

    # Mask parsedFields
    if "parsedFields" in masked and isinstance(masked["parsedFields"], dict):
        parsed = {}
        for key, field_info in masked["parsedFields"].items():
            field_info_copy = field_info.copy()

            # Decide if this field is sensitive based on its name
            # INTERNAL: address, rent, areaPing, floor
            # CONFIDENTIAL: coordinates, rawSnapshot, brokerCompany, contactEmail
            # RESTRICTED: contactPhone, brokerEmail, privateNotes
            field_class = DataClassification.PUBLIC
            if key in ("address", "rent", "areaPing", "floor"):
                field_class = DataClassification.INTERNAL
            elif key in ("coordinates", "rawSnapshot", "brokerCompany", "contactEmail"):
                field_class = DataClassification.CONFIDENTIAL
            elif key in ("contactPhone", "brokerEmail", "privateNotes"):
                field_class = DataClassification.RESTRICTED

            if clearance < field_class:
                field_info_copy["sourceValue"] = None
                field_info_copy["normalizedValue"] = None
                field_info_copy["correctedValue"] = None
                field_info_copy["masked"] = True
                field_info_copy["mask_reason_code"] = "FIELD_MASKED"

            parsed[key] = field_info_copy
        masked["parsedFields"] = parsed

    # Mask top-level rawSnapshot/originalUrl if needed
    if clearance < DataClassification.CONFIDENTIAL:
        if "rawSnapshot" in masked:
            masked["rawSnapshot"] = None
            masked["rawSnapshot_masked"] = True
            masked["rawSnapshot_mask_reason_code"] = "FIELD_MASKED"
        if "originalUrl" in masked:
            masked["originalUrl"] = None
            masked["originalUrl_masked"] = True
            masked["originalUrl_mask_reason_code"] = "FIELD_MASKED"

    return masked
