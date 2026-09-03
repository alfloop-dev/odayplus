"""Integration and regression tests for ODP-INT001 CDC disposition (ODP-INT001-CDC-DISPOSITION-001).

Acceptance checks:
1. ODP-FR-INT-001 requirement members are governed in set_valued_requirements.json with exact 5 members.
2. Satisfied members (BATCH, API, FILE) resolve to genuine codebase symbols.
3. Absent members (EVENT, CDC) carry auditable OPEN dispositions and NO AI self-signed waivers.
4. Contract integration mode taxonomy maintains integrity and excludes unapproved CDC modes.
5. Production credential and source database boundaries fail closed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.data_platform.config import DataPlaneConfig, DataPlaneConfigurationError
from delivery_toolchain.governance.check_requirement_members import (
    MANIFEST_PATH,
    REPO_ROOT,
    check,
    is_ai_decider,
    resolve,
)
from modules.external_data.connectors.provider_registry import (
    ExternalProviderMode,
    PROVIDER_REGISTRY,
    provider_registry,
)
from modules.integration.domain.contracts import load_index


class TestInt001RequirementDisposition:
    """Verifies that INT-001 / ODP-FR-INT-001 is fully governed in the set-valued manifest."""

    def test_odp_fr_int_001_passes_governance_checker(self) -> None:
        failures, tally = check(REPO_ROOT, MANIFEST_PATH)
        int_001_failures = [f for f in failures if f.requirement == "ODP-FR-INT-001"]
        assert not int_001_failures, "\n".join(f.describe() for f in int_001_failures)

    def test_odp_fr_int_001_members_and_statuses(self) -> None:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        req = next(r for r in payload["requirements"] if r["id"] == "ODP-FR-INT-001")
        assert req["member_count"] == 5
        assert len(req["members"]) == 5

        members = {m["name"]: m for m in req["members"]}
        assert set(members.keys()) == {"BATCH", "API", "FILE", "EVENT", "CDC"}

        # Satisfied members must resolve to real symbols
        for name in ("BATCH", "API", "FILE"):
            member = members[name]
            assert member["status"] == "satisfied"
            assert "evidence" in member
            err = resolve(REPO_ROOT, member["evidence"])
            assert err is None, f"Evidence for {name} failed to resolve: {err}"
            assert member.get("disposition", {}).get("state") == "VERIFIED"

        # Absent members must have notes and valid OPEN dispositions
        for name in ("EVENT", "CDC"):
            member = members[name]
            assert member["status"] == "absent"
            assert member.get("note", "").strip()
            disp = member.get("disposition", {})
            assert disp.get("state") == "OPEN"
            assert disp.get("assigned_to")
            assert disp.get("rationale")
            assert disp.get("next_review_date")

    def test_cdc_disposition_forbids_ai_self_signed_waiver(self) -> None:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        req = next(r for r in payload["requirements"] if r["id"] == "ODP-FR-INT-001")
        cdc = next(m for m in req["members"] if m["name"] == "CDC")
        disp = cdc["disposition"]

        # If a decider is declared, it must not be an AI
        decider = disp.get("decider")
        if decider:
            assert not is_ai_decider(decider), f"AI decider {decider!r} is forbidden on CDC disposition"

    def test_governance_document_mirrors_int_001_disposition(self) -> None:
        policy_path = REPO_ROOT / "docs" / "governance" / "ODP_REQUIREMENT_DISPOSITIONS.md"
        assert policy_path.is_file()
        content = policy_path.read_text(encoding="utf-8")
        assert "ODP-FR-INT-001" in content
        assert "BATCH" in content
        assert "API" in content
        assert "FILE" in content
        assert "EVENT" in content
        assert "CDC" in content


class TestIngestionContractsTaxonomyAndBoundaries:
    """Verifies that schema contracts do not declare unapproved CDC modes without upstream."""

    def test_source_contract_taxonomy_excludes_unsupported_cdc(self) -> None:
        index = load_index()
        integration_modes = set(index["integration_modes"])
        # Standard supported operational taxonomy
        assert integration_modes == {
            "batch_snapshot",
            "incremental_batch",
            "event_stream",
            "backfill",
            "api_lookup",
        }
        # Unapproved/mock CDC is not in taxonomy
        assert "cdc" not in integration_modes

    def test_all_external_providers_in_registry_are_governed(self) -> None:
        providers = provider_registry()
        assert len(providers) >= 6
        provider_ids = {p.provider_id for p in providers}
        assert "listing.partner_feed" in provider_ids
        assert "poi.commercial_api" in provider_ids
        assert "geocode.primary_api" in provider_ids
        assert "admin_boundary.official_dataset" in provider_ids
        assert "store_opening_authority" in provider_ids


class TestDataPlaneFailClosedBoundaries:
    """Verifies that data plane and provider boundaries fail closed upon invalid configurations."""

    def test_data_plane_config_rejects_non_production_or_wrong_db(self) -> None:
        # Non-production env is rejected
        cfg_staging = DataPlaneConfig(
            environment="staging",
            mongo_uri="mongodb://prod-mongo:27017",
            mongo_database="fongniao_prod",
            postgres_dsn="postgresql://user:pass@prod-pg:5432/odp",
        )
        with pytest.raises(DataPlaneConfigurationError, match="production-only"):
            cfg_staging.validate()

        # Non fongniao_prod db is rejected
        cfg_wrong_db = DataPlaneConfig(
            environment="production",
            mongo_uri="mongodb://prod-mongo:27017",
            mongo_database="other_db",
            postgres_dsn="postgresql://user:pass@prod-pg:5432/odp",
        )
        with pytest.raises(DataPlaneConfigurationError, match="fongniao_prod"):
            cfg_wrong_db.validate()

        # Localhost mongo is rejected
        cfg_local = DataPlaneConfig(
            environment="production",
            mongo_uri="mongodb://127.0.0.1:27017",
            mongo_database="fongniao_prod",
            postgres_dsn="postgresql://user:pass@prod-pg:5432/odp",
        )
        with pytest.raises(DataPlaneConfigurationError, match="Local MongoDB is not a production source"):
            cfg_local.validate()
