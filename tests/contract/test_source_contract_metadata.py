"""Tests for Source Contract Catalog Metadata Alignment and Runtime Capability Decoupling.

Exercises the deliverables of ODP-DATA-CATALOG-METADATA-ALIGNMENT-001:
  * Backward-compatible addition of data_owner, target_latency_sla, contact_channel / contact_ref.
  * Explicit unconfirmed / unknown representation for absent or unconfirmed values.
  * Validation rules rejecting illegal latency values, blank fake owners, and unknown runtime capabilities.
  * Machine-readable decoupling between declared integration_mode (e.g. event_stream) and
    actual runtime capability (e.g. batch_watermark_only) for machine_status_event without
    removing or waiving the CDC/event requirement.
  * Registry index taxonomy consistency.
"""

from __future__ import annotations

import pytest

from modules.external_data.application.external_contracts import external_contracts
from modules.integration.application.internal_contracts import (
    internal_contract,
    internal_contracts,
)
from modules.integration.domain.contracts import (
    RUNTIME_CAPABILITIES,
    UNCONFIRMED_METADATA,
    ContractError,
    SourceContract,
    load_index,
)


def _sample_raw_contract_dict(**kwargs) -> dict:
    base = {
        "contract_id": "sample_test_contract",
        "title": "Sample Test Contract",
        "kind": "internal",
        "source_system": "sample_sys",
        "source_dataset": "sample_ds",
        "canonical_target": "store",
        "mapping_id": "MAP-SAMPLE-v1",
        "integration_mode": "batch_snapshot",
        "envelope": "batch",
        "fields": [
            {"name": "id", "type": "string", "required": True},
        ],
    }
    base.update(kwargs)
    return base


# --- 1. Catalog metadata presence on all registered contracts ----------------


def test_all_registered_contracts_have_catalog_metadata() -> None:
    all_contracts = internal_contracts() + external_contracts()
    assert len(all_contracts) >= 16, "expected at least 16 source contracts in registry"

    for contract in all_contracts:
        assert isinstance(contract.data_owner, str)
        assert len(contract.data_owner.strip()) > 0

        assert isinstance(contract.target_latency_sla, str)
        assert len(contract.target_latency_sla.strip()) > 0

        assert isinstance(contract.contact_channel, str)
        assert len(contract.contact_channel.strip()) > 0

        # Alias contact_ref and contact_reference
        assert contract.contact_ref == contract.contact_channel
        assert contract.contact_reference == contract.contact_channel

        assert isinstance(contract.runtime_capability, str)
        assert contract.runtime_capability in RUNTIME_CAPABILITIES


# --- 2. Backward compatibility when metadata fields are omitted --------------


def test_backward_compatibility_omitted_metadata() -> None:
    data = _sample_raw_contract_dict()
    contract = SourceContract.from_dict(data)

    assert contract.data_owner == UNCONFIRMED_METADATA
    assert contract.target_latency_sla == UNCONFIRMED_METADATA
    assert contract.contact_channel == UNCONFIRMED_METADATA
    assert contract.runtime_capability == "unverified"

    assert contract.is_data_owner_confirmed is False
    assert contract.is_latency_sla_confirmed is False
    assert contract.is_contact_confirmed is False
    assert contract.is_runtime_verified is False


# --- 3. Distinction between unconfirmed/unknown and confirmed values ---------


def test_unconfirmed_vs_confirmed_distinction() -> None:
    # Unconfirmed cases
    for marker in ("unconfirmed", "unknown", "unspecified"):
        c_unconfirmed = SourceContract.from_dict(
            _sample_raw_contract_dict(
                data_owner=marker,
                target_latency_sla=marker,
                contact_channel=marker,
                runtime_capability="unverified",
            )
        )
        assert c_unconfirmed.is_data_owner_confirmed is False
        assert c_unconfirmed.is_latency_sla_confirmed is False
        assert c_unconfirmed.is_contact_confirmed is False
        assert c_unconfirmed.is_runtime_verified is False

    # Confirmed cases
    c_confirmed = SourceContract.from_dict(
        _sample_raw_contract_dict(
            data_owner="Store Operations Team",
            target_latency_sla="PT1H",
            contact_channel="slack:#store-ops",
            runtime_capability="verified",
        )
    )
    assert c_confirmed.is_data_owner_confirmed is True
    assert c_confirmed.data_owner == "Store Operations Team"
    assert c_confirmed.is_latency_sla_confirmed is True
    assert c_confirmed.target_latency_sla == "PT1H"
    assert c_confirmed.is_contact_confirmed is True
    assert c_confirmed.contact_channel == "slack:#store-ops"
    assert c_confirmed.is_runtime_verified is True


