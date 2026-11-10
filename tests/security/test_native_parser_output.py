"""Child result substitution cannot become successful canonical parser facts."""
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from modules.external_data.application import native_manual_json_parser as provider
from modules.external_data.application import native_parser_artifacts as artifacts
from modules.external_data.application import native_parser_output as output
from modules.listing.application.native_intake_assisted_entry_input import (
    NativeAssistedEntryFields,
    manual_evidence_document,
)
from tests.security.test_native_parser_artifacts import (
    artifact_case as artifact_case,
)
from tests.security.test_native_parser_artifacts import (
    write_archive,
)


def case(artifact_case, **changes):
    registry, _, release, _ = artifact_case
    artifact = registry.verify_release(release, now=datetime.now(UTC))
    fields = NativeAssistedEntryFields.model_validate({"address": " 台北市 Ａ 路 18 號 ",
        "rent": 25000, "areaPing": 12.5, **changes})
    ids = {name: str(uuid4()) for name in fields.values()}
    context = {name: str(uuid4()) for name in ("tenant_id", "intake_id", "request_id", "requested_by")}
    context["source_id"] = artifact.source_id
    data = manual_evidence_document(**context, fields=fields, correction_ids=ids)
    lineage = provider.ManualParserLineage(**context, content_sha256=hashlib.sha256(data).hexdigest(),
        fields_sha256=fields.sha256, correction_ids=tuple(ids.items()))
    payload = provider.parse_manual_evidence(data, lineage).payloads()
    return artifact, lineage, fields.values(), payload


def validate(case, payload=None):
    artifact, lineage, fields, original = case
    raw = json.dumps(original if payload is None else payload, sort_keys=True, separators=(",", ":")).encode()
    return output.validate_native_manual_parser_output(raw, artifact=artifact, lineage=lineage,
        original_fields=fields)


def test_real_producer_and_attested_provider_output_preserve_exact_values_and_unknown_confidence(artifact_case):
    example = case(artifact_case, floor=" b1 ")
    result = validate(example)
    assert result.status == "SUCCEEDED"
    assert result.facts.payloads() == example[3]
    assert result.facts.payloads()["field_confidence"] == {}
    assert result.missing_normalized_fields == result.missing_confidence_fields == ()
    assert result.artifact_sha256 == example[0].sha256
    assert result.quality_summary() == {"parsed_field_count": 4, "normalized_field_count": 4,
        "confidence_field_count": 0, "validation_error_count": 0,
        "missing_normalized_fields": [], "missing_confidence_fields": []}
    exposed = result.quality_summary()
    exposed["missing_normalized_fields"].append("rent")
    assert result.quality_summary()["missing_normalized_fields"] == []


@pytest.mark.parametrize("kind", ["ambiguous_floor", "missing_address"])
def test_partial_keeps_evidence_without_default_values_or_invented_confidence(artifact_case, kind):
    example = case(artifact_case, **({"floor": "多樓層"} if kind == "ambiguous_floor" else {"address": "＜script＞"}))
    result = validate(example)
    assert result.status == "PARTIAL"
    assert result.facts.payloads() == example[3]
    assert result.missing_normalized_fields == (() if kind == "ambiguous_floor" else ("address",))
    assert result.facts.payloads()["field_confidence"] == {}
    assert result.quality_summary()["validation_error_count"] == 1


@pytest.mark.parametrize("mutation", ["raw", "raw_type", "raw_missing", "normalized", "default_floor",
    "extra_normalized", "confidence", "hidden_error", "false_error", "swapped_error", "status", "approval"])
def test_schema_valid_or_rehashed_child_substitution_cannot_become_success(artifact_case, mutation):
    example = case(artifact_case, floor="unknown")
    payload = json.loads(json.dumps(example[3]))
    if mutation == "raw":
        payload["parsed_payload"]["rent"] = 99999
    elif mutation == "raw_type":
        payload["parsed_payload"]["rent"] = True
    elif mutation == "raw_missing":
        del payload["parsed_payload"]["rent"]
    elif mutation == "normalized":
        payload["normalized_payload"]["address"] = "substituted canonical address"
    elif mutation == "default_floor":
        payload["normalized_payload"]["floor"] = "1"
    elif mutation == "extra_normalized":
        payload["normalized_payload"]["providerListingId"] = "invented"
    elif mutation == "confidence":
        payload["field_confidence"]["address"] = 1
    elif mutation == "hidden_error":
        payload["validation_errors"] = []
    elif mutation == "false_error":
        payload["validation_errors"].append({"field_path": "address", "code": "ADDRESS_NOT_CANONICAL"})
    elif mutation == "swapped_error":
        payload["validation_errors"][0]["code"] = "ADDRESS_NOT_CANONICAL"
    else:
        payload[mutation] = "SUCCEEDED" if mutation == "status" else True
    with pytest.raises(output.NativeParserOutputRejected, match="^NATIVE_PARSER_OUTPUT_REJECTED$"):
        validate(example, payload)


@pytest.mark.parametrize("wire", [b"", b"{", b"\xff", b"null", b"[]", b'{"rent":NaN}',
    b'{"parsed_payload":{},"parsed_payload":{}}', b"x" * (provider.MAX_DOCUMENT_BYTES + 1)])
def test_bounded_closed_wire_errors_are_typed_and_do_not_echo_input(artifact_case, wire):
    artifact, lineage, fields, _ = case(artifact_case)
    with pytest.raises(output.NativeParserOutputRejected, match="^NATIVE_PARSER_OUTPUT_REJECTED$"):
        output.validate_native_manual_parser_output(wire, artifact=artifact, lineage=lineage, original_fields=fields)


@pytest.mark.parametrize("change", ["original_value", "correction_names", "source", "schema_digest"])
def test_output_does_not_outlive_its_proposal_and_installed_schema_binding(artifact_case, change):
    from dataclasses import replace
    artifact, lineage, fields, payload = case(artifact_case)
    if change == "original_value":
        fields["rent"] += 1
    elif change == "correction_names":
        lineage = replace(lineage, correction_ids=(*lineage.correction_ids, ("floor", str(uuid4()))))
    elif change == "source":
        lineage = replace(lineage, source_id="other-source")
    else:
        artifact = replace(artifact, installed_sha256=(("output-schema.json", "0" * 64),))
    with pytest.raises(output.NativeParserOutputRejected):
        validate((artifact, lineage, fields, payload))


def test_actual_attested_schema_requiring_unknown_confidence_yields_partial(artifact_case, tmp_path, monkeypatch):
    _, deployment, release, installed = artifact_case
    schema = json.loads(installed["output-schema.json"])
    schema["x-required-confidence-fields"] = ["address"]
    installed["output-schema.json"] = json.dumps(schema).encode()
    image = {}
    for i, (name, data) in enumerate(installed.items()):
        path = tmp_path / f"installed-{i}"
        path.write_bytes(data)
        image[name] = path
    monkeypatch.setattr(artifacts, "_INSTALLED", image)
    monkeypatch.setattr(output, "_INSTALLED", image)
    digest = write_archive(deployment.archive_path, installed.items())
    registry = artifacts.NativeParserArtifactRegistry((deployment,))
    revised = (registry, deployment, {**release, "artifact_sha256": digest}, installed)
    result = validate(case(revised))
    assert result.status == "PARTIAL"
    assert result.missing_confidence_fields == ("address",)
    assert result.facts.payloads()["field_confidence"] == {}
    assert result.quality_summary()["validation_error_count"] == 0
