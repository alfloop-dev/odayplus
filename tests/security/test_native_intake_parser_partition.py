"""Mock scan control and routing tests; no SQL/history/authority proof."""
import base64
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi import HTTPException

from modules.listing.application import native_intake_parser_partition as partition
from modules.listing.application.native_intake_parser import NativeIntakeParserOwner
from modules.listing.application.native_intake_parser_policy import (
    NativeParserConflict,
    NativeParserUnavailable,
)
from shared.jobs.queue import JobFenceRejectedError

TENANT = str(UUID(int=1))
RELEASE = str(UUID(int=2))
SOURCE = "synthetic-partition.manual"
AT = datetime(2026, 10, 18, tzinfo=UTC)


def candidate(number):
    return {"transition_id": str(UUID(int=100 + number)), "occurred_at": AT,
        "intake_id": str(UUID(int=200 + number)), "source_snapshot_id": str(UUID(int=300 + number)),
        "version": number + 5}


@pytest.fixture
def scan(monkeypatch):
    engine = SimpleNamespace(_local=SimpleNamespace(connection=None))

    @contextmanager
    def transaction(tenant):
        assert tenant == TENANT and engine._local.connection is None
        engine._local.connection = object()
        try:
            yield
        finally:
            engine._local.connection = None

    owner = SimpleNamespace(engine=engine, graph=SimpleNamespace(engine=engine, transaction=transaction),
        clock=object(), _prerequisites=Mock(), admit=Mock())
    policy = Mock()
    monkeypatch.setattr(partition, "native_intake_parser_policy", policy)
    worker = partition.NativeParserPartition(owner)
    return worker, owner, policy


def mock_rows(worker, rows, monkeypatch):
    queries = []

    def read(tenant, source, position, limit):
        assert tenant == TENANT and source == SOURCE
        assert worker.engine._local.connection is None
        queries.append((position, limit))
        return [r for r in rows if position is None or
            (r["occurred_at"], r["transition_id"]) > (partition._time(position[0]), position[1])][:limit]

    monkeypatch.setattr(worker, "_rows", read)
    return queries


def execute(worker, **overrides):
    return list(worker.admit(TENANT, **({"source_id": SOURCE, "release_id": RELEASE, "limit": 25} | overrides)))


def test_mock_candidates_use_exact_snapshot_version_release_outside_scan_root(scan, monkeypatch):
    worker, owner, policy = scan
    first, second = candidate(1), candidate(2)
    mock_rows(worker, [first, second], monkeypatch)

    def admit(*args, **kwargs):
        assert owner.engine._local.connection is None
        return {"job_id": args[1]}

    owner.admit.side_effect = admit
    result = execute(worker)
    assert result == [{"job_id": first["intake_id"]}, {"job_id": second["intake_id"]}]
    assert owner.admit.call_args_list[0].args == (TENANT, first["intake_id"], first["source_snapshot_id"], RELEASE)
    assert owner.admit.call_args_list[1].kwargs == {"expected_version": second["version"]}
    owner._prerequisites.assert_called_once_with()
    policy.assert_called_once_with(TENANT)
    assert owner.last_partition_progress["admitted"] == 2 and not owner.last_partition_progress["has_more"]


def test_mock_limit_continuation_preserves_tie_order_and_never_retries_prior_candidate(scan, monkeypatch):
    worker, owner, _ = scan
    rows = [candidate(n) for n in range(1, 4)]
    mock_rows(worker, rows, monkeypatch)
    owner.admit.side_effect = lambda *args, **kwargs: {"job_id": args[1]}
    assert len(execute(worker, limit=1)) == 1
    progress = owner.last_partition_progress
    assert progress["scanned"] == progress["admitted"] == 1 and progress["has_more"]
    next_worker = partition.NativeParserPartition(owner)
    mock_rows(next_worker, rows, monkeypatch)
    assert len(execute(next_worker, cursor=progress["next_cursor"])) == 2
    assert [call.args[1] for call in owner.admit.call_args_list] == [r["intake_id"] for r in rows]


@pytest.mark.parametrize("error", [NativeParserConflict("private"), JobFenceRejectedError("private"),
    HTTPException(401, "private"), HTTPException(403, "private"), HTTPException(409, "private")])
