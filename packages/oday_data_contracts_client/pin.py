"""Read `config/oday_data_contracts.toml`, the foundation release pin.

The pin is the only place that names a producer version. Everything else in
this package — the vendored release artifacts, the compatibility gate, the
generated models — is derived from it, so a pin that does not parse is a hard
failure rather than a fallback to "latest".
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import PinError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PIN_PATH = REPO_ROOT / "config" / "oday_data_contracts.toml"

SUPPORTED_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReleasePin:
    """Identity of the released foundation package this consumer is pinned to."""

    id: str
    name: str
    type: str
    semantic_version: str
    status: str
    content_digest: str
    internal_registry_digest: str
    owner_task_id: str
    published_at: str


@dataclass(frozen=True, slots=True)
class SourcePin:
    """Exact producer revision the vendored artifacts were taken from."""

    repository: str
    branch: str
    commit_sha: str
    release_path: str


@dataclass(frozen=True, slots=True)
class CompatibilityPin:
    """The compatibility envelope the consumer accepts."""

    required_contract_id: str
    required_semantic_version: str
    required_compatibility_mode: str
    consumer_client_contract: str
    allow_breaking_change: bool
    supported_release_versions: tuple[str, ...]
    enforced_categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VendorPin:
    """Where the release artifacts and generated models live, and their digests."""

    release_root: Path
    generated_root: Path
    artifacts: Mapping[str, str]
    excluded: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class PinnedContract:
    """One contract schema the generated client is built from."""

    contract_id: str
    category: str
    contract_version: str
    schema_file: str
    sha256: str
    module: str


@dataclass(frozen=True, slots=True)
class ContractPin:
    """The parsed `config/oday_data_contracts.toml` document."""

    path: Path
    schema_version: int
    client_contract: str
    release: ReleasePin
    source: SourcePin
    compatibility: CompatibilityPin
    vendor: VendorPin
    contracts: tuple[PinnedContract, ...]

    def contract(self, contract_id: str) -> PinnedContract:
        for pinned in self.contracts:
            if pinned.contract_id == contract_id:
                return pinned
        raise PinError(f"{contract_id!r} is not pinned in {self.path}")

    def contracts_in(self, *categories: str) -> tuple[PinnedContract, ...]:
        wanted = set(categories)
        return tuple(c for c in self.contracts if c.category in wanted)

    @property
    def enforced_contracts(self) -> tuple[PinnedContract, ...]:
        return self.contracts_in(*self.compatibility.enforced_categories)


def _table(document: Mapping[str, Any], key: str, path: Path) -> Mapping[str, Any]:
    value = document.get(key)
    if not isinstance(value, Mapping):
        raise PinError(f"{path}: missing [{key}] table")
    return value


def _text(table: Mapping[str, Any], key: str, path: Path, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PinError(f"{path}: [{where}] {key} must be a non-empty string")
    return value


def _flag(table: Mapping[str, Any], key: str, path: Path, where: str) -> bool:
    value = table.get(key)
    if not isinstance(value, bool):
        raise PinError(f"{path}: [{where}] {key} must be a boolean")
    return value


def _text_tuple(table: Mapping[str, Any], key: str, path: Path, where: str) -> tuple[str, ...]:
    value = table.get(key)
    if not isinstance(value, Sequence) or isinstance(value, str) or not value:
        raise PinError(f"{path}: [{where}] {key} must be a non-empty array of strings")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise PinError(f"{path}: [{where}] {key} must contain only non-empty strings")
    return tuple(value)


def _digest_map(table: Mapping[str, Any], path: Path, where: str) -> Mapping[str, str]:
    if not table:
        raise PinError(f"{path}: [{where}] must not be empty")
    for name, digest in table.items():
        if not isinstance(digest, str) or len(digest) != 64:
            raise PinError(f"{path}: [{where}] {name} must be a sha256 hex digest")
    return dict(table)


def load_pin(path: Path | str | None = None) -> ContractPin:
    """Parse the pin document. Every structural problem raises `PinError`."""
    pin_path = Path(path) if path is not None else DEFAULT_PIN_PATH
    try:
        document = tomllib.loads(pin_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PinError(f"{pin_path}: foundation contract pin is missing") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PinError(f"{pin_path}: foundation contract pin is not valid TOML: {exc}") from exc

    schema_version = document.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise PinError(
            f"{pin_path}: schema_version {schema_version!r} is not supported "
            f"(expected {SUPPORTED_SCHEMA_VERSION})"
        )

    release_table = _table(document, "release", pin_path)
    source_table = _table(document, "source", pin_path)
    compat_table = _table(document, "compatibility", pin_path)
    vendor_table = _table(document, "vendor", pin_path)

    release = ReleasePin(
        id=_text(release_table, "id", pin_path, "release"),
        name=_text(release_table, "name", pin_path, "release"),
        type=_text(release_table, "type", pin_path, "release"),
        semantic_version=_text(release_table, "semantic_version", pin_path, "release"),
        status=_text(release_table, "status", pin_path, "release"),
        content_digest=_text(release_table, "content_digest", pin_path, "release"),
        internal_registry_digest=_text(
            release_table, "internal_registry_digest", pin_path, "release"
        ),
        owner_task_id=_text(release_table, "owner_task_id", pin_path, "release"),
        published_at=_text(release_table, "published_at", pin_path, "release"),
    )
    source = SourcePin(
        repository=_text(source_table, "repository", pin_path, "source"),
        branch=_text(source_table, "branch", pin_path, "source"),
        commit_sha=_text(source_table, "commit_sha", pin_path, "source"),
        release_path=_text(source_table, "release_path", pin_path, "source"),
    )
    compatibility = CompatibilityPin(
        required_contract_id=_text(compat_table, "required_contract_id", pin_path, "compatibility"),
        required_semantic_version=_text(
            compat_table, "required_semantic_version", pin_path, "compatibility"
        ),
        required_compatibility_mode=_text(
            compat_table, "required_compatibility_mode", pin_path, "compatibility"
        ),
        consumer_client_contract=_text(
            compat_table, "consumer_client_contract", pin_path, "compatibility"
        ),
        allow_breaking_change=_flag(
            compat_table, "allow_breaking_change", pin_path, "compatibility"
        ),
        supported_release_versions=_text_tuple(
            compat_table, "supported_release_versions", pin_path, "compatibility"
        ),
        enforced_categories=_text_tuple(
            compat_table, "enforced_categories", pin_path, "compatibility"
        ),
    )
    vendor = VendorPin(
        release_root=REPO_ROOT / _text(vendor_table, "release_root", pin_path, "vendor"),
        generated_root=REPO_ROOT / _text(vendor_table, "generated_root", pin_path, "vendor"),
        artifacts=_digest_map(
            _table(vendor_table, "artifacts", pin_path), pin_path, "vendor.artifacts"
        ),
        excluded=dict(_table(vendor_table, "excluded", pin_path)),
    )

    entries = document.get("contracts")
    if not isinstance(entries, list) or not entries:
        raise PinError(f"{pin_path}: at least one [[contracts]] entry is required")
    contracts: list[PinnedContract] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise PinError(f"{pin_path}: [[contracts]] entry {index} is not a table")
        where = f"contracts[{index}]"
        pinned = PinnedContract(
            contract_id=_text(entry, "contract_id", pin_path, where),
            category=_text(entry, "category", pin_path, where),
            contract_version=_text(entry, "contract_version", pin_path, where),
            schema_file=_text(entry, "schema_file", pin_path, where),
            sha256=_text(entry, "sha256", pin_path, where),
            module=_text(entry, "module", pin_path, where),
        )
        if pinned.contract_id in seen:
            raise PinError(f"{pin_path}: {pinned.contract_id} is pinned more than once")
        if len(pinned.sha256) != 64:
            raise PinError(f"{pin_path}: [{where}] sha256 must be a sha256 hex digest")
        seen.add(pinned.contract_id)
        contracts.append(pinned)

    unpinned = sorted(set(compatibility.enforced_categories) - {c.category for c in contracts})
    if unpinned:
        raise PinError(
            f"{pin_path}: enforced categories with no pinned contract: {', '.join(unpinned)}"
        )

    return ContractPin(
        path=pin_path,
        schema_version=SUPPORTED_SCHEMA_VERSION,
        client_contract=_text(document, "client_contract", pin_path, "root"),
        release=release,
        source=source,
        compatibility=compatibility,
        vendor=vendor,
        contracts=tuple(contracts),
    )