# --- 4. Validation: rejects blank fake owner ---------------------------------


def test_validation_rejects_blank_fake_owner() -> None:
    with pytest.raises(ContractError, match="fake blank owner"):
        SourceContract.from_dict(_sample_raw_contract_dict(data_owner="   "))

    with pytest.raises(ContractError, match="must be a string"):
        SourceContract.from_dict(_sample_raw_contract_dict(data_owner=12345))

    with pytest.raises(ContractError, match="must be a string"):
        SourceContract.from_dict(_sample_raw_contract_dict(data_owner={"team": "ops"}))


# --- 5. Validation: rejects illegal latency values & accepts valid ones ------


@pytest.mark.parametrize(
    "valid_sla",
    [
        "unconfirmed",
        "unknown",
        "unspecified",
        "PT5S",
        "PT15M",
        "PT1H",
        "PT24H",
        "PT1H30M",
        "PT0.5S",
        "P1D",
        "P7D",
        "P1W",
        "P1M",
        "P1Y",
        "P1Y2M3D",
        "P1DT12H",
        "5s",
        "15m",
        "1h",
        "24h",
        "1d",
        "7d",
        "2 weeks",
        "realtime",
        "subsecond",
        "near_realtime",
        "streaming",
        "hourly",
        "daily",
        "weekly",
        "monthly",
        "batch_hourly",
        "batch_daily",
        "on_demand",
    ],
)
def test_validation_accepts_valid_latency_sla(valid_sla: str) -> None:
    c = SourceContract.from_dict(_sample_raw_contract_dict(target_latency_sla=valid_sla))
    assert c.target_latency_sla == valid_sla


@pytest.mark.parametrize(
    "invalid_sla",
    [
        "   ",
        "P",
        "PT",
        "P1",
        "PT1",
        "PTM",
        "PS",
        "-5s",
        "-PT1H",
        "asap",
        "fast",
        "never",
        "instant",
        "invalid_format",
        "12345",  # numbers without unit
    ],
)
def test_validation_rejects_illegal_latency_sla(invalid_sla: str) -> None:
    with pytest.raises(ContractError, match="Invalid target_latency_sla format|empty whitespace"):
        SourceContract.from_dict(_sample_raw_contract_dict(target_latency_sla=invalid_sla))


def test_validation_rejects_empty_iso_duration() -> None:
    """Empty ISO duration markers 'P' and 'PT' without numeric components must be rejected."""
    for empty_val in ("P", "PT", "p", "pt"):
        with pytest.raises(ContractError, match="Invalid target_latency_sla format"):
            SourceContract.from_dict(_sample_raw_contract_dict(target_latency_sla=empty_val))


def test_validation_rejects_non_string_latency_sla() -> None:
    with pytest.raises(ContractError, match="must be a string"):
        SourceContract.from_dict(_sample_raw_contract_dict(target_latency_sla=300))


# --- 6. Validation: contact channel & aliases --------------------------------


def test_validation_rejects_blank_contact_channel() -> None:
    with pytest.raises(ContractError, match="contact_channel cannot be empty whitespace"):
        SourceContract.from_dict(_sample_raw_contract_dict(contact_channel="   "))

    with pytest.raises(ContractError, match="contact_channel must be a string"):
        SourceContract.from_dict(_sample_raw_contract_dict(contact_channel=999))


def test_contact_ref_alias_support() -> None:
    c1 = SourceContract.from_dict(_sample_raw_contract_dict(contact_ref="email:ops@example.com"))
    assert c1.contact_channel == "email:ops@example.com"
    assert c1.contact_ref == "email:ops@example.com"
    assert c1.contact_reference == "email:ops@example.com"

    c2 = SourceContract.from_dict(_sample_raw_contract_dict(contact_reference="slack:#alerts"))
    assert c2.contact_channel == "slack:#alerts"


# --- 7. Validation: runtime capabilities -------------------------------------


def test_validation_rejects_invalid_runtime_capability() -> None:
    with pytest.raises(ContractError, match="Unknown runtime_capability"):
        SourceContract.from_dict(_sample_raw_contract_dict(runtime_capability="magical_streaming"))

    with pytest.raises(ContractError, match="runtime_capability cannot be empty whitespace"):
        SourceContract.from_dict(_sample_raw_contract_dict(runtime_capability="   "))


# --- 8. Machine status event: decoupling event_stream vs batch runtime -------


