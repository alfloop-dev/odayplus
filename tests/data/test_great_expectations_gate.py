import pytest

from pipelines.quality import DataQualityFailure, GreatExpectationsGate, QualityCheck

CHECKS = (
    QualityCheck(name="id-present", kind="not_null", column="entity_id"),
    QualityCheck(name="id-unique", kind="unique", column="entity_id"),
    QualityCheck(name="label-range", kind="between", column="label", min_value=0, max_value=1),
)


def test_great_expectations_gate_accepts_model_ready_rows() -> None:
    result = GreatExpectationsGate().validate(
        [{"entity_id": "a", "label": 0.2}, {"entity_id": "b", "label": 0.8}],
        CHECKS,
    )

    assert result.success is True
    assert result.row_count == 2
    assert result.engine == "great_expectations"


def test_great_expectations_gate_fails_closed_with_evidence() -> None:
    with pytest.raises(DataQualityFailure) as error:
        GreatExpectationsGate().validate(
            [{"entity_id": "a", "label": 0.2}, {"entity_id": "a", "label": 2.0}],
            CHECKS,
        )

    failed = {check.name for check in error.value.result.checks if not check.success}
    assert failed == {"id-unique", "label-range"}
