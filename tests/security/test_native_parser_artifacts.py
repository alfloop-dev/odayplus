"""Actual installed/archive bytes and corpus results confer no activation grant."""
import hashlib
import io
import json
import stat
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from modules.external_data.application import native_manual_json_parser as provider
from modules.external_data.application import native_parser_artifacts as artifacts
from shared.infrastructure.persistence.postgresql import _normalize_row


def installed_bytes():
    directory = Path(provider.__file__).with_name("native_parser_specs")
    return {"provider.py": Path(provider.__file__).read_bytes(),
        "input-schema.json": (directory / "manual_input_v1.json").read_bytes(),
        "output-schema.json": (directory / "manual_output_v1.json").read_bytes(),
        "corpus.json": (directory / "manual_corpus_v1.json").read_bytes()}


def write_archive(path, entries, special=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            if special and special.filename == name:
                archive.writestr(special, data)
            else:
                archive.writestr(name, data)
    data = output.getvalue()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def artifact_case(tmp_path):
    now = datetime.now(UTC)
    path = tmp_path / "manual-parser.zip"
    content = installed_bytes()
    digest = write_archive(path, content.items())
    release_id = str(uuid4())
    deployment = artifacts.NativeParserArtifactDeployment(release_id, "synthetic-parser-source",
        "gs://synthetic-approved-packages/manual-v1.zip", path)
    # The actual engine returns ISO strings and floats, not datetime/Decimal.
    release = _normalize_row({"parser_release_id": release_id, "source_id": deployment.source_id,
        "package_name": provider.PACKAGE_NAME, "semantic_version": "1.0.0",
        "input_schema_version": provider.INPUT_SCHEMA_VERSION, "output_schema_version": provider.OUTPUT_SCHEMA_VERSION,
        "artifact_uri": deployment.archive_uri, "artifact_sha256": digest,
        "test_corpus_version": provider.CORPUS_VERSION, "validation_status": "PRODUCTION",
        "canary_percent": Decimal("100.00"), "released_by": str(uuid4()),
        "released_at": now - timedelta(minutes=1), "deprecated_at": None,
        "rollback_to_release_id": None, "version": 1, "created_at": now - timedelta(hours=1)})
    return artifacts.NativeParserArtifactRegistry((deployment,)), deployment, release, content


def test_real_archive_matches_actual_installed_provider_schemas_and_executed_corpus(artifact_case):
    registry, _, release, installed = artifact_case
    binding = registry.verify_release(release, now=datetime.now(UTC))
    assert binding.actual_corpus_cases == 4
    assert dict(binding.installed_sha256) == {name: hashlib.sha256(data).hexdigest() for name, data in installed.items()}
    assert len(binding.corpus_result_sha256) == len(binding.release_metadata_sha256) == 64
    assert binding.archive_sha256 == release["artifact_sha256"]
    assert binding.snapshot()["test_corpus_version"] == provider.CORPUS_VERSION
    exposed = binding.snapshot()
    exposed["installed_sha256"].clear()
    assert len(binding.snapshot()["installed_sha256"]) == 4


@pytest.mark.parametrize("key,value", [("package_name", "os.system"), ("source_id", "other-source"),
    ("input_schema_version", "unknown"), ("output_schema_version", "unknown"), ("test_corpus_version", "unknown"),
    ("artifact_uri", "https://unregistered.invalid/package.zip"), ("artifact_sha256", "0" * 64),
    ("validation_status", "DRAFT"), ("validation_status", "CANARY"), ("validation_status", "ROLLED_BACK"),
    ("released_by", None), ("released_by", "00000000-0000-0000-0000-000000000000"),
    ("released_at", None), ("released_at", "2026-01-01T00:00:00"), ("version", True), ("version", 0),
    ("canary_percent", float("nan")), ("canary_percent", -1), ("canary_percent", 101),
    ("semantic_version", ""), ("deprecated_at", "2026-01-01T00:00:00+00:00")])
def test_catalog_status_or_copied_metadata_cannot_substitute_for_complete_byte_proof(artifact_case, key, value):
    registry, _, release, _ = artifact_case
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release({**release, key: value}, now=datetime.now(UTC))


def test_future_release_and_changed_metadata_are_not_hidden_by_cached_proof(artifact_case):
    registry, _, release, _ = artifact_case
    first = registry.verify_release(release, now=datetime.now(UTC))
    later = registry.verify_release({**release, "version": 2, "semantic_version": "1.0.1"}, now=datetime.now(UTC))
    assert first.sha256 != later.sha256
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release({**release, "released_at": datetime.now(UTC) + timedelta(days=1)}, now=datetime.now(UTC))


@pytest.mark.parametrize("mutation", ["provider", "schema", "corpus", "extra", "traversal", "duplicate"])
def test_rehashed_archive_still_requires_exact_installed_bytes_and_closed_members(artifact_case, mutation):
    registry, deployment, release, content = artifact_case
    entries = list(content.items())
    if mutation in {"provider", "schema", "corpus"}:
        name = {"provider": "provider.py", "schema": "input-schema.json", "corpus": "corpus.json"}[mutation]
        entries = [(key, b"unregistered-runtime-bytes" if key == name else data) for key, data in entries]
    elif mutation == "extra":
        entries.append(("unregistered.py", b"raise RuntimeError('must-not-execute')"))
    elif mutation == "traversal":
        entries[0] = ("../provider.py", entries[0][1])
    else:
        entries.append(entries[0])
    if mutation == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            digest = write_archive(deployment.archive_path, entries)
    else:
        digest = write_archive(deployment.archive_path, entries)
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release({**release, "artifact_sha256": digest}, now=datetime.now(UTC))


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600, stat.S_IFREG | 0o755])
def test_archive_special_or_executable_entries_are_never_installed(artifact_case, mode):
    registry, deployment, release, content = artifact_case
    info = zipfile.ZipInfo("provider.py")
    info.external_attr = mode << 16
    digest = write_archive(deployment.archive_path, content.items(), special=info)
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release({**release, "artifact_sha256": digest}, now=datetime.now(UTC))


