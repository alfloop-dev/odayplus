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
from datetime import date
from pathlib import Path

import pytest

from apps.data_platform.store_opening import (
    APPROVED_STORE_OPENING_SOURCES,
    UnauthoritativeStoreOpeningError,
    validate_store_opening_record,
)
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
            cases.append((f"{contract_id}: {label}", case["record"], case["expect_codes"]))
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
        assert contract.canonical_target in canonical_entities, (
            f"{contract.contract_id} -> unknown canonical {contract.canonical_target!r}"
        )


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


# --- store opening authority: contract mirrors the executable engine -------
#
# ``modules/external_data/connectors/provider_registry.py`` has referenced the
# ``store_opening_authority_snapshot`` contract id since ODP-STORE-OPENING-001,
# but the contract itself was never published (recorded as a gap in
# docs/evidence/ODP_INT001_CDC_SOURCE_EVIDENCE_2026-09-03.md §7). These tests
# pin the published contract to the engine that already validates the records,
# so the two cannot drift apart again.

STORE_OPENING_CONTRACT_ID = "store_opening_authority_snapshot"
STORE_OPENING_REQUIRED_FIELDS = {
    "source_id",
    "snapshot_id",
    "tenant_id",
    "store_id",
    "opened_on",
}


def _store_opening_contract() -> SourceContract:
    return _contract_by_id(STORE_OPENING_CONTRACT_ID)


def _store_opening_fixture(kind: str) -> dict:
    path = FIXTURES_ROOT / "external" / f"{STORE_OPENING_CONTRACT_ID}.{kind}.json"
    return _load_fixture(path)


def test_store_opening_contract_matches_the_registered_provider_semantics() -> None:
    contract = _store_opening_contract()

    # External: the provider is registered in the external provider registry
    # with a manual attestation credential, not an internal upstream dataset.
    assert contract.kind == "external"
    assert contract.acquisition_method == "manual"
    # The engine writes core.stores.opened_on and lineage rows whose
    # canonical_table is core.stores, through a replayable backfill run.
    assert contract.canonical_target == "store"
    assert contract.integration_mode == "backfill"


def test_store_opening_source_id_enum_mirrors_the_engine_allowlist() -> None:
    enum = _store_opening_contract().field_map()["source_id"].enum

    assert enum is not None
    assert set(enum) == APPROVED_STORE_OPENING_SOURCES


def test_store_opening_required_fields_are_exactly_the_engine_hard_fails() -> None:
    """Every contract-required field is one the engine refuses to do without."""
    contract = _store_opening_contract()
    record = _store_opening_fixture("valid")["records"][0]
    required = set(contract.required_fields())

    assert required == STORE_OPENING_REQUIRED_FIELDS
    for name in sorted(required):
        with pytest.raises(UnauthoritativeStoreOpeningError):
            validate_store_opening_record({k: v for k, v in record.items() if k != name})
    for name in sorted({f.name for f in contract.fields} - required):
        # Optional in the contract because the engine supplies a default or
        # ignores the field; dropping it must not fail the engine.
        validate_store_opening_record({k: v for k, v in record.items() if k != name})


def test_store_opening_valid_fixtures_are_accepted_by_the_engine() -> None:
    for record in _store_opening_fixture("valid")["records"]:
        authority = validate_store_opening_record(record)

        assert authority.opened_on.isoformat() == record["opened_on"]
        assert authority.created_at_ignored is True
        # opened_on is an independent business date, never a restatement of the
        # record-keeping timestamp the same payload carries.
        assert not record.get("created_at", "").startswith(record["opened_on"])


def test_store_opening_invalid_fixtures_are_rejected_by_the_engine_too() -> None:
    for case in _store_opening_fixture("invalid")["cases"]:
        with pytest.raises(UnauthoritativeStoreOpeningError):
            validate_store_opening_record(case["record"])


def test_store_opening_contract_pins_the_canonical_field_spelling() -> None:
    """The engine answers to legacy aliases; the landing contract does not.

    ``validate_store_opening_record`` accepts ``opening_date`` for ``opened_on``
    and ``source_store_id`` for ``store_id``. The contract declares only the
    spelling that ``intake.store_opening_authority_lineage`` persists, so an
    alias-only payload is quarantined at landing rather than admitted under a
    second name. The narrowing is fail-closed: it can reject, never fabricate.
    """
    record = dict(_store_opening_fixture("valid")["records"][1])
    record["opening_date"] = record.pop("opened_on")
    record["source_store_id"] = record.pop("store_id")

    assert validate_store_opening_record(record).opened_on == date(2024, 11, 2)

    result = validate_record(_store_opening_contract(), record)
    assert result.quarantine_reasons() == ("missing_required_field",)
