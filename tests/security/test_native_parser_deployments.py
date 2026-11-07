"""Startup bindings select local evidence, never code or activation authority."""
import hashlib
import json
import os
import zipfile
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from modules.external_data.application import native_manual_json_parser as provider
from modules.external_data.application import native_parser_artifacts as artifacts
from modules.external_data.application import native_parser_deployments as startup

RELEASE_ID = "8b1657cd-d8dc-4949-b82f-2189f9d01576"
SOURCE_ID = "native-parser-startup.synthetic-source"


def _write_archive(path, content):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in content.items():
            archive.writestr(name, data)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def deployment_case(tmp_path):
    directory = Path(provider.__file__).with_name("native_parser_specs")
    content = {
        "provider.py": Path(provider.__file__).read_bytes(),
        "input-schema.json": (directory / "manual_input_v1.json").read_bytes(),
        "output-schema.json": (directory / "manual_output_v1.json").read_bytes(),
        "corpus.json": (directory / "manual_corpus_v1.json").read_bytes(),
    }
    path = tmp_path / "manual-parser.zip"
    digest = _write_archive(path, content)
    row = {"parser_release_id": RELEASE_ID, "source_id": SOURCE_ID,
        "artifact_uri": "gs://synthetic-approved-packages/manual-v1.zip", "archive_path": str(path)}
    now = datetime.now(UTC)
    release = {
        "parser_release_id": RELEASE_ID, "source_id": SOURCE_ID,
        "package_name": provider.PACKAGE_NAME, "semantic_version": "1.0.0",
        "input_schema_version": provider.INPUT_SCHEMA_VERSION,
        "output_schema_version": provider.OUTPUT_SCHEMA_VERSION,
        "artifact_uri": row["artifact_uri"], "artifact_sha256": digest,
        "test_corpus_version": provider.CORPUS_VERSION, "validation_status": "PRODUCTION",
        "canary_percent": 100, "released_by": str(UUID(int=2)),
        "released_at": (now - timedelta(minutes=1)).isoformat(), "deprecated_at": None,
        "rollback_to_release_id": None, "version": 1,
        "created_at": (now - timedelta(hours=1)).isoformat(),
    }
    return {"version": 1, "deployments": [row]}, release, content


def _parse(document):
    return startup.parse_native_parser_deployments(json.dumps(document))


