"""Bind Operator write attribution to the authenticated principal."""

from dataclasses import dataclass

from shared.auth import Principal, Role


@dataclass(frozen=True)
class OperatorActor:
    subject_id: str
    role_id: str
    tenant_id: str


def operator_actor(principal: Principal) -> OperatorActor:
    """Body persona/display fields never grant a role or attest an identity.

    The request boundary separately authorizes the action and full resource
    scope. The service enforces production UUID/tenant binding before replay.
    """
    if not principal.authenticated or not principal.subject_id.strip():
        raise PermissionError("AUTHENTICATED_ACTOR_REQUIRED")
    if not principal.tenant_id or not principal.tenant_id.strip():
        raise PermissionError("TENANT_SCOPE_REQUIRED")
    for role in (
        Role.SITE_REVIEWER, Role.EXECUTIVE, Role.DATA_OWNER,
        Role.EXPANSION_USER, Role.FINANCE_LEGAL,
    ):
        if principal.has_role(role):
            return OperatorActor(principal.subject_id, role.value, principal.tenant_id)
    raise PermissionError("ROLE_DENIED")
