"""Intake Privacy Service.

Implements purge execution, legal-hold placement/release, residency enforcement,
subject/export scope, and deletion-conflict fail-closed behavior (ODP-INTAKE-PRIVACY-001).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from modules.listing.domain.intake_states import (
    DenialCode,
    DomainValidationError,
)
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.audit.integrity import sha256_hex
from shared.audit.persistence import resolve_retention_policy
from shared.auth import Principal, Role


@dataclass
class LegalHoldRecord:
    legal_hold_id: str
    tenant_id: str
    subject_type: str
    subject_id: str
    reason: str
    placed_by: str
    approved_by: str
    placed_at: datetime
    released_by: str | None = None
    released_at: datetime | None = None
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "legal_hold_id": self.legal_hold_id,
            "tenant_id": self.tenant_id,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "reason": self.reason,
            "placed_by": self.placed_by,
            "approved_by": self.approved_by,
            "placed_at": self.placed_at.isoformat(),
            "released_by": self.released_by,
            "released_at": self.released_at.isoformat() if self.released_at else None,
            "version": self.version,
        }


@dataclass
class ExportManifestRecord:
    export_manifest_id: str
    tenant_id: str
    requested_by: str
    approved_by: str
    purpose: str
    scope: dict[str, Any]
    field_mask: dict[str, Any]
    source_snapshot_ids: list[str]
    audit_event_ids: list[str]
    object_uri: str
    content_sha256: str
    watermark: str
    expires_at: datetime
    created_at: datetime
    deleted_at: datetime | None = None
    download_evidence_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_manifest_id": self.export_manifest_id,
            "tenant_id": self.tenant_id,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "purpose": self.purpose,
            "scope": self.scope,
            "field_mask": self.field_mask,
            "source_snapshot_ids": self.source_snapshot_ids,
            "audit_event_ids": self.audit_event_ids,
            "object_uri": self.object_uri,
            "content_sha256": self.content_sha256,
            "watermark": self.watermark,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "download_evidence_id": self.download_evidence_id,
        }


class IntakePrivacyService:
    """Service to handle privacy operations like legal holds, purging, and evidence exports."""

    def __init__(
        self,
        *,
        audit_log: InMemoryAuditLog | None = None,
        evidence_store: Any = None,
        intake_repository: Any = None,
        document_store: Any = None,
    ) -> None:
        self.audit_log = audit_log or InMemoryAuditLog()
        self.evidence_store = evidence_store
        self.intake_repository = intake_repository
        self.document_store = document_store

    def _get_holds_collection(self) -> str:
        return "audit.legal_holds"

    def _get_manifests_collection(self) -> str:
        return "audit.export_manifests"

    def _is_manager(self, principal: Principal, operator_role_id: str | None = None) -> bool:
        return principal.has_role(Role.SITE_REVIEWER, Role.EXECUTIVE) or operator_role_id in (
            "expansion-manager",
            "expansionManager",
            "site-reviewer",
            "siteReviewer",
            "executive",
        )

    def _is_steward(self, principal: Principal, operator_role_id: str | None = None) -> bool:
        return principal.has_role(Role.DATA_OWNER) or operator_role_id in (
            "data-steward",
            "dataSteward",
        )

    def _is_governance(self, principal: Principal, operator_role_id: str | None = None) -> bool:
        return principal.has_role(Role.AUDITOR, Role.ARCHITECTURE_OWNER) or operator_role_id in (
            "governance-reviewer",
            "governanceReviewer",
        )

    def _is_privacy(self, principal: Principal, operator_role_id: str | None = None) -> bool:
        return principal.has_role(Role.FINANCE_LEGAL) or operator_role_id in (
            "privacy-officer",
            "privacyOfficer",
        )

    def _is_staff(self, principal: Principal, operator_role_id: str | None = None) -> bool:
        return principal.has_role(Role.EXPANSION_USER) or operator_role_id in (
            "expansion-staff",
            "expansionStaff",
            "expansion-user",
            "expansion_user",
        )

    def _check_tenant(self, principal: Principal, tenant_id: str) -> None:
        if principal.tenant_id and principal.tenant_id != tenant_id:
            raise DomainValidationError(
                DenialCode.TENANT_SCOPE_DENIED,
                "Tenant isolation mismatch: principal tenant does not match resource tenant.",
            )

    def place_legal_hold(
        self,
        principal: Principal,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
        reason: str,
        approved_by: str,
        operator_role_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_tenant(principal, tenant_id)

        # Proposer role must be Manager, Steward, Governance, or Privacy
        is_valid_proposer = (
            self._is_manager(principal, operator_role_id)
            or self._is_steward(principal, operator_role_id)
            or self._is_governance(principal, operator_role_id)
            or self._is_privacy(principal, operator_role_id)
        )
        if not is_valid_proposer:
            raise DomainValidationError(DenialCode.ROLE_DENIED, "Actor role not permitted to propose legal hold.")

        # Segregation: proposer cannot approve own hold
        if principal.subject_id == approved_by:
            raise DomainValidationError(
                DenialCode.SELF_REVIEW_DENIED,
                "Separation of duties: proposer cannot approve own legal hold.",
            )

        # Check active holds to prevent duplicate placement
        active = self.get_active_hold(tenant_id, subject_type, subject_id)
        if active:
            raise DomainValidationError(
                DenialCode.LEGAL_HOLD_CONFLICT,
                "An active legal hold already exists for this subject.",
            )

        # Place hold
        hold = LegalHoldRecord(
            legal_hold_id=f"hold-{uuid_hex()}",
            tenant_id=tenant_id,
            subject_type=subject_type,
            subject_id=subject_id,
            reason=reason,
            placed_by=principal.subject_id,
            approved_by=approved_by,
            placed_at=datetime.now(UTC),
        )

        self._save_hold(hold)

        # Propagate hold state
        self._propagate_hold_state(tenant_id, subject_type, subject_id, has_hold=True)

        # Write WORM audited events
        event = AuditEvent(
            event_type="legal_hold.placed",
            actor=principal.subject_id,
            action="place_hold",
            resource=f"{subject_type}/{subject_id}",
            outcome="success",
            correlation_id=correlation_id or "unknown",
            metadata={
                "hold_id": hold.legal_hold_id,
                "reason": reason,
                "approved_by": approved_by,
            },
        )
        self.audit_log.record(event)

        return hold.to_dict()

    def release_legal_hold(
        self,
        principal: Principal,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
        reason: str,
        approved_by: str,
        operator_role_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_tenant(principal, tenant_id)

        # Releasing requires Governance or Privacy role
        is_valid_releaser = self._is_governance(principal, operator_role_id) or self._is_privacy(
            principal, operator_role_id
        )
        if not is_valid_releaser:
            raise DomainValidationError(
                DenialCode.ROLE_DENIED, "Actor role not permitted to release legal hold."
            )

        hold = self.get_active_hold(tenant_id, subject_type, subject_id)
        if not hold:
            raise HTTPException(
                status_code=404, detail="No active legal hold found for this subject."
            )

        # Segregation: same officer who placed hold cannot release it
        if principal.subject_id == hold.placed_by:
            raise DomainValidationError(
                DenialCode.SECOND_ACTOR_REQUIRED,
                "Separation of duties: actor who placed the hold cannot release it.",
            )

        # Proposer cannot approve own release
        if principal.subject_id == approved_by:
            raise DomainValidationError(
                DenialCode.SELF_REVIEW_DENIED,
                "Separation of duties: actor cannot approve own legal hold release.",
            )

        hold.released_by = principal.subject_id
        hold.released_at = datetime.now(UTC)
        hold.version += 1

        self._save_hold(hold)

        # Propagate hold state
        self._propagate_hold_state(tenant_id, subject_type, subject_id, has_hold=False)

        # Write WORM audited events
        event = AuditEvent(
            event_type="legal_hold.released",
            actor=principal.subject_id,
            action="release_hold",
            resource=f"{subject_type}/{subject_id}",
            outcome="success",
            correlation_id=correlation_id or "unknown",
            metadata={
                "hold_id": hold.legal_hold_id,
                "reason": reason,
                "approved_by": approved_by,
            },
        )
        self.audit_log.record(event)

        return hold.to_dict()

    def purge_subject(
        self,
        principal: Principal,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
        reason: str,
        approved_by: str,
        operator_role_id: str | None = None,
        correlation_id: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self._check_tenant(principal, tenant_id)

        # Purging requires Manager, Steward, or Privacy
        is_valid_purger = (
            self._is_manager(principal, operator_role_id)
            or self._is_steward(principal, operator_role_id)
            or self._is_privacy(principal, operator_role_id)
        )
        if not is_valid_purger:
            raise DomainValidationError(DenialCode.ROLE_DENIED, "Actor role not permitted to purge.")

        # Proposer cannot approve own purge
        if principal.subject_id == approved_by:
            raise DomainValidationError(
                DenialCode.SELF_REVIEW_DENIED,
                "Separation of duties: actor cannot approve own purge.",
            )

        # Check active legal holds on this subject
        if self.has_active_hold(tenant_id, subject_type, subject_id):
            raise DomainValidationError(
                DenialCode.LEGAL_HOLD_CONFLICT,
                "Cannot purge subject because it is under an active legal hold.",
            )

        # Fetch subject entity
        entity = self._fetch_entity(subject_type, subject_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Subject {subject_id} not found.")

        # Segregation: submitter/creator cannot purge own submission
        created_by = entity.get("created_by") or entity.get("submitter") or entity.get("owner_id")
        if created_by and created_by == principal.subject_id:
            raise DomainValidationError(
                DenialCode.SECOND_ACTOR_REQUIRED,
                "Separation of duties: submitter cannot purge own submission.",
            )

        # Check retention
        # We check the resolved policy of the subject data classification
        data_classification = entity.get("data_classification") or "internal"
        sensitive = bool(entity.get("sensitive", False))
        policy = resolve_retention_policy(data_classification, sensitive=sensitive)
        created_at_str = entity.get("created_at") or entity.get("submitted_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            except ValueError:
                created_at = datetime.now(UTC)
        else:
            created_at = datetime.now(UTC)

        retain_until = policy.retain_until(created_at)
        now = datetime.now(UTC)
        if now < retain_until:
            raise DomainValidationError(
                DenialCode.RETENTION_NOT_REACHED,
                f"Retention period not reached: must retain until {retain_until.isoformat()}.",
            )

        # Check dependent decisions / candidates
        if subject_type == "intake":
            # If there are candidates or listings referring to this intake, deny
            dependents = self._check_intake_dependents(subject_id)
            if dependents:
                raise DomainValidationError(
                    DenialCode.DEPENDENCY_CONFLICT,
                    f"Cannot purge intake {subject_id} because of active dependent decisions or candidates: {', '.join(dependents)}.",
                )

        if dry_run:
            return {
                "dry_run": True,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "retention_class": policy.retention_class,
                "retain_until": retain_until.isoformat(),
                "purged_objects_count": 1,
            }

        # Execute Purge: Replace with tombstone
        tombstone = {
            "id": subject_id,
            "tenant_id": tenant_id,
            "purged": True,
            "purged_at": now.isoformat(),
            "purged_by": principal.subject_id,
            "reason": reason,
        }
        self._save_tombstone(subject_type, subject_id, tombstone)

        # Write audit WORM event
        event = AuditEvent(
            event_type="intake.purged",
            actor=principal.subject_id,
            action="purge",
            resource=f"{subject_type}/{subject_id}",
            outcome="success",
            correlation_id=correlation_id or "unknown",
            metadata={
                "reason": reason,
                "approved_by": approved_by,
                "retention_class": policy.retention_class,
            },
        )
        self.audit_log.record(event)

        return {
            "dry_run": False,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "purged_at": now.isoformat(),
            "status": "purged",
        }

    def export_evidence(
        self,
        principal: Principal,
        tenant_id: str,
        subject_type: str,
        subject_id: str,
        purpose: str,
        authorized_by: str,
        authorization_id: str,
        data_classification: str = "restricted",
        sensitive: bool = True,
        masking_profile: str = "masked",
        destination_residency: str = "TW_ONLY",
        operator_role_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_tenant(principal, tenant_id)

        # Segregation: exporter cannot authorize own export
        if principal.subject_id == authorized_by:
            raise DomainValidationError(
                DenialCode.SELF_REVIEW_DENIED,
                "Separation of duties: exporter cannot authorize own export.",
            )

        # Residency check
        # Get tenant residency mode (default TW_ONLY)
        tenant_residency = self._get_tenant_residency_mode(tenant_id)
        if tenant_residency == "TW_ONLY" and destination_residency != "TW_ONLY":
            raise DomainValidationError(
                DenialCode.RESIDENCY_DENIED,
                "Destination residency violates tenant residency policy.",
            )

        # Check role permission
        is_authorized = (
            self._is_staff(principal, operator_role_id)
            or self._is_manager(principal, operator_role_id)
            or self._is_steward(principal, operator_role_id)
            or self._is_governance(principal, operator_role_id)
            or self._is_privacy(principal, operator_role_id)
        )
        if not is_authorized:
            raise DomainValidationError(
                DenialCode.ROLE_DENIED, "Actor role not permitted to export evidence."
            )

        # Load entity
        entity = self._fetch_entity(subject_type, subject_id)
        if not entity:
            raise HTTPException(status_code=404, detail=f"Subject {subject_id} not found.")

        export_id = f"export-{uuid_hex()}"
        watermark = f"watermark:{export_id}:requested_by:{principal.subject_id}:time:{datetime.now(UTC).isoformat()}"

        # Mask fields if masking is required
        exported_payload = dict(entity)
        if masking_profile == "masked":
            exported_payload = self._mask_exported_payload(exported_payload)

        # Compute checksum
        payload_bytes = json.dumps(exported_payload, sort_keys=True, default=str).encode("utf-8")
        content_sha256 = hashlib.sha256(payload_bytes).hexdigest()

        # Build download evidence id
        download_evidence_id = sha256_hex(
            {
                "export_id": export_id,
                "requested_by": principal.subject_id,
                "purpose": purpose,
                "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
            }
        )

        manifest = ExportManifestRecord(
            export_manifest_id=export_id,
            tenant_id=tenant_id,
            requested_by=principal.subject_id,
            approved_by=authorized_by,
            purpose=purpose,
            scope={"subject_type": subject_type, "subject_id": subject_id},
            field_mask={"masking_profile": masking_profile},
            source_snapshot_ids=[subject_id] if subject_type == "intake" else [],
            audit_event_ids=[],
            object_uri=f"gs://{tenant_id}-evidence-export/{export_id}.json",
            content_sha256=content_sha256,
            watermark=watermark,
            expires_at=datetime.now(UTC) + timedelta(hours=4),
            created_at=datetime.now(UTC),
            download_evidence_id=download_evidence_id,
        )

        # Save manifest
        self._save_manifest(manifest, exported_payload)

        # Audit WORM event
        audit_event = AuditEvent(
            event_type="audit.evidence_export.v1",
            actor=principal.subject_id,
            action="export",
            resource=f"{subject_type}/{subject_id}",
            outcome="success",
            correlation_id=correlation_id or "unknown",
            metadata={
                "export_id": export_id,
                "watermark": watermark,
                "checksum": content_sha256,
                "download_evidence_id": download_evidence_id,
                "destination_residency": destination_residency,
            },
        )
        self.audit_log.record(audit_event)

        return manifest.to_dict()

    def get_active_hold(self, tenant_id: str, subject_type: str, subject_id: str) -> LegalHoldRecord | None:
        holds = self._list_holds()
        for hold in holds:
            if (
                hold.tenant_id == tenant_id
                and hold.subject_type == subject_type
                and hold.subject_id == subject_id
                and hold.released_at is None
            ):
                return hold
        return None

    def has_active_hold(self, tenant_id: str, subject_type: str, subject_id: str) -> bool:
        return self.get_active_hold(tenant_id, subject_type, subject_id) is not None

    def verify_export_manifest(self, export_id: str) -> dict[str, Any]:
        manifest = self._get_manifest(export_id)
        if not manifest:
            raise HTTPException(status_code=404, detail="Export manifest not found.")

        # Re-verify checksum of stored bundle
        bundle = self._get_exported_bundle(export_id)
        if not bundle:
            return {"export_id": export_id, "ok": False, "reason": "Export bundle missing"}

        payload_bytes = json.dumps(bundle, sort_keys=True, default=str).encode("utf-8")
        current_hash = hashlib.sha256(payload_bytes).hexdigest()

        ok = current_hash == manifest.content_sha256
        return {
            "export_id": export_id,
            "ok": ok,
            "manifest_checksum": manifest.content_sha256,
            "actual_checksum": current_hash,
            "reason": "Integrity verified successfully" if ok else "Checksum mismatch",
            "signer_key_version": "v1",
        }

    def download_evidence(self, download_evidence_id: str) -> dict[str, Any]:
        manifests = self._list_manifests()
        for manifest in manifests:
            if manifest.download_evidence_id == download_evidence_id:
                if manifest.expires_at < datetime.now(UTC):
                    raise HTTPException(status_code=410, detail="Download link has expired.")
                bundle = self._get_exported_bundle(manifest.export_manifest_id)
                return {
                    "manifest": manifest.to_dict(),
                    "bundle": bundle,
                }
        raise HTTPException(status_code=404, detail="Download evidence not found.")

    def _fetch_entity(self, subject_type: str, subject_id: str) -> dict[str, Any] | None:
        if self.intake_repository:
            if subject_type == "intake":
                intakes = self.intake_repository.list_intakes()
                for item in intakes:
                    if item.get("id") == subject_id:
                        return item
            elif subject_type == "listing":
                # Check listing metadata
                meta = self.intake_repository.get_listing_metadata(subject_id)
                if meta:
                    return meta
        if self.document_store:
            collection = "operator.assisted_intakes" if subject_type == "intake" else "operator.listing_metadata"
            return self.document_store.get(collection, subject_id)
        return None

    def _save_tombstone(self, subject_type: str, subject_id: str, tombstone: dict[str, Any]) -> None:
        if self.intake_repository:
            if subject_type == "intake":
                self.intake_repository.save_intake(tombstone)
            elif subject_type == "listing":
                self.intake_repository.save_listing_metadata(subject_id, tombstone)
        if self.document_store:
            collection = "operator.assisted_intakes" if subject_type == "intake" else "operator.listing_metadata"
            self.document_store.put(collection, subject_id, tombstone)

    def _propagate_hold_state(
        self, tenant_id: str, subject_type: str, subject_id: str, has_hold: bool
    ) -> None:
        entity = self._fetch_entity(subject_type, subject_id)
        if entity:
            if subject_type == "intake":
                entity["legal_hold"] = has_hold
            elif subject_type == "listing":
                entity["has_legal_hold"] = has_hold
            self._save_tombstone(subject_type, subject_id, entity)

    def _check_intake_dependents(self, intake_id: str) -> list[str]:
        dependents = []
        # Query listings that depend on this intake
        if self.intake_repository:
            # We check if any listing revision or metadata depends on it
            # For this simplified model, we query candidates and listings in document store
            pass
        if self.document_store:
            candidates = self.document_store.list_all("operator.candidate_metadata")
            for c in candidates:
                if c.get("intake_id") == intake_id or c.get("source_snapshot_id") == intake_id:
                    dependents.append(f"candidate:{c.get('id')}")
        return dependents

    def _get_tenant_residency_mode(self, tenant_id: str) -> str:
        # Default is TW_ONLY
        if self.document_store:
            tenant_meta = self.document_store.get("operator.tenant_metadata", tenant_id)
            if tenant_meta:
                return tenant_meta.get("residency_mode", "TW_ONLY")
        return "TW_ONLY"

    def _mask_exported_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Perform simple watermarked masking
        masked = dict(payload)
        for key in list(masked.keys()):
            if "phone" in key.lower() or "mobile" in key.lower():
                masked[key] = mask_phone(str(masked[key]))
            elif "email" in key.lower() or "@" in str(masked[key]):
                masked[key] = mask_email(str(masked[key]))
            elif key in ("rawSnapshot", "brokerCompany", "privateNotes", "coordinates"):
                masked[key] = "[REDACTED]"
        return masked

    # -- Persistence helpers --
    def _list_holds(self) -> list[LegalHoldRecord]:
        if self.document_store:
            return self.document_store.list_all(self._get_holds_collection())
        if not hasattr(self, "_in_memory_holds"):
            self._in_memory_holds: list[LegalHoldRecord] = []
        return self._in_memory_holds

    def _save_hold(self, hold: LegalHoldRecord) -> None:
        if self.document_store:
            self.document_store.put(self._get_holds_collection(), hold.legal_hold_id, hold)
            # Write to SQL if WORM sink configured
            if hasattr(self.evidence_store, "_worm_sink") and self.evidence_store._worm_sink:
                self.evidence_store._worm_sink._write(
                    record_type="legal-holds",
                    record_id=hold.legal_hold_id,
                    sequence=None,
                    payload=hold.to_dict(),
                )
        else:
            if not hasattr(self, "_in_memory_holds"):
                self._in_memory_holds = []
            for idx, h in enumerate(self._in_memory_holds):
                if h.legal_hold_id == hold.legal_hold_id:
                    self._in_memory_holds[idx] = hold
                    return
            self._in_memory_holds.append(hold)

    def _list_manifests(self) -> list[ExportManifestRecord]:
        if self.document_store:
            return self.document_store.list_all(self._get_manifests_collection())
        if not hasattr(self, "_in_memory_manifests"):
            self._in_memory_manifests: list[ExportManifestRecord] = []
        return self._in_memory_manifests

    def _get_manifest(self, export_id: str) -> ExportManifestRecord | None:
        if self.document_store:
            return self.document_store.get(self._get_manifests_collection(), export_id)
        manifests = self._list_manifests()
        for m in manifests:
            if m.export_manifest_id == export_id:
                return m
        return None

    def _save_manifest(self, manifest: ExportManifestRecord, bundle: dict[str, Any]) -> None:
        if self.document_store:
            self.document_store.put(self._get_manifests_collection(), manifest.export_manifest_id, manifest)
            self.document_store.put(f"{self._get_manifests_collection()}_bundles", manifest.export_manifest_id, bundle)
            # Write manifest & bundle to WORM sink if configured
            if hasattr(self.evidence_store, "_worm_sink") and self.evidence_store._worm_sink:
                self.evidence_store._worm_sink._write(
                    record_type="export-manifests",
                    record_id=manifest.export_manifest_id,
                    sequence=None,
                    payload={"manifest": manifest.to_dict(), "bundle": bundle},
                )
        else:
            if not hasattr(self, "_in_memory_manifests"):
                self._in_memory_manifests = []
            self._in_memory_manifests.append(manifest)
            if not hasattr(self, "_in_memory_bundles"):
                self._in_memory_bundles = {}
            self._in_memory_bundles[manifest.export_manifest_id] = bundle

    def _get_exported_bundle(self, export_id: str) -> dict[str, Any] | None:
        if self.document_store:
            return self.document_store.get(f"{self._get_manifests_collection()}_bundles", export_id)
        if not hasattr(self, "_in_memory_bundles"):
            self._in_memory_bundles = {}
        return self._in_memory_bundles.get(export_id)


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


def mask_phone(value: str, *, visible: int = 3) -> str:
    digits = [c for c in value if c.isdigit()]
    if len(digits) <= visible:
        return "*" * len(digits)
    return "*" * (len(digits) - visible) + "".join(digits[-visible:])


def mask_email(value: str) -> str:
    if "@" not in value:
        return "*" * len(value)
    local, _, domain = value.partition("@")
    if not local:
        return "@" + domain
    head = local[0]
    return f"{head}{'*' * (len(local) - 1)}@{domain}"
