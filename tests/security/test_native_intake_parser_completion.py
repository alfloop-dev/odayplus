"""Adversarial private completion facts, using synthetic state and MOCK receipts.

Nothing here launches a runner, proves reaping, authenticates historical SQL
rows, or grants permission to publish output. The lifecycle module is replaced
only at its private consume boundary: these tests check forwarding and the
completion state machine, never the kernel's issuance/provenance checks.
The connection sentinel models a root-presence predicate, not a transaction or
commit receipt. Root must separately integrate and verify real kernel/SQL IO.
"""

import copy
import json
import pickle
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from modules.external_data.application.native_intake_process import (
    NativeIORejected,
    NativeIOUnreaped,
)
from modules.external_data.application.native_parser_output import NativeParserOutputRejected
from modules.listing.application import native_intake_parser_completion as completion
from modules.listing.application.native_intake_parser_execution import NativeParserExecutionState
from modules.listing.application.native_intake_parser_policy import JOB_TYPE, NativeParserConflict
from modules.listing.application.native_intake_retrieval_policy import (
    canonical_json,
    canonical_sha256,
)
from shared.jobs.queue import JobStatus

_REJECT = "^NATIVE_PARSER_LIVE_COMPLETION_REQUIRED$"
_LIFECYCLE_MODULE = "modules.external_data.application.native_intake_process_lifecycle"
_OPERATIONS = ("gcs_bucket", "gcs_head", "gcs_download", "parser_child")
_ACTIONS = ("open", "attach", "observed", "facts", "seal", "expected", "confirm", "unresolved")
_PENDING_ACTIONS = ("facts", "seal", "expected", "confirm", "unresolved")
_AT = datetime(2026, 10, 18, 12, tzinfo=UTC)


def _uuid(number):
    return str(UUID(int=number))


def _no_authority(*args, **kwargs):
    raise AssertionError("Synthetic completion facts must not query SQL or positive authority")


class _IntSubclass(int):
    pass


class _StringSubclass(str):
    pass


class _DictSubclass(dict):
    pass


class _EqualObservation:
    """Equality must not substitute for the exact attached observation."""

    def __eq__(self, other):
        return True


class _MockCompletionUnavailable(RuntimeError):
    """Synthetic kernel refusal, not an observation of any process."""


@pytest.fixture
def mock_kernel(monkeypatch):
    # Deliberately no fake kernel registry, runner, Popen or reaping algorithm.
    # Even an absent/wrong observation would get this mock reply unless the
    # completion module rejects it before calling the boundary.
    module = ModuleType(_LIFECYCLE_MODULE)
    consume = Mock(name="MOCK_ONLY_consume_native_process_completion", return_value="REAPED")
    module._consume_native_process_completion = consume
    monkeypatch.setitem(sys.modules, _LIFECYCLE_MODULE, module)
    return consume


