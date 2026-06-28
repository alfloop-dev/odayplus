from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from modules.avm.application import AVMService
from modules.avm.domain import DataRoom, ValuationReport
from modules.avm.infrastructure import InMemoryAVMRepository


@dataclass(frozen=True)
class AVMBatchResult:
    job_id: str
    status: str
    reports: tuple[ValuationReport, ...]
    datarooms: tuple[DataRoom, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "reports": [report.to_dict() for report in self.reports],
            "datarooms": [dataroom.to_dict() for dataroom in self.datarooms],
        }


class AVMValuationWorker:
    def __init__(self, *, repository: InMemoryAVMRepository | None = None) -> None:
        self.service = AVMService(repository=repository or InMemoryAVMRepository())

    def run(
        self,
        inputs: Iterable[Mapping[str, Any]],
        *,
        job_id: str | None = None,
        actor: str = "avm-score-worker",
        correlation_id: str = "avm-worker",
        build_dataroom: bool = True,
    ) -> AVMBatchResult:
        reports: list[ValuationReport] = []
        datarooms: list[DataRoom] = []
        for item in inputs:
            case = self.service.create_case(item, created_by=actor, correlation_id=correlation_id)
            report = self.service.value(case.case_id, actor=actor, correlation_id=correlation_id)
            reports.append(report)
            if build_dataroom:
                datarooms.append(
                    self.service.build_dataroom(
                        case.case_id, actor=actor, correlation_id=correlation_id
                    )
                )
        return AVMBatchResult(
            job_id=job_id or f"avm-job-{uuid4()}",
            status="succeeded",
            reports=tuple(reports),
            datarooms=tuple(datarooms),
        )


def run_avm_batch_valuation(
    inputs: Iterable[Mapping[str, Any]],
    *,
    job_id: str | None = None,
    repository: InMemoryAVMRepository | None = None,
) -> AVMBatchResult:
    return AVMValuationWorker(repository=repository).run(inputs, job_id=job_id)