def test_real_registry_verifies_actual_bytes_schemas_and_corpus(deployment_case):
    document, release, content = deployment_case
    configured = _parse(document)
    assert configured.configured is True
    assert configured.release_for_source(SOURCE_ID) == RELEASE_ID
    assert configured.deployments == (artifacts.NativeParserArtifactDeployment(
        RELEASE_ID, SOURCE_ID, release["artifact_uri"], Path(document["deployments"][0]["archive_path"])),)
    registry = configured.build_registry()
    assert type(registry) is artifacts.NativeParserArtifactRegistry
    verified = registry.verify_release(release, now=datetime.now(UTC))
    assert verified.actual_corpus_cases == 4
    assert verified.archive_sha256 == release["artifact_sha256"]
    assert dict(verified.installed_sha256) == {
        name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
    assert verified.input_schema_version == provider.INPUT_SCHEMA_VERSION
    assert verified.output_schema_version == provider.OUTPUT_SCHEMA_VERSION
    assert verified.test_corpus_version == provider.CORPUS_VERSION


def test_archive_can_be_read_only_and_verification_does_not_change_it(deployment_case):
    document, release, _ = deployment_case
    path = Path(document["deployments"][0]["archive_path"])
    path.chmod(0o444)
    before = path.read_bytes(), path.stat().st_mode
    registry = _parse(document).build_registry()
    registry.verify_release(release, now=datetime.now(UTC))
    assert (path.read_bytes(), path.stat().st_mode) == before


def test_automatic_handoff_requires_explicit_policy_in_the_existing_source_binding(deployment_case):
    document, release, _ = deployment_case
    assert _parse(document).matching_policy_versions == ()
    document["deployments"][0]["matching_policy_version_id"] = "synthetic-approved-matching-v1"
    selected = _parse(document)
    assert selected.matching_policy_versions == ((SOURCE_ID, "synthetic-approved-matching-v1"),)
    assert selected.build_registry().verify_release(release, now=datetime.now(UTC)).actual_corpus_cases == 4


@pytest.mark.parametrize("policy", [None, "", " x", "x\n", "x" * 513, 1, {}])
def test_bad_handoff_policy_does_not_silently_disable_automation(deployment_case, policy):
    document, _, _ = deployment_case
    document["deployments"][0]["matching_policy_version_id"] = policy
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


def test_absence_preserves_owner_construction_without_positive_capability(monkeypatch):
    from modules.listing.application.native_intake_parser import NativeIntakeParserOwner
    from modules.listing.application.native_intake_parser_policy import NativeParserUnavailable
    from modules.listing.application.native_intake_parser_prerequisites import (
        NativeParserAdmissionPrerequisites,
    )

    monkeypatch.delenv(startup.DEPLOYMENTS_ENV, raising=False)
    configured = startup.native_parser_deployments()
    assert configured.configured is False
    assert configured.deployments == ()
    assert configured.build_registry() is None
    graph = SimpleNamespace(engine=object(), queue=object())
    owner = NativeIntakeParserOwner(graph, artifact_registry=configured.build_registry())
    assert owner.artifact_registry is None
    with pytest.raises(startup.NativeParserDeploymentSelectionUnavailable):
        configured.release_for_source(SOURCE_ID)
    with pytest.raises(NativeParserUnavailable, match="NATIVE_PARSER_ARTIFACT_REGISTRY_REQUIRED"):
        NativeParserAdmissionPrerequisites(graph, owner.artifact_registry)


def test_loader_reads_only_its_dedicated_startup_setting(deployment_case, monkeypatch):
    document, _, _ = deployment_case

    class StartupEnvironment:
        def get(self, key):
            assert key == startup.DEPLOYMENTS_ENV
            return json.dumps(document)

    monkeypatch.setattr(startup, "os", SimpleNamespace(environ=StartupEnvironment()))
    assert startup.native_parser_deployments().release_for_source(SOURCE_ID) == RELEASE_ID


def test_startup_selection_is_immutable_and_does_not_follow_environment_changes(deployment_case, monkeypatch):
    document, _, _ = deployment_case
    monkeypatch.setenv(startup.DEPLOYMENTS_ENV, json.dumps(document))
    configured = startup.native_parser_deployments()
    monkeypatch.setenv(startup.DEPLOYMENTS_ENV, "malformed")
    document["deployments"][0]["parser_release_id"] = str(UUID(int=9))
    assert configured.release_for_source(SOURCE_ID) == RELEASE_ID
    with pytest.raises(FrozenInstanceError):
        configured.deployments = ()
    with pytest.raises(FrozenInstanceError):
        configured.deployments[0].parser_release_id = str(UUID(int=9))
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        startup.native_parser_deployments()


@pytest.mark.parametrize("raw", ["", " ", "null", "[]", "true", "1", "\"text\"", "{", "{}",
    '{"version":1,"deployments":[]}', '{"version":1,"deployments":NaN}',
    '{"version":1,"deployments":Infinity}', '{"version":1,"deployments":-Infinity}',
    "[" * 2000 + "]" * 2000, "x" * (startup.MAX_CONFIG_BYTES + 1), "\ud800",
    b"{}", {}, [], True, 1])
def test_configured_invalid_values_are_not_absence(raw):
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected,
            match="^NATIVE_PARSER_DEPLOYMENT_CONFIGURATION_REJECTED$"):
        startup.parse_native_parser_deployments(raw)


@pytest.mark.parametrize("key,value", [("version", True), ("version", 1.0), ("version", "1"),
    ("version", 0), ("version", 2), ("version", None), ("deployments", {}),
    ("deployments", None), ("deployments", "[]"), ("deployments", [None]),
    ("deployments", [True]), ("deployments", [[{}]])])
def test_closed_top_level_types_and_version(deployment_case, key, value):
    document, _, _ = deployment_case
    document[key] = value
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


@pytest.mark.parametrize("key", ["version", "deployments"])
def test_missing_top_level_fields_are_rejected(deployment_case, key):
    document, _, _ = deployment_case
    del document[key]
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


@pytest.mark.parametrize("location", ["top", "row"])
@pytest.mark.parametrize("key", ["import", "executable", "credentials", "database_uri", "latest", "enabled"])
def test_no_additional_execution_credentials_or_selection_fields(deployment_case, location, key):
    document, _, _ = deployment_case
    target = document if location == "top" else document["deployments"][0]
    target[key] = "synthetic-untrusted-value"
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


@pytest.mark.parametrize("key", ["parser_release_id", "source_id", "artifact_uri", "archive_path"])
@pytest.mark.parametrize("value", [None, True, 1, 1.0, [], {}])
def test_row_type_substitutions_are_rejected(deployment_case, key, value):
    document, _, _ = deployment_case
    document["deployments"][0][key] = value
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


@pytest.mark.parametrize("key", ["parser_release_id", "source_id", "artifact_uri", "archive_path"])
def test_missing_row_fields_are_rejected(deployment_case, key):
    document, _, _ = deployment_case
    del document["deployments"][0][key]
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


