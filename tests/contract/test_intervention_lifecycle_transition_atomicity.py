"""Native SQLite transition CAS, cohort freshness and multi-participant rollback."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from modules.intervention.domain.lifecycle import (
    InterventionError,
    InterventionStatus,
)
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.repositories import DurableLabelRegistry
from tests.contract.test_intervention_approval_authority import (
    REVIEWER,
    STORE,
    app_for,
    create,
    headers,
    lifecycle_transport,
)
from tests.contract.test_intervention_approval_authority import (
    owned as inherited_owned,
)
from tests.contract.test_intervention_lifecycle_command_authority import (
    COMMANDS,
    invoke,
    ready,
    send,
    storage,
)

owned = inherited_owned


def test_immature_evaluate_one_delta_then_genuine_mature_retry_two(owned):
    _, client, workflow, _ = owned
    iid = ready(owned, "evaluate")
    original = workflow.get(iid)
    immature = invoke(client, iid, "evaluate", {"now": "2025-01-02T00:00:00+00:00"})
    assert immature["status"] == "EVALUATING"
    assert immature["version"] == original.version + 1
    assert immature["effect"]["observation_mature"] is False
    mature = invoke(client, iid, "evaluate")
    assert mature["status"] == "COMPLETED" and mature["version"] == original.version + 3
    assert mature["effect"]["observation_mature"] is True
    assert [h.action for h in workflow.get(iid).history[-3:]] == [
        "evaluate_effect",
        "evaluate_effect",
        "complete",
    ]


@pytest.mark.parametrize("cmd", ["execute", "evaluate", "close", "adjust"])
@pytest.mark.parametrize("fault", ["audit", "receipt", "relational"])
def test_all_atomic_participants_rollback_after_real_native_write(owned, monkeypatch, cmd, fault):
    engine, client, workflow, queue = owned
    iid = ready(owned, cmd)
    payload = {**COMMANDS[cmd], **({"follow_up": True} if cmd == "close" else {})}
    body, hdr = lifecycle_transport(client, iid, cmd, payload)
    before = storage(engine, workflow)
    if fault == "audit":
        original = workflow.audit_log.record

        def fail(event):
            if event.event_type == "intervention.lifecycle.v1":
                raise RuntimeError("owned native audit fault")
            return original(event)

        monkeypatch.setattr(workflow.audit_log, "record", fail)
    elif fault == "receipt":
        original = queue.update_status

        def fail(*args, **kw):
            if kw.get("payload", {}).get("command_scope") == f"interventions:{iid}:{cmd}":
                raise RuntimeError("owned final receipt fault")
            return original(*args, **kw)

        monkeypatch.setattr(queue, "update_status", fail)
    else:
        original = workflow.repository._sync_sql

        def fail(intervention, **kw):
            original(intervention, **kw)
            if intervention.intervention_id == iid:
                raise RuntimeError("owned relational fault after mirror")

        monkeypatch.setattr(workflow.repository, "_sync_sql", fail)
    result = send(client, iid, cmd, body, hdr)
    assert result.status_code == 503, result.text
    assert storage(engine, workflow) == before


@pytest.mark.parametrize("fault", ["label_write", "unbound_hook", "foreign_label_engine"])
def test_label_participant_fault_cannot_commit_case_receipt_or_audit(
    owned, monkeypatch, tmp_path, fault
):
    engine, client, workflow, _ = owned
    iid = ready(owned, "evaluate")
    body, hdr = lifecycle_transport(client, iid, "evaluate", COMMANDS["evaluate"])
    before = storage(engine, workflow)
    other = None
    if fault == "label_write":
        original = DurableLabelRegistry.__call__

        def fail(self, label):
            original(self, label)
            raise RuntimeError("owned native label fault after write")

        monkeypatch.setattr(DurableLabelRegistry, "__call__", fail)
    elif fault == "unbound_hook":
        workflow.register_label_hook(
            lambda label: (_ for _ in ()).throw(AssertionError("must refuse before effects"))
        )
    else:
        from shared.infrastructure.persistence.document_store import SqliteDocumentStore

        other = SqliteEngine(tmp_path / "other-label.sqlite")
        workflow.register_label_hook(DurableLabelRegistry(SqliteDocumentStore(other)))
    try:
        result = send(client, iid, "evaluate", body, hdr)
        assert result.status_code == 503, result.text
        assert storage(engine, workflow) == before
        if other is not None:
            assert other.query_one("SELECT COUNT(*) AS n FROM durable_documents")["n"] == 0
    finally:
        if other is not None:
            other.close()


@pytest.mark.parametrize("cmd", ["close", "adjust"])
def test_linked_creation_is_absent_and_pair_is_mutually_bound(owned, cmd):
    engine, client, workflow, _ = owned
    iid = ready(owned, cmd)
    invoke(client, iid, cmd, {"follow_up": True} if cmd == "close" else {})
    current = workflow.get(iid)
    child_id = current.close.follow_up_intervention_id if cmd == "close" else current.replacement_id
    child = workflow.get(child_id)
    assert child is not None and child.status == InterventionStatus.CANDIDATE and child.version == 1
    assert child.created_by == REVIEWER and child.store_id == current.store_id
    assert (
        child.predecessor_id == iid if cmd == "adjust" else child.trigger_ref == f"follow-up:{iid}"
    )
    before = storage(engine, workflow)
    with pytest.raises(InterventionError, match="already exists"):
        workflow.repository.create_if_absent(child)
    assert storage(engine, workflow) == before


@pytest.mark.parametrize("cmd", ["close", "adjust"])
def test_existing_child_collision_rolls_back_parent_and_all_effects(owned, monkeypatch, cmd):
    engine, client, workflow, _ = owned
    iid = ready(owned, cmd)
    existing = create(client)
    body, hdr = lifecycle_transport(
        client, iid, cmd, {**COMMANDS[cmd], **({"follow_up": True} if cmd == "close" else {})}
    )
    original = workflow.repository.create_if_absent

    def collision(child):
        return original(replace(child, intervention_id=existing))

    monkeypatch.setattr(workflow.repository, "create_if_absent", collision)
    before = storage(engine, workflow)
    result = send(client, iid, cmd, body, hdr)
    assert result.status_code == 409, result.text
    assert storage(engine, workflow) == before


def test_new_peer_after_cleared_check_requires_new_conflict_check(owned):
    engine, client, workflow, _ = owned
    iid = ready(owned, "submit")
    original_body, original_hdr = lifecycle_transport(client, iid, "submit", COMMANDS["submit"])
    peer = create(client)
    before = storage(engine, workflow)
    stale = send(client, iid, "submit", original_body, original_hdr)
    assert stale.status_code == 409, stale.text
    assert storage(engine, workflow) == before
    # A newly obtained submit context does not refresh the accepted old check.
    fresh_body, fresh_hdr = lifecycle_transport(client, iid, "submit", COMMANDS["submit"])
    fresh = send(client, iid, "submit", fresh_body, fresh_hdr)
    assert fresh.status_code == 409, fresh.text
    assert storage(engine, workflow) == before
    invoke(
        client, iid, "conflict-check", {"allow_overlap": True, "reason": "explicit native overlap"}
    )
    accepted = invoke(client, iid, "submit")
    assert accepted["status"] == "PENDING_APPROVAL"
    assert workflow.get(peer).status == InterventionStatus.CANDIDATE


def test_finite_cohort_bound_refuses_before_any_peer_decode(owned, monkeypatch):
    _, client, workflow, _ = owned
    iid = ready(owned, "conflict-check")
    monkeypatch.setattr(
        workflow.repository,
        "list_scope_ids_by_store",
        lambda store: tuple(str(n) for n in range(513)),
    )
    monkeypatch.setattr(
        workflow.repository,
        "_read_scoped_aggregate",
        lambda *args: (_ for _ in ()).throw(AssertionError("no overbound decode")),
    )
    result = client.get(
        f"/api/v1/interventions/{iid}/command-context",
        headers=headers(),
        params={"command": "conflict-check"},
    )
    assert result.status_code == 503, result.text


def test_two_independent_owned_sqlite_writers_have_one_fresh_transition_winner(owned):
    engine, client, workflow, _ = owned
    iid = ready(owned, "assign")
    other = SqliteEngine(engine.path)
    second, second_workflow, _ = app_for(other)
    try:
        one = lifecycle_transport(client, iid, "assign", COMMANDS["assign"], key="native-life-one")
        two = lifecycle_transport(second, iid, "assign", COMMANDS["assign"], key="native-life-two")
        barrier = Barrier(2)

        def write(args):
            target, tuple_ = args
            barrier.wait(timeout=5)
            return send(target, iid, "assign", *tuple_)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, [(client, one), (second, two)]))
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        assert workflow.get(iid).version == one[0]["expected_version"] + 1
        assert second_workflow.get(iid).assigned_to == REVIEWER
        assert (
            len(
                [
                    e
                    for e in workflow.audit_log.list_events()
                    if e.event_type == "intervention.lifecycle.v1" and e.action == "assign"
                ]
            )
            == 1
        )
    finally:
        second.close()
        other.close()


@pytest.mark.parametrize(
    "corruption",
    ["equal_version_body", "history_prefix", "next_float", "history_actor", "history_cid"],
)
def test_actual_locked_raw_snapshot_and_history_fence_rejects_corruption(
    owned, monkeypatch, corruption
):
    engine, client, workflow, _ = owned
    iid = ready(owned, "assign")
    body, hdr = lifecycle_transport(client, iid, "assign", COMMANDS["assign"])
    before = storage(engine, workflow)
    original = workflow.repository.save_transition

    def corrupt(updated, *, transition):
        if corruption == "equal_version_body":
            workflow.repository._store.put(
                workflow.repository._C,
                iid,
                replace(transition.original, expected_outcome="concurrent equal version"),
                group_key=STORE,
            )
        elif corruption == "history_prefix":
            updated = (
                replace(
                    updated,
                    history=(
                        replace(updated.history[0], reason="bad prefix"),
                        *updated.history[1:],
                    ),
                )
                if len(updated.history) > 1
                else replace(updated, history=())
            )
        elif corruption == "next_float":
            updated = replace(updated, version=float(updated.version))
        elif corruption == "history_actor":
            updated = replace(
                updated,
                history=(*updated.history[:-1], replace(updated.history[-1], actor="forged")),
            )
        else:
            updated = replace(
                updated,
                history=(
                    *updated.history[:-1],
                    replace(updated.history[-1], correlation_id="forged"),
                ),
            )
        return original(updated, transition=transition)

    monkeypatch.setattr(workflow.repository, "save_transition", corrupt)
    response = send(client, iid, "assign", body, hdr)
    assert response.status_code == 409, response.text
    assert storage(engine, workflow) == before


@pytest.mark.parametrize("fault", ["outbox", "receipt"])
def test_worm_intent_and_receipt_fault_roll_back_native_effects_without_inline_delivery(
    owned, tmp_path, monkeypatch, fault
):
    engine, _, _, _ = owned
    sink = LocalAppendOnlyWormSink(tmp_path / "owned-lifecycle-worm")
    client, workflow, queue = app_for(engine, sink=sink)
    try:
        candidate = (engine, client, workflow, queue)
        iid = ready(candidate, "evaluate")
        body, hdr = lifecycle_transport(client, iid, "evaluate", COMMANDS["evaluate"])
        before = storage(engine, workflow)
        if fault == "outbox":
            original = DurableAuditDelivery.write_audit_event

            def fail(self, event):
                result = original(self, event)
                if event.event_type == "intervention.lifecycle.v1":
                    raise RuntimeError("owned outbox fault after intent")
                return result

            monkeypatch.setattr(DurableAuditDelivery, "write_audit_event", fail)
        else:
            original = queue.update_status

            def fail(*args, **kwargs):
                if (
                    kwargs.get("payload", {}).get("command_scope")
                    == f"interventions:{iid}:evaluate"
                ):
                    raise RuntimeError("owned final receipt fault after outbox")
                return original(*args, **kwargs)

            monkeypatch.setattr(queue, "update_status", fail)
        response = send(client, iid, "evaluate", body, hdr)
        assert response.status_code == 503, response.text
        assert storage(engine, workflow) == before
        assert not any(
            '"event_type":"intervention.lifecycle.v1"' in p.read_text()
            and '"action":"evaluate_effect"' in p.read_text()
            for p in (tmp_path / "owned-lifecycle-worm").rglob("*.json")
        )
    finally:
        client.close()
