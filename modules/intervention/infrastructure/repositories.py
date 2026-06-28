"""In-memory persistence for the InterventionOps lifecycle.

The repository keeps a per-store index so conflict / overlap checks can resolve
the other interventions competing for a store's timeline in one lookup. The
label registry is the default :class:`LabelRegistryHook`: it stores matured
intervention labels and exposes the intervened windows ForecastOps must exclude
or mark in its organic baseline (ODP-MOD-05 AC-05-05).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from modules.intervention.domain.lifecycle import Intervention, LabelRecord


@dataclass
class InMemoryInterventionRepository:
    _by_id: dict[str, Intervention] = field(default_factory=dict)
    _by_store: dict[str, list[str]] = field(default_factory=dict)

    def save(self, intervention: Intervention) -> Intervention:
        """Upsert an intervention, keeping the per-store index in sync."""
        if intervention.intervention_id not in self._by_id:
            self._by_store.setdefault(intervention.store_id, []).append(
                intervention.intervention_id
            )
        self._by_id[intervention.intervention_id] = intervention
        return intervention

    def get(self, intervention_id: str) -> Intervention | None:
        return self._by_id.get(intervention_id)

    def list_all(self) -> list[Intervention]:
        return list(self._by_id.values())

    def list_by_store(self, store_id: str) -> list[Intervention]:
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
