"""In-memory persistence for the InterventionOps lifecycle.

The repository keeps a per-store index so conflict / overlap checks can resolve
the other interventions competing for a store's timeline in one lookup. The
label registry is the default :class:`LabelRegistryHook`: it stores matured
intervention labels and exposes the intervened windows ForecastOps must exclude
or mark in its organic baseline (ODP-MOD-05 AC-05-05).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from modules.intervention.domain.lifecycle import (
    ACTIVE_INTERVENTION_STATUSES,
    Intervention,
    InterventionError,
    InterventionStatus,
    LabelRecord,
)


@dataclass
class InMemoryInterventionRepository:
    _by_id: dict[str, Intervention] = field(default_factory=dict)
    _by_store: dict[str, list[str]] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def save(self, intervention: Intervention) -> Intervention:
        """Upsert an intervention, keeping the per-store index in sync."""
        with self._lock:
            existing = self._by_id.get(intervention.intervention_id)
            if existing is not None:
                if existing.replacement_id:
                    if (
                        intervention.replacement_id != existing.replacement_id
                        or intervention.status != InterventionStatus.STOPPED
                    ):
                        raise InterventionError(
                            f"stale update: intervention {intervention.intervention_id} is already stopped and replaced by {existing.replacement_id}"
                        )
                if intervention.replacement_id is not None and intervention.status == InterventionStatus.STOPPED:
                    if existing.status not in ACTIVE_INTERVENTION_STATUSES:
                        raise InterventionError(
                            f"stale update: intervention {intervention.intervention_id} status is {existing.status.value}, cannot adjust"
                        )
                    if existing.version >= intervention.version:
                        raise InterventionError(
                            f"stale update: intervention {intervention.intervention_id} was modified concurrently (current version {existing.version}, saving version {intervention.version})"
                        )
                elif existing.version > intervention.version:
                    raise InterventionError(
                        f"stale update: intervention {intervention.intervention_id} was modified concurrently (current version {existing.version}, saving version {intervention.version})"
                    )
            if intervention.intervention_id not in self._by_id:
                self._by_store.setdefault(intervention.store_id, []).append(
                    intervention.intervention_id
                )
            self._by_id[intervention.intervention_id] = intervention
            return intervention

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Apply every ``save`` made inside the block together, or none.

        Mirrors ``DurableInterventionRepository.atomic`` so a caller that needs
        two saves to land as a pair -- stopping an intervention and opening its
        replacement -- behaves identically on both repositories. The indexes
        are restored from a snapshot rather than undone write by write, so a
        failure anywhere in the block is equivalent to never having entered it.
        """
        with self._lock:
            by_id = dict(self._by_id)
            by_store = {store: list(ids) for store, ids in self._by_store.items()}
            try:
                yield
            except BaseException:
                self._by_id = by_id
                self._by_store = by_store
                raise

    def get(self, intervention_id: str, *, for_update: bool = False) -> Intervention | None:
        with self._lock:
            return self._by_id.get(intervention_id)

    def get_for_update(self, intervention_id: str) -> Intervention | None:
        return self.get(intervention_id, for_update=True)

    def list_all(self) -> list[Intervention]:
        with self._lock:
            return list(self._by_id.values())

    def list_by_store(self, store_id: str) -> list[Intervention]:
        with self._lock:
            return [self._by_id[i] for i in self._by_store.get(store_id, []) if i in self._by_id]


@dataclass
class InMemoryLabelRegistry:
    """Default Label Registry hook for intervention effect labels."""

    _labels: dict[str, LabelRecord] = field(default_factory=dict)

    def __call__(self, label: LabelRecord) -> None:
        self._labels[label.intervention_id] = label

    def get(self, intervention_id: str) -> LabelRecord | None:
        return self._labels.get(intervention_id)

    def list_labels(self) -> list[LabelRecord]:
        return list(self._labels.values())

    def intervened_windows(self, store_id: str) -> list[LabelRecord]:
        """Labels whose intervened period a forecast baseline must exclude/mark."""
        return [
            label
            for label in self._labels.values()
            if label.store_id == store_id and label.exclude_from_baseline
        ]


__all__ = ["InMemoryInterventionRepository", "InMemoryLabelRegistry"]
