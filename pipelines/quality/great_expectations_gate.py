"""Great Expectations-backed validation for model-ready datasets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

import pandas as pd

from models.shared_ml import OssCapability, require_oss_capability

CheckKind = Literal["not_null", "unique", "between", "in_set"]


class DataQualityFailure(ValueError):
    def __init__(self, result: QualityGateResult) -> None:
        self.result = result
        failed = ", ".join(check.name for check in result.checks if not check.success)
        super().__init__(f"model-ready data failed Great Expectations checks: {failed}")


@dataclass(frozen=True)
class QualityCheck:
    name: str
    kind: CheckKind
    column: str
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: tuple[Any, ...] = ()


@dataclass(frozen=True)
class QualityCheckResult:
    name: str
    success: bool
    unexpected_count: int
    expectation_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "unexpected_count": self.unexpected_count,
            "expectation_type": self.expectation_type,
        }


@dataclass(frozen=True)
class QualityGateResult:
    run_id: str
    success: bool
    row_count: int
    checks: tuple[QualityCheckResult, ...]
    engine: str = "great_expectations"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "success": self.success,
            "row_count": self.row_count,
            "engine": self.engine,
            "checks": [check.to_dict() for check in self.checks],
        }


class GreatExpectationsGate:
    def validate(
        self,
        rows: Sequence[Mapping[str, Any]],
        checks: Sequence[QualityCheck],
        *,
        fail_on_error: bool = True,
        run_id: str | None = None,
    ) -> QualityGateResult:
        require_oss_capability(OssCapability.DATA_QUALITY)
        import great_expectations as gx

        frame = pd.DataFrame(rows)
        context = gx.get_context(mode="ephemeral")
        suffix = uuid4().hex
        data_source = context.data_sources.add_pandas(name=f"model_ready_{suffix}")
        asset = data_source.add_dataframe_asset(name=f"dataset_{suffix}")
        batch_definition = asset.add_batch_definition_whole_dataframe(name=f"batch_{suffix}")
        batch = batch_definition.get_batch(batch_parameters={"dataframe": frame})

        results: list[QualityCheckResult] = []
        for check in checks:
            expectation = _expectation(gx, check)
            validation = batch.validate(expectation)
            payload = validation.to_json_dict()
            result_payload = payload.get("result", {})
            results.append(
                QualityCheckResult(
                    name=check.name,
                    success=bool(validation.success),
                    unexpected_count=int(result_payload.get("unexpected_count", 0) or 0),
                    expectation_type=expectation.__class__.__name__,
                )
            )

        gate_result = QualityGateResult(
            run_id=run_id or f"gx-{uuid4()}",
            success=all(result.success for result in results),
            row_count=len(frame),
            checks=tuple(results),
        )
        if fail_on_error and not gate_result.success:
            raise DataQualityFailure(gate_result)
        return gate_result


def _expectation(gx: Any, check: QualityCheck) -> Any:
    if check.kind == "not_null":
        return gx.expectations.ExpectColumnValuesToNotBeNull(column=check.column)
    if check.kind == "unique":
        return gx.expectations.ExpectColumnValuesToBeUnique(column=check.column)
    if check.kind == "between":
        return gx.expectations.ExpectColumnValuesToBeBetween(
            column=check.column,
            min_value=check.min_value,
            max_value=check.max_value,
        )
    if check.kind == "in_set":
        return gx.expectations.ExpectColumnValuesToBeInSet(
            column=check.column,
            value_set=list(check.allowed_values),
        )
    raise ValueError(f"unsupported Great Expectations check kind: {check.kind}")


__all__ = [
    "DataQualityFailure",
    "GreatExpectationsGate",
    "QualityCheck",
    "QualityGateResult",
]
