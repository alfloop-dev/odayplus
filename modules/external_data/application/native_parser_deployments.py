"""Trusted, default-unset startup bindings for the fixed manual parser.

ODP_NATIVE_INTAKE_PARSER_DEPLOYMENTS_JSON has this closed version-1 shape::

    {"version": 1, "deployments": [{"parser_release_id": "<canonical UUID>",
      "source_id": "<source>", "artifact_uri": "gs://packages/manual.zip",
      "archive_path": "/srv/parser/manual.zip"}]}

Each source and release occurs exactly once: the deployment itself is the
explicit source-to-release selection. An artifact URI is only an identity to
compare with catalog metadata; it is never fetched. Archives are local files
read by the existing registry, never extracted, installed or imported.
An optional matching_policy_version_id on a deployment opts that source into
the existing scheduler's parser/matching/exact handoff. This reference selects
an existing DATA_OWNER approval and grants no authority itself. Older bindings
without the reference continue to support explicit parser admission only.

Only an absent environment variable means unconfigured. Empty or malformed
configuration raises instead of silently disabling positive checks. Absence
can supply a None registry to historical/owned-negative control paths. Neither
a deployment nor a verified artifact grants source, human or runtime authority.
The owner must still verify current authority and registry bytes at every
positive boundary; startup selection never queries or ranks catalog rows.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from modules.external_data.application.native_parser_artifacts import (
    MAX_ARCHIVE_BYTES,
    NativeParserArtifactDeployment,
    NativeParserArtifactRegistry,
)

DEPLOYMENTS_ENV = "ODP_NATIVE_INTAKE_PARSER_DEPLOYMENTS_JSON"
MAX_CONFIG_BYTES = 65_536
MAX_DEPLOYMENTS = 100
_ROW_KEYS = frozenset({"parser_release_id", "source_id", "artifact_uri", "archive_path"})
_AUTOMATIC_KEYS = _ROW_KEYS | {"matching_policy_version_id"}


class NativeParserDeploymentConfigurationRejected(ValueError):
    """Configured startup data is invalid, not absent."""


class NativeParserDeploymentSelectionUnavailable(RuntimeError):
    """No explicitly configured release exists for the requested source."""


def _reject():
    # Do not echo configuration, paths or untrusted values in diagnostics.
    raise NativeParserDeploymentConfigurationRejected(
        "NATIVE_PARSER_DEPLOYMENT_CONFIGURATION_REJECTED"
    ) from None


def _closed_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject()
        result[key] = value
    return result


def _invalid_number(_value):
    _reject()


def _text(value, maximum):
    if (type(value) is not str or not value or value != value.strip()
            or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _reject()
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        _reject()
    return value


def _archive_path(value):
    value = _text(value, 4096)
    path = Path(value)
    if (not path.is_absolute() or str(path) != value or path.anchor != "/"
            or ".." in path.parts or len(value.encode("utf-8")) > 4096):
        _reject()
    # lstat never opens a FIFO/device. Reject symlink aliases, including parent
    # directories, without resolving an untrusted path into another binding.
    try:
        for parent in reversed(path.parents):
            if not stat.S_ISDIR(parent.lstat().st_mode):
                _reject()
        observed = path.lstat()
        if (not stat.S_ISREG(observed.st_mode)
                or not 1 <= observed.st_size <= MAX_ARCHIVE_BYTES):
            _reject()
    except (OSError, ValueError):
        _reject()
    return path


def _artifact_uri(value):
    value = _text(value, 2048)
    try:
        parsed = urlsplit(value)
        # This is an inert artifact identity, not a credential or download URL.
        if (not parsed.scheme or not parsed.path or any(c.isspace() for c in value)
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            _reject()
    except ValueError:
        _reject()
    return value


@dataclass(frozen=True)
class NativeParserDeployments:
    """Immutable startup selection; an empty tuple represents absence only."""

    deployments: tuple[NativeParserArtifactDeployment, ...] = ()
    matching_policy_versions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if type(self.deployments) is not tuple or len(self.deployments) > MAX_DEPLOYMENTS:
            _reject()
        sources, releases = set(), set()
        for item in self.deployments:
            if type(item) is not NativeParserArtifactDeployment:
                _reject()
            _text(item.source_id, 256)
            _artifact_uri(item.archive_uri)
            _archive_path(str(item.archive_path))
            if item.source_id in sources or item.parser_release_id in releases:
                _reject()
            sources.add(item.source_id)
            releases.add(item.parser_release_id)
        if type(self.matching_policy_versions) is not tuple:
            _reject()
        selected = set()
        for binding in self.matching_policy_versions:
            if type(binding) is not tuple or len(binding) != 2:
                _reject()
            source, policy = binding
            _text(source, 256)
            _text(policy, 512)
            if source not in sources or source in selected:
                _reject()
            selected.add(source)

    @property
    def configured(self) -> bool:
        return bool(self.deployments)

    def release_for_source(self, source_id: str) -> str:
        """Select only the release bound at startup, without a catalog fallback."""
        _text(source_id, 256)
        for item in self.deployments:
            if item.source_id == source_id:
                return item.parser_release_id
        raise NativeParserDeploymentSelectionUnavailable(
            "NATIVE_PARSER_DEPLOYMENT_SELECTION_UNAVAILABLE"
        ) from None

    def build_registry(self) -> NativeParserArtifactRegistry | None:
        """Keep absence usable by control paths; never cache verified artifacts."""
        if not self.configured:
            return None
        # Recheck local bindings if registry construction follows parsing later.
        for item in self.deployments:
            _archive_path(str(item.archive_path))
        return NativeParserArtifactRegistry(self.deployments)


def parse_native_parser_deployments(raw: str | None) -> NativeParserDeployments:
    """Parse trusted startup JSON, never job, Intake or request data."""
    if raw is None:
        return NativeParserDeployments()
    try:
        if (type(raw) is not str or not 1 <= len(raw) <= MAX_CONFIG_BYTES
                or len(raw.encode("utf-8", errors="strict")) > MAX_CONFIG_BYTES):
            _reject()
        value = json.loads(raw, object_pairs_hook=_closed_object, parse_constant=_invalid_number)
        if (type(value) is not dict or set(value) != {"version", "deployments"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["deployments"]) is not list
                or not 1 <= len(value["deployments"]) <= MAX_DEPLOYMENTS):
            _reject()
        deployments, policies = [], []
        for row in value["deployments"]:
            if (type(row) is not dict or set(row) not in (_ROW_KEYS, _AUTOMATIC_KEYS)
                    or any(type(field) is not str for field in row.values())):
                _reject()
            deployments.append(NativeParserArtifactDeployment(
                parser_release_id=row["parser_release_id"], source_id=row["source_id"],
                archive_uri=row["artifact_uri"], archive_path=Path(row["archive_path"]),
            ))
            # Validate the original text before Path can normalize it.
            _archive_path(row["archive_path"])
            if "matching_policy_version_id" in row:
                policies.append((row["source_id"], row["matching_policy_version_id"]))
        return NativeParserDeployments(tuple(deployments), tuple(policies))
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        _reject()


def native_parser_deployments() -> NativeParserDeployments:
    """Read only the parser-specific startup setting; no implicit defaults."""
    return parse_native_parser_deployments(os.environ.get(DEPLOYMENTS_ENV))
