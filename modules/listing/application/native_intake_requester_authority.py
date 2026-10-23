"""Read current recheck authority without owning the caller's SQL transaction.

The identity store's connection factory enters its result. Giving it a raw
psycopg connection would commit and close the surrounding Intake transaction.
This adapter lends only the three fixed identity reads used by this command.
"""

from collections.abc import Callable
from contextlib import contextmanager
from uuid import UUID

from modules.listing.application.intake_authorization import authorize_intake_action
from shared.auth import Principal
from shared.identity.store import SqlIdentityStore


class NativeIntakeRequesterAuthorityUnavailable(RuntimeError):
    pass


_IDENTITY_READS = frozenset({
    "SELECT account_id, tenant_id, username, email, display_name, status, "
    "created_at, created_by, updated_at, disabled_at, disabled_reason "
    "FROM identity.accounts WHERE account_id = %s",
    "SELECT role FROM identity.account_roles WHERE account_id = %s",
    "SELECT s.brand_ids, s.region_ids, s.store_ids, s.assigned_area_ids, "
    "s.heat_zone_ids, s.modules, s.clearance, a.tenant_id "
    "FROM identity.account_scopes s JOIN identity.accounts a ON s.account_id = a.account_id "
    "WHERE s.account_id = %s",
})


def _check_pure_boundary(before):
    # Normal client-side cursor construction/fetches issue no SQL. Keep the
    # same original deadline without a redundant database roundtrip. Unknown
    # callbacks retain their complete behavior, including any extra checks.
    from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget

    if (isinstance(before, NativeIntakeCommandBudget)
            and type(before).__call__ is NativeIntakeCommandBudget.__call__):
        before.check_in_memory()
    else:
        before()


class _ReadCursor:
    __slots__ = ("_cursor", "_before")

    def __init__(self, cursor, before):
        self._cursor, self._before = cursor, before

    def execute(self, sql, params):
        if not isinstance(sql, str) or " ".join(sql.split()) not in _IDENTITY_READS:
            raise NativeIntakeRequesterAuthorityUnavailable("NATIVE_RECHECK_IDENTITY_READ_ONLY_REQUIRED")
        self._before()
        self._cursor.execute(sql, params)

    def fetchone(self):
        _check_pure_boundary(self._before)
        return self._cursor.fetchone()

    def fetchall(self):
        _check_pure_boundary(self._before)
        return self._cursor.fetchall()


class _ReadConnection:
    __slots__ = ("_connection", "_before")

    def __init__(self, connection, before):
        self._connection, self._before = connection, before

    @contextmanager
    def cursor(self):
        _check_pure_boundary(self._before)
        # A cursor context closes only this cursor, never its connection.
        with self._connection.cursor() as cursor:
            yield _ReadCursor(cursor, self._before)


def current_native_identity_store(engine, tenant_id: str, *, before: Callable[[], None]) -> SqlIdentityStore:
    """Lend the existing three read-only identity queries inside this SQL root."""
    if (getattr(engine, "dialect", None) != "postgresql"
            or (connection := getattr(getattr(engine, "_local", None), "connection", None)) is None):
        raise NativeIntakeRequesterAuthorityUnavailable("NATIVE_RECHECK_CURRENT_TRANSACTION_REQUIRED")
    before()
    root = engine.query_one("SELECT current_setting('transaction_isolation') AS isolation, "
        "current_setting('app.tenant_id',true) AS tenant")
    if root["isolation"] != "serializable":
        raise NativeIntakeRequesterAuthorityUnavailable("NATIVE_RECHECK_SERIALIZABLE_REQUIRED")
    tenant = root["tenant"]
    if tenant != tenant_id:
        raise PermissionError("NATIVE_RECHECK_TRANSACTION_TENANT_CONFLICT")
    @contextmanager
    def borrowed_connection():
        if getattr(engine._local, "connection", None) is not connection:
            raise NativeIntakeRequesterAuthorityUnavailable("NATIVE_RECHECK_CURRENT_TRANSACTION_UNAVAILABLE")
        _check_pure_boundary(before)
        yield _ReadConnection(connection, before)

    return SqlIdentityStore(borrowed_connection)


def _current_native_requester_profile(engine, subject_id: str, intake: dict, *,
                                      before: Callable[[], None]) -> Principal:
    """Require a current active SQL account and an explicit native scope grant.

    There is no pool checkout, identity write, recorded-role fallback or default
    Scope. The caller owns a SERIALIZABLE root and its deadline. The returned
    principal describes this accepted action; it grants no future source use.
    """
    identities = current_native_identity_store(engine, intake["tenant_id"], before=before)
    try:
        account_id = UUID(subject_id)
        if not account_id.int or str(account_id) != subject_id:
            raise ValueError("Invalid requester")
    except (TypeError, ValueError, AttributeError):
        raise PermissionError("NATIVE_RECHECK_REQUESTER_INVALID") from None

    account = identities.find_account_by_id(account_id)
    if account is None or not account.is_active or str(account.tenant_id) != intake["tenant_id"]:
        raise PermissionError("NATIVE_RECHECK_REQUESTER_INACTIVE_OR_FOREIGN")
    principal = Principal(subject_id=subject_id, roles=identities.get_account_roles(account_id),
                          scope=identities.get_account_scope(account_id, require_explicit=True))
    return principal