class _SyntheticCase:
    """Minimal domain-shaped data; never submitted to a SQL/history validator."""

    def __init__(self):
        self.engine = SimpleNamespace(
            _local=SimpleNamespace(connection=None), execute=_no_authority,
            query_one=_no_authority, transaction=_no_authority,
        )
        self.owner = SimpleNamespace(engine=self.engine, _confirm=_no_authority)
        self.original_binding = {
            "synthetic_only": True,
            "lineage": {"source_id": "synthetic-completion-facts", "content_sha256": "a" * 64},
        }
        self.original = SimpleNamespace(binding=lambda: self.original_binding)
        self.context = SimpleNamespace(
            original=self.original, positive_grant=object(), verify=_no_authority,
        )
        self.job = {
            "tenant_id": _uuid(1), "job_id": _uuid(2), "job_type": JOB_TYPE,
            "aggregate_type": "parser_run", "aggregate_id": _uuid(3),
            "checkpoint": "PARSING", "attempt": 1, "max_attempts": 3,
            "fence_token": 2, "correlation_id": _uuid(4), "idempotency_key": "synthetic-completion",
            "status": "RUNNING", "version": 7, "timeout_at": _AT + timedelta(minutes=2),
        }
        self.run = {
            "tenant_id": _uuid(1), "parser_run_id": _uuid(3), "intake_id": _uuid(5),
            "source_snapshot_id": _uuid(6), "parser_release_id": _uuid(7),
            "correlation_id": _uuid(4), "status": "RUNNING", "version": 2,
            "parsed_payload": None, "normalized_payload": None,
            "created_at": _AT - timedelta(seconds=2), "started_at": _AT - timedelta(seconds=1),
            "completed_at": None, "field_confidence": {"synthetic": {"score": 0.5}},
            "validation_errors": [{"code": "synthetic-only"}],
        }
        self.physical = SimpleNamespace(
            job_id=_uuid(2), job_type=JOB_TYPE, locked_by="synthetic-completion-owner",
            fence_token=1, attempts=1, max_retries=3, status=JobStatus.RUNNING,
            correlation_id=_uuid(4), idempotency_key="synthetic-completion",
            payload={"synthetic_only": True, "delivery_generation": 1},
        )
        self.delivery = copy.deepcopy(self.physical)
        self.leases = SimpleNamespace(business_fence=2)
        self.io = SimpleNamespace(
            owner=self.owner, leases=self.leases, delivery=self.delivery,
            tenant=_uuid(1), generation=1,
        )
        self.state_payload = {
            "admission": {"synthetic_only": True, "binding": {"original_input": self.original_binding}},
            "delivery_generation": 1, "attempt_intents": [],
        }
        self.state = NativeParserExecutionState(
            physical=self.physical, job=self.job, payload=self.state_payload,
            run=self.run, before=_no_authority, context=self.context,
        )
        self.finishes = completion._ParserOperationFinishes(self.io)
        self.sequence = 0
        self.new_intent()

    def new_intent(self, operation="gcs_bucket"):
        self.sequence += 1
        arguments = {
            "gcs_bucket": {},
            "gcs_head": {"key": "synthetic-original", "generation": 1},
            "gcs_download": {"key": "synthetic-original", "generation": 1},
            "parser_child": {
                "document_sha256": "a" * 64, "byte_length": 137,
                "artifact_sha256": "b" * 64, "output_schema_version": "synthetic-output-v1",
            },
        }
        self.intent = {
            "intent_id": _uuid(100 + self.sequence), "operation": operation,
            "arguments": arguments[operation], "state": "ADMITTED", "delivery_generation": 1,
            "physical_fence": self.delivery.fence_token, "business_fence": self.leases.business_fence,
            "attempt": self.job["attempt"], "worker": self.delivery.locked_by,
            "parser_run_id": self.run["parser_run_id"], "source_snapshot_id": self.run["source_snapshot_id"],
            "original_input_sha256": "a" * 64, "artifact_sha256": "b" * 64,
            "binding_sha256": "c" * 64, "policy_sha256": "d" * 64, "source_version": 1,
            "source_sha256": "e" * 64, "source_valid_until": (_AT + timedelta(minutes=2)).isoformat(),
            "human_authority_sha256": "f" * 64,
            "reservation": {
                "reservation_id": _uuid(200 + self.sequence), "rate_reservation_id": None,
                "source_id": "synthetic-completion-facts", "expires_at": (_AT + timedelta(seconds=45)).isoformat(),
            },
            "admitted_at": _AT.isoformat(),
        }
        self.payload = copy.deepcopy(self.state_payload)
        self.payload["attempt_intents"] = [self.intent]

    def open(self):
        return self.finishes.open(self.intent, self.state, self.payload)

    def attached(self):
        live, observation = self.open(), _EqualObservation()
        self.finishes.attach(live, observation)
        return live, observation

    def pending(self, *, error=None):
        live, observation = self.attached()
        kwargs = {"observed": {"sha256": "9" * 64}} if error is None else {"error": error}
        pending = self.finishes.observed(live, observation, **kwargs)
        return live, observation, pending

    @contextmanager
    def modeled_root(self):
        # Sentinel only: no SQL connection, clock query, commit, or permission.
        assert self.engine._local.connection is None
        self.engine._local.connection = object()
        try:
            yield
        finally:
            self.engine._local.connection = None

    def seal(self, pending, at=_AT):
        with self.modeled_root():
            return self.finishes.seal(pending, at)


@pytest.fixture
def case(mock_kernel):
    return _SyntheticCase()


def _prepare_action(case, action):
    if action == "open":
        return case.open
    if action == "attach":
        live = case.open()
        return lambda: case.finishes.attach(live, _EqualObservation())
    if action == "observed":
        live, observation = case.attached()
        return lambda: case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
    _, _, pending = case.pending()
    if action == "confirm":
        case.seal(pending)
    if action == "seal":
        return lambda: case.seal(pending)
    return lambda: getattr(case.finishes, action)(pending)


def _assert_no_pending(case):
    assert not case.finishes._pending
    assert all(record.phase != "PENDING" for record in case.finishes._live.values())


def _assert_record_contains_only_encoded_facts(record):
    # OPEN must temporarily hold the observation. After consumption, every
    # field must be a bounded state label, encoded value/hash, or None.
    assert record.observation is None
    assert all(type(value) in (str, type(None)) for value in vars(record).values())