def test_mock_known_candidate_conflict_advances_without_values_or_retry(scan, monkeypatch, error):
    worker, owner, _ = scan
    mock_rows(worker, [candidate(1), candidate(2)], monkeypatch)
    owner.admit.side_effect = [error, {"job_id": "synthetic-durable-receipt"}]
    assert execute(worker) == [{"job_id": "synthetic-durable-receipt"}]
    progress = owner.last_partition_progress
    assert progress["scanned"] == 2 and progress["conflicts"] == progress["admitted"] == 1
    assert "private" not in json.dumps(progress)


@pytest.mark.parametrize("state", ["40001", "40P01", "55P03", "57014"])
def test_mock_wrapped_expected_SQL_conflict_is_bounded_hint_only(state):
    cause = RuntimeError("private-driver-values")
    cause.sqlstate = state
    error = RuntimeError("private-wrapper-values")
    error.__cause__ = cause
    assert partition._candidate_conflict(error)
    cause.__cause__ = error
    cause.sqlstate = "08006"
    assert not partition._candidate_conflict(error)


@pytest.mark.parametrize("error", [RuntimeError("private"), ValueError("programming-failure"),
    NativeParserUnavailable("private"), HTTPException(500, "private")])
def test_mock_unknown_outcome_preserves_original_and_prior_committed_receipt(scan, monkeypatch, error):
    worker, owner, _ = scan
    rows = [candidate(1), candidate(2)]
    mock_rows(worker, rows, monkeypatch)
    owner.admit.side_effect = [{"job_id": "already-committed"}, error]
    iterator = worker.admit(TENANT, source_id=SOURCE, release_id=RELEASE, limit=25)
    assert next(iterator) == {"job_id": "already-committed"}
    with pytest.raises(type(error)) as caught:
        next(iterator)
    assert caught.value is error
    progress = owner.last_partition_progress
    assert progress["scanned"] == progress["admitted"] == 1 and progress["conflicts"] == 0
    assert partition._position(progress["next_cursor"], TENANT, SOURCE, RELEASE)[1] == rows[0]["transition_id"]


@pytest.mark.parametrize("limit", [True, False, None, 0, 101, 1.0, "1"])
def test_mock_invalid_limit_fails_before_positive_or_SQL_reads(scan, limit):
    worker, owner, policy = scan
    with pytest.raises(ValueError, match="PARTITION_LIMIT_REJECTED"):
        execute(worker, limit=limit)
    owner._prerequisites.assert_not_called()
    policy.assert_not_called()
    owner.admit.assert_not_called()


@pytest.mark.parametrize("source", [None, 1, "", " bad", "bad ", "x" * 257, "x\x00", "x\x7f", "\ud800"])
def test_mock_invalid_source_fails_without_echo_or_owner_call(scan, source):
    worker, owner, _ = scan
    with pytest.raises(ValueError, match="^NATIVE_PARSER_PARTITION_SOURCE_REJECTED$"):
        execute(worker, source_id=source)
    owner.admit.assert_not_called()
    owner._prerequisites.assert_not_called()


def test_mock_outer_root_and_cold_startup_failure_cannot_scan_or_admit(scan, monkeypatch):
    worker, owner, _ = scan
    reader = Mock()
    monkeypatch.setattr(worker, "_rows", reader)
    owner.engine._local.connection = object()
    with pytest.raises(NativeParserConflict, match="PARTITION_OUTER_ROOT_REJECTED"):
        execute(worker)
    owner._prerequisites.assert_not_called()
    owner.engine._local.connection = None
    error = NativeParserUnavailable("cold-startup-not-a-grant")
    owner._prerequisites.side_effect = error
    with pytest.raises(NativeParserUnavailable) as caught:
        execute(worker)
    assert caught.value is error
    reader.assert_not_called()
    owner.admit.assert_not_called()


@pytest.mark.parametrize("mutation", ["tenant_id", "source_id", "parser_release_id", "kind", "version",
    "extra", "transition_id", "occurred_at", "duplicate"])
