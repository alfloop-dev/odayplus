"""Intake Authorization implementation.

Enforces the role/action/resource/scope/state/field/risk matrix from docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_AUTHORIZATION_MATRIX.md.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import HTTPException

from shared.audit import build_security_event
from shared.auth import (
    AccessRequest,
    Action,
    DataClassification,
    Decision,
    Environment,
    Principal,
    ResourceDescriptor,
    Role,
    TenantAccessWaiver,
    TenantAccessWaiverRegistry,
    check_tenant_isolation,
)

_SCOPE_AXIS_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("brand", ("brand_id", "brandId")),
    ("region", ("region_id", "regionId")),
    ("store", ("store_id", "storeId")),
    ("assigned_area", ("assigned_area_id", "assignedAreaId")),
    ("heat_zone", ("heat_zone_id", "heatZoneId")),
)


def _resource_scope_value(resource: dict[str, Any], keys: tuple[str, ...]) -> Any:
    nested = resource.get("scope")
    for source in (resource, nested if isinstance(nested, dict) else {}):
        for key in keys:
            value = source.get(key)
            if value is not None:
                return value
    return None


def collection_filters_in_scope(principal: Principal, filters: dict[str, Any]) -> bool:
    """Return whether every filter a read collection request supplies is in scope.

    A read query is not an object envelope: an axis the caller did not filter on
    is not missing evidence, because every returned record is still projected
    against the complete principal scope.  Each supplied axis must be allowed.
    Object targets and creates keep the complete-envelope check of
    :func:`intake_resource_in_scope`.
    """

    checks = {
        "brand": principal.scope.permits_brand,
        "region": principal.scope.permits_region,
        "store": principal.scope.permits_store,
        "assigned_area": principal.scope.permits_assigned_area,
        "heat_zone": principal.scope.permits_heat_zone,
    }
    for axis, keys in _SCOPE_AXIS_KEYS:
        value = _resource_scope_value(filters, keys)
        if value is not None and not checks[axis](value):
            return False
    return True


def collection_rows_are_scope_projected(principal: Principal) -> bool:
    """Return whether collection routes project every returned row for ``principal``.

    Only the verified operator read grant is paired with a per-row projection
    against its complete scope (before counts and pagination).  Any other caller
    receives the service rows for the declared filters unprojected, so its
    collection filters must still form a complete scope envelope.
    """

    return principal.has_role(Role.OPERATOR_VIEWER)


# A collection request addresses a query scope, not an object.  It is audited
# and waiver-matched under its own resource type so that a waiver written for a
# single listing can never silently authorize an entire cross-tenant collection.
COLLECTION_RESOURCE_TYPE = "listing_collection"


def _target_resource_tenant(resource: dict[str, Any]) -> str | None:
    """Return the tenant the target resource itself declares, else ``None``.

    Only the accessed object is consulted.  The caller's own tenant is never
    consulted here, so a target resource that carries no tenant (an intake
    payload, a scope-only envelope, an object missing ``tenantId``) resolves to
    ``None`` and is denied by the shared guard.
    """
    value = _resource_scope_value(resource, ("tenant_id", "tenantId"))
    if isinstance(value, str):
        value = value.strip()
    return value or None


def _declared_query_scope_tenant(tenant_id: str | None) -> str | None:
    """Return the explicitly declared tenant query scope of a collection request."""
    if isinstance(tenant_id, str) and tenant_id.strip():
        return tenant_id.strip()
    return None


def intake_resource_in_scope(principal: Principal, resource: dict[str, Any]) -> bool:
    """Return whether every non-empty principal intake scope contains resource.

    Intake scope is represented both as a nested ``scope`` object in the v1 API
    and as flat camelCase fields in Operator Console resources.  Missing resource
    metadata fails closed whenever the principal is restricted on that axis.
    """

    checks = {
        "brand": principal.scope.permits_brand,
        "region": principal.scope.permits_region,
        "store": principal.scope.permits_store,
        "assigned_area": principal.scope.permits_assigned_area,
        "heat_zone": principal.scope.permits_heat_zone,
    }
    return all(
        checks[axis](_resource_scope_value(resource, keys))
        for axis, keys in _SCOPE_AXIS_KEYS
    )


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
    audit_log: Any = None,
    correlation_id: str | None = None,
    source_ip: str | None = None,
    waiver: TenantAccessWaiver | str | None = None,
    waiver_registry: TenantAccessWaiverRegistry | None = None,
    tenant_id: str | None = None,
    collection_scope: dict[str, Any] | None = None,
) -> None:
    """Enforce deny-by-default assisted intake authorization and segregation.

    Tenant evidence comes from exactly one of two mutually exclusive sources,
    which are never allowed to substitute for one another:

    ``resource``
        The envelope of a single target object.  Its tenant is read from the
        object itself; ``tenant_id`` is *not* a fallback for it.  A target that
        declares no tenant fails closed even when the caller supplies its own
        tenant, so a tenant-less object can never be authorized as though it
        belonged to the principal.
    ``tenant_id`` (with ``resource=None``)
        The declared query scope of a collection request, which has no object
        envelope to read a tenant from.  A missing query scope fails closed.

    ``collection_scope`` carries the brand/region/area/heat-zone filters of a
    collection request so those axes are still enforced without dressing the
    filters up as a target resource.
    """
    def map_action_to_enum(action_str: str) -> Action:
        mapping = {
            "view": Action.VIEW,
            "submit_url": Action.CREATE,
            "submit_csv": Action.CREATE,
            "cancel": Action.UPDATE,
            "correct": Action.UPDATE,
            "decide": Action.UPDATE,
            "merge": Action.UPDATE,
            "split": Action.UPDATE,
            "promote": Action.UPDATE,
            "convert": Action.UPDATE,
            "reopen_failed": Action.UPDATE,
            "reopen_quarantine": Action.UPDATE,
            "purge": Action.DELETE,
            "export": Action.EXPORT,
        }
        return mapping.get(action_str, Action.UPDATE)

    # Resolve the request shape once, before any decision or audit record, so
    # that the allow path, the deny path, and the audit trail can never disagree
    # about which tenant was actually evaluated.
    is_collection_request = resource is None
    if is_collection_request:
        target_tenant = _declared_query_scope_tenant(tenant_id)
        res_type = COLLECTION_RESOURCE_TYPE
        res_id = None
        scope_subject = collection_scope
    else:
        target_tenant = _target_resource_tenant(resource)
        res_type = "intake" if ("url" in resource or "parsedFields" in resource) else "listing"
        res_id = resource.get("id") or resource.get("listingId") or resource.get("listing_id")
        scope_subject = resource

    def _raise_and_audit(status_code: int, detail: str, *, decision: Decision | None = None) -> None:
        if audit_log is not None:
            action_enum = map_action_to_enum(action)
            desc = ResourceDescriptor(
                type=res_type,
                resource_id=res_id,
                tenant_id=target_tenant,
            )
            access = AccessRequest(
                principal=principal,
                action=action_enum,
                resource=desc,
                environment=Environment(
                    source_ip=source_ip,
                    attributes={"correlation_id": correlation_id or "unknown"}
                ),
            )
            dec = decision or Decision.deny(
                reason=detail,
                policy_id="intake_authorization",
            )
            event = build_security_event(access, dec)
            audit_log.record(event)
        raise HTTPException(status_code=status_code, detail=detail)

    # 1. Authenticated check
    if not principal.authenticated:
        _raise_and_audit(status_code=401, detail="AUTHENTICATION_REQUIRED")

    # Module restrictions are independent of role and every object axis.
    if not principal.scope.permits_module("listing"):
        _raise_and_audit(status_code=403, detail="MODULE_SCOPE_DENIED")

    # 2. Tenant Isolation (shared fail-closed guard)
    effective_waiver = waiver or (
        resource.get("waiver")
        if isinstance(resource, dict)
        and isinstance(resource.get("waiver"), (TenantAccessWaiver, str))
        else None
    )

    if principal.has_role(Role.OPERATOR_VIEWER) and target_tenant != principal.tenant_id:
        _raise_and_audit(status_code=403, detail="TENANT_SCOPE_DENIED")

    tenant_decision = check_tenant_isolation(
        principal=principal,
        resource_tenant_id=target_tenant,
        resource_type=res_type,
        resource_id=res_id,
        waiver=effective_waiver,
        waiver_registry=waiver_registry,
    )
    if not tenant_decision.allowed:
        _raise_and_audit(status_code=403, detail="TENANT_SCOPE_DENIED", decision=tenant_decision)

    # 3. Brand/Region/Area/HeatZone scope
    #
    # Read collection requests from a caller whose returned records are each
    # projected against the full scope are checked against the filters they
    # declare.  Every other subject -- unprojected collection reads, target
    # objects and creates (which must land inside every restricted axis) -- is
    # checked as a complete envelope.  Either way the principal's own
    # restrictions must contain the subject.
    if scope_subject is not None:
        in_scope = (
            collection_filters_in_scope(principal, scope_subject)
            if is_collection_request
            and action == "view"
            and collection_rows_are_scope_projected(principal)
            else intake_resource_in_scope(principal, scope_subject)
        )
        if not in_scope:
            _raise_and_audit(status_code=403, detail="SCOPE_DENIED")

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

    # An explicit verified read grant composes with administration, but only
    # for VIEW. A caller-selected persona can never manufacture this grant.
    is_operator_viewer = principal.has_role(Role.OPERATOR_VIEWER)
    if is_admin and not (
        is_manager or is_staff or is_steward or (action == "view" and is_operator_viewer)
    ):
        _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    def _is_owner(owner: Any, submitter: Any) -> bool:
        sentinels = {"system", "unassigned", "SYSTEM", "UNASSIGNED", None, ""}
        ownership_subjects = {
            subject for subject in (owner, submitter) if subject not in sentinels
        }
        return principal.subject_id in ownership_subjects

    # Action-specific checks
    if action == "view":
        # Staff can only view own submissions
        if is_staff and resource is not None:
            owner = resource.get("owner")
            submitter = resource.get("submitter")
            if not _is_owner(owner, submitter):
                _raise_and_audit(status_code=403, detail="OWNERSHIP_REQUIRED")
        # Ensure allowed roles
        if not (is_staff or is_manager or is_steward or is_governance or is_privacy or is_operator_viewer):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    elif action in ("submit_url", "submit_csv"):
        if not (is_staff or is_manager or is_steward):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    elif action == "cancel":
        if is_staff and resource is not None:
            owner = resource.get("owner")
            submitter = resource.get("submitter")
            if not _is_owner(owner, submitter):
                _raise_and_audit(status_code=403, detail="OWNERSHIP_REQUIRED")
        if not (is_staff or is_manager or is_steward):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    elif action == "correct":
        # Check risk acknowledgement for corrections
        if is_identity_affecting:
            if not risk_summary or not risk_summary.strip():
                _raise_and_audit(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
            if not risk_acknowledged:
                _raise_and_audit(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

        # Staff can only correct own submissions
        if is_staff and resource is not None:
            owner = resource.get("owner")
            submitter = resource.get("submitter")
            if not _is_owner(owner, submitter):
                _raise_and_audit(status_code=403, detail="OWNERSHIP_REQUIRED")

        if not (is_staff or is_manager or is_steward or is_privacy):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    elif action == "decide":
        # Check risk acknowledgement for decide
        if not risk_summary or not risk_summary.strip():
            _raise_and_audit(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
        if not risk_acknowledged:
            _raise_and_audit(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

        if is_staff:
            # Staff can only propose
            pass
        elif not (is_manager or is_steward):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    elif action in ("merge", "split", "unmerge"):
        if is_staff:
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")
        if not (is_manager or is_steward):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")

        # Check risk acknowledgement for merge/split/unmerge
        if not risk_summary or not risk_summary.strip():
            _raise_and_audit(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
        if not risk_acknowledged:
            _raise_and_audit(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

        # Merge/split require independent second actor
        if first_actor_id and first_actor_id == principal.subject_id:
            _raise_and_audit(status_code=409, detail="SECOND_ACTOR_REQUIRED")

    elif action == "promote":
        # Check risk acknowledgement for promote
        if not risk_summary or not risk_summary.strip():
            _raise_and_audit(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk summary is required")
        if not risk_acknowledged:
            _raise_and_audit(status_code=422, detail="RISK_ACKNOWLEDGEMENT_REQUIRED: risk acknowledgement is required")

        if not (is_staff or is_manager or is_steward):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")



    elif action == "convert":
        if not (is_manager or is_steward):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    elif action == "purge":
        if not (is_manager or is_steward or is_privacy):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")
        if has_legal_hold:
            _raise_and_audit(status_code=409, detail="LEGAL_HOLD_CONFLICT")
        if first_actor_id and first_actor_id == principal.subject_id:
            _raise_and_audit(status_code=409, detail="SECOND_ACTOR_REQUIRED")

    elif action == "export":
        if not is_residency_compliant:
            _raise_and_audit(status_code=403, detail="RESIDENCY_DENIED")
        if not (is_staff or is_manager or is_steward or is_governance or is_privacy):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    elif action == "reopen_failed":
        if is_staff and resource is not None:
            owner = resource.get("owner")
            submitter = resource.get("submitter")
            if not _is_owner(owner, submitter):
                _raise_and_audit(status_code=403, detail="OWNERSHIP_REQUIRED")
        if not (is_staff or is_manager or is_steward):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    elif action == "reopen_quarantine":
        if is_staff:
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")
        if not (is_manager or is_steward or is_privacy):
            _raise_and_audit(status_code=403, detail="ROLE_DENIED")
        if first_actor_id and first_actor_id == principal.subject_id:
            _raise_and_audit(status_code=409, detail="SECOND_ACTOR_REQUIRED")

    else:
        # Default deny-by-default for unknown actions
        _raise_and_audit(status_code=403, detail="ROLE_DENIED")

    # Mandatory immutable audit recording for all authorized intake actions
    if audit_log is not None:
        action_enum = map_action_to_enum(action)
        desc = ResourceDescriptor(
            type=res_type,
            resource_id=res_id,
            tenant_id=target_tenant,
        )
        access = AccessRequest(
            principal=principal,
            action=action_enum,
            resource=desc,
            environment=Environment(
                source_ip=source_ip,
                attributes={"correlation_id": correlation_id or "unknown"},
            ),
        )
        decision = (
            tenant_decision
            if "cross_tenant_waiver" in tenant_decision.obligations
            else Decision.allow("authorized")
        )
        audit_log.record(build_security_event(access, decision))





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
    # Canonical field classifications may exceed RESTRICTED; never skip the
    # per-field decision merely because the principal has a high clearance.
    masked = intake.copy()

    # Mask the canonical v1 FieldValue collection according to each field's
    # declared classification. Field names are not a safe substitute for the
    # classification carried by the contract.
    if "fields" in masked and not isinstance(masked["fields"], list) and clearance < DataClassification.RESTRICTED:
        masked["fields"] = None
    if "fields" in masked and isinstance(masked["fields"], list):
        fields = []
        masked_field_paths = list(masked.get("masked_fields") or [])
        for field_info in masked["fields"]:
            if not isinstance(field_info, dict):
                # No classification contract: legacy malformed values stay
                # restricted, just like other unknown import columns.
                fields.append(field_info if clearance >= DataClassification.RESTRICTED else None)
                continue

            field_info_copy = field_info.copy()
            raw_classification = field_info_copy.get("classification", "RESTRICTED")
            if isinstance(raw_classification, DataClassification):
                field_class = raw_classification
            elif isinstance(raw_classification, str):
                field_class = DataClassification.__members__.get(
                    raw_classification.upper(), DataClassification.HIGHLY_RESTRICTED
                )
            else:
                # Never recursively stringify untrusted containers or coerce
                # runtime types into a classification declaration.
                field_class = DataClassification.HIGHLY_RESTRICTED
            if "classification" in field_info_copy and not isinstance(
                raw_classification, (str, DataClassification)
            ):
                # Container metadata must be safe even at maximum clearance;
                # keep legitimate/unknown string labels at that clearance.
                field_info_copy["classification"] = field_class.name

            if clearance < field_class:
                # Classification/path metadata must not smuggle containers or
                # unknown classification text around the value projection.
                field_info_copy = {"field_path": field_info_copy["field_path"]} if isinstance(field_info_copy.get("field_path"), str) else {}
                if "classification" in field_info:
                    field_info_copy["classification"] = field_class.name
                for value_key in ("parsed", "normalized", "corrected", "effective"):
                    field_info_copy[value_key] = None
                field_info_copy["masked"] = True
                field_info_copy["mask_reason_code"] = "FIELD_MASKED"
                field_path = field_info_copy.get("field_path")
                if field_path and field_path not in masked_field_paths:
                    masked_field_paths.append(field_path)

            fields.append(field_info_copy)
        masked["fields"] = fields
        masked["masked_fields"] = masked_field_paths

    # Mask parsedFields
    if "parsedFields" in masked and isinstance(masked["parsedFields"], dict):
        parsed = {}
        for key, field_info in masked["parsedFields"].items():
            if not isinstance(field_info, dict):
                parsed[key] = field_info if clearance >= DataClassification.RESTRICTED else None
                continue
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
                # Keep only the server-defined schema identity needed by the
                # evidence panel. Never copy client key/label or other metadata:
                # they may contain the very values this projection must hide.
                field_info_copy = {"key": key, "label": key}
                field_info_copy["sourceValue"] = None
                field_info_copy["normalizedValue"] = None
                field_info_copy["correctedValue"] = None
                field_info_copy["masked"] = True
                field_info_copy["mask_reason_code"] = "FIELD_MASKED"

            parsed[key] = field_info_copy
        masked["parsedFields"] = parsed
    elif "parsedFields" in masked and clearance < DataClassification.RESTRICTED:
        masked["parsedFields"] = None

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


def mask_batch_intake_job(principal: Principal, job: dict[str, Any]) -> dict[str, Any]:
    """Apply intake field policy to raw batch rows and unstructured errors.

    Unknown import columns and free-form diagnostics have no classification
    contract, so they stay restricted. Mask copies only: job payloads can alias
    the in-memory queue's authoritative record.
    """
    clearance = principal.scope.clearance if principal.authenticated else DataClassification.PUBLIC
    # Clone JSON containers iteratively: deep canonical structures must not
    # exhaust the Python stack, and no response mask may alter stored records.
    masked: dict[str, Any] = {}
    memo: dict[int, Any] = {id(job): masked}
    pending: list[tuple[Any, Any]] = [(job, masked)]
    while pending:
        source, target = pending.pop()
        entries = source.items() if isinstance(source, dict) else enumerate(source)
        for key, value in entries:
            if isinstance(value, (dict, list)):
                if id(value) not in memo:
                    memo[id(value)] = {} if isinstance(value, dict) else []
                    pending.append((value, memo[id(value)]))
                copied = memo[id(value)]
            else:
                copied = deepcopy(value)
            if isinstance(target, dict):
                target[key] = copied
            else:
                target.append(copied)
    # Canonical fields can occur in rows, envelopes, receipts or summaries.
    # Apply classifications even at RESTRICTED, before the legacy coarse mask.
    # Compile constraints against the UNMODIFIED clone, then apply their union.
    # Never apply an independently copied weaker view after a stricter one.
    changes: dict[int, tuple[dict[str, Any], set[str], dict[str, Any]]] = {}
    rejected_pending: list[Any] = []
    rejected: set[int] = set()
    replaced_edges: list[tuple[Any, Any]] = []
    nodes = list(memo.values())

    def require_projection(original: dict[str, Any], projected: dict[str, Any]) -> None:
        _, drops, puts = changes.setdefault(id(original), (original, set(), {}))
        for key in original.keys() - projected.keys():
            drops.add(key)
            if isinstance(original[key], (dict, list)):
                rejected_pending.append(original[key])
        for key, value in projected.items():
            if key not in original or value is not original[key]:
                if key in original and isinstance(original[key], (dict, list)):
                    rejected_pending.append(original[key])
                # Null/drop requirements dominate any metadata addition.
                if key not in puts or value is None or puts[key] is not None:
                    puts[key] = value

    for node in nodes:
        if not isinstance(node, dict):
            continue
        if "classification" in node:
            require_projection(node, mask_intake(principal, {"fields": [node]})["fields"][0])
        inputs = {key: node[key] for key in ("fields", "parsedFields", "rawSnapshot", "originalUrl") if key in node}
        if not inputs:
            continue
        view = mask_intake(principal, inputs)
        root_view = node.copy()
        for collection, expected in (("fields", list), ("parsedFields", dict)):
            if collection not in node:
                continue
            original, projected = node[collection], view[collection]
            if isinstance(original, expected) and isinstance(projected, expected):
                entries = original.items() if isinstance(original, dict) else enumerate(original)
                for key, member in entries:
                    replacement = projected[key]
                    if isinstance(member, dict) and isinstance(replacement, dict):
                        require_projection(member, replacement)
                    elif replacement is not member:
                        replaced_edges.append((original, key))
                        if isinstance(member, (dict, list)):
                            rejected_pending.append(member)
            else:
                root_view[collection] = projected
        for key, value in view.items():
            if key not in ("fields", "parsedFields"):
                root_view[key] = value
        require_projection(node, root_view)

    # Compile the remaining coarse owning policies BEFORE applying anything.
    # Replacing rows/diagnostics afterwards would leave retained aliases intact.
    if clearance < DataClassification.RESTRICTED:
        require_projection(masked, {**masked, "error_message": None})
        aliases = {
            "address_raw": "address", "addressRaw": "address", "address": "address",
            "rent_per_month": "rent", "rentPerMonth": "rent", "rent": "rent",
            "area_ping": "areaPing", "areaPing": "areaPing", "floor": "floor",
            "coordinates": "coordinates", "rawSnapshot": "rawSnapshot",
            "brokerCompany": "brokerCompany", "contactEmail": "contactEmail",
            "contactPhone": "contactPhone", "brokerEmail": "brokerEmail", "privateNotes": "privateNotes",
        }
        payload = masked.get("payload")
        if isinstance(payload, dict):
            payload_view = payload.copy()
            for field, value in payload.items():
                if field in ("items", "rows") and isinstance(value, list):
                    for index, row in enumerate(value):
                        if not isinstance(row, dict):
                            replaced_edges.append((value, index))
                            if isinstance(row, (dict, list)):
                                rejected_pending.append(row)
                            continue
                        clean = {}
                        for key, raw in row.items():
                            if key == "item_id" or (key == "fields" and isinstance(raw, list)):
                                clean[key] = raw
                            elif key in aliases:
                                canonical = aliases[key]
                                view = mask_intake(principal, {"parsedFields": {canonical: {"sourceValue": raw}}})
                                clean[key] = view["parsedFields"][canonical].get("sourceValue")
                            else:
                                clean[key] = None
                        require_projection(row, clean)
                elif field not in ("tenant_id", "submitter", "actor_role_id", "receipt", "summary", "fields", "masked_fields"):
                    payload_view[field] = None
            require_projection(payload, payload_view)
            receipt = payload.get("receipt")
            if isinstance(receipt, dict) and "items" in receipt:
                items = receipt["items"]
                if not isinstance(items, list):
                    require_projection(receipt, {**receipt, "items": None})
                else:
                    for index, item in enumerate(items):
                        if not isinstance(item, dict):
                            replaced_edges.append((items, index))
                            if isinstance(item, (dict, list)):
                                rejected_pending.append(item)
                            continue
                        if "error" not in item or item["error"] is None:
                            continue
                        error = item["error"]
                        if not isinstance(error, dict):
                            require_projection(item, {**item, "error": None})
                            continue
                        safe_error = {"message": "Item processing failed; details are restricted", "details": None}
                        if isinstance(error.get("code"), str):
                            safe_error["code"] = error["code"]
                        if isinstance(error.get("retryable"), bool):
                            safe_error["retryable"] = error["retryable"]
                        require_projection(error, safe_error)

    # A rejected container taints all its descendant containers as well: a
    # separately retained deep alias cannot bypass removal of its ancestor.
    while rejected_pending:
        node = rejected_pending.pop()
        if id(node) in rejected:
            continue
        rejected.add(id(node))
        values = node.values() if isinstance(node, dict) else node
        rejected_pending.extend(value for value in values if isinstance(value, (dict, list)))
    for original, drops, puts in changes.values():
        for key in drops:
            original.pop(key, None)
        original.update({key: value for key, value in puts.items() if key not in drops})
    for parent, key in replaced_edges:
        # A stronger dictionary projection may have removed this member key.
        # Nulling an invalid edge must not recreate the excluded attribute.
        if isinstance(parent, list) or key in parent:
            parent[key] = None
    for node in nodes:
        entries = list(node.items()) if isinstance(node, dict) else enumerate(node)
        for key, value in entries:
            if isinstance(value, (dict, list)) and id(value) in rejected:
                node[key] = None
    if id(masked) in rejected:
        raise HTTPException(status_code=422, detail="JOB_FIELDS_INVALID")
    return masked
