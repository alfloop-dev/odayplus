from pipelines.orchestration import DagsterTrainingOrchestrator


def test_dagster_runs_quality_training_and_registry_in_order() -> None:
    calls: list[str] = []

    result = DagsterTrainingOrchestrator().run(
        request={"dataset_snapshot_id": "ds-1"},
        quality_gate=lambda request: calls.append("quality")
        or {"dataset_snapshot_id": request["dataset_snapshot_id"], "quality_passed": True},
        trainer=lambda quality: calls.append("training")
        or {"artifact_uri": f"model://{quality['dataset_snapshot_id']}"},
        registrar=lambda model: calls.append("registry")
        or {"registered_uri": model["artifact_uri"]},
    )

    assert result.success is True
    assert calls == ["quality", "training", "registry"]
    assert result.registry_output == {"registered_uri": "model://ds-1"}
    assert result.run_id


def test_dagster_stops_after_failed_quality_gate() -> None:
    calls: list[str] = []

    def reject(_: object) -> dict[str, object]:
        calls.append("quality")
        raise ValueError("quality failed")

    result = DagsterTrainingOrchestrator().run(
        request={"dataset_snapshot_id": "ds-bad"},
        quality_gate=reject,
        trainer=lambda _: calls.append("training") or {},
        registrar=lambda _: calls.append("registry") or {},
    )

    assert result.success is False
    assert result.failed_stage == "quality_gate"
    assert calls == ["quality"]
