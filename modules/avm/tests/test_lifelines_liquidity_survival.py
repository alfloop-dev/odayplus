from __future__ import annotations

import json

import pytest

from modules.avm import (
    LIFELINES_ARTIFACT_SCHEMA_VERSION,
    LifelinesLiquiditySurvivalAdapter,
    LiquidityTrainingRecord,
    SurvivalModelNotFittedError,
)


def _training_records() -> list[LiquidityTrainingRecord]:
    records: list[LiquidityTrainingRecord] = []
    for index in range(90):
        price_ratio = 0.80 + ((index * 7) % 31) / 50
        demand_score = 0.20 + ((index * 11) % 29) / 35
        observed_days = max(
            5.0,
            92.0 + 65.0 * (price_ratio - 1.0) - 48.0 * demand_score + float((index * 13) % 9),
        )
        records.append(
            LiquidityTrainingRecord(
                duration_days=observed_days,
                sold=index % 6 != 0,
                features={
                    "asking_price_ratio": price_ratio,
                    "demand_score": demand_score,
                },
            )
        )
    return records


def test_lifelines_model_fits_and_predicts_liquidity_probabilities() -> None:
    adapter = LifelinesLiquiditySurvivalAdapter(penalizer=0.2)

    assert adapter.capability().available is True
    with pytest.raises(SurvivalModelNotFittedError):
        adapter.predict({"asking_price_ratio": 1.0, "demand_score": 0.5})

    adapter.fit(_training_records())
    liquid = adapter.predict({"asking_price_ratio": 0.85, "demand_score": 0.95})
    illiquid = adapter.predict({"asking_price_ratio": 1.30, "demand_score": 0.25})

    for prediction in (liquid, illiquid):
        assert 0.0 <= prediction.sale_probability_30d <= 1.0
        assert prediction.sale_probability_30d <= prediction.sale_probability_90d <= 1.0
        assert prediction.expected_days > 0
        assert prediction.feature_names == ("asking_price_ratio", "demand_score")
    assert liquid.to_dict() != illiquid.to_dict()
    assert adapter.training_metadata["library"] == "lifelines"
    assert adapter.training_metadata["estimator"] == "CoxPHFitter"
    assert adapter.training_metadata["sample_count"] == 90


def test_lifelines_artifact_round_trip_preserves_learned_predictions(tmp_path) -> None:
    adapter = LifelinesLiquiditySurvivalAdapter(penalizer=0.15).fit(_training_records())
    features = {"asking_price_ratio": 1.05, "demand_score": 0.70}
    before = adapter.predict(features)

    artifact_path = tmp_path / "liquidity-model.json"
    artifact_path.write_text(adapter.serialize_artifact(), encoding="utf-8")
    document = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert document["artifact_schema_version"] == LIFELINES_ARTIFACT_SCHEMA_VERSION
    assert document["training_metadata"]["library"] == "lifelines"
    assert document["payload_encoding"] == "base64+pickle"

    restored = LifelinesLiquiditySurvivalAdapter.from_artifact(
        artifact_path.read_text(encoding="utf-8")
    )
    after = restored.predict(features)

    assert after.sale_probability_30d == pytest.approx(before.sale_probability_30d)
    assert after.sale_probability_90d == pytest.approx(before.sale_probability_90d)
    assert after.expected_days == pytest.approx(before.expected_days)
    assert restored.training_metadata == adapter.training_metadata


def test_lifelines_artifact_rejects_tampered_model_payload() -> None:
    adapter = LifelinesLiquiditySurvivalAdapter().fit(_training_records())
    document = json.loads(adapter.serialize_artifact())
    document["payload_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="checksum mismatch"):
        LifelinesLiquiditySurvivalAdapter.from_artifact(json.dumps(document))
