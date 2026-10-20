"""Attest installed parser bytes; never grant release or source authority.

Archives are supplied as local deployment content, never fetched from a row's
URI and never installed, imported or extracted. Only the fixed manual provider
is supported. The native SQL owner must separately prove current human/source/
runtime authority before using any verified artifact binding.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from modules.external_data.application import native_manual_json_parser as provider

MAX_ARCHIVE_BYTES = 524_288
MAX_MEMBER_BYTES = 262_144
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_RELEASE_FIELDS = frozenset({"parser_release_id", "source_id", "package_name", "semantic_version",
    "input_schema_version", "output_schema_version", "artifact_uri", "artifact_sha256", "test_corpus_version",
    "validation_status", "canary_percent", "released_by", "released_at", "deprecated_at",
    "rollback_to_release_id", "version", "created_at"})
_SPEC_DIR = Path(__file__).with_name("native_parser_specs")
_INSTALLED = {
    "provider.py": Path(provider.__file__),
    "input-schema.json": _SPEC_DIR / "manual_input_v1.json",
    "output-schema.json": _SPEC_DIR / "manual_output_v1.json",
    "corpus.json": _SPEC_DIR / "manual_corpus_v1.json",
}


class NativeParserArtifactRejected(ValueError):
    pass


class NativeParserArtifactUnavailable(RuntimeError):
    pass


def _reject():
    raise NativeParserArtifactRejected("NATIVE_PARSER_ARTIFACT_REJECTED") from None


def _uuid(value):
    try:
        if (not isinstance(value, str) or len(value) != 36
                or not UUID(value).int or str(UUID(value)) != value):
            _reject()
    except (ValueError, AttributeError, TypeError):
        _reject()
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _metadata_value(value):
    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal) and value.is_finite():
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    _reject()


def _time(value):
    if isinstance(value, str) and len(value) <= 64:
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _reject()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _reject()
    return value.astimezone(UTC)


def _read_regular(path, limit):
    if not isinstance(path, Path) or not path.is_absolute() or not hasattr(os, "O_NOFOLLOW"):
        _reject()
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= limit:
                _reject()
            data = source.read(limit + 1)
            after = os.fstat(source.fileno())
            def observed(value):
                return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns
            if len(data) != before.st_size or observed(before) != observed(after):
                _reject()
            return data
    except OSError:
        raise NativeParserArtifactUnavailable("NATIVE_PARSER_ARTIFACT_READ_UNAVAILABLE") from None


@dataclass(frozen=True)
class NativeParserArtifactDeployment:
    parser_release_id: str
    source_id: str
    archive_uri: str
    archive_path: Path

    def __post_init__(self):
        _uuid(self.parser_release_id)
        if (not isinstance(self.source_id, str) or not self.source_id.strip() or len(self.source_id) > 256
                or not isinstance(self.archive_uri, str) or not self.archive_uri.strip()
                or len(self.archive_uri) > 2048
                or any(ord(c) < 32 for c in self.source_id + self.archive_uri)
                or not isinstance(self.archive_path, Path) or not self.archive_path.is_absolute()):
            _reject()


@dataclass(frozen=True)
class VerifiedNativeParserArtifact:
    """Byte/metadata evidence only; this object is not an activation grant."""
    parser_release_id: str
    source_id: str
    release_version: int
    semantic_version: str
    release_metadata_sha256: str
    archive_uri: str
    archive_sha256: str
    package_name: str
    input_schema_version: str
    output_schema_version: str
    test_corpus_version: str
    installed_sha256: tuple[tuple[str, str], ...]
    actual_corpus_cases: int
    corpus_result_sha256: str

    def snapshot(self):
        return {"parser_release_id": self.parser_release_id, "source_id": self.source_id,
            "release_version": self.release_version, "artifact_uri": self.archive_uri,
            "semantic_version": self.semantic_version, "release_metadata_sha256": self.release_metadata_sha256,
            "artifact_sha256": self.archive_sha256, "package_name": self.package_name,
            "input_schema_version": self.input_schema_version,
            "output_schema_version": self.output_schema_version,
            "test_corpus_version": self.test_corpus_version,
            "installed_sha256": dict(self.installed_sha256), "actual_corpus_cases": self.actual_corpus_cases,
            "corpus_result_sha256": self.corpus_result_sha256}

    @property
    def sha256(self):
        return _sha(_canonical(self.snapshot()).encode())


class NativeParserArtifactRegistry:
    def __init__(self, deployments: tuple[NativeParserArtifactDeployment, ...]):
        if (not isinstance(deployments, tuple) or not deployments or len(deployments) > 100
                or any(not isinstance(item, NativeParserArtifactDeployment) for item in deployments)):
            _reject()
        self._deployments = {item.parser_release_id: item for item in deployments}
        if len(self._deployments) != len(deployments):
            _reject()
        # This seals immutable installed code/data facts, not current grants.
        # Every verification below rereads the files and rejects any drift.
        self._installed = {name: _read_regular(path, MAX_MEMBER_BYTES) for name, path in _INSTALLED.items()}
        self._provider_functions = self._functions()
        self._provider_identity = self._identity()

    @staticmethod
    def _identity():
        return (provider.PACKAGE_NAME, provider.INPUT_SCHEMA_VERSION, provider.OUTPUT_SCHEMA_VERSION,
            provider.CORPUS_VERSION, provider.MAX_DOCUMENT_BYTES, provider.FIELDS, provider.REQUIRED_FIELDS,
            provider._SHA.pattern, provider._FLOOR.pattern, provider._DOCUMENT_KEYS)

    @staticmethod
    def _functions():
        functions = (provider.parse_manual_evidence, provider.normalize_manual_fields,
            provider._field_value, provider._closed_object,
            provider._canonical_json, provider._invalid_number, provider._uuid, provider._reject,
            provider.ManualParserLineage.__post_init__, provider.NativeManualParserFacts.payloads,
            provider.NativeManualParserFacts.quality_summary)
        return tuple((function, function.__code__) for function in functions)

    def verify_release(self, release: dict, *, now: datetime) -> VerifiedNativeParserArtifact:
        try:
            if (not isinstance(release, dict) or not _RELEASE_FIELDS <= release.keys() or not isinstance(now, datetime)
                    or now.tzinfo is None or now.utcoffset() is None):
                _reject()
            deployment = self._deployments.get(release["parser_release_id"])
            at = _time(release["released_at"])
            created = _time(release["created_at"])
            canary = release["canary_percent"]
            if (deployment is None or release["source_id"] != deployment.source_id
                    or release["artifact_uri"] != deployment.archive_uri
                    or release["package_name"] != provider.PACKAGE_NAME
                    or release["input_schema_version"] != provider.INPUT_SCHEMA_VERSION
                    or release["output_schema_version"] != provider.OUTPUT_SCHEMA_VERSION
                    or release["test_corpus_version"] != provider.CORPUS_VERSION
                    or not isinstance(release["semantic_version"], str)
                    or not release["semantic_version"].strip() or len(release["semantic_version"]) > 128
                    or any(ord(c) < 32 for c in release["semantic_version"])
                    or release["validation_status"] != "PRODUCTION" or release["deprecated_at"] is not None
                    or type(release["version"]) is not int or not 1 <= release["version"] <= 2**63 - 1
                    or not isinstance(release["artifact_sha256"], str) or not _SHA.fullmatch(release["artifact_sha256"])
                    or at > now or created > now or type(canary) not in {int, float, Decimal}
                    or not math.isfinite(canary) or not 0 <= canary <= 100):
                _reject()
            _uuid(release["released_by"])
            if self._identity() != self._provider_identity:
                _reject()
            if release["rollback_to_release_id"] is not None:
                _uuid(release["rollback_to_release_id"])
            metadata_digest = _sha(json.dumps(release, sort_keys=True, separators=(",", ":"),
                allow_nan=False, default=_metadata_value).encode())
            if any(a is not b or ac is not bc for (a, ac), (b, bc) in
                    zip(self._provider_functions, self._functions(), strict=True)):
                _reject()
            current = {name: _read_regular(path, MAX_MEMBER_BYTES) for name, path in _INSTALLED.items()}
            if current != self._installed:
                _reject()
            archive = _read_regular(deployment.archive_path, MAX_ARCHIVE_BYTES)
            if _sha(archive) != release["artifact_sha256"]:
                _reject()
            with zipfile.ZipFile(io.BytesIO(archive)) as container:
                entries = container.infolist()
                if len(entries) != len(current) or {item.filename for item in entries} != set(current):
                    _reject()
                for item in entries:
                    mode = item.external_attr >> 16
                    if (item.orig_filename != item.filename or item.flag_bits & 1
                            or stat.S_IFMT(mode) not in {0, stat.S_IFREG} or mode & 0o111
                            or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                            or not 1 <= item.file_size <= MAX_MEMBER_BYTES):
                        _reject()
                    with container.open(item) as member:
                        data = member.read(MAX_MEMBER_BYTES + 1)
                    if len(data) != item.file_size or data != current[item.filename]:
                        _reject()
            corpus_count, corpus_digest = self._verify_corpus(current)
            return VerifiedNativeParserArtifact(parser_release_id=deployment.parser_release_id,
                source_id=deployment.source_id, release_version=release["version"],
                semantic_version=release["semantic_version"], release_metadata_sha256=metadata_digest,
                archive_uri=deployment.archive_uri, archive_sha256=_sha(archive), package_name=provider.PACKAGE_NAME,
                input_schema_version=provider.INPUT_SCHEMA_VERSION, output_schema_version=provider.OUTPUT_SCHEMA_VERSION,
                test_corpus_version=provider.CORPUS_VERSION,
                installed_sha256=tuple(sorted((name, _sha(data)) for name, data in current.items())),
                actual_corpus_cases=corpus_count, corpus_result_sha256=corpus_digest)
        except NativeParserArtifactUnavailable:
            raise
        except (KeyError, TypeError, AttributeError, ValueError, OverflowError, RuntimeError, zipfile.BadZipFile):
            _reject()

    @staticmethod
    def _verify_corpus(installed):
        input_schema = json.loads(installed["input-schema.json"])
        output_schema = json.loads(installed["output-schema.json"])
        corpus = json.loads(installed["corpus.json"])
        if (input_schema.get("x-schema-version") != provider.INPUT_SCHEMA_VERSION
                or output_schema.get("x-schema-version") != provider.OUTPUT_SCHEMA_VERSION
                or set(corpus) != {"version", "cases"} or corpus["version"] != provider.CORPUS_VERSION
                or not isinstance(corpus["cases"], list) or not 1 <= len(corpus["cases"]) <= 32):
            _reject()
        for key in ("x-required-normalized-fields", "x-required-confidence-fields"):
            fields = output_schema.get(key)
            if (not isinstance(fields, list) or any(not isinstance(name, str) for name in fields)
                    or len(set(fields)) != len(fields) or not set(fields) <= provider.FIELDS):
                _reject()
        if not provider.REQUIRED_FIELDS <= set(output_schema["x-required-normalized-fields"]):
            _reject()
        results, names = [], set()
        for case in corpus["cases"]:
            if (not isinstance(case, dict) or set(case) != {"name", "fields", "expected_normalized", "expected_errors"}
                    or not isinstance(case["name"], str) or not 1 <= len(case["name"]) <= 64
                    or case["name"] in names or not isinstance(case["fields"], dict)):
                _reject()
            names.add(case["name"])
            ids = tuple((name, str(UUID(int=100 + i))) for i, name in enumerate(sorted(case["fields"])))
            context = {name: str(UUID(int=i + 1)) for i, name in enumerate(
                ("tenant_id", "intake_id", "request_id", "requested_by"))}
            fields_digest = _sha(_canonical(case["fields"]).encode())
            document = {**context, "source_id": "synthetic-parser-corpus", "schema_version": 1,
                "capture_method": "MANUAL_ENTRY", "fields_sha256": fields_digest,
                "corrections": [{"field_path": name, "correction_id": ident,
                    "corrected_value": case["fields"][name]} for name, ident in ids]}
            data = _canonical(document).encode()
            lineage = provider.ManualParserLineage(**context, source_id=document["source_id"],
                content_sha256=_sha(data), fields_sha256=fields_digest, correction_ids=ids)
            result = provider.parse_manual_evidence(data, lineage).payloads()
            if (result["parsed_payload"] != case["fields"] or result["field_confidence"] != {}
                    or result["normalized_payload"] != case["expected_normalized"]
                    or result["validation_errors"] != case["expected_errors"]):
                _reject()
            results.append({"case": case["name"], "result_sha256": _sha(_canonical(result).encode())})
        return len(results), _sha(_canonical(results).encode())