@pytest.mark.parametrize("key", ["version", "deployments", "parser_release_id", "source_id", "artifact_uri", "archive_path"])
def test_duplicate_keys_are_rejected_even_when_equal_or_escaped(deployment_case, key):
    document, _, _ = deployment_case
    raw = json.dumps(document)
    value = document[key] if key in document else document["deployments"][0][key]
    escaped_key = f'"\\u{ord(key[0]):04x}{key[1:]}"'
    duplicate = f"{json.dumps(key)}: {json.dumps(value)}, {escaped_key}:"
    raw = raw.replace(f"{json.dumps(key)}:", duplicate, 1)
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        startup.parse_native_parser_deployments(raw)


@pytest.mark.parametrize("value", [str(UUID(int=0)), RELEASE_ID.upper(), RELEASE_ID.replace("-", ""),
    "{" + RELEASE_ID + "}", " " + RELEASE_ID, "not-a-uuid"])
def test_release_id_requires_nonzero_canonical_uuid(deployment_case, value):
    document, _, _ = deployment_case
    document["deployments"][0]["parser_release_id"] = value
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


@pytest.mark.parametrize("key,value", [("source_id", ""), ("source_id", " "), ("source_id", " source"),
    ("source_id", "source\n"), ("source_id", "a\x7fb"), ("source_id", "x" * 257),
    ("source_id", "\ud800"), ("artifact_uri", ""), ("artifact_uri", "not-a-uri"),
    ("artifact_uri", "gs://bucket/with space.zip"), ("artifact_uri", "gs://bucket/a\x00.zip"),
    ("artifact_uri", "https://user:password@synthetic.invalid/a.zip"),
    ("artifact_uri", "https://synthetic.invalid/a.zip?token=synthetic"),
    ("artifact_uri", "gs://bucket/a.zip#fragment"), ("artifact_uri", "https://[broken/a.zip"),
    ("artifact_uri", "gs://bucket/" + "x" * 2048), ("artifact_uri", "gs://bucket/\ud800")])
def test_invalid_source_and_artifact_identities_are_rejected(deployment_case, key, value):
    document, _, _ = deployment_case
    document["deployments"][0][key] = value
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


@pytest.mark.parametrize("case", ["duplicate", "same_source", "same_release"])
def test_ambiguous_source_or_release_never_selects_first_or_latest(deployment_case, case):
    document, _, _ = deployment_case
    second = dict(document["deployments"][0])
    if case == "same_source":
        second["parser_release_id"] = str(UUID(int=3))
    elif case == "same_release":
        second["source_id"] = "different-source"
    document["deployments"].append(second)
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


def test_at_most_100_explicit_source_release_selections(deployment_case):
    document, _, _ = deployment_case
    template = document["deployments"][0]
    document["deployments"] = [{**template, "parser_release_id": str(UUID(int=i + 1)),
        "source_id": f"source-{i}"} for i in range(100)]
    configured = _parse(document)
    for i in range(100):
        assert configured.release_for_source(f"source-{i}") == str(UUID(int=i + 1))
    with pytest.raises(startup.NativeParserDeploymentSelectionUnavailable):
        configured.release_for_source("unknown-source")
    document["deployments"].append({**template, "parser_release_id": str(UUID(int=101)),
        "source_id": "source-100"})
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


def test_configuration_limit_counts_utf8_bytes_not_just_characters(deployment_case):
    document, _, _ = deployment_case
    document["deployments"][0]["source_id"] = "界" * 256
    raw = json.dumps(document, ensure_ascii=False)
    raw += " " * (startup.MAX_CONFIG_BYTES - len(raw))
    assert len(raw) == startup.MAX_CONFIG_BYTES
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        startup.parse_native_parser_deployments(raw)


@pytest.mark.parametrize("value", ["", "manual.zip", "./manual.zip", "~/manual.zip", "$HOME/manual.zip",
    "https://synthetic.invalid/manual.zip", "file:///srv/manual.zip", "C:\\manual.zip",
    "/srv/../manual.zip", "/srv/./manual.zip", "//srv/manual.zip", "/srv//manual.zip",
    "/srv/manual.zip/", "/srv/manual\x00.zip", "/srv/manual\n.zip", "/" + "x" * 4096])
def test_invalid_or_noncanonical_local_paths_are_rejected(deployment_case, value):
    document, _, _ = deployment_case
    document["deployments"][0]["archive_path"] = value
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "parent_symlink", "fifo", "empty", "oversize"])
def test_local_bindings_require_bounded_regular_files_without_symlinks(deployment_case, tmp_path, kind):
    document, _, _ = deployment_case
    original = Path(document["deployments"][0]["archive_path"])
    path = tmp_path / "invalid-archive"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(original)
    elif kind == "parent_symlink":
        path.symlink_to(tmp_path, target_is_directory=True)
        path /= original.name
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "empty":
        path.touch()
    elif kind == "oversize":
        with path.open("wb") as stream:
            stream.truncate(artifacts.MAX_ARCHIVE_BYTES + 1)
    document["deployments"][0]["archive_path"] = str(path)
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        _parse(document)


