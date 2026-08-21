"""Fail closed when the released foundation package drifts away from the pin.

This is the gate the acceptance criterion asks for: an incompatible kernel or
internal schema must fail CI rather than degrade silently at runtime. Every
check below compares the *released* catalog against `config/oday_data_contracts.toml`,
so the failure names the contract that moved and how.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .errors import IncompatibleContractError
from .pin import PinnedContract
from .release import FoundationRelease, canonical_digest, load_release


@dataclass(frozen=True, slots=True)
class ContractDrift:
    """One way a single contract stopped matching the pin."""

    contract_id: str
    category: str
    reason: str
    expected: str
    actual: str

    def __str__(self) -> str:
        return (
            f"{self.contract_id} [{self.category}]: {self.reason} "
            f"(pinned {self.expected!r}, released {self.actual!r})"
        )


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """Outcome of comparing the released package against the pin."""

    release_id: str
    semantic_version: str
    checked_contracts: tuple[str, ...]
    drifts: tuple[ContractDrift, ...] = field(default=())

    @property
    def compatible(self) -> bool:
        return not self.drifts

    def raise_for_drift(self) -> None:
        if self.drifts:
            detail = "\n  - ".join(str(drift) for drift in self.drifts)
            raise IncompatibleContractError(
                "the released ODay data-platform foundation package is not compatible "
                f"with config/oday_data_contracts.toml:\n  - {detail}"
            )


def _release_identity_drifts(release: FoundationRelease) -> Iterator[ContractDrift]:
    pin = release.pin
    manifest = release.manifest
    compat = release.compatibility
    checks: Sequence[tuple[str, str, Any, Any]] = (
        ("release identity changed", "release", pin.release.id, manifest.get("release_id")),
        (
            "release version changed",
            "release",
            pin.release.semantic_version,
            manifest.get("semantic_version"),
        ),
        (
            "release content digest changed",
            "release",
            pin.release.content_digest,
            manifest.get("content_digest"),
        ),
        ("release status changed", "release", pin.release.status, manifest.get("status")),
        (
            "release owner task changed",
            "release",
            pin.release.owner_task_id,
            manifest.get("owner_task_id"),
        ),
        (
            "declared contract id changed",
            "compatibility",
            pin.compatibility.required_contract_id,
            compat.get("contract_id"),
        ),
        (
            "compatibility mode changed",
            "compatibility",
            pin.compatibility.required_compatibility_mode,
            compat.get("compatibility_mode"),
        ),
        (
            "consumer client contract changed",
            "compatibility",
            pin.compatibility.consumer_client_contract,
            compat.get("consumer_client_contract"),
        ),
        (
            "supported consumer versions changed",
            "compatibility",
            list(pin.compatibility.supported_release_versions),
            compat.get("supported_consumer_versions"),
        ),
    )
    for reason, category, expected, actual in checks:
        if expected != actual:
            yield ContractDrift(
                contract_id=pin.release.id,
                category=category,
                reason=reason,
                expected=str(expected),
                actual=str(actual),
            )

    if compat.get("breaking_change") and not pin.compatibility.allow_breaking_change:
        yield ContractDrift(
            contract_id=pin.release.id,
            category="compatibility",
            reason="producer declared a breaking change the consumer has not accepted",
            expected="breaking_change=False",
            actual="breaking_change=True",
        )

    supported = compat.get("supported_consumer_versions")
    if isinstance(supported, list) and pin.release.semantic_version not in supported:
        yield ContractDrift(
            contract_id=pin.release.id,
            category="compatibility",
            reason="pinned version is no longer a supported consumer version",
            expected=pin.release.semantic_version,
            actual=", ".join(str(item) for item in supported),
        )


def _contract_drifts(
    release: FoundationRelease,
    pinned: PinnedContract,
    catalog: Mapping[str, Mapping[str, Any]],
) -> Iterator[ContractDrift]:
    entry = catalog.get(pinned.contract_id)
    if entry is None:
        yield ContractDrift(
            contract_id=pinned.contract_id,
            category=pinned.category,
            reason="contract is no longer published by the foundation release",
            expected=pinned.contract_version,
            actual="<absent>",
        )
        return

    fields = (
        ("category changed", pinned.category, entry.get("category")),
        ("contract version changed", pinned.contract_version, entry.get("contract_version")),
        ("schema file moved", pinned.schema_file, entry.get("schema_file")),
        ("catalog schema digest changed", pinned.sha256, entry.get("sha256")),
    )
    for reason, expected, actual in fields:
        if expected != actual:
            yield ContractDrift(
                contract_id=pinned.contract_id,
                category=pinned.category,
                reason=reason,
                expected=str(expected),
                actual=str(actual),
            )

    schema = release.schemas.get(pinned.schema_file)
    if not isinstance(schema, Mapping):
        yield ContractDrift(
            contract_id=pinned.contract_id,
            category=pinned.category,
            reason="schema document is missing from the released bundle",
            expected=pinned.schema_file,
            actual="<absent>",
        )
        return

    actual_digest = canonical_digest(schema)
    if actual_digest != pinned.sha256:
        yield ContractDrift(
            contract_id=pinned.contract_id,
            category=pinned.category,
            reason="schema content changed under the pin",
            expected=pinned.sha256,
            actual=actual_digest,
        )


def _unpinned_contract_drifts(
    release: FoundationRelease,
    catalog: Mapping[str, Mapping[str, Any]],
) -> Iterator[ContractDrift]:
    """A new contract in an enforced category must be pinned before it is used."""
    enforced = set(release.pin.compatibility.enforced_categories)
    known = {pinned.contract_id for pinned in release.pin.contracts}
    for contract_id, entry in sorted(catalog.items()):
        category = str(entry.get("category", ""))
        if category in enforced and contract_id not in known:
            yield ContractDrift(
                contract_id=contract_id,
                category=category,
                reason="released contract is not pinned by the consumer",
                expected="<pinned>",
                actual=str(entry.get("contract_version", "")),
            )


def check_release(release: FoundationRelease | None = None) -> CompatibilityReport:
    """Compare the released package against the pin and report every drift."""
    release = release or load_release()
    catalog = release.catalog
    drifts: list[ContractDrift] = list(_release_identity_drifts(release))
    enforced = release.pin.enforced_contracts
    for pinned in enforced:
        drifts.extend(_contract_drifts(release, pinned, catalog))
    drifts.extend(_unpinned_contract_drifts(release, catalog))
    return CompatibilityReport(
        release_id=release.release_id,
        semantic_version=release.semantic_version,
        checked_contracts=tuple(pinned.contract_id for pinned in enforced),
        drifts=tuple(drifts),
    )


def verify_release(release: FoundationRelease | None = None) -> CompatibilityReport:
    """Like `check_release`, but raise `IncompatibleContractError` on any drift."""
    report = check_release(release)
    report.raise_for_drift()
    return report
