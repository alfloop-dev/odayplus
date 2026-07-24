"""Evidently-backed drift snapshots for released model inputs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pandas as pd

from models.shared_ml import OssCapability, require_oss_capability


@dataclass(frozen=True)
class EvidentlyDriftResult:
    snapshot_id: str
    drift_detected: bool
    drifted_columns: int
    drift_share: float
    report_json: str
    engine: str = "evidently"

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "drift_detected": self.drift_detected,
            "drifted_columns": self.drifted_columns,
            "drift_share": self.drift_share,
            "engine": self.engine,
            "report": json.loads(self.report_json),
        }


class EvidentlyDriftMonitor:
    def run(
        self,
        *,
        reference_rows: Sequence[Mapping[str, Any]],
        current_rows: Sequence[Mapping[str, Any]],
        drift_share_threshold: float = 0.5,
        snapshot_id: str | None = None,
    ) -> EvidentlyDriftResult:
        if not reference_rows or not current_rows:
            raise ValueError("Evidently drift monitoring requires reference and current rows")
        require_oss_capability(OssCapability.MODEL_MONITORING)
        from evidently import Report
        from evidently.presets import DataDriftPreset

        reference = pd.DataFrame(reference_rows)
        current = pd.DataFrame(current_rows)
        if set(reference.columns) != set(current.columns):
            raise ValueError("reference and current drift datasets must have identical columns")

        evaluation = Report(
            [DataDriftPreset(drift_share=drift_share_threshold)]
        ).run(current, reference)
        payload = evaluation.dict()
        summary = next(
            (
                metric.get("value", {})
                for metric in payload.get("metrics", [])
                if metric.get("metric_name", "").startswith("DriftedColumnsCount")
            ),
            {},
        )
        count = int(summary.get("count", 0) or 0)
        share = float(summary.get("share", 0.0) or 0.0)
        return EvidentlyDriftResult(
            snapshot_id=snapshot_id or f"evidently-{uuid4()}",
            drift_detected=share >= drift_share_threshold,
            drifted_columns=count,
            drift_share=share,
            report_json=evaluation.json(),
        )


__all__ = ["EvidentlyDriftMonitor", "EvidentlyDriftResult"]
