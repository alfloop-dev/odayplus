from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from models.shared_ml.production_runtime import (
    ProductionExecutionConfigurationError,
    production_execution_required,
)
from modules.avm.application.production import AVMProductionExecutor
from modules.avm.domain import (
    ApprovalDecision,
    DataRoom,
    NormalizedMargin,
    ValuationCase,
    ValuationCaseStatus,
    ValuationInput,
    ValuationReport,
    build_valuation_view,
    generate_data_room,
    normalize_margin,
    value_store,
)
from modules.avm.infrastructure import InMemoryAVMRepository


class AVMError(ValueError):
    pass


class AVMService:
    def __init__(
        self,
        *,
        repository: InMemoryAVMRepository | None = None,
        production_executor: AVMProductionExecutor | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        self.production_required = production_execution_required(runtime_mode)
        self.strict_production_composition = runtime_mode is not None and self.production_required
        if self.strict_production_composition and (
            repository is None or isinstance(repository, InMemoryAVMRepository)
        ):
            raise ProductionExecutionConfigurationError(
                "AVM production requires an injected durable repository"
            )
        if self.strict_production_composition and production_executor is None:
            raise ProductionExecutionConfigurationError(
                "AVM production requires an injected approved model and liquidity executor"
            )
        self.repository = repository or InMemoryAVMRepository()
        self.production_executor = production_executor

    def create_case(
        self,
        data: ValuationInput | Mapping[str, Any],
        *,
        created_by: str,
        correlation_id: str,
    ) -> ValuationCase:
        valuation_input = data if isinstance(data, ValuationInput) else build_valuation_view(data)
        case = ValuationCase.create(
            valuation_input, created_by=created_by, correlation_id=correlation_id
        )
        return self.repository.save_case(case)

    def get_case(self, case_id: str) -> ValuationCase | None:
        return self.repository.get_case(case_id)

    def normalize(self, case_id: str, *, actor: str, correlation_id: str) -> NormalizedMargin:
        case = self._case(case_id)
        self._require_status(
            case,
            {ValuationCaseStatus.DATA_READY},
            action="normalize valuation inputs",
        )
        active = self.repository.save_case(
            case.transition(
                ValuationCaseStatus.NORMALIZING,
                actor=actor,
                reason="normalization started",
                correlation_id=correlation_id,
            )
        )
        margin = self.repository.save_margin(normalize_margin(active))
        self.repository.save_case(
            active.transition(
                ValuationCaseStatus.DATA_READY,
                actor=actor,
                reason="normalization completed",
                correlation_id=correlation_id,
            )
        )
        return margin

    def value(self, case_id: str, *, actor: str, correlation_id: str) -> ValuationReport:
        case = self._case(case_id)
        self._require_status(
            case,
            {ValuationCaseStatus.DATA_READY, ValuationCaseStatus.REVIEW_REQUIRED},
            action="run AVM valuation",
        )
        margin = self.repository.get_margin(case_id)
        if margin is None:
            if case.status is not ValuationCaseStatus.DATA_READY:
                raise AVMError("normalized margin required before valuation")
            margin = self.normalize(case_id, actor=actor, correlation_id=correlation_id)
            case = self._case(case_id)
        valuing = case.transition(
            ValuationCaseStatus.VALUING,
            actor=actor,
            reason="valuation started",
            correlation_id=correlation_id,
        )
        if self.production_required:
            executor = self.production_executor
            if executor is None:
                executor = AVMProductionExecutor.from_environment()
                self.production_executor = executor
            report = executor.execute(valuing, margin)
        else:
            report = value_store(valuing, margin)
        self.repository.save_case(valuing)
        report = self.repository.save_report(report)
        self.repository.save_case(
            valuing.transition(
                ValuationCaseStatus.REVIEW_REQUIRED,
                actor=actor,
                reason="valuation completed; finance approval required",
                correlation_id=correlation_id,
            )
        )
        return report

    def approve_finance(
        self,
        case_id: str,
        *,
        actor: str,
        reason: str,
        correlation_id: str,
        reserve_price: float | None = None,
    ) -> ValuationReport:
        if not reason.strip():
            raise AVMError("finance approval requires a reason")
        case = self._case(case_id)
        self._require_status(
            case,
            {ValuationCaseStatus.REVIEW_REQUIRED},
            action="approve finance decision",
        )
        if actor == case.created_by:
            raise AVMError("case creator cannot approve their own valuation case")
        report = self.repository.latest_report(case_id)
        if report is None:
            raise AVMError("valuation report required before finance approval")
        if report.finance_approval is not None:
            raise AVMError("latest valuation report is already finance approved")
        approval = ApprovalDecision(
            decision_id=f"avm-decision-{uuid4()}",
            actor_id=actor,
            approved_at=datetime.now(UTC),
            decision_reason=reason,
            reserve_price=round(
                float(reserve_price if reserve_price is not None else report.reserve_price),
                2,
            ),
            correlation_id=correlation_id,
        )
        approved_report = self.repository.replace_latest_report(report.with_approval(approval))
        self.repository.save_case(
            case.transition(
                ValuationCaseStatus.APPROVED,
                actor=actor,
                reason=reason,
                correlation_id=correlation_id,
            )
        )
        return approved_report

    def build_dataroom(self, case_id: str, *, actor: str, correlation_id: str) -> DataRoom:
        case = self._case(case_id)
        self._require_status(
            case,
            {ValuationCaseStatus.APPROVED, ValuationCaseStatus.DATAROOM_READY},
            action="build data room",
        )
        if case.status is ValuationCaseStatus.DATAROOM_READY:
            existing = self.repository.get_dataroom(case_id)
            if existing is not None:
                return existing
        report = self.repository.latest_report(case_id)
        if report is None:
            raise AVMError("valuation report required before data room")
        if report.finance_approval is None:
            raise AVMError("finance approval required before data room")
        dataroom = self.repository.save_dataroom(generate_data_room(report))
        self.repository.save_case(
            case.transition(
                ValuationCaseStatus.DATAROOM_READY,
                actor=actor,
                reason="data room checklist generated",
                correlation_id=correlation_id,
            )
        )
        return dataroom

    def export_dataroom(
        self,
        case_id: str,
        *,
        actor: str,
        reason: str,
        correlation_id: str,
    ) -> DataRoom:
        if not reason.strip():
            raise AVMError("data room export requires a reason")
        case = self._case(case_id)
        self._require_status(
            case,
            {ValuationCaseStatus.DATAROOM_READY},
            action="export data room",
        )
        dataroom = self.repository.get_dataroom(case_id)
        if dataroom is None:
            raise AVMError("data room must be built before export")
        return self.repository.save_dataroom(
            dataroom.with_export(actor=actor, reason=reason, correlation_id=correlation_id)
        )

    def latest_report(self, case_id: str) -> ValuationReport | None:
        return self.repository.latest_report(case_id)

    def report_history(self, case_id: str) -> list[ValuationReport]:
        return self.repository.report_history(case_id)

    def dataroom(self, case_id: str) -> DataRoom | None:
        return self.repository.get_dataroom(case_id)

    def _case(self, case_id: str) -> ValuationCase:
        case = self.repository.get_case(case_id)
        if case is None:
            raise AVMError("valuation case not found")
        return case

    def _require_status(
        self,
        case: ValuationCase,
        allowed: set[ValuationCaseStatus],
        *,
        action: str,
    ) -> None:
        if case.status not in allowed:
            allowed_values = ", ".join(sorted(status.value for status in allowed))
            raise AVMError(
                f"cannot {action} while case status is {case.status.value}; "
                f"expected one of: {allowed_values}"
            )