def test_mock_receipt_is_forwarded_by_identity_and_only_one_pending_is_issued(case, mock_kernel):
    live, observation, pending = case.pending()
    assert type(live) is completion._LiveParserOperation
    assert type(pending) is completion._PendingParserFinish
    mock_kernel.assert_called_once_with(observation)
    assert mock_kernel.call_args.args[0] is observation
    assert mock_kernel.call_args.kwargs == {}
    assert len(case.finishes._live) == len(case.finishes._pending) == 1
    record = case.finishes._pending[pending]
    assert record is case.finishes._live[live]
    assert record.phase == "PENDING"
    assert case.finishes.expected(pending) is None
    assert json.loads(record.outcome_json) == {
        "state": "FINISHED", "observed": {"sha256": "9" * 64},
        "error_class": None, "process_state": "NO_CHILD_REMAINS",
    }
    _assert_record_contains_only_encoded_facts(record)
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
    assert mock_kernel.call_count == 1


@pytest.mark.parametrize("handle_type", [completion._LiveParserOperation, completion._PendingParserFinish])
@pytest.mark.parametrize("issuer", [None, object(), True, {"issuer": "synthetic"}])
def test_private_handle_construction_requires_internal_issuer(handle_type, issuer):
    with pytest.raises(NativeParserConflict, match=_REJECT):
        handle_type(issuer)


@pytest.mark.parametrize("kind", ["live", "pending"])
@pytest.mark.parametrize("method", ["copy", "deepcopy", "pickle0", "pickle5"])
def test_handles_cannot_be_copied_or_serialized(case, kind, method):
    live, _, pending = case.pending()
    token = live if kind == "live" else pending
    with pytest.raises(TypeError, match="^NATIVE_PARSER_COMPLETION_NOT_SERIALIZABLE$"):
        if method == "copy":
            copy.copy(token)
        elif method == "deepcopy":
            copy.deepcopy(token)
        else:
            pickle.dumps(token, protocol=int(method[-1]))
    assert not hasattr(token, "__dict__")


@pytest.mark.parametrize("action", ["attach", "observed", *_PENDING_ACTIONS])
@pytest.mark.parametrize("forgery", ["object", "dict", "boolean", "exact_unregistered", "subclass", "wrong_handle_kind"])
def test_unregistered_or_wrong_exact_handle_types_cannot_use_an_issuer(case, mock_kernel, action, forgery):
    live, observation, pending = case.pending()
    live_action = action in {"attach", "observed"}
    handle_type = completion._LiveParserOperation if live_action else completion._PendingParserFinish
    if forgery == "exact_unregistered":
        forged = object.__new__(handle_type)
    elif forgery == "subclass":
        forged = object.__new__(type("ForgedCompletionHandle", (handle_type,), {"__slots__": ()}))
    elif forgery == "wrong_handle_kind":
        forged = pending if live_action else live
    else:
        forged = {"object": object(), "dict": {"phase": "PENDING"}, "boolean": True}[forgery]
    mock_kernel.reset_mock()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        if action == "attach":
            case.finishes.attach(forged, observation)
        elif action == "observed":
            case.finishes.observed(forged, observation, observed={"sha256": "9" * 64})
        elif action == "seal":
            case.seal(forged)
        else:
            getattr(case.finishes, action)(forged)
    mock_kernel.assert_not_called()


@pytest.mark.parametrize("same_io", [True, False])
@pytest.mark.parametrize("action", ["attach", "observed", *_PENDING_ACTIONS])
def test_another_issuer_cannot_use_live_or_pending_handles(case, mock_kernel, same_io, action):
    live, observation = case.attached()
    pending = None
    if action in _PENDING_ACTIONS:
        pending = case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
        case.seal(pending)
    other = completion._ParserOperationFinishes(case.io) if same_io else _SyntheticCase().finishes
    mock_kernel.reset_mock()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        if action == "attach":
            other.attach(live, observation)
        elif action == "observed":
            other.observed(live, observation, observed={"sha256": "9" * 64})
        elif action == "seal":
            with case.modeled_root():
                other.seal(pending, _AT)
        else:
            getattr(other, action)(pending)
    assert not other._live and not other._pending
    mock_kernel.assert_not_called()


@pytest.mark.parametrize("action", _ACTIONS)
@pytest.mark.parametrize("component", ["owner", "engine", "leases", "delivery", "io"])
def test_scope_requires_original_object_identity_even_for_equal_replacements(case, mock_kernel, action, component):
    invoke = _prepare_action(case, action)
    mock_kernel.reset_mock()
    if component == "io":
        # Private caller misuse still must not transfer existing handles to a
        # distinct IO that happens to have the same owner/leases/delivery.
        case.finishes.io = copy.copy(case.io)
    elif component == "engine":
        case.owner.engine = copy.copy(case.engine)
    else:
        setattr(case.io, component, copy.copy(getattr(case.io, component)))
    with pytest.raises(NativeParserConflict, match=_REJECT):
        invoke()
    mock_kernel.assert_not_called()


