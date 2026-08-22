"""Load the vendored product release and verify it against the pin.

The release artifacts under `_release/` are byte-for-byte copies of
`contracts/releases/emgi/product/` at the pinned producer commit. They are
vendored so the client — and CI — resolve contracts without network access, and
checksum-verified so a local edit cannot quietly become the contract.

`storage-schema.sql` and `relation-ownership.yaml` describe producer-internal
tables, and this consumer reads contracts, not the producer's DDL.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ArtifactDigestError, IncompatibleContractError
from .pin import ContractPin, PinnedContract, load_pin

RELEASE_MANIFEST = "release.json"
COMPATIBILITY_MANIFEST = "compatibility.json"
SCHEMA_BUNDLE = "schemas.json"
DEPENDENCY_CLOSURE = "dependency-closure.json"


def canonical_digest(schema: Mapping[str, Any]) -> str:
    """Digest a schema document the way the producer's release catalog does."""
    payload = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_verified(root: Path, name: str, expected_sha256: str) -> Any:
    path = root / name
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ArtifactDigestError(f"{path}: vendored release artifact is missing") from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ArtifactDigestError(
            f"{path}: sha256 {actual} does not match the pinned {expected_sha256}; "
            "re-vendor the artifact from the pinned producer commit instead of editing it"
        )
    return json.loads(raw.decode("utf-8"))


@dataclass(frozen=True, slots=True)
class ProductRelease:
    """The verified release bundle: manifest, compatibility record, schemas, closure."""

    pin: ContractPin
    manifest: Mapping[str, Any]
    compatibility: Mapping[str, Any]
    schemas: Mapping[str, Any]
    dependency_closure: Mapping[str, Any]

    @property
    def release_id(self) -> str:
        return str(self.manifest.get("release_id", ""))

    @property
    def semantic_version(self) -> str:
        return str(self.manifest.get("semantic_version", ""))

    @property
    def content_digest(self) -> str:
        return str(self.manifest.get("content_digest", ""))

    @property
    def catalog(self) -> Mapping[str, Mapping[str, Any]]:
        """Released contract catalog, keyed by contract id."""
        entries = self.manifest.get("contract_catalog")
        if not isinstance(entries, list):
            raise IncompatibleContractError(
                "released manifest has no contract_catalog; the release bundle is not usable"
            )
        catalog: dict[str, Mapping[str, Any]] = {}
        for entry in entries:
            if isinstance(entry, Mapping) and isinstance(entry.get("contract_id"), str):
                catalog[entry["contract_id"]] = entry
        return catalog

    def schema_for(self, pinned: PinnedContract) -> Mapping[str, Any]:
        """Return the released schema document for a pinned contract."""
        schema = self.schemas.get(pinned.schema_file)
        if not isinstance(schema, Mapping):
            raise IncompatibleContractError(
                f"{pinned.contract_id}: released bundle has no schema at "
                f"{pinned.schema_file!r}; the contract was removed or renamed upstream"
            )
        return schema


def load_release(pin: ContractPin | None = None) -> ProductRelease:
    """Load and checksum-verify the vendored release artifacts."""
    pin = pin or load_pin()
    root = pin.vendor.release_root
    artifacts = pin.vendor.artifacts
    missing = sorted(
        {RELEASE_MANIFEST, COMPATIBILITY_MANIFEST, SCHEMA_BUNDLE, DEPENDENCY_CLOSURE}
        - set(artifacts)
    )
    if missing:
        raise ArtifactDigestError(
            f"{pin.path}: [vendor.artifacts] does not pin {', '.join(missing)}"
        )

    manifest = _read_verified(root, RELEASE_MANIFEST, artifacts[RELEASE_MANIFEST])
    compatibility = _read_verified(root, COMPATIBILITY_MANIFEST, artifacts[COMPATIBILITY_MANIFEST])
    bundle = _read_verified(root, SCHEMA_BUNDLE, artifacts[SCHEMA_BUNDLE])
    dependency_closure = _read_verified(
        root, DEPENDENCY_CLOSURE, artifacts[DEPENDENCY_CLOSURE]
    )

    schemas = bundle.get("schemas") if isinstance(bundle, Mapping) else None
    if not isinstance(schemas, Mapping):
        raise ArtifactDigestError(f"{root / SCHEMA_BUNDLE}: bundle has no 'schemas' mapping")

    for excluded in pin.vendor.excluded:
        if (root / excluded).exists():
            raise ArtifactDigestError(
                f"{root / excluded}: producer implementation artifact must not be vendored "
                f"({pin.vendor.excluded[excluded]})"
            )

    return ProductRelease(
        pin=pin,
        manifest=manifest,
        compatibility=compatibility,
        schemas=schemas,
        dependency_closure=dependency_closure,
    )
