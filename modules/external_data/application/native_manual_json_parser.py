"""Parse the existing signed manual-evidence document without applying proposals.

This module uses only the standard library so its exact installed source can
be attested and run in a bounded child. Lineage arguments are bindings, not
authorization: the SQL owner must independently prove their current authority.
No extraction confidence, review decision or effective identity is supplied.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import unicodedata
from dataclasses import dataclass
from uuid import UUID

PACKAGE_NAME = "odayplus.native.manual-json.v1"
INPUT_SCHEMA_VERSION = "native-assisted-manual-evidence/1"
OUTPUT_SCHEMA_VERSION = "native-manual-parser-fields/1"
CORPUS_VERSION = "native-manual-json-corpus/1"
MAX_DOCUMENT_BYTES = 1_048_576
FIELDS = frozenset({"address", "rent", "areaPing", "providerListingId", "floor", "listingType", "listingStatus"})
REQUIRED_FIELDS = frozenset({"address", "rent", "areaPing"})
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FLOOR = re.compile(r"(?:B[1-9][0-9]{0,2}|0|[1-9][0-9]{0,2})\Z")
_DOCUMENT_KEYS = frozenset({"schema_version", "capture_method", "tenant_id", "intake_id", "request_id",
    "requested_by", "source_id", "fields_sha256", "corrections"})


class NativeManualParserInputRejected(ValueError):
    pass


def _reject():
    raise NativeManualParserInputRejected("NATIVE_MANUAL_PARSER_INPUT_REJECTED") from None


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _uuid(value):
    try:
        if not isinstance(value, str) or len(value) != 36 or not UUID(value).int or str(UUID(value)) != value:
            _reject()
    except (ValueError, TypeError, AttributeError):
        _reject()
    return value


def _closed_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject()
        result[key] = value
    return result


def _invalid_number(_value):
    _reject()


@dataclass(frozen=True)
class ManualParserLineage:
    tenant_id: str
    intake_id: str
    request_id: str
    requested_by: str
    source_id: str
    content_sha256: str
    fields_sha256: str
    correction_ids: tuple[tuple[str, str], ...]

    def __post_init__(self):
        for value in (self.tenant_id, self.intake_id, self.request_id, self.requested_by):
            _uuid(value)
        if (not isinstance(self.source_id, str) or not 1 <= len(self.source_id) <= 256
                or any(ord(c) < 32 for c in self.source_id)
                or not isinstance(self.content_sha256, str) or not _SHA.fullmatch(self.content_sha256)
                or not isinstance(self.fields_sha256, str) or not _SHA.fullmatch(self.fields_sha256)
                or not isinstance(self.correction_ids, tuple) or not 3 <= len(self.correction_ids) <= len(FIELDS)):
            _reject()
        names, ids = set(), set()
        for item in self.correction_ids:
            if (not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str)
                    or item[0] not in FIELDS or item[0] in names):
                _reject()
            names.add(item[0])
            ident = _uuid(item[1])
            if ident in ids:
                _reject()
            ids.add(ident)
        if not REQUIRED_FIELDS <= names:
            _reject()


@dataclass(frozen=True)
class NativeManualParserFacts:
    """Immutable JSON facts; a runtime owner still derives the governed outcome."""
    parsed_payload_json: str
    normalized_payload_json: str
    field_confidence_json: str
    validation_errors_json: str

    def payloads(self):
        return {name: json.loads(getattr(self, name + "_json")) for name in (
            "parsed_payload", "normalized_payload", "field_confidence", "validation_errors")}

    def quality_summary(self):
        value = self.payloads()
        return {"parsed_field_count": len(value["parsed_payload"]),
            "normalized_field_count": len(value["normalized_payload"]),
            "confidence_field_count": len(value["field_confidence"]),
            "validation_error_count": len(value["validation_errors"])}


def _field_value(name, value):
    if name in {"rent", "areaPing"}:
        if type(value) not in {int, float}:
            _reject()
        try:
            if not math.isfinite(value) or value <= 0 or value > sys.float_info.max:
                _reject()
        except (ValueError, OverflowError):
            _reject()
    elif (not isinstance(value, str) or not value.strip()
            or len(value) > (512 if name == "address" else 128)
            or any(ord(c) < 32 or c in "<>" for c in value)):
        _reject()
    return value


def parse_manual_evidence(data: bytes, lineage: ManualParserLineage) -> NativeManualParserFacts:
    """Return only values actually present in the exact frozen proposal set."""
    if (not isinstance(data, bytes) or not 1 <= len(data) <= MAX_DOCUMENT_BYTES
            or not isinstance(lineage, ManualParserLineage)
            or hashlib.sha256(data).hexdigest() != lineage.content_sha256):
        _reject()
    try:
        document = json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=_closed_object,
            parse_constant=_invalid_number)
        if (not isinstance(document, dict) or set(document) != _DOCUMENT_KEYS
                or type(document["schema_version"]) is not int or document["schema_version"] != 1
                or document["capture_method"] != "MANUAL_ENTRY"
                or any(document[name] != getattr(lineage, name) for name in (
                    "tenant_id", "intake_id", "request_id", "requested_by", "source_id", "fields_sha256"))
                or not isinstance(document["corrections"], list)
                or len(document["corrections"]) != len(lineage.correction_ids)):
            _reject()
        expected = dict(lineage.correction_ids)
        fields = {}
        for row in document["corrections"]:
            if (not isinstance(row, dict) or set(row) != {"correction_id", "field_path", "corrected_value"}
                    or not isinstance(row["field_path"], str) or row["field_path"] not in expected
                    or row["field_path"] in fields or row["correction_id"] != expected[row["field_path"]]):
                _reject()
            fields[row["field_path"]] = _field_value(row["field_path"], row["corrected_value"])
        if (set(fields) != set(expected) or not REQUIRED_FIELDS <= fields.keys()
                or hashlib.sha256(_canonical_json(fields).encode()).hexdigest() != lineage.fields_sha256):
            _reject()
    except (UnicodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        _reject()
    normalized, errors = normalize_manual_fields(fields)
    return NativeManualParserFacts(_canonical_json(fields), _canonical_json(normalized),
        _canonical_json({}), _canonical_json(errors))


def normalize_manual_fields(fields):
    """Normalize already validated proposal values without adding any values."""
    normalized, errors = dict(fields), []
    address = " ".join(unicodedata.normalize("NFKC", fields["address"]).split())
    if (address and len(address) <= 512
            and not any(ord(c) < 32 or c in "<>" for c in address)):
        normalized["address"] = address
    else:
        del normalized["address"]
        errors.append({"field_path": "address", "code": "ADDRESS_NOT_CANONICAL"})
    if "floor" in fields:
        floor = unicodedata.normalize("NFKC", fields["floor"]).strip().upper()
        if _FLOOR.fullmatch(floor):
            normalized["floor"] = floor
        else:
            del normalized["floor"]
            errors.append({"field_path": "floor", "code": "FLOOR_NOT_CANONICAL"})
    return normalized, errors
