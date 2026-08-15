from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from modules.avm.domain import DataRoom, NormalizedMargin, ValuationCase, ValuationReport


@dataclass
class InMemoryAVMRepository:
    _cases: dict[str, ValuationCase] = field(default_factory=dict)
    _margins: dict[str, NormalizedMargin] = field(default_factory=dict)
    _reports: dict[str, list[ValuationReport]] = field(default_factory=dict)
    _datarooms: dict[str, DataRoom] = field(default_factory=dict)

    def save_case(self, case: ValuationCase) -> ValuationCase:
        self._cases[case.case_id] = case
        return case

    def get_case(self, case_id: str) -> ValuationCase | None:
        return self._cases.get(case_id)

    def list_cases(self) -> list[ValuationCase]:
        return list(self._cases.values())

    def save_margin(self, margin: NormalizedMargin) -> NormalizedMargin:
        self._margins[margin.case_id] = margin
        return margin

    def get_margin(self, case_id: str) -> NormalizedMargin | None:
        return self._margins.get(case_id)

    def save_report(self, report: ValuationReport) -> ValuationReport:
        versions = self._reports.setdefault(report.case_id, [])
        versioned = report.with_version(
            valuation_version=len(versions) + 1,
            report_id=f"avm-report-{uuid4()}",
        )
        versions.append(versioned)
        return versioned

    def replace_latest_report(self, report: ValuationReport) -> ValuationReport:
        versions = self._reports.setdefault(report.case_id, [])
        if versions:
            versions[-1] = report
        else:
            versions.append(report)
        return report

    def latest_report(self, case_id: str) -> ValuationReport | None:
        versions = self._reports.get(case_id, [])
        return versions[-1] if versions else None

    def report_history(self, case_id: str) -> list[ValuationReport]:
        return list(self._reports.get(case_id, []))

    def save_dataroom(self, dataroom: DataRoom) -> DataRoom:
        self._datarooms[dataroom.case_id] = dataroom
        return dataroom

    def get_dataroom(self, case_id: str) -> DataRoom | None:
        return self._datarooms.get(case_id)