@pytest.mark.parametrize("action", _ACTIONS)
def test_every_entry_rejects_a_modeled_foreign_thread(case, mock_kernel, monkeypatch, action):
    invoke = _prepare_action(case, action)
    mock_kernel.reset_mock()
    # No threads are started. Only the completion module's identity predicate
    # is modeled; real runner/thread capture belongs to kernel lifecycle tests.
    foreign_thread = case.finishes.thread + 1
    monkeypatch.setattr(completion, "threading", SimpleNamespace(get_ident=lambda: foreign_thread))
    with pytest.raises(NativeParserConflict, match=_REJECT):
        invoke()
    mock_kernel.assert_not_called()


@pytest.mark.parametrize("action", ["open", "attach", "observed", "confirm", "unresolved"])
def test_live_actions_require_no_modeled_sql_root(case, mock_kernel, action):
    invoke = _prepare_action(case, action)
    mock_kernel.reset_mock()
    with case.modeled_root(), pytest.raises(NativeParserConflict, match=_REJECT):
        invoke()
    mock_kernel.assert_not_called()


def test_seal_requires_root_presence_and_confirm_requires_exit(case):
    _, _, pending = case.pending()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.seal(pending, _AT)
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.confirm(pending)
    with case.modeled_root():
        expected = case.finishes.seal(pending, _AT)
        assert case.finishes.expected(pending) == expected
        assert case.finishes.facts(pending)[1] == case.intent
        with pytest.raises(NativeParserConflict, match=_REJECT):
            case.finishes.confirm(pending)
    assert case.finishes.confirm(pending) == expected


@pytest.mark.parametrize("missing", ["no_attach_none", "no_attach_object", "different_observation"])
def test_completion_requires_a_previously_attached_non_null_matching_observation(case, mock_kernel, missing):
    live = case.open()
    observation = None if missing == "no_attach_none" else _EqualObservation()
    if missing == "different_observation":
        case.finishes.attach(live, _EqualObservation())
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
    mock_kernel.assert_not_called()
    _assert_no_pending(case)


def test_only_one_non_null_observation_can_be_attached(case, mock_kernel):
    live = case.open()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.attach(live, None)
    first = _EqualObservation()
    case.finishes.attach(live, first)
    for replacement in (first, _EqualObservation()):
        with pytest.raises(NativeParserConflict, match=_REJECT):
            case.finishes.attach(live, replacement)
    assert case.finishes._live[live].observation is first
    mock_kernel.assert_not_called()


@pytest.mark.parametrize("damage", ["not_exact_state", "state_subclass", "intent_subclass", "not_admitted", "unknown_operation", "job_closed", "run_closed", "physical_closed", "no_intents", "last_intent_mismatch", "admission_mismatch"])
def test_open_rejects_invalid_local_capture_without_issuing_a_handle(case, damage):
    if damage == "not_exact_state":
        case.state = SimpleNamespace(**vars(case.state))
    elif damage == "state_subclass":
        case.state = type("DerivedExecutionState", (NativeParserExecutionState,), {})(**vars(case.state))
    elif damage == "intent_subclass":
        case.intent = _DictSubclass(case.intent)
        case.payload["attempt_intents"][-1] = case.intent
    elif damage == "not_admitted":
        case.intent["state"] = "FINISHED"
    elif damage == "unknown_operation":
        case.intent["operation"] = "synthetic_unknown_operation"
    elif damage in {"job_closed", "run_closed"}:
        getattr(case, damage.split("_")[0])["status"] = "SUCCEEDED"
    elif damage == "physical_closed":
        case.physical.status = "SUCCEEDED"
    elif damage == "no_intents":
        case.payload["attempt_intents"] = []
    elif damage == "last_intent_mismatch":
        case.payload["attempt_intents"][-1] = {**case.intent, "intent_id": _uuid(999)}
    else:
        case.payload["admission"]["synthetic_only"] = False
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.open()
    assert not case.finishes._live and not case.finishes._seen_intents
    _assert_no_pending(case)


@pytest.mark.parametrize("field", ["job.attempt", "job.max_attempts", "job.fence_token", "run.version", "physical.fence_token", "physical.attempts", "physical.max_retries", "intent.attempt", "intent.business_fence", "intent.physical_fence"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 2**63, 1.0, "1", None, _IntSubclass(1)])
def test_identity_integers_are_exact_bounded_ints(case, field, value):
    target, name = field.split(".")
    container = getattr(case, target)
    if target == "physical":
        setattr(container, name, value)
    else:
        container[name] = value
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.open()
    assert not case.finishes._live


