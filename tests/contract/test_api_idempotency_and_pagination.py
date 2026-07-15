"""Idempotency policy and collection semantics (ODP-PGAP-API-001).

Criterion 5 asks for "replay or conflict tests" on mutations, and criterion 4
for consistent pagination/filtering/sorting. The priceops router is the worked
example: it owned the largest state machine and, before this task, guarded
`POST /plans` while leaving every transition -- submit, approve, activate,
rollback -- unguarded. Those are the operations where a double-submit is a real
state change rather than a duplicate row.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.api.errors import ErrorCode
from shared.api.idempotency import (
    MISSING,
    IdempotencyConflictError,
    IdempotencyStore,
    request_fingerprint,
)
from shared.api.pagination import MAX_LIMIT, PageParams, page_params, paginate
from shared.auth import Role
from tests.integration._authz import auth_headers

# PriceOps view/create/approve/execute is held by the pricing manager; reuse the
# shared bundle rather than hand-rolling headers that drift from the RBAC matrix.
WRITE_HEADERS = auth_headers(Role.PRICING_MANAGER)


def _plan_body(plan_id: str = "PLAN-IDEM-1", *, tenant_id: str = "tenant-a") -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "plan_id": plan_id,
        "items": [
            {
                "item_id": f"{plan_id}-item-1",
                "store_id": "api-store-1",
                "machine_type": "washer-20kg",
                "unit_cost": 10.0,
                "current_price": 20.0,
                "baseline_demand": 500.0,
                "elasticity_value": -1.0,
                "margin_floor_ratio": 0.2,
                "max_increase_pct": 0.1,
            }
        ],
    }


def _client() -> TestClient:
    return TestClient(create_app(external_provider_validation=lambda: None))


def _create_plan(client: TestClient, plan_id: str) -> None:
    response = client.post("/api/v1/priceops/plans", json=_plan_body(plan_id),
                           headers=WRITE_HEADERS)
    assert response.status_code == 201, response.text


def _seed_submittable_plan(client: TestClient, plan_id: str) -> None:
    """Drive a plan to the state `submit` accepts.

    A plan created via POST /plans is a `candidate` and cannot be submitted; the
    optimizer job is what produces a submittable plan, so the lifecycle tests
    seed through it exactly as the integration suite does.
    """
    optimizer = client.post(
        "/api/v1/priceops/optimizer-jobs",
        json={"optimized_at": "2026-06-28T03:00:00Z", "plans": [_plan_body(plan_id)]},
        headers=WRITE_HEADERS,
    )
    assert optimizer.status_code == 202, optimizer.text
    submit = client.post(
        f"/api/v1/priceops/plans/{plan_id}/submit",
        json={"actor": "pricing-manager", "reason": "send plan to approval"},
        headers=WRITE_HEADERS,
    )
    assert submit.status_code == 200, submit.text


# --- the store itself ---


def test_store_replays_the_same_request_and_does_not_re_execute() -> None:
    store = IdempotencyStore()
    calls: list[int] = []

    def operation() -> str:
        calls.append(1)
        return "result"

    payload = {"a": 1}
    first = store.run(key="k1", scope="s", payload=payload, operation=operation)
    second = store.run(key="k1", scope="s", payload=payload, operation=operation)

    assert (first.value, first.replayed) == ("result", False)
    assert (second.value, second.replayed) == ("result", True)
    assert calls == [1], "the replay must not run the operation a second time"


def test_store_raises_conflict_when_a_key_is_reused_for_a_different_payload() -> None:
    """The bug the old per-router dicts had.

    They keyed on the bare key, so a second, *different* request reusing a key
    got the first response back — silently acknowledging a mutation that never
    happened. This must be a 409 instead.
    """
    store = IdempotencyStore()
    store.run(key="k1", scope="s", payload={"amount": 10}, operation=lambda: "first")

    with pytest.raises(IdempotencyConflictError):
        store.run(key="k1", scope="s", payload={"amount": 999}, operation=lambda: "second")


def test_store_scopes_keys_per_operation() -> None:
    """The same client key on two operations is two mutations, not a replay."""
    store = IdempotencyStore()
    a = store.run(key="k1", scope="approve", payload={}, operation=lambda: "approved")
    b = store.run(key="k1", scope="rollback", payload={}, operation=lambda: "rolled-back")

    assert (a.value, b.value) == ("approved", "rolled-back")
    assert not b.replayed


def test_store_without_a_key_always_executes() -> None:
    store = IdempotencyStore()
    calls: list[int] = []
    for _ in range(2):
        store.run(key=None, scope="s", payload={}, operation=lambda: calls.append(1))
    assert len(calls) == 2


def test_fingerprint_ignores_key_order_but_not_values() -> None:
    assert request_fingerprint({"a": 1, "b": 2}) == request_fingerprint({"b": 2, "a": 1})
    assert request_fingerprint({"a": 1}) != request_fingerprint({"a": 2})


def test_fingerprint_survives_non_json_native_values() -> None:
    """Payloads carry datetimes and enums; a raising fingerprint would 500."""
    from datetime import UTC, datetime

    assert request_fingerprint({"at": datetime(2026, 7, 15, tzinfo=UTC)})


def test_store_evicts_oldest_entries_and_stays_bounded() -> None:
    """The dicts this replaces were unbounded and leaked for the process life."""
    store = IdempotencyStore(max_entries=2)
    for index in range(3):
        store.run(key=f"k{index}", scope="s", payload={}, operation=lambda: "v")

    assert store.lookup(key="k0", scope="s", fingerprint=request_fingerprint({})) is MISSING
    assert store.lookup(key="k2", scope="s", fingerprint=request_fingerprint({})) == "v"


def test_store_does_not_evict_when_overwriting_an_existing_key() -> None:
    """Re-remembering a key must not evict a live record: the dict does not
    grow, so the cap is not reached."""
    store = IdempotencyStore(max_entries=2)
    fingerprint = request_fingerprint({})
    store.remember(key="a", scope="s", fingerprint=fingerprint, value="a1")
    store.remember(key="b", scope="s", fingerprint=fingerprint, value="b1")
    store.remember(key="b", scope="s", fingerprint=fingerprint, value="b2")

    assert store.lookup(key="a", scope="s", fingerprint=fingerprint) == "a1"
    assert store.lookup(key="b", scope="s", fingerprint=fingerprint) == "b2"


def test_store_distinguishes_a_stored_none_from_no_record() -> None:
    """An operation legitimately returning None must still replay, not re-run."""
    store = IdempotencyStore()
    calls: list[int] = []

    def operation() -> None:
        calls.append(1)
        return None

    store.run(key="k", scope="s", payload={}, operation=operation)
    second = store.run(key="k", scope="s", payload={}, operation=operation)

    assert second.replayed is True
    assert calls == [1], "a stored None must not be mistaken for an absent record"


def test_store_run_is_atomic_under_concurrency() -> None:
    """Concurrent submits of the same key must execute exactly once.

    Sync routes are served from a threadpool, so two simultaneous `approve`
    requests really do race. With the lock taken only inside lookup/remember
    there was a check-then-act window and both executed — approving twice and
    writing two audit events, the exact failure this store exists to prevent.
    """
    store = IdempotencyStore()
    calls: list[int] = []
    barrier = threading.Barrier(8)

    def operation() -> str:
        calls.append(1)
        # Widen the window so a check-then-act bug is reliably caught rather
        # than depending on scheduler luck.
        time.sleep(0.05)
        return "value"

    def submit() -> None:
        barrier.wait()
        store.run(key="same-key", scope="s", payload={"a": 1}, operation=operation)

    threads = [threading.Thread(target=submit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == [1], f"operation ran {len(calls)} times under concurrency; must be exactly 1"


def test_store_does_not_serialise_distinct_keys() -> None:
    """The lock is per-entry: a slow mutation must not block an unrelated one."""
    store = IdempotencyStore()
    started = threading.Barrier(2)

    def slow() -> str:
        started.wait(timeout=2)
        time.sleep(0.2)
        return "slow"

    def quick() -> str:
        started.wait(timeout=2)
        return "quick"

    thread = threading.Thread(
        target=lambda: store.run(key="slow", scope="s", payload={}, operation=slow)
    )
    thread.start()
    start = time.perf_counter()
    store.run(key="quick", scope="s", payload={}, operation=quick)
    elapsed = time.perf_counter() - start
    thread.join()

    assert elapsed < 0.2, f"distinct keys serialised ({elapsed:.3f}s); the lock must be per-entry"


def test_failed_operation_is_not_remembered_as_success() -> None:
    """A 4xx must never be replayed as though the mutation had succeeded."""
    store = IdempotencyStore()

    with pytest.raises(ValueError):
        store.run(key="k", scope="s", payload={}, operation=_raise)

    outcome = store.run(key="k", scope="s", payload={}, operation=lambda: "ok")
    assert (outcome.value, outcome.replayed) == ("ok", False)


def _raise() -> None:
    raise ValueError("operation failed")


# --- the policy over HTTP ---


def test_transition_replay_returns_the_first_result_and_does_not_double_apply() -> None:
    """Replaying `approve` must not approve twice, nor re-audit."""
    client = _client()
    _seed_submittable_plan(client, "PLAN-REPLAY")
    body = {"actor_id": "approver", "reason": "pilot approved", "decision": "APPROVE"}
    headers = {**WRITE_HEADERS, "Idempotency-Key": "idem-approve-1",
               "x-correlation-id": "corr-approve-1"}

    first = client.post("/api/v1/priceops/plans/PLAN-REPLAY/approve", json=body, headers=headers)
    second = client.post("/api/v1/priceops/plans/PLAN-REPLAY/approve", json=body, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["idempotent_replay"] is False
    assert second.json()["idempotent_replay"] is True
    assert second.json()["audit_event_id"] == first.json()["audit_event_id"], (
        "a replay must not append a second audit event claiming the approval happened again"
    )

    events = client.get(
        "/api/v1/audit/events", params={"correlation_id": "corr-approve-1"}
    ).json()["events"]
    approvals = [e for e in events if e["event_type"] == "priceops.approved.v1"]
    assert len(approvals) == 1, "the plan must be approved exactly once"


def test_reusing_a_key_with_a_different_body_is_a_409_conflict() -> None:
    """One key must not be able to both approve and reject the same plan.

    This is the sharpest form of the conflict case, and it caught a real bug
    during this task: the guard originally scoped entries by the audit action,
    which for `approve` is `body.decision`. APPROVE and REJECT therefore landed
    in different scopes, so a retry that flipped the decision was not seen as a
    conflict and *both* transitions applied. The scope is now the event type,
    which is stable for the operation.
    """
    client = _client()
    _seed_submittable_plan(client, "PLAN-CONFLICT")
    headers = {**WRITE_HEADERS, "Idempotency-Key": "idem-conflict-1"}

    approve = client.post(
        "/api/v1/priceops/plans/PLAN-CONFLICT/approve",
        json={"actor_id": "approver", "reason": "approved", "decision": "APPROVE"},
        headers=headers,
    )
    assert approve.status_code == 200, approve.text

    reject = client.post(
        "/api/v1/priceops/plans/PLAN-CONFLICT/approve",
        json={"actor_id": "approver", "reason": "actually reject", "decision": "REJECT"},
        headers=headers,
    )

    assert reject.status_code == 409, reject.text
    assert reject.json()["error"]["code"] == ErrorCode.IDEMPOTENCY_CONFLICT
    assert reject.json()["error"]["next_action"]


def test_create_plan_replay_keeps_the_legacy_created_flag() -> None:
    """`created` predates this task and callers branch on it; it must survive."""
    client = _client()
    headers = {**WRITE_HEADERS, "Idempotency-Key": "idem-create-1"}

    first = client.post("/api/v1/priceops/plans", json=_plan_body("PLAN-CREATE"), headers=headers)
    second = client.post("/api/v1/priceops/plans", json=_plan_body("PLAN-CREATE"), headers=headers)

    assert first.json()["created"] is True
    assert first.json()["idempotent_replay"] is False
    assert second.json()["created"] is False
    assert second.json()["idempotent_replay"] is True


def test_mutation_without_an_idempotency_key_still_works() -> None:
    """The header stays optional; the 34 endpoints already accepting it keep
    their contract and the rest are not forced to send one."""
    client = _client()
    _seed_submittable_plan(client, "PLAN-NOKEY")
    response = client.post(
        "/api/v1/priceops/plans/PLAN-NOKEY/approve",
        json={"actor_id": "approver", "reason": "approved without a key", "decision": "APPROVE"},
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 200, response.text


# --- collection semantics ---


def test_list_plans_paginates_and_reports_a_total() -> None:
    client = _client()
    for index in range(5):
        _create_plan(client, f"PLAN-PAGE-{index}")

    page = client.get(
        "/api/v1/priceops/plans", params={"limit": 2, "offset": 0}, headers=WRITE_HEADERS
    ).json()

    assert page["count"] == 2, "count is this page's size"
    assert page["total"] == 5, "total is every matching row"
    assert page["has_more"] is True
    assert page["limit"] == 2 and page["offset"] == 0


def test_list_plans_last_page_reports_no_more() -> None:
    client = _client()
    for index in range(3):
        _create_plan(client, f"PLAN-LAST-{index}")

    page = client.get(
        "/api/v1/priceops/plans", params={"limit": 2, "offset": 2}, headers=WRITE_HEADERS
    ).json()

    assert page["count"] == 1
    assert page["has_more"] is False


def test_list_plans_filters_by_tenant() -> None:
    client = _client()
    _create_plan(client, "PLAN-TENANT-A")
    client.post(
        "/api/v1/priceops/plans",
        json=_plan_body("PLAN-TENANT-B", tenant_id="tenant-b"),
        headers=WRITE_HEADERS,
    )

    page = client.get(
        "/api/v1/priceops/plans", params={"tenant_id": "tenant-b"}, headers=WRITE_HEADERS
    ).json()

    assert page["total"] == 1
    assert {row["tenant_id"] for row in page["items"]} == {"tenant-b"}


def test_list_plans_default_response_stays_backward_compatible() -> None:
    """No query parameters must return what callers saw before this task."""
    client = _client()
    _create_plan(client, "PLAN-COMPAT")

    page = client.get("/api/v1/priceops/plans", headers=WRITE_HEADERS).json()

    assert page["items"] and page["count"] == len(page["items"])


# --- the pagination helper ---


def test_paginate_windows_and_counts() -> None:
    rows = [{"id": index} for index in range(10)]
    page = paginate(rows, PageParams(limit=3, offset=6))

    assert [row["id"] for row in page["items"]] == [6, 7, 8]
    assert (page["count"], page["total"], page["has_more"]) == (3, 10, True)


def test_paginate_offset_past_the_end_is_empty_not_an_error() -> None:
    page = paginate([{"id": 1}], PageParams(limit=10, offset=99))
    assert (page["items"], page["count"], page["has_more"]) == ([], 0, False)


def test_paginate_sorts_and_reverses() -> None:
    rows = [{"name": "c"}, {"name": "a"}, {"name": "b"}]
    ascending = paginate(rows, PageParams(sort="name"))
    descending = paginate(rows, PageParams(sort="name", order="desc"))

    assert [row["name"] for row in ascending["items"]] == ["a", "b", "c"]
    assert [row["name"] for row in descending["items"]] == ["c", "b", "a"]


def test_paginate_sort_tolerates_missing_and_mixed_values() -> None:
    """Rows come from many domains' to_dict(); comparing None to str raises
    TypeError and would 500 the endpoint. Missing sorts last."""
    rows = [{"k": "b"}, {}, {"k": None}, {"k": 2}]
    page = paginate(rows, PageParams(sort="k"))

    assert page["count"] == 4
    assert page["items"][-2:] == [{}, {"k": None}]


def test_page_params_clamps_hostile_bounds() -> None:
    """The negative limit that bypassed external_data's cap cannot recur."""
    assert page_params(limit=-5).limit == 1
    assert page_params(limit=10**6).limit == MAX_LIMIT
    assert page_params(offset=-10).offset == 0
    assert page_params(order="sideways").order == "asc"


def test_no_limit_requested_returns_every_row() -> None:
    """Adoption must not truncate an existing caller.

    Defaulting to a page size when the caller asked for none would silently cut
    a >100-row response the moment a router adopted the helper — a real
    regression dressed up as a feature.
    """
    rows = [{"id": index} for index in range(250)]
    page = paginate(rows, page_params())

    assert page["count"] == 250
    assert page["total"] == 250
    assert page["limit"] is None
    assert page["has_more"] is False


def test_paginate_sorts_numbers_numerically_not_lexicographically() -> None:
    """Stringifying would order 10 before 9."""
    rows = [{"n": 9}, {"n": 10}, {"n": 1}]
    page = paginate(rows, PageParams(sort="n"))
    assert [row["n"] for row in page["items"]] == [1, 9, 10]


def test_list_plans_without_a_limit_is_not_truncated_over_http() -> None:
    client = _client()
    for index in range(12):
        _create_plan(client, f"PLAN-FULL-{index}")

    page = client.get("/api/v1/priceops/plans", headers=WRITE_HEADERS).json()

    assert page["count"] == 12 == page["total"]
    assert page["has_more"] is False