def current_native_recheck_principal(engine, subject_id: str, intake: dict, *,
                                     before: Callable[[], None]) -> Principal:
    principal = _current_native_requester_profile(engine, subject_id, intake, before=before)
    authorize_intake_action(principal, "recheck_source_policy",
                           resource={**intake, "submitter": intake["submitter_subject_id"]})
    return principal


def current_native_retrieval_submitter(engine, intake: dict, *, before: Callable[[], None]) -> Principal:
    """Resolve the immutable submitter, never a supplied/service substitute.

    Submission does not require human CONFIDENTIAL clearance. The retrieval
    service must independently prove its content/runtime capability. An
    explicit restricted module scope must permit listing; unrestricted module
    scope retains the existing no-op semantics.
    """
    principal = _current_native_requester_profile(engine, intake["submitter_subject_id"], intake, before=before)
    authorize_intake_action(principal, "submit_url",
                           resource={**intake, "submitter": intake["submitter_subject_id"]})
    if not principal.scope.permits_module("listing"):
        raise PermissionError("NATIVE_RETRIEVAL_SUBMITTER_MODULE_DENIED")
    return principal


def current_native_assisted_principal(engine, subject_id: str, intake: dict, *,
                                      risk_summary: str, risk_acknowledged: bool,
                                      before: Callable[[], None]) -> Principal:
    """Resolve the actual initiating human inside the caller's current root.

    This authorizes proposed evidence only. The owner separately proves the
    frozen command/source and previews the human-only Intake transition with
    the immutable original submitter as owner. It grants no later application
    of identity corrections and introduces no human clearance requirement.
    """
    if (type(risk_acknowledged) is not bool or risk_acknowledged is not True
            or not isinstance(risk_summary, str) or not 3 <= len(risk_summary.strip()) <= 4000):
        raise ValueError("NATIVE_ASSISTED_RISK_ACKNOWLEDGEMENT_REQUIRED")
    principal = _current_native_requester_profile(engine, subject_id, intake, before=before)
    authorize_intake_action(principal, "correct",
        resource={**intake, "submitter": intake["submitter_subject_id"]},
        is_identity_affecting=True, risk_summary=risk_summary, risk_acknowledged=risk_acknowledged)
    if not principal.scope.permits_module("listing"):
        raise PermissionError("NATIVE_ASSISTED_REQUESTER_MODULE_DENIED")
    return principal


def current_native_identity_principal(engine, subject_id: str, intake: dict, *,
                                      action: str, risk_summary: str, risk_acknowledged: bool,
                                      before: Callable[[], None]) -> Principal:
    """Apply the existing identity permission to the same fresh SQL profile.

    Proposal is not approval. The decision owner separately checks the actual
    reviewer's role and separation; a rejection grants no positive action.
    """
    if (action not in {"decide", "reject_identity"} or type(risk_acknowledged) is not bool
            or not isinstance(risk_summary, str) or not 3 <= len(risk_summary.strip()) <= 4000):
        raise ValueError("NATIVE_IDENTITY_REASON_REQUIRED")
    principal = _current_native_requester_profile(engine, subject_id, intake, before=before)
    resource = {**intake, "id": intake["intake_id"], "url": None, "submitter": intake["submitter_subject_id"]}
    authorize_intake_action(principal, "view", resource=resource)
    authorize_intake_action(principal, action, resource=resource,
        risk_summary=risk_summary, risk_acknowledged=risk_acknowledged)
    if not principal.scope.permits_module("listing"):
        raise PermissionError("NATIVE_IDENTITY_REQUESTER_MODULE_DENIED")
    return principal


def current_native_command_withdrawal_principal(engine, subject_id: str, intake: dict, *,
                                                request_kind: str, before: Callable[[], None]) -> Principal:
    """A fresh negative action uses its existing lane-specific permission.

    It needs neither a historical actor's role nor current positive source,
    runtime, storage or signing settings. The caller owns the locked Intake.
    """
    if request_kind not in {"ASSISTED_ENTRY", "SOURCE_POLICY_RECHECK"}:
        raise ValueError("NATIVE_COMMAND_KIND_REQUIRED")
    principal = _current_native_requester_profile(engine, subject_id, intake, before=before)
    authorize_intake_action(principal, "cancel" if request_kind == "ASSISTED_ENTRY" else "recheck_source_policy",
        resource={**intake, "submitter": intake["submitter_subject_id"]})
    if not principal.scope.permits_module("listing"):
        raise PermissionError("NATIVE_COMMAND_WITHDRAWAL_MODULE_DENIED")
    return principal