def test_machine_status_event_decoupling_event_stream_vs_batch_runtime() -> None:
    """Verifies that machine_status_event retains its declared event_stream mode

    without falsely claiming that current batch runtime supports streaming.
    """
    contract = internal_contract("machine_status_event")

    # Declared mode remains event_stream (preserving CDC / streaming requirement)
    assert contract.integration_mode == "event_stream"
    assert contract.envelope == "event"

    # Runtime capability is explicitly recorded as batch_watermark_only
    assert contract.runtime_capability == "batch_watermark_only"

    # False readiness guards:
    assert contract.has_streaming_runtime is False
    assert contract.is_runtime_verified is False
    assert contract.runtime_matches_declared_mode is False


# --- 9. Registry index taxonomy & contract metadata consistency --------------


def test_index_registry_taxonomy_and_contract_metadata_consistency() -> None:
    index = load_index()
    assert "runtime_capabilities" in index
    assert set(index["runtime_capabilities"]) >= RUNTIME_CAPABILITIES

    declared_contracts = {e["contract_id"]: e for e in index["contracts"]}

    for contract in internal_contracts() + external_contracts():
        assert contract.contract_id in declared_contracts
        entry = declared_contracts[contract.contract_id]

        assert entry["data_owner"] == contract.data_owner
        assert entry["target_latency_sla"] == contract.target_latency_sla
        assert entry["contact_channel"] == contract.contact_channel
        assert entry["runtime_capability"] == contract.runtime_capability


# --- 10. Capability x Declared-Mode matching matrix --------------------------


def test_runtime_matches_declared_mode_matrix() -> None:
    """Verifies that runtime_matches_declared_mode correctly evaluates capabilities against declared modes:

    - unsupported and simulated_only never match any declared mode.
    - unverified and unconfirmed never match any declared mode.
    - batch_only and batch_watermark_only match batch modes (batch_snapshot, incremental_batch, backfill)
      but never match api_lookup or event_stream.
    - supported matches batch and api_lookup modes, but not event_stream.
    - streaming_supported matches event_stream, but not batch or api_lookup.
    - verified matches all declared modes.
    - manual_attestation matches manual / backfill / batch modes, but not event_stream.
    """
    all_modes = ["batch_snapshot", "incremental_batch", "event_stream", "backfill", "api_lookup"]

    # 1. Capabilities that NEVER match real runtime for any mode
    for cap in ("unsupported", "simulated_only", "unverified", "unconfirmed"):
        for mode in all_modes:
            c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode=mode, runtime_capability=cap))
            assert c.runtime_matches_declared_mode is False, f"Expected {cap} with {mode} to not match"

    # 2. Batch-only capabilities (batch_only, batch_watermark_only)
    for cap in ("batch_only", "batch_watermark_only"):
        for mode in ("batch_snapshot", "incremental_batch", "backfill"):
            c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode=mode, runtime_capability=cap))
            assert c.runtime_matches_declared_mode is True, f"Expected {cap} with {mode} to match"
        for mode in ("api_lookup", "event_stream"):
            c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode=mode, runtime_capability=cap))
            assert c.runtime_matches_declared_mode is False, f"Expected {cap} with {mode} to not match"

    # 3. Supported capability
    for mode in ("batch_snapshot", "incremental_batch", "backfill", "api_lookup"):
        c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode=mode, runtime_capability="supported"))
        assert c.runtime_matches_declared_mode is True, f"Expected supported with {mode} to match"
    c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode="event_stream", runtime_capability="supported"))
    assert c.runtime_matches_declared_mode is False, "Expected supported with event_stream to require streaming_supported/verified"

    # 4. Streaming-supported capability
    c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode="event_stream", runtime_capability="streaming_supported"))
    assert c.runtime_matches_declared_mode is True
    assert c.has_streaming_runtime is True

    for mode in ("batch_snapshot", "incremental_batch", "backfill", "api_lookup"):
        c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode=mode, runtime_capability="streaming_supported"))
        assert c.runtime_matches_declared_mode is False

    # 5. Verified capability matches all modes
    for mode in all_modes:
        c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode=mode, runtime_capability="verified"))
        assert c.runtime_matches_declared_mode is True, f"Expected verified with {mode} to match"

    # 6. Manual attestation
    for mode in ("backfill", "batch_snapshot", "api_lookup"):
        c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode=mode, runtime_capability="manual_attestation"))
        assert c.runtime_matches_declared_mode is True
    c = SourceContract.from_dict(_sample_raw_contract_dict(integration_mode="event_stream", runtime_capability="manual_attestation"))
    assert c.runtime_matches_declared_mode is False
