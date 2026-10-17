"""Typed manual proposals remain human evidence rather than fabricated parsing."""

import hashlib
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from modules.listing.application.native_intake_assisted_entry_input import (
    NativeAssistedEntryCommand,
    NativeAssistedEntryFields,
    manual_evidence_document,
)


def values():
    return {"address": "台北市示範路 18 號", "rent": 25000, "areaPing": 12.5}


def command(**kwargs):
    return {"fields": values(), "reason": "人工確認欄位", "risk_summary": "身份欄位待獨立覆核",
            "risk_acknowledged": True, **kwargs}


@pytest.mark.parametrize("field,value", [
    ("rent", 0), ("rent", -1), ("rent", True), ("rent", "25000"), ("rent", float("inf")),
    ("rent", float("nan")), ("areaPing", 0), ("areaPing", False), ("areaPing", float("-inf")),
    ("address", " "), ("address", 18), ("address", "a" * 513),
    ("address", "<script>page</script>"), ("address", "human\x00text"),
    ("providerListingId", "Bearer sensitive-token-value"), ("floor", "<html>"),
    ("listingType", {}), ("listingStatus", ""),
])
def test_typed_fields_reject_coercion_nonfinite_html_secrets_and_unbounded_text(field, value):
    with pytest.raises(ValidationError):
        NativeAssistedEntryFields.model_validate({**values(), field: value})


@pytest.mark.parametrize("key", ["html", "raw_html", "snapshot_id", "source_id", "contact", "phone",
                                  "cookie", "password", "confidence", "normalized_value"])
def test_no_caller_capability_raw_content_contact_or_parser_fields(key):
    with pytest.raises(ValidationError):
        NativeAssistedEntryFields.model_validate({**values(), key: "untrusted"})


@pytest.mark.parametrize("key", ["address", "rent", "areaPing"])
def test_all_required_human_fields_present(key):
    data = values()
    del data[key]
    with pytest.raises(ValidationError):
        NativeAssistedEntryFields.model_validate(data)


@pytest.mark.parametrize("kwargs", [
    {"risk_acknowledged": False}, {"risk_acknowledged": 1}, {"risk_acknowledged": "true"},
    {"risk_summary": " "}, {"reason": " "}, {"reason": "Bearer sensitive-token-value"},
    {"risk_summary": "<html>not a summary</html>"}, {"credential": "not supported"},
    {"reason": "a" * 4000, "risk_summary": "漢" * 4000},
])
def test_command_requires_explicit_risk_acknowledgement_safe_reason_and_bounded_canonical_bytes(kwargs):
    with pytest.raises(ValidationError):
        NativeAssistedEntryCommand.model_validate(command(**kwargs))


def test_human_text_is_preserved_with_no_confidence_or_parsed_values():
    entered = {**values(), "address": "  台北市示範路 18 號  ", "floor": "B1", "listingStatus": "待確認"}
    body = NativeAssistedEntryCommand.model_validate(command(fields=entered))
    assert body.fields.values() == {**entered, "rent": 25000.0}
    assert len(body.sha256) == len(body.fields.sha256) == 64
    assert "人工確認欄位" in body.correction_reason()
    assert "身份欄位待獨立覆核" in body.correction_reason()
    assert "Risk acknowledged: yes" in body.correction_reason()
    with pytest.raises(ValidationError):
        body.fields.address = "mutated"
    exposed = body.fields.values()
    exposed["address"] = "mutated"
    assert body.fields.address == entered["address"]


def document_case():
    fields = NativeAssistedEntryFields.model_validate(values())
    return {"tenant_id": str(uuid4()), "intake_id": str(uuid4()), "request_id": str(uuid4()),
        "requested_by": str(uuid4()), "source_id": "manual-only-source", "fields": fields,
        "correction_ids": {field: str(uuid4()) for field in fields.values()}}


def test_document_replay_is_exact_and_distinct_requests_never_share_content_key():
    data = document_case()
    first = manual_evidence_document(**data)
    reordered = {**data, "correction_ids": dict(reversed(list(data["correction_ids"].items())))}
    assert manual_evidence_document(**reordered) == first
    assert manual_evidence_document(**{**data, "request_id": str(uuid4())}) != first
    ids = {**data["correction_ids"], "rent": str(uuid4())}
    assert manual_evidence_document(**{**data, "correction_ids": ids}) != first
    parsed = json.loads(first)
    assert parsed["capture_method"] == "MANUAL_ENTRY" and parsed["schema_version"] == 1
    assert parsed["request_id"] == data["request_id"] and parsed["fields_sha256"] == data["fields"].sha256
    assert {r["field_path"]: r["corrected_value"] for r in parsed["corrections"]} == data["fields"].values()
    assert {r["correction_id"] for r in parsed["corrections"]} == set(data["correction_ids"].values())
    assert not {"confidence", "parsed", "normalized", "raw_html"} & set(parsed)
    assert hashlib.sha256(first).hexdigest() != hashlib.sha256(
        manual_evidence_document(**{**data, "request_id": str(uuid4())})).hexdigest()


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "unknown", "noncanonical", "zero"])
def test_document_requires_exact_unique_canonical_correction_identity_set(invalid):
    data = document_case()
    ids = data["correction_ids"]
    if invalid == "missing":
        del ids["rent"]
    elif invalid == "duplicate":
        ids["rent"] = ids["address"]
    elif invalid == "unknown":
        ids["raw_html"] = str(uuid4())
    elif invalid == "noncanonical":
        ids["rent"] = str(uuid4()).upper()
    else:
        ids["rent"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValueError):
        manual_evidence_document(**data)
