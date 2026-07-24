"""Dagster execution boundary for the model training and registration flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from models.shared_ml import OssCapability, require_oss_capability

Stage = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class DagsterTrainingResult:
    success: bool
    run_id: str
    quality_output: Mapping[str, Any] | None
    training_output: Mapping[str, Any] | None
    registry_output: Mapping[str, Any] | None
    failed_stage: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "run_id": self.run_id,
            "quality_output": self.quality_output,
            "training_output": self.training_output,
            "registry_output": self.registry_output,
            "failed_stage": self.failed_stage,
            "engine": "dagster",
        }


class DagsterTrainingOrchestrator:
    def run(
        self,
        *,
        request: Mapping[str, Any],
        quality_gate: Stage,
        trainer: Stage,
        registrar: Stage,
    ) -> DagsterTrainingResult:
        require_oss_capability(OssCapability.TRAINING_ORCHESTRATION)
        from dagster import graph, op

        suffix = uuid4().hex

        @op(name=f"quality_gate_{suffix}")
        def quality_gate_op() -> dict[str, Any]:
            return dict(quality_gate(request))

        @op(name=f"train_model_{suffix}")
        def train_model_op(quality_output: dict[str, Any]) -> dict[str, Any]:
            return dict(trainer(quality_output))

        @op(name=f"register_model_{suffix}")
        def register_model_op(training_output: dict[str, Any]) -> dict[str, Any]:
            return dict(registrar(training_output))

        @graph(name=f"oss_training_graph_{suffix}")
        def training_graph() -> Any:
            return register_model_op(train_model_op(quality_gate_op()))

        quality_name = quality_gate_op.name
        training_name = train_model_op.name
        registry_name = register_model_op.name
        execution = training_graph.to_job(name=f"oss_training_job_{suffix}").execute_in_process(
            raise_on_error=False
        )

        quality_output = _output(execution, quality_name)
        training_output = _output(execution, training_name)
        registry_output = _output(execution, registry_name)
        failed_stage = None
        if not execution.success:
            if quality_output is None:
                failed_stage = "quality_gate"
            elif training_output is None:
                failed_stage = "training"
            else:
                failed_stage = "registry"
        return DagsterTrainingResult(
            success=execution.success,
            run_id=execution.run_id,
            quality_output=quality_output,
            training_output=training_output,
            registry_output=registry_output,
            failed_stage=failed_stage,
        )


def _output(execution: Any, node_name: str) -> Mapping[str, Any] | None:
    try:
        return execution.output_for_node(node_name)
    except Exception:
        return None


__all__ = ["DagsterTrainingOrchestrator", "DagsterTrainingResult"]