@pytest.mark.parametrize("field", ["intent.delivery_generation", "intent.source_version", "payload.delivery_generation", "delivery.fence_token", "leases.business_fence"])
def test_other_identity_fields_cannot_smuggle_boolean_one(case, field):
    # Keep numeric equality true so this exercises type validation, not an
    # incidental mismatch elsewhere in the synthetic state.
    if field == "leases.business_fence":
        case.job["fence_token"] = case.intent["business_fence"] = 1
    target, name = field.split(".")
    container = getattr(case, target)
    if target in {"delivery", "leases"}:
        setattr(container, name, True)
    else:
        container[name] = True
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.open()
    assert not case.finishes._live


@pytest.mark.parametrize("damage", ["physical_worker", "physical_fence", "physical_job", "physical_payload", "business_job", "tenant", "run", "snapshot", "generation"])
def test_open_binds_synthetic_state_and_intent_to_this_io_delivery(case, damage):
    if damage == "physical_worker":
        case.physical.locked_by = "another-synthetic-owner"
    elif damage == "physical_fence":
        case.physical.fence_token += 1
    elif damage == "physical_job":
        case.physical.job_id = _uuid(999)
    elif damage == "physical_payload":
        case.physical.payload["delivery_generation"] = 2
    elif damage == "business_job":
        case.job["job_id"] = _uuid(999)
    elif damage == "tenant":
        case.job["tenant_id"] = case.run["tenant_id"] = _uuid(999)
    elif damage == "run":
        case.intent["parser_run_id"] = _uuid(999)
    elif damage == "snapshot":
        case.intent["source_snapshot_id"] = _uuid(999)
    else:
        case.intent["delivery_generation"] = case.payload["delivery_generation"] = 2
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.open()
    assert not case.finishes._live


def test_duplicate_intent_is_rejected_while_open(case):
    first = case.open()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.open()
    assert list(case.finishes._live) == [first]


@pytest.mark.parametrize("terminal", ["confirmed", "unresolved"])
def test_duplicate_intent_cannot_be_reissued_after_terminal_state(case, terminal):
    _, _, pending = case.pending()
    if terminal == "confirmed":
        case.seal(pending)
        case.finishes.confirm(pending)
    else:
        case.finishes.unresolved(pending)
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.open()
    assert len(case.finishes._live) == len(case.finishes._seen_intents) == 1


@pytest.mark.parametrize("terminal", ["confirmed", "unresolved"])
def test_four_operation_lifetime_bound_is_not_reset_by_closing_records(case, mock_kernel, terminal):
    for operation in _OPERATIONS:
        case.new_intent(operation)
        _, _, pending = case.pending()
        if terminal == "confirmed":
            case.seal(pending)
            case.finishes.confirm(pending)
        else:
            case.finishes.unresolved(pending)
    case.new_intent()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.open()
    assert len(case.finishes._live) == len(case.finishes._pending) == 4
    assert len(case.finishes._seen_intents) == 4
    assert mock_kernel.call_count == 4


@pytest.mark.parametrize("kwargs", [{}, {"observed": {}, "error": RuntimeError("synthetic")}, {"observed": True}, {"observed": []}, {"observed": _DictSubclass(sha256="9" * 64)}, {"error": "synthetic error text"}, {"error": object()}])
def test_completion_needs_exactly_one_typed_outcome(case, mock_kernel, kwargs):
    live, observation = case.attached()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, **kwargs)
    mock_kernel.assert_not_called()
    _assert_no_pending(case)


@pytest.mark.parametrize("derived", [False, True])
def test_actual_unreaped_exception_type_never_creates_pending(case, mock_kernel, derived):
    error_type = type("DerivedUnreaped", (NativeIOUnreaped,), {}) if derived else NativeIOUnreaped
    live, observation = case.attached()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, error=error_type("NATIVE_IO_CHILD_UNREAPED"))
    mock_kernel.assert_not_called()
    _assert_no_pending(case)


@pytest.mark.parametrize("reason", ["ZERO_RUNS", "AMBIGUOUS_SPAWN", "MULTIPLE_RUNS", "CAPTURE_OPEN"])
def test_kernel_refusal_never_creates_pending_or_allows_reconsumption(case, mock_kernel, reason):
    live, observation = case.attached()
    unavailable = _MockCompletionUnavailable("MOCK_ONLY_" + reason)
    mock_kernel.side_effect = unavailable
    with pytest.raises(_MockCompletionUnavailable) as raised:
        case.finishes.observed(live, observation, error=RuntimeError("synthetic invocation failure"))
    assert raised.value is unavailable
    _assert_no_pending(case)
    mock_kernel.side_effect = None
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.attach(live, observation)
    assert mock_kernel.call_count == 1


def test_consume_failure_releases_the_transient_observation_reference(case, mock_kernel):
    live, observation = case.attached()
    mock_kernel.side_effect = _MockCompletionUnavailable("MOCK_ONLY_AMBIGUOUS")
    with pytest.raises(_MockCompletionUnavailable):
        case.finishes.observed(live, observation, error=RuntimeError("synthetic"))
    _assert_no_pending(case)
    _assert_record_contains_only_encoded_facts(case.finishes._live[live])


