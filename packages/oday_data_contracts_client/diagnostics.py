"""Expose the exact pinned foundation version to runtime callers.

`foundation_version()` is what an API health route, a worker startup log, or an
operator diagnostic should report. It verifies before it answers: the version
string is only produced once the vendored artifacts match their checksums and
the released catalog still satisfies the pin, so "which foundation are we on?"
and "is that foundation still the one we validated against?" cannot disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .compatibility import verify_release
from .release import FoundationRelease, load_release


@dataclass(frozen=True, slots=True)
class ContractInfo:
    """One pinned contract, as reported at runtime."""

    contract_id: str
    category: str
    contract_version: str
    sha256: str
    module: str

    def as_dict(self) -> dict[str, str]:
        return {
            "contract_id": self.contract_id,
            "category": self.category,
            "contract_version": self.contract_version,
            "sha256": self.sha256,
            "module": self.module,
        }


@dataclass(frozen=True, slots=True)
class FoundationVersion:
    """The exact released foundation package this process is running against."""

    client_contract: str
    release_id: str
    release_name: str
    semantic_version: str
    content_digest: str
    status: str
    published_at: str
    owner_task_id: str
    producer_repository: str
    producer_commit_sha: str
    producer_release_path: str
    contract_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_contract": self.client_contract,
            "release_id": self.release_id,
            "release_name": self.release_name,
            "semantic_version": self.semantic_version,
            "content_digest": self.content_digest,
            "status": self.status,
            "published_at": self.published_at,
            "owner_task_id": self.owner_task_id,
            "producer_repository": self.producer_repository,
            "producer_commit_sha": self.producer_commit_sha,
            "producer_release_path": self.producer_release_path,
            "contract_count": self.contract_count,
        }

    def __str__(self) -> str:
        return (
            f"{self.release_id} (semver {self.semantic_version}) "
            f"from {self.producer_repository}@{self.producer_commit_sha[:12]}"
        )


def _version_of(release: FoundationRelease) -> FoundationVersion:
    pin = release.pin
    return FoundationVersion(
        client_contract=pin.client_contract,
        release_id=pin.release.id,
        release_name=pin.release.name,
        semantic_version=pin.release.semantic_version,
        content_digest=pin.release.content_digest,
        status=pin.release.status,
        published_at=pin.release.published_at,
        owner_task_id=pin.release.owner_task_id,
        producer_repository=pin.source.repository,
        producer_commit_sha=pin.source.commit_sha,
        producer_release_path=pin.source.release_path,
        contract_count=len(pin.enforced_contracts),
    )


@lru_cache(maxsize=1)
def _verified_release() -> FoundationRelease:
    release = load_release()
    verify_release(release)
    return release


def foundation_version() -> FoundationVersion:
    """Return the verified foundation version. Raises if the pin no longer holds."""
    return _version_of(_verified_release())


def foundation_contracts() -> tuple[ContractInfo, ...]:
    """Return every pinned contract this client generates models for."""
    pin = _verified_release().pin
    return tuple(
        ContractInfo(
            contract_id=pinned.contract_id,
            category=pinned.category,
            contract_version=pinned.contract_version,
            sha256=pinned.sha256,
            module=pinned.module,
        )
        for pinned in pin.enforced_contracts
    )


def diagnostics() -> dict[str, Any]:
    """A JSON-serialisable runtime diagnostic block for health/version surfaces."""
    version = foundation_version()
    return {
        "foundation": version.as_dict(),
        "contracts": [contract.as_dict() for contract in foundation_contracts()],
    }


def reset_cache() -> None:
    """Drop the verified-release cache. Intended for tests."""
    _verified_release.cache_clear()
