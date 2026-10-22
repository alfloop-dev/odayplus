"""Tenant-partitioned configuration CAS using the existing document store."""

from __future__ import annotations

from typing import Any

from modules.adlift.domain.campaign import (
    CampaignConfiguration,
    CampaignConflict,
    CampaignUnavailable,
    validate_configuration,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore


class DurableAdLiftCampaignRepository:
    COLLECTION = "adlift.campaign-configurations.v1"

    def __init__(self, store: SqliteDocumentStore | TenantScopedDocumentStore) -> None:
        self._store = store

    @property
    def engine(self) -> Any:
        return self._store.engine

    def for_tenant(self, tenant_id: str) -> DurableAdLiftCampaignRepository:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise CampaignUnavailable("CAMPAIGN_TENANT_SCOPE_REQUIRED")
        tenant_id = tenant_id.strip()
        if isinstance(self._store, TenantScopedDocumentStore):
            if self._store.tenant_id != tenant_id:
                raise CampaignUnavailable("CAMPAIGN_TENANT_BINDING_CONFLICT")
            return self
        return type(self)(TenantScopedDocumentStore(self._store, tenant_id))

    def _tenant(self) -> str:
        if not isinstance(self._store, TenantScopedDocumentStore):
            raise CampaignUnavailable("CAMPAIGN_TENANT_SCOPE_REQUIRED")
        return self._store.tenant_id

    def get(self, campaign_id: str) -> CampaignConfiguration | None:
        tenant = self._tenant()
        row = self._store.get_versioned(self.COLLECTION, campaign_id)
        if row is None:
            return None
        value, revision = row
        return validate_configuration(value, tenant_id=tenant,
                                      campaign_id=campaign_id, revision=revision)

    def compare_and_save(self, value: CampaignConfiguration, *,
                         expected_revision: int | None) -> CampaignConfiguration:
        tenant = self._tenant()
        if expected_revision is not None and (type(expected_revision) is not int
                                             or expected_revision < 1):
            raise CampaignConflict("CAMPAIGN_REVISION_CONFLICT")
        next_revision = 1 if expected_revision is None else expected_revision + 1
        validate_configuration(value, tenant_id=tenant,
                               campaign_id=value.campaign_id, revision=next_revision)
        written = self._store.compare_and_put(self.COLLECTION, value.campaign_id,
                                             value, expected_version=expected_revision)
        if written is None:
            raise CampaignConflict("CAMPAIGN_REVISION_CONFLICT")
        if written != next_revision:
            raise CampaignUnavailable("CAMPAIGN_REVISION_INVALID")
        return value

    def _read_technical(self, collection: str, key: str, cls: Any):
        self._tenant()
        row = self._store.get_versioned(collection, key)
        if row is None:
            return None
        value, version = row
        if not isinstance(value, cls) or type(version) is not int or version < 1:
            raise CampaignUnavailable("CAMPAIGN_TECHNICAL_STATE_INVALID")
        if hasattr(value, "version") and value.version != version:
            raise CampaignUnavailable("CAMPAIGN_TECHNICAL_VERSION_INVALID")
        return value, version

    def _write_technical(self, collection: str, key: str, value: Any, expected: int | None):
        self._tenant()
        written = self._store.compare_and_put(collection, key, value, expected_version=expected)
        if written is None:
            raise CampaignConflict("CAMPAIGN_AUTHORITY_CAS_CONFLICT")
        if written != (1 if expected is None else expected + 1):
            raise CampaignUnavailable("CAMPAIGN_TECHNICAL_VERSION_INVALID")
        return written

    def readiness(self, campaign_id: str, revision: int):
        from modules.adlift.domain.campaign_authority import CampaignReadiness
        return self._read_technical("adlift.campaign-readiness.v1", f"{campaign_id}:{revision}", CampaignReadiness)

    def readiness_rows(self):
        from modules.adlift.domain.campaign_authority import CampaignReadiness

        tenant = self._tenant()
        values = self._store.list_all("adlift.campaign-readiness.v1")
        if any(not isinstance(v, CampaignReadiness) or v.tenant_id != tenant for v in values):
            raise CampaignUnavailable("CAMPAIGN_READINESS_TENANT_BINDING_INVALID")
        return values

    def save_readiness(self, value: Any, *, expected: int | None):
        if value.tenant_id != self._tenant():
            raise CampaignUnavailable("CAMPAIGN_TENANT_BINDING_CONFLICT")
        return self._write_technical("adlift.campaign-readiness.v1",
            f"{value.campaign_id}:{value.configuration_revision}", value, expected)

    def native_round(self, campaign_id: str, revision: int):
        from modules.adlift.domain.campaign_authority import NativeCampaignRound
        return self._read_technical("adlift.campaign-native-round.v1", f"{campaign_id}:{revision}", NativeCampaignRound)

    def save_native_round(self, value: Any, *, expected: int | None):
        if value.binding.tenant_id != self._tenant():
            raise CampaignUnavailable("CAMPAIGN_TENANT_BINDING_CONFLICT")
        return self._write_technical("adlift.campaign-native-round.v1",
            f"{value.binding.campaign_id}:{value.binding.configuration_revision}", value, expected)

    def budget_history(self, campaign_id: str):
        from modules.adlift.domain.campaign_authority import CampaignBudgetHistory
        value = self._read_technical("adlift.campaign-budget-authority.v1", campaign_id, CampaignBudgetHistory)
        if value is not None:
            history, version = value
            if (len(history.authorizations) != version or any(a.revision != i
                    or a.binding.tenant_id != self._tenant() or a.binding.campaign_id != campaign_id
                    for i, a in enumerate(history.authorizations, 1))):
                raise CampaignUnavailable("CAMPAIGN_BUDGET_HISTORY_INVALID")
        return value

    def save_budget_history(self, campaign_id: str, value: Any, *, expected: int | None):
        if (len(value.authorizations) != value.version or any(a.binding.tenant_id != self._tenant()
                or a.binding.campaign_id != campaign_id for a in value.authorizations)):
            raise CampaignUnavailable("CAMPAIGN_BUDGET_HISTORY_INVALID")
        return self._write_technical("adlift.campaign-budget-authority.v1", campaign_id, value, expected)
