from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from modules.avm.domain import (
    DataRoom,
    DealOutcome,
    NormalizedMargin,
    ValuationCase,
    ValuationReport,
)


@dataclass
class InMemoryAVMRepository:
    _cases: dict[str, ValuationCase] = field(default_factory=dict)
    _margins: dict[str, NormalizedMargin] = field(default_factory=dict)
    _reports: dict[str, list[ValuationReport]] = field(default_factory=dict)
    _datarooms: dict[str, DataRoom] = field(default_factory=dict)
    _deal_outcomes: dict[str, DealOutcome] = field(default_factory=dict)

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

    def _case_has_legacy_quality(self, case_id: str) -> bool:
        case = self._cases.get(case_id)
        return bool(
            case is not None
            and case.valuation_input.effective_quality_score_status == "legacy_unknown"
        )

    def _dispose_legacy_report(self, report: ValuationReport) -> ValuationReport:
        if not (
            self._case_has_legacy_quality(report.case_id)
            or report.is_legacy_quality_unknown
        ):
            return report
        return report.with_legacy_quality_disposition()

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
        if not versions:
            return None
        versions[-1] = self._dispose_legacy_report(versions[-1])
        return versions[-1]

    def report_history(self, case_id: str) -> list[ValuationReport]:
        versions = self._reports.get(case_id, [])
        for index, report in enumerate(versions):
            versions[index] = self._dispose_legacy_report(report)
        return list(versions)

    def save_dataroom(self, dataroom: DataRoom) -> DataRoom:
        self._datarooms[dataroom.case_id] = dataroom
        return dataroom

    def get_dataroom(self, case_id: str) -> DataRoom | None:
        dataroom = self._datarooms.get(case_id)
        if dataroom is None:
            return None
        if self._case_has_legacy_quality(case_id) or dataroom.is_legacy_quality_unknown:
            dataroom = dataroom.with_legacy_quality_disposition()
            self._datarooms[case_id] = dataroom
        return dataroom

    def save_deal_outcome(self, outcome: DealOutcome) -> DealOutcome:
        self._deal_outcomes[outcome.outcome_id] = outcome
        return outcome

    def get_deal_outcome(self, outcome_id: str) -> DealOutcome | None:
        return self._deal_outcomes.get(outcome_id)

    def get_deal_outcomes_for_valuation(self, valuation_id: str) -> list[DealOutcome]:
        return [o for o in self._deal_outcomes.values() if o.valuation_id == valuation_id]

    def list_deal_outcomes(self) -> list[DealOutcome]:
        return list(self._deal_outcomes.values())