@pytest.mark.parametrize("receipt", [None, True, False, 0, "UNKNOWN", "REAPED ", "reaped", b"REAPED", _StringSubclass("REAPED"), {}, []])
def test_invalid_mock_kernel_receipt_is_rejected_and_burns_the_attempt(case, mock_kernel, receipt):
    live, observation = case.attached()
    mock_kernel.return_value = receipt
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, error=RuntimeError("synthetic"))
    _assert_no_pending(case)
    mock_kernel.return_value = "REAPED"
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, error=RuntimeError("synthetic"))
    assert mock_kernel.call_count == 1


def test_never_launched_mock_receipt_cannot_finish_a_success(case, mock_kernel):
    live, observation = case.attached()
    mock_kernel.return_value = "NEVER_LAUNCHED"
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
    _assert_no_pending(case)
    mock_kernel.return_value = "REAPED"
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
    assert mock_kernel.call_count == 1


@pytest.mark.parametrize("receipt", ["REAPED", "NEVER_LAUNCHED"])
@pytest.mark.parametrize("error,metadata", [
    (NativeIORejected("NATIVE_GCS_PROVIDER_REJECTED", status=503),
     {"version": 1, "family": "NativeIORejected", "code": "NATIVE_GCS_PROVIDER_REJECTED", "status": 503}),
    (NativeParserOutputRejected("synthetic secret body"),
     {"version": 1, "family": "NativeParserOutputRejected", "code": "NATIVE_PARSER_OUTPUT_REJECTED", "status": None}),
])
def test_error_metadata_uses_original_bounded_typed_observation(case, mock_kernel, receipt, error, metadata):
    mock_kernel.return_value = receipt
    _, _, pending = case.pending(error=error)
    expected = case.seal(pending)
    assert expected["state"] == "ERROR"
    assert expected["observed"] is None
    assert expected["error_class"] == type(error).__name__
    assert expected["failure_observation"] == metadata
    record = case.finishes._pending[pending]
    assert "synthetic secret body" not in record.outcome_json
    _assert_record_contains_only_encoded_facts(record)


@pytest.mark.parametrize("error", [
    RuntimeError("synthetic secret body"),
    SystemExit("synthetic secret body"),
    type("NativeIORejected", (RuntimeError,), {})("NATIVE_IO_PROTOCOL_REJECTED"),
    type("DerivedNativeIORejected", (NativeIORejected,), {})("NATIVE_IO_PROTOCOL_REJECTED"),
    NativeIORejected("Bearer synthetic-secret"),
    NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED", status=True),
    NativeIORejected("NATIVE_IO_PROTOCOL_REJECTED", status=600),
])
def test_unknown_or_invalid_error_metadata_does_not_acquire_failure_classification(case, error):
    _, _, pending = case.pending(error=error)
    expected = case.seal(pending)
    assert expected["state"] == "ERROR" and expected["observed"] is None
    assert expected["error_class"] == type(error).__name__
    assert "failure_observation" not in expected
    record = case.finishes._pending[pending]
    assert "synthetic secret body" not in record.outcome_json
    assert "synthetic-secret" not in record.outcome_json
    _assert_record_contains_only_encoded_facts(record)


@pytest.mark.parametrize("name", ["", "9Invalid", "Bad\nName", "échec", "X" * 97])
def test_unknown_exception_class_name_is_bounded_without_retaining_text(case, name):
    error = type(name, (RuntimeError,), {})("synthetic secret body")
    _, _, pending = case.pending(error=error)
    expected = case.seal(pending)
    assert expected["error_class"] == "NativeParserOperationError"
    assert "failure_observation" not in expected
    assert "synthetic secret body" not in canonical_json(expected)


def test_error_class_name_access_failure_has_a_bounded_fallback(case):
    class BrokenName(type):
        def __getattribute__(cls, name):
            if name == "__name__":
                raise RuntimeError("synthetic class-name secret")
            return super().__getattribute__(name)

    class UnknownError(RuntimeError, metaclass=BrokenName):
        pass

    _, _, pending = case.pending(error=UnknownError("synthetic secret body"))
    expected = case.seal(pending)
    assert expected["error_class"] == "NativeParserOperationError"
    assert "failure_observation" not in expected
    assert "secret" not in canonical_json(expected)


@pytest.mark.parametrize("bad_value,exception", [(object(), TypeError), (float("nan"), ValueError)])
def test_outcome_encoding_failure_cannot_issue_again(case, mock_kernel, bad_value, exception):
    live, observation = case.attached()
    with pytest.raises(exception):
        case.finishes.observed(live, observation, observed={"sha256": "9" * 64, "bad": bad_value})
    _assert_no_pending(case)
    _assert_record_contains_only_encoded_facts(case.finishes._live[live])
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
    assert mock_kernel.call_count == 1


