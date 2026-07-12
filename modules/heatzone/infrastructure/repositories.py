"""HeatZone scoring result stores.

``HeatZoneResultStore`` is the in-memory store used by the HeatZone API router;
it keeps the latest batch-score result, an index of prior jobs, and an
idempotency-key index so a replayed score job returns the original ranking
instead of recomputing it. It previously lived inside the API route module —
it is defined here so a durable, restart-surviving twin
(:class:`~shared.infrastructure.persistence.repositories.DurableHeatZoneResultStore`)
can mirror the exact same surface (ODP-FLOW-002).
"""

from __future__ import annotations

from typing import Any

from modules.heatzone.workers import HeatZoneBatchScoreResult


class HeatZoneResultStore:
    """In-memory store of HeatZone batch-score results and their rankings."""

    def __init__(self) -> None:
        self._latest: HeatZoneBatchScoreResult | None = None
        self._jobs: dict[str, HeatZoneBatchScoreResult] = {}
        self._idempotency_index: dict[str, str] = {}

    def put(
        self,
        result: HeatZoneBatchScoreResult,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[HeatZoneBatchScoreResult, bool]:
        if idempotency_key and idempotency_key in self._idempotency_index:
            existing = self._jobs[self._idempotency_index[idempotency_key]]
            return existing, False
        self._jobs[result.job_id] = result
        self._latest = result
        if idempotency_key:
            self._idempotency_index[idempotency_key] = result.job_id
        return result, True

    def find_by_idempotency_key(
        self, idempotency_key: str | None
    ) -> HeatZoneBatchScoreResult | None:
        if not idempotency_key:
            return None
        job_id = self._idempotency_index.get(idempotency_key)
        if job_id is None:
            return None
        return self._jobs.get(job_id)

    def list_scores(self) -> list[dict[str, Any]]:
        if self._latest is None:
            return []
        return [score.to_dict() for score in self._latest.scores]

    def map_features(self) -> list[dict[str, Any]]:
        if self._latest is None:
            return []
        return [score.to_map_feature() for score in self._latest.scores]

    def snapshot(self, snapshot_id: str) -> HeatZoneBatchScoreResult | None:
        if self._latest and snapshot_id == "latest":
            return self._latest
        return self._jobs.get(snapshot_id)


__all__ = ["HeatZoneResultStore"]
