"""Authenticated application boundary for immutable, queued fresh executions."""
from __future__ import annotations

from typing import Any

from modules.listing.application.intake_score_delivery import ScoreDeliveryDenied
from modules.sitescore.domain.execution import ScoreExecutionCommand
from shared.auth import Action, Principal
from shared.auth.rbac import rbac_allows


class SiteScoreExecutionService:
    def __init__(self, repository: Any):
        self.repository = repository

    @staticmethod
    def _principal(principal: Principal, *, execute: bool = False) -> None:
        if (not isinstance(principal, Principal) or not principal.authenticated
                or not principal.tenant_id or not principal.subject_id
                or not rbac_allows(principal, "sitescore", Action.EXECUTE if execute else Action.VIEW)):
            raise ScoreDeliveryDenied("SITESCORE_EXECUTION_PRINCIPAL_DENIED")

    def create(self, command: ScoreExecutionCommand, *, principal: Principal) -> dict:
        self._principal(principal, execute=True)
        command.validate()
        return self.repository.create_execution(command, principal=principal)

    def context(self, candidate_site_id: str, *, principal: Principal) -> dict:
        self._principal(principal)
        return self.repository.execution_context(candidate_site_id, principal=principal)

    def retry(self, request_id: str, *, principal: Principal, **command: Any) -> dict:
        self._principal(principal, execute=True)
        return self.repository.retry_execution(request_id, principal=principal, **command)

    def get(self, request_id: str, *, principal: Principal) -> dict | None:
        self._principal(principal)
        return self.repository.get_execution(request_id, principal=principal)
