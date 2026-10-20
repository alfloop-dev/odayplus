"""Validate fixed-provider output and derive facts, never activation authority.

The native owner must freshly verify the artifact and source/human authority.
This module neither changes Intake state nor treats an artifact as approval.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from modules.external_data.application import native_manual_json_parser as provider
from modules.external_data.application.native_parser_artifacts import (
    _INSTALLED,
    MAX_MEMBER_BYTES,
    VerifiedNativeParserArtifact,
    _read_regular,
)


class NativeParserOutputRejected(ValueError):
    pass


def _reject():
    raise NativeParserOutputRejected("NATIVE_PARSER_OUTPUT_REJECTED") from None


def _closed(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _reject()
        value[key] = item
    return value


def _invalid_number(_value):
    _reject()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class NativeParserOutput:
    """Validated column values and a proposed outcome, without a commit grant."""
    facts: provider.NativeManualParserFacts
    status: str
    missing_normalized_fields: tuple[str, ...]
    missing_confidence_fields: tuple[str, ...]
    artifact_sha256: str
    output_sha256: str

    def quality_summary(self):
        return {**self.facts.quality_summary(),
            "missing_normalized_fields": list(self.missing_normalized_fields),
            "missing_confidence_fields": list(self.missing_confidence_fields)}


def validate_native_manual_parser_output(data: bytes, *, artifact: VerifiedNativeParserArtifact,
        lineage: provider.ManualParserLineage, original_fields: dict) -> NativeParserOutput:
    """Bind bounded child output to attested schema and exact original proposals.

    A malformed or substituted output raises a typed failure; it never becomes
    a successful empty result. Missing required facts yield PARTIAL, and no
    confidence, score, threshold or human review decision is invented.
    """
    if (not isinstance(data, bytes) or not 1 <= len(data) <= provider.MAX_DOCUMENT_BYTES
            or not isinstance(artifact, VerifiedNativeParserArtifact)
            or not isinstance(lineage, provider.ManualParserLineage)
            or not isinstance(original_fields, dict) or artifact.source_id != lineage.source_id
            or artifact.package_name != provider.PACKAGE_NAME
            or artifact.output_schema_version != provider.OUTPUT_SCHEMA_VERSION):
        _reject()
    try:
        schema_bytes = _read_regular(_INSTALLED["output-schema.json"], MAX_MEMBER_BYTES)
        if (dict(artifact.installed_sha256).get("output-schema.json") != hashlib.sha256(schema_bytes).hexdigest()
                or hashlib.sha256(_json(original_fields).encode()).hexdigest() != lineage.fields_sha256
                or set(original_fields) != {name for name, _ in lineage.correction_ids}):
            _reject()
        schema = json.loads(schema_bytes.decode("utf-8"), object_pairs_hook=_closed, parse_constant=_invalid_number)
        # This fixed local schema requires no reference resolution or network.
        def contains_reference(value):
            if isinstance(value, dict):
                return "$ref" in value or "$dynamicRef" in value or any(contains_reference(v) for v in value.values())
            return isinstance(value, list) and any(contains_reference(v) for v in value)
        if (contains_reference(schema) or schema.get("x-schema-version") != artifact.output_schema_version):
            _reject()
        Draft202012Validator.check_schema(schema)
        value = json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=_closed,
            parse_constant=_invalid_number)
        validator = Draft202012Validator(schema)
        if not validator.is_valid(value):
            _reject()
        # Canonical JSON preserves type as well as value (e.g. True != 1).
        if _json(value["parsed_payload"]) != _json(original_fields):
            _reject()
        normalized, errors = provider.normalize_manual_fields(original_fields)
        if (_json(value["normalized_payload"]) != _json(normalized)
                or value["validation_errors"] != errors or value["field_confidence"] != {}):
            _reject()
        required = []
        for key in ("x-required-normalized-fields", "x-required-confidence-fields"):
            fields = schema.get(key)
            if (not isinstance(fields, list) or any(not isinstance(name, str) for name in fields)
                    or len(set(fields)) != len(fields) or not set(fields) <= provider.FIELDS):
                _reject()
            required.append(set(fields))
        if not provider.REQUIRED_FIELDS <= required[0]:
            _reject()
        missing_normalized = tuple(sorted(required[0] - value["normalized_payload"].keys()))
        missing_confidence = tuple(sorted(required[1] - value["field_confidence"].keys()))
        status = "PARTIAL" if errors or missing_normalized or missing_confidence else "SUCCEEDED"
        facts = provider.NativeManualParserFacts(*(_json(value[name]) for name in (
            "parsed_payload", "normalized_payload", "field_confidence", "validation_errors")))
        return NativeParserOutput(facts, status, missing_normalized, missing_confidence,
            artifact.sha256, hashlib.sha256(data).hexdigest())
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, SchemaError):
        _reject()
