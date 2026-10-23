"""The real manual renderer and parser retain proposals and unknown confidence."""
import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from modules.external_data.application.native_manual_json_parser import (
    ManualParserLineage,
    NativeManualParserInputRejected,
    parse_manual_evidence,
)
from modules.listing.application.native_intake_assisted_entry_input import (
    NativeAssistedEntryFields,
    manual_evidence_document,
)


def example(**fields):
    values = NativeAssistedEntryFields.model_validate({
        "address": "  台北市 Ａ 路　18 號  ", "rent": 25000, "areaPing": 12.5, **fields})
    ids = {key: str(uuid4()) for key in values.values()}
    context = {name: str(uuid4()) for name in ("tenant_id", "intake_id", "request_id", "requested_by")}
    context["source_id"] = "synthetic-manual-parser-source"
    data = manual_evidence_document(**context, fields=values, correction_ids=ids)
    binding = ManualParserLineage(**context, content_sha256=hashlib.sha256(data).hexdigest(),
        fields_sha256=values.sha256, correction_ids=tuple(ids.items()))
    return data, binding, values.values()


def changed_document(data, binding, mutate):
    document = json.loads(data)
    mutate(document)
    fields = {row["field_path"]: row["corrected_value"] for row in document["corrections"]}
    try:
        fields_hash = hashlib.sha256(json.dumps(fields, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except ValueError:
        fields_hash = binding.fields_sha256
    document["fields_sha256"] = fields_hash
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return raw, replace(binding, content_sha256=hashlib.sha256(raw).hexdigest(), fields_sha256=fields_hash)


def test_actual_producer_roundtrip_preserves_source_values_and_unknown_confidence():
    data, binding, fields = example(floor=" b1 ", listingStatus="待確認")
    result = parse_manual_evidence(data, binding)
    payload = result.payloads()
    assert payload["parsed_payload"] == fields
    assert payload["normalized_payload"] == {**fields, "address": "台北市 A 路 18 號", "floor": "B1"}
    assert payload["field_confidence"] == {}
    assert payload["validation_errors"] == []
    assert result.quality_summary() == {"parsed_field_count": 5, "normalized_field_count": 5,
        "confidence_field_count": 0, "validation_error_count": 0}
    payload["parsed_payload"]["address"] = "changed copy"
    assert result.payloads()["parsed_payload"] == fields


@pytest.mark.parametrize("floor", ["多樓層", "1/2樓", "unknown", "B0"])
def test_ambiguous_floor_retains_real_partial_evidence_without_a_default(floor):
    data, binding, fields = example(floor=floor)
    facts = parse_manual_evidence(data, binding).payloads()
    assert facts["parsed_payload"] == fields
    assert "floor" not in facts["normalized_payload"]
    assert facts["validation_errors"] == [{"field_path": "floor", "code": "FLOOR_NOT_CANONICAL"}]
    assert facts["field_confidence"] == {}


@pytest.mark.parametrize("address", ["＜script＞", "㍿" * 200])
def test_normalization_cannot_introduce_forbidden_or_oversized_address(address):
    data, binding, fields = example(address=address)
    facts = parse_manual_evidence(data, binding).payloads()
    assert facts["parsed_payload"] == fields and "address" not in facts["normalized_payload"]
    assert facts["validation_errors"] == [{"field_path": "address", "code": "ADDRESS_NOT_CANONICAL"}]


@pytest.mark.parametrize("name", ["tenant_id", "intake_id", "request_id", "requested_by", "source_id"])
def test_even_rehashed_document_cannot_adopt_another_lineage(name):
    data, binding, _ = example()
    raw, expected = changed_document(data, binding,
        lambda d: d.__setitem__(name, "other-source" if name == "source_id" else str(uuid4())))
    with pytest.raises(NativeManualParserInputRejected):
        parse_manual_evidence(raw, expected)


@pytest.mark.parametrize("name,value", [("schema_version", True), ("schema_version", 2),
    ("capture_method", "SERVER_RETRIEVAL"), ("confidence", 1), ("raw_html", "private-source-material"),
    ("approved", True), ("role", "admin")])
def test_closed_document_never_accepts_capabilities_or_other_source_protocol(name, value):
    data, binding, _ = example()
    raw, expected = changed_document(data, binding, lambda d: d.__setitem__(name, value))
    with pytest.raises(NativeManualParserInputRejected) as error:
        parse_manual_evidence(raw, expected)
    assert str(error.value) == "NATIVE_MANUAL_PARSER_INPUT_REJECTED"


@pytest.mark.parametrize("name,value", [("rent", True), ("rent", "25000"), ("rent", -1),
    ("rent", float("nan")), ("areaPing", float("inf")), ("areaPing", 0),
    ("address", " "), ("address", "<html>"), ("address", "private\x00value"),
    ("address", "x" * 513), ("floor", "x" * 129), ("floor", None)])
def test_field_errors_reject_even_when_the_recording_binding_is_rehashed(name, value):
    data, binding, _ = example(floor="1")

    def mutate(document):
        next(row for row in document["corrections"] if row["field_path"] == name)["corrected_value"] = value

    raw, expected = changed_document(data, binding, mutate)
    with pytest.raises(NativeManualParserInputRejected):
        parse_manual_evidence(raw, expected)


@pytest.mark.parametrize("scope", ["document", "correction"])
def test_duplicate_identical_JSON_keys_are_not_silently_collapsed(scope):
    data, binding, _ = example()
    if scope == "document":
        raw = b'{"tenant_id":' + json.dumps(binding.tenant_id).encode() + b"," + data[1:]
    else:
        needle = b'"field_path":"address"'
        assert needle in data
        raw = data.replace(needle, needle + b"," + needle, 1)
    expected = replace(binding, content_sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(NativeManualParserInputRejected):
        parse_manual_evidence(raw, expected)


def test_correction_identity_and_snapshot_checksum_remain_independent_requirements():
    data, binding, _ = example()
    with pytest.raises(NativeManualParserInputRejected):
        parse_manual_evidence(data + b" ", binding)
    raw, expected = changed_document(data, binding,
        lambda d: d["corrections"][0].__setitem__("correction_id", str(uuid4())))
    with pytest.raises(NativeManualParserInputRejected):
        parse_manual_evidence(raw, expected)


def test_explicit_binding_rejects_duplicate_correction_ids_and_missing_required_fields():
    _, binding, _ = example()
    pairs = binding.correction_ids
    with pytest.raises(NativeManualParserInputRejected):
        replace(binding, correction_ids=(pairs[0], (pairs[1][0], pairs[0][1]), pairs[2]))
    with pytest.raises(NativeManualParserInputRejected):
        replace(binding, correction_ids=(pairs[0], pairs[1], ("floor", str(uuid4()))))