def test_failure_metadata_encoding_error_also_burns_the_receipt(case, mock_kernel, monkeypatch):
    live, observation = case.attached()
    failure = RuntimeError("synthetic metadata failure")
    monkeypatch.setattr(completion, "parser_failure_observation", Mock(side_effect=failure))
    with pytest.raises(RuntimeError) as raised:
        case.finishes.observed(live, observation, error=RuntimeError("synthetic original"))
    assert raised.value is failure
    _assert_no_pending(case)
    _assert_record_contains_only_encoded_facts(case.finishes._live[live])
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
    assert mock_kernel.call_count == 1


def test_facts_are_deep_snapshots_and_readbacks_never_retain_positive_grants(case):
    live = case.open()
    # Mutate nested source references after capture, before observation.
    case.original_binding["lineage"]["source_id"] = "changed-original"
    case.run["field_confidence"]["synthetic"]["score"] = 0.0
    case.run["validation_errors"][0]["code"] = "changed-error"
    case.intent["reservation"]["source_id"] = "changed-reservation"
    case.payload["admission"]["synthetic_only"] = False
    case.physical.payload["delivery_generation"] = 9
    observation = _EqualObservation()
    case.finishes.attach(live, observation)
    observed = {"sha256": "9" * 64}
    pending = case.finishes.observed(live, observation, observed=observed)
    observed["sha256"] = "0" * 64
    facts, intent, payload = case.finishes.facts(pending)
    assert facts["original_input"]["lineage"]["source_id"] == "synthetic-completion-facts"
    assert facts["run"]["field_confidence"] == {"synthetic": {"score": 0.5}}
    assert facts["run"]["validation_errors"] == [{"code": "synthetic-only"}]
    assert facts["physical"]["payload_sha256"] == canonical_sha256({"synthetic_only": True, "delivery_generation": 1})
    assert facts["run"]["started_at"] == (_AT - timedelta(seconds=1)).isoformat()
    assert intent["reservation"]["source_id"] == "synthetic-completion-facts"
    assert payload["admission"]["synthetic_only"] is True
    baseline = canonical_json([facts, intent, payload])
    facts["original_input"]["lineage"].clear()
    facts["run"]["field_confidence"]["synthetic"]["score"] = 2
    intent["reservation"].clear()
    payload["attempt_intents"][0]["arguments"]["invented"] = True
    assert canonical_json(case.finishes.facts(pending)) == baseline
    record = case.finishes._pending[pending]
    assert json.loads(record.outcome_json)["observed"] == {"sha256": "9" * 64}
    _assert_record_contains_only_encoded_facts(record)
    # Grant/context/state/error/observation objects never reside in records.
    assert "positive_grant" not in record.facts_json
    assert "before" not in json.loads(record.facts_json)


def test_original_typed_failure_is_frozen_before_error_object_mutation(case):
    error = NativeIORejected("NATIVE_GCS_PROVIDER_REJECTED", status=503)
    error.private_body = "synthetic secret body"
    _, _, pending = case.pending(error=error)
    record = case.finishes._pending[pending]
    frozen_json, frozen_hash = record.outcome_json, record.outcome_sha256
    error.code, error.status, error.args = "NATIVE_IO_PROTOCOL_REJECTED", 403, ("changed secret",)
    expected = case.seal(pending)
    assert expected["failure_observation"] == {
        "version": 1, "family": "NativeIORejected", "code": "NATIVE_GCS_PROVIDER_REJECTED", "status": 503,
    }
    expected["failure_observation"]["status"] = 200
    assert case.finishes.expected(pending)["failure_observation"]["status"] == 503
    assert record.outcome_json == frozen_json and record.outcome_sha256 == frozen_hash
    assert "secret" not in record.outcome_json
    _assert_record_contains_only_encoded_facts(record)


def test_outcome_digest_binds_domain_intent_and_mock_lifecycle_fact(case, mock_kernel):
    _, _, pending = case.pending(error=RuntimeError("synthetic"))
    record = case.finishes._pending[pending]
    assert record.outcome_sha256 == canonical_sha256({
        "domain": "native-parser-live-completion-v1", "intent_sha256": canonical_sha256(case.intent),
        "lifecycle_fact": "REAPED", "outcome": json.loads(record.outcome_json),
    })
    other = _SyntheticCase()
    mock_kernel.return_value = "NEVER_LAUNCHED"
    _, _, other_pending = other.pending(error=RuntimeError("synthetic"))
    other_record = other.finishes._pending[other_pending]
    assert other_record.intent_json == record.intent_json
    assert other_record.outcome_json == record.outcome_json
    assert other_record.outcome_sha256 != record.outcome_sha256
    third = _SyntheticCase()
    third.new_intent()
    mock_kernel.return_value = "REAPED"
    _, _, third_pending = third.pending(error=RuntimeError("synthetic"))
    assert third.finishes._pending[third_pending].outcome_sha256 != record.outcome_sha256