def test_archive_path_is_rechecked_when_building_registry(deployment_case, tmp_path):
    document, _, _ = deployment_case
    configured = _parse(document)
    path = configured.deployments[0].archive_path
    renamed = tmp_path / "renamed.zip"
    path.rename(renamed)
    path.symlink_to(renamed)
    with pytest.raises(startup.NativeParserDeploymentConfigurationRejected):
        configured.build_registry()


def test_remote_artifact_identity_uses_only_local_archive(deployment_case, monkeypatch):
    import socket
    import urllib.request

    document, release, _ = deployment_case
    uri = "https://synthetic.invalid/never-download.zip"
    document["deployments"][0]["artifact_uri"] = release["artifact_uri"] = uri

    def forbidden(*args, **kwargs):
        pytest.fail("Startup and registry verification must not perform network IO")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    registry = _parse(document).build_registry()
    assert registry.verify_release(release, now=datetime.now(UTC)).archive_uri == uri


@pytest.mark.parametrize("member", ["provider.py", "input-schema.json", "output-schema.json", "corpus.json"])
def test_rehashed_archive_cannot_replace_fixed_installed_code_or_specs(deployment_case, member):
    document, release, content = deployment_case
    registry = _parse(document).build_registry()
    registry.verify_release(release, now=datetime.now(UTC))
    content[member] += b"\n"
    release["artifact_sha256"] = _write_archive(Path(document["deployments"][0]["archive_path"]), content)
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release(release, now=datetime.now(UTC))


def test_installed_drift_is_not_hidden_by_matching_replacement_archive(deployment_case, tmp_path, monkeypatch):
    document, release, content = deployment_case
    installed = {}
    for index, (name, data) in enumerate(content.items()):
        path = tmp_path / f"installed-{index}"
        path.write_bytes(data)
        installed[name] = path
    monkeypatch.setattr(artifacts, "_INSTALLED", installed)
    registry = _parse(document).build_registry()
    registry.verify_release(release, now=datetime.now(UTC))
    content["provider.py"] += b"\n"
    installed["provider.py"].write_bytes(content["provider.py"])
    release["artifact_sha256"] = _write_archive(Path(document["deployments"][0]["archive_path"]), content)
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release(release, now=datetime.now(UTC))


@pytest.mark.parametrize("member", ["input-schema.json", "output-schema.json", "corpus.json"])
def test_matching_installed_archive_still_requires_valid_schema_and_executed_corpus(
        deployment_case, tmp_path, monkeypatch, member):
    document, release, content = deployment_case
    changed = json.loads(content[member])
    if member == "corpus.json":
        changed["cases"][0]["expected_normalized"] = {}
    else:
        changed["x-schema-version"] = "unregistered-schema"
    content[member] = json.dumps(changed).encode()
    installed = {}
    for index, (name, data) in enumerate(content.items()):
        path = tmp_path / f"installed-{index}"
        path.write_bytes(data)
        installed[name] = path
    monkeypatch.setattr(artifacts, "_INSTALLED", installed)
    release["artifact_sha256"] = _write_archive(Path(document["deployments"][0]["archive_path"]), content)
    registry = _parse(document).build_registry()
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release(release, now=datetime.now(UTC))


def test_runtime_provider_replacement_remains_rejected(deployment_case, monkeypatch):
    document, release, _ = deployment_case
    registry = _parse(document).build_registry()
    registry.verify_release(release, now=datetime.now(UTC))
    monkeypatch.setattr(provider, "parse_manual_evidence", lambda *args, **kwargs: None)
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release(release, now=datetime.now(UTC))


@pytest.mark.parametrize("key,value", [("parser_release_id", str(UUID(int=7))), ("source_id", "other-source"),
    ("artifact_uri", "gs://other-bucket/other.zip"), ("package_name", "os.system"),
    ("input_schema_version", "unknown"), ("output_schema_version", "unknown"),
    ("test_corpus_version", "unknown"), ("validation_status", "DRAFT"),
    ("released_by", None), ("deprecated_at", "2026-01-01T00:00:00+00:00")])
def test_startup_mapping_does_not_override_catalog_or_release_checks(deployment_case, key, value):
    document, release, _ = deployment_case
    registry = _parse(document).build_registry()
    registry.verify_release(release, now=datetime.now(UTC))
    with pytest.raises(artifacts.NativeParserArtifactRejected):
        registry.verify_release({**release, key: value}, now=datetime.now(UTC))
