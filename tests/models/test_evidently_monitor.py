from modules.learninghub.infrastructure import EvidentlyDriftMonitor


def test_evidently_monitor_persists_real_report_payload() -> None:
    reference = [{"demand": value, "rent": 100 + value} for value in range(1, 101)]
    current = [{"demand": value, "rent": 100 + value} for value in range(1, 101)]

    result = EvidentlyDriftMonitor().run(
        reference_rows=reference,
        current_rows=current,
        drift_share_threshold=0.5,
        snapshot_id="drift-test",
    )

    assert result.snapshot_id == "drift-test"
    assert result.drift_detected is False
    assert result.engine == "evidently"
    assert result.to_dict()["report"]["metrics"]


def test_evidently_monitor_detects_shifted_features() -> None:
    reference = [{"demand": value} for value in range(1, 101)]
    current = [{"demand": value} for value in range(1001, 1101)]

    result = EvidentlyDriftMonitor().run(
        reference_rows=reference,
        current_rows=current,
        drift_share_threshold=0.5,
    )

    assert result.drift_detected is True
    assert result.drifted_columns == 1