def test_mock_cursor_is_closed_and_bound_to_entire_explicit_selection(mutation):
    token = partition._cursor((AT, str(UUID(int=3))), TENANT, SOURCE, RELEASE)
    value = json.loads(base64.urlsafe_b64decode(token))
    if mutation == "duplicate":
        raw = json.dumps(value)[:-1] + ',"version":1}'
    else:
        value[mutation] = True if mutation == "version" else "private-cursor-value"
        raw = json.dumps(value)
    changed = base64.urlsafe_b64encode(raw.encode()).decode()
    with pytest.raises(ValueError, match="^NATIVE_PARSER_PARTITION_CURSOR_REJECTED$"):
        partition._position(changed, TENANT, SOURCE, RELEASE)


@pytest.mark.parametrize("cursor", ["", "not-base64!", "x" * 2049, True, 1, "W10=", "bnVsbA=="])
def test_mock_malformed_cursor_cannot_reach_scan(scan, monkeypatch, cursor):
    worker, owner, _ = scan
    reader = Mock()
    monkeypatch.setattr(worker, "_rows", reader)
    with pytest.raises(ValueError, match="PARTITION_CURSOR_REJECTED"):
        execute(worker, cursor=cursor)
    reader.assert_not_called()
    owner._prerequisites.assert_not_called()


def test_mock_conflict_scan_cap_and_examples_are_bounded(scan, monkeypatch):
    worker, owner, _ = scan
    rows = [candidate(n) for n in range(1, 102)]
    queries = mock_rows(worker, rows, monkeypatch)
    monkeypatch.setattr(partition, "MAX_SCAN_EVENTS", 70)
    owner.admit.side_effect = NativeParserConflict("private")
    assert execute(worker) == []
    progress = owner.last_partition_progress
    assert progress["scanned"] == progress["conflicts"] == 70
    assert progress["has_more"] and len(progress["conflict_examples"]) == 10
    assert queries[0][1] == 64 and queries[1][1] == 6 and owner.admit.call_count == 70


def test_mock_scan_wall_bound_does_not_admit_a_new_candidate_after_expiry(scan, monkeypatch):
    worker, owner, _ = scan
    reader = Mock(return_value=[candidate(1)])
    monkeypatch.setattr(worker, "_rows", reader)
    monkeypatch.setattr(partition, "time", SimpleNamespace(monotonic=Mock(side_effect=[0, 11])))
    assert execute(worker) == []
    reader.assert_called_once_with(TENANT, SOURCE, None, 1)
    owner.admit.assert_not_called()


def test_mock_scan_read_exits_its_budgeted_root_before_returning_candidates(scan, monkeypatch):
    worker, owner, _ = scan
    before = Mock(side_effect=lambda: owner.engine._local.connection is not None or pytest.fail("No root"))
    budget = Mock(return_value=before)
    monkeypatch.setattr(partition, "NativeParserCommandBudget", budget)

    def query(statement, arguments):
        assert owner.engine._local.connection is not None
        assert arguments == (TENANT, SOURCE, None, None, None, 64)
        assert "parser_releases" not in statement
        return [candidate(1)]

    owner.engine.query = Mock(side_effect=query)
    assert worker._rows(TENANT, SOURCE, None, 64) == [candidate(1)]
    assert owner.engine._local.connection is None and before.call_count == 2
    budget.assert_called_once_with(owner.clock)
    owner.admit.assert_not_called()


def test_mock_generator_close_preserves_durable_receipt_and_conservative_continuation(scan, monkeypatch):
    worker, owner, _ = scan
    mock_rows(worker, [candidate(1), candidate(2)], monkeypatch)
    owner.admit.return_value = {"job_id": "durable"}
    iterator = worker.admit(TENANT, source_id=SOURCE, release_id=RELEASE, limit=25)
    assert next(iterator) == {"job_id": "durable"}
    iterator.close()
    assert owner.last_partition_progress["admitted"] == 1 and owner.last_partition_progress["has_more"]
    assert owner.admit.call_count == 1


def test_mock_owner_public_method_forwards_to_partition_without_running_a_worker(scan):
    _, owner, _ = scan
    # The real SQL selection and durable admission are independently covered
    # by PostgreSQL tests. This is only the actual method's argument routing.
    owner._prerequisites.side_effect = NativeParserUnavailable("expected-preflight")
    with pytest.raises(NativeParserUnavailable, match="expected-preflight"):
        list(NativeIntakeParserOwner.admit_partition(owner, TENANT, source_id=SOURCE,
            release_id=RELEASE, limit=1))
    owner.admit.assert_not_called()
