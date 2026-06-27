"""Contract tests for source-data ingestion contracts and fixtures.

These tests exercise the ODP-R1-001 deliverable: the source-contract registry
under ``packages/schemas/source_contracts`` (loaded via the Integration Layer /
External Data Platform facades) and the golden fixtures under
``tests/fixtures/source_data``.

They assert that:
  * the registry is internally consistent and maps to known canonical entities;
  * the Batch/CDC/event integration modes and both exchange envelopes are
    represented, and the common envelope retains the required exchange fields;
  * every valid (golden) fixture record passes its contract; and
  * every invalid fixture record is rejected and routed to a quarantine result
    with the expected reason codes (ODP-DATA-05 §8).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.external_data.application.external_contracts import external_contracts
from modules.integration.application.internal_contracts import (
    batch_envelope,
    event_envelope,
    internal_contracts,
)
from modules.integration.domain.contracts import (
    ContractError,
    SourceContract,
    load_envelope,
    load_index,
    validate_record,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "source_data"

_ENVELOPE_BY_ID = {"batch_envelope": "batch", "event_envelope": "event"}


def _resolve_contract(contract_id: str) -> SourceContract:
    envelope_kind = _ENVELOPE_BY_ID.get(contract_id)
    if envelope_kind is not None:
        return load_envelope(envelope_kind)
    return _contract_by_id(contract_id)


def _contract_by_id(contract_id: str) -> SourceContract:
    from modules.integration.domain.contracts import load_contract

    return load_contract(contract_id)


def _load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _valid_fixture_files() -> list[Path]:
    return sorted(FIXTURES_ROOT.rglob("*.valid.json"))


def _invalid_fixture_files() -> list[Path]:
    return sorted(FIXTURES_ROOT.rglob("*.invalid.json"))


def _valid_record_cases() -> list[tuple[str, dict]]:
    cases: list[tuple[str, dict]] = []
    for path in _valid_fixture_files():
        data = _load_fixture(path)
        contract_id = data["contract_id"]
        for i, record in enumerate(data["records"]):
            cases.append((f"{contract_id}#{i}", record))
    return cases


def _invalid_record_cases() -> list[tuple[str, dict, list[str]]]:
    cases: list[tuple[str, dict, list[str]]] = []
    for path in _invalid_fixture_files():
        data = _load_fixture(path)
        contract_id = data["contract_id"]
        for case in data["cases"]:
            label = case["label"].replace(" ", "_")
            cases.append(
                (f"{contract_id}: {label}", case["record"], case["expect_codes"])
            )
    return cases


# --- registry integrity ----------------------------------------------------


def test_registry_index_lists_loadable_contracts() -> None:
    index = load_index()
    entries = index["contracts"]
    assert entries, "registry index declares no contracts"
    loaded_ids = {c.contract_id for c in internal_contracts() + external_contracts()}
    declared_ids = {e["contract_id"] for e in entries}
    assert declared_ids == loaded_ids


def test_every_contract_has_required_metadata() -> None:
    canonical_entities = set(load_index()["canonical_entities"])
    for contract in internal_contracts() + external_contracts():
        assert contract.contract_id
        assert contract.source_system
        assert contract.mapping_id, f"{contract.contract_id} missing mapping_id"
        assert contract.fields, f"{contract.contract_id} declares no fields"
        assert contract.required_fields(), f"{contract.contract_id} has no required field"
        assert (
            contract.canonical_target in canonical_entities
        ), f"{contract.contract_id} -> unknown canonical {contract.canonical_target!r}"


def test_external_contracts_declare_acquisition_method() -> None:
    for contract in external_contracts():
        assert contract.acquisition_method, f"{contract.contract_id} missing acquisition_method"


# --- acceptance #1: Batch/CDC/API/file/event envelopes represented ---------


def test_integration_mode_and_envelope_taxonomy_represented() -> None:
    index = load_index()
    all_contracts = internal_contracts() + external_contracts()
    modes = {c.integration_mode for c in all_contracts}
    # Concrete dataset-shaped modes must be present.
    assert {"batch_snapshot", "incremental_batch", "event_stream"} <= modes
    # The full operational taxonomy (incl. backfill/api_lookup) is declared.
    assert {"batch_snapshot", "incremental_batch", "event_stream", "backfill", "api_lookup"} == set(
        index["integration_modes"]
    )
    # Both exchange envelopes are used and loadable.
    assert {c.envelope for c in all_contracts} == {"batch", "event"}
    assert load_envelope("batch").envelope == "batch"
    assert load_envelope("event").envelope == "event"
    # External acquisition methods cover api / file-style / manual inputs.
    acq = {c.acquisition_method for c in external_contracts()}
    assert {"api", "feed", "manual"} <= acq


# --- acceptance #2: envelope retains exchange identity/time fields ----------


def test_batch_envelope_retains_required_exchange_fields() -> None:
    envelope = batch_envelope()
    field_map = envelope.field_map()
    for name in (
        "source_system",
        "source_record_id",
        "event_time",
        "observation_time",
        "ingested_at",
    ):
        assert name in field_map, f"batch envelope missing {name}"
    # The identity/time anchors are mandatory; ingested_at is Integration-filled.
    for name in ("source_system", "source_record_id", "event_time", "observation_time"):
        assert field_map[name].required, f"{name} should be required"
    assert field_map["ingested_at"].required is False


def test_event_envelope_retains_event_fields() -> None:
    envelope = event_envelope()
    field_map = envelope.field_map()
    for name in ("event_id", "event_type", "event_time", "observation_time", "payload"):
        assert name in field_map and field_map[name].required


# --- acceptance #3: IoT / internal / external samples included -------------


def test_iot_internal_and_external_samples_present() -> None:
    valid_ids = {_load_fixture(p)["contract_id"] for p in _valid_fixture_files()}
    # IoT event-stream sample, internal batch sample, external connector sample.
    assert "machine_status_event" in valid_ids
    assert "transaction_event" in valid_ids
    assert "poi_snapshot" in valid_ids


def test_every_contract_has_valid_and_invalid_fixtures() -> None:
    valid_ids = {_load_fixture(p)["contract_id"] for p in _valid_fixture_files()}
    invalid_ids = {_load_fixture(p)["contract_id"] for p in _invalid_fixture_files()}
    for contract in internal_contracts() + external_contracts():
        assert contract.contract_id in valid_ids, f"no valid fixture for {contract.contract_id}"
        assert contract.contract_id in invalid_ids, f"no invalid fixture for {contract.contract_id}"


# --- acceptance: golden records pass; invalid records quarantine -----------


@pytest.mark.parametrize(
    "record",
    [c[1] for c in _valid_record_cases()],
    ids=[c[0] for c in _valid_record_cases()],
)
def test_valid_fixtures_pass_their_contract(record: dict) -> None:
    contract_id = _find_contract_id_for_valid(record)
    contract = _resolve_contract(contract_id)
    result = validate_record(contract, record)
    assert result.ok, f"expected accepted, got issues: {result.issues}"
    assert result.quarantine_reasons() == ()


def _find_contract_id_for_valid(record: dict) -> str:
    # Records are unique enough; map them back via the originating fixture files.
    for path in _valid_fixture_files():
        data = _load_fixture(path)
        if record in data["records"]:
            return data["contract_id"]
    raise AssertionError("record not found in any valid fixture")  # pragma: no cover


@pytest.mark.parametrize(
    "contract_id,record,expect_codes",
    [(c[0].split(":")[0], c[1], c[2]) for c in _invalid_record_cases()],
    ids=[c[0] for c in _invalid_record_cases()],
)
def test_invalid_fixtures_route_to_quarantine(
    contract_id: str, record: dict, expect_codes: list[str]
) -> None:
    contract = _resolve_contract(contract_id)
    result = validate_record(contract, record)
    assert not result.ok, "expected the record to be rejected"
    assert result.quarantined
    assert result.quarantine_reasons(), "rejected record must carry quarantine reasons"
    for code in expect_codes:
        assert code in result.error_codes, (
            f"expected error code {code!r} for {contract_id}, got {result.error_codes}"
        )


def test_unknown_contract_id_raises() -> None:
    with pytest.raises(ContractError):
        _contract_by_id("does_not_exist")