def test_archive_symlink_and_missing_file_remain_unavailable(artifact_case, tmp_path):
    _, deployment, release, _ = artifact_case
    link = tmp_path / "untrusted-link.zip"
    link.symlink_to(deployment.archive_path)
    linked = artifacts.NativeParserArtifactDeployment(deployment.parser_release_id, deployment.source_id,
        deployment.archive_uri, link)
    registry = artifacts.NativeParserArtifactRegistry((linked,))
    with pytest.raises(artifacts.NativeParserArtifactUnavailable):
        registry.verify_release(release, now=datetime.now(UTC))
    link.unlink()
    with pytest.raises(artifacts.NativeParserArtifactUnavailable):
        registry.verify_release(release, now=datetime.now(UTC))


def test_current_installed_image_drift_rejects_even_matching_replacement_archive(artifact_case, tmp_path, monkeypatch):
    _, deployment, release, content = artifact_case
    image = {}
    for index, (name, data) in enumerate(content.items()):
        path = tmp_path / f"image-{index}"
        path.write_bytes(data)
        image[name] = path
    monkeypatch.setattr(artifacts, "_INSTALLED", image)
    registry = artifacts.NativeParserArtifactRegistry((deployment,))
    image["provider.py"].write_bytes(content["provider.py"] + b"\n")
    digest = write_archive(deployment.archive_path, [(name, path.read_bytes()) for name, path in image.items()])
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release({**release, "artifact_sha256": digest, "version": 2}, now=datetime.now(UTC))


def test_runtime_provider_replacement_cannot_be_hidden_by_unchanged_source_file(artifact_case, monkeypatch):
    registry, _, release, _ = artifact_case
    monkeypatch.setattr(provider, "parse_manual_evidence", lambda *args, **kwargs: None)
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release(release, now=datetime.now(UTC))


def test_installed_schema_documents_are_valid_and_explicit_about_unknown_confidence():
    files = installed_bytes()
    for name in ("input-schema.json", "output-schema.json"):
        Draft202012Validator.check_schema(json.loads(files[name]))
    output = json.loads(files["output-schema.json"])
    assert output["x-required-normalized-fields"] == ["address", "rent", "areaPing"]
    assert output["x-required-confidence-fields"] == []
    assert output["properties"]["field_confidence"]["maxProperties"] == 0