def test_finished_timestamp_is_sealed_once_and_readbacks_are_byte_identical(case):
    _, _, pending = case.pending()
    at = _AT + timedelta(microseconds=123456)
    first = case.seal(pending, at)
    expected_bytes = canonical_json(first).encode("utf-8")
    assert first["finished_at"] == at.isoformat()
    assert first["state"] == "FINISHED" and first["observed"] == {"sha256": "9" * 64}
    first["reservation"]["source_id"] = "changed-return-copy"
    first["observed"]["sha256"] = "0" * 64
    for later in (at + timedelta(seconds=1), at + timedelta(days=1)):
        assert canonical_json(case.seal(pending, later)).encode("utf-8") == expected_bytes
        readback = case.finishes.expected(pending)
        assert canonical_json(readback).encode("utf-8") == expected_bytes
        readback["arguments"]["invented"] = True
    record = case.finishes._pending[pending]
    assert record.expected_json.encode("utf-8") == expected_bytes
    assert canonical_json(case.finishes.confirm(pending)).encode("utf-8") == expected_bytes
    assert record.expected_json.encode("utf-8") == expected_bytes


def test_first_finish_timestamp_cannot_precede_admission(case):
    _, _, pending = case.pending()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.seal(pending, _AT - timedelta(microseconds=1))
    assert case.finishes.expected(pending) is None
    assert case.seal(pending, _AT)["finished_at"] == _AT.isoformat()


@pytest.mark.parametrize("terminal", ["confirmed", "unresolved_unsealed", "unresolved_sealed"])
@pytest.mark.parametrize("action", ["attach", "observed", *_PENDING_ACTIONS])
def test_terminal_tokens_cannot_resume_any_live_or_pending_operation(case, mock_kernel, terminal, action):
    live, observation, pending = case.pending()
    if terminal != "unresolved_unsealed":
        case.seal(pending)
    if terminal == "confirmed":
        case.finishes.confirm(pending)
    else:
        case.finishes.unresolved(pending)
    mock_kernel.reset_mock()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        if action == "attach":
            case.finishes.attach(live, observation)
        elif action == "observed":
            case.finishes.observed(live, observation, observed={"sha256": "9" * 64})
        elif action == "seal":
            case.seal(pending)
        else:
            getattr(case.finishes, action)(pending)
    mock_kernel.assert_not_called()
    record = case.finishes._pending[pending]
    assert record.phase == ("CONFIRMED" if terminal == "confirmed" else "UNRESOLVED")
    _assert_record_contains_only_encoded_facts(record)


def test_execution_state_dataclass_is_used_without_calling_authority_readers(case):
    # A dataclass copy is still synthetic input, never proof of current rows.
    # Its exact type is accepted; fake namespaces/subclasses are tested above.
    case.state = replace(case.state)
    _, _, pending = case.pending()
    facts, _, _ = case.finishes.facts(pending)
    assert facts["job"]["job_id"] == _uuid(2)
    assert facts["run"]["parser_run_id"] == _uuid(3)


def test_owned_pre_adapter_failure_can_finish_without_entering_the_kernel(case, mock_kernel):
    live = case.open()
    error = NativeIORejected("NATIVE_IO_PARSER_DEADLINE_EXPIRED")
    pending = case.finishes.not_invoked(live, error)
    expected = case.seal(pending)
    assert expected["state"] == "ERROR" and expected["observed"] is None
    assert expected["failure_observation"]["code"] == error.code
    assert case.finishes.confirm(pending) == expected
    mock_kernel.assert_not_called()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.not_invoked(live, error)


def test_adapter_entry_cannot_fall_back_to_a_never_invoked_claim(case, mock_kernel):
    live, observation = case.attached()
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.not_invoked(live, RuntimeError("synthetic"))
    mock_kernel.assert_not_called()
    mock_kernel.side_effect = _MockCompletionUnavailable("MOCK_ONLY_ZERO_RUN")
    with pytest.raises(_MockCompletionUnavailable):
        case.finishes.observed(live, observation, error=RuntimeError("synthetic"))
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.not_invoked(live, RuntimeError("synthetic"))
    assert mock_kernel.call_count == 1


@pytest.mark.parametrize("error", [None, True, "synthetic", NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED")])
def test_never_invoked_path_requires_a_real_error_and_refuses_unreaped(case, mock_kernel, error):
    with pytest.raises(NativeParserConflict, match=_REJECT):
        case.finishes.not_invoked(case.open(), error)
    mock_kernel.assert_not_called()
    _assert_no_pending(case)
