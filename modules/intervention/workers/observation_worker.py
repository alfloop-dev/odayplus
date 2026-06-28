"""Observation-window maturity sweep worker.

Combines the ``reward-window-worker`` / ``outcome-collector`` /
``effect-evaluation-worker`` responsibilities (ODP-MOD-05 §10): it scans
observing interventions, finds those whose observation window has matured, and
optionally evaluates their effect so a mature label is written back to the Label
Registry without manual intervention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from modules.intervention.application.workflow import InterventionWorkflow
from modules.intervention.domain.lifecycle import InterventionStatus


@dataclass
class ObservationSweepResult:
    job_id: str
    swept_at: datetime
    matured_ids: tuple[str, ...]
    pending_ids: tuple[str, ...]
    evaluated_ids: tuple[str, ...]
    status: str = "SUCCEEDED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "swept_at": self.swept_at.isoformat(),
            "matured_ids": list(self.matured_ids),
            "pending_ids": list(self.pending_ids),
            "evaluated_ids": list(self.evaluated_ids),
        }


def run_observation_sweep(
    workflow: InterventionWorkflow,
    *,
    job_id: str,
    now: datetime,
    actor: str = "observation-worker",
    auto_evaluate: bool = False,
) -> ObservationSweepResult:
    """Sweep observing interventions for matured observation windows.

    ``matured_ids`` are observing interventions whose window has matured;
    ``pending_ids`` are still inside their window. When ``auto_evaluate`` is set,
    matured interventions that already have a collected outcome are evaluated and
    appear in ``evaluated_ids``.
    """
    matured: list[str] = []
    pending: list[str] = []
    evaluated: list[str] = []

    for intervention in workflow.list_all():
        if intervention.status is not InterventionStatus.OBSERVING:
            continue
        window = intervention.observation_window
        if window is None:
            continue
        if window.is_mature(now=now):
            matured.append(intervention.intervention_id)
            if auto_evaluate and intervention.outcome is not None:
                workflow.evaluate_effect(
                    intervention.intervention_id, actor=actor, now=now
                )
                evaluated.append(intervention.intervention_id)
        else:
            pending.append(intervention.intervention_id)

    return ObservationSweepResult(
        job_id=job_id,
        swept_at=now,
        matured_ids=tuple(matured),
        pending_ids=tuple(pending),
        evaluated_ids=tuple(evaluated),
    )


__all__ = ["ObservationSweepResult", "run_observation_sweep"]
