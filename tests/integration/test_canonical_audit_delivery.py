"""Actual PostgreSQL commands, worker delivery and minimal receipt-column grants."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import pytest
from psycopg import errors, sql

from apps.worker.oday_worker.main import ODayWorker
from shared.audit.canonical_delivery import JOB_TYPE
from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog, canonical_audit_payload
from shared.audit.integrity import sha256_hex
from shared.audit.worm import GcsWormEvidenceSink, LocalAppendOnlyWormSink
from tests.integration import test_canonical_intake_audit as physical
from tests.integration import test_intake_canonical_audit_wiring as source
from tests.integration._gcs_worm_stub import GcsWormProtocolStub

pytestmark = pytest.mark.requires_live_env
canonical_audit = physical.canonical_audit


@pytest.fixture
def case(intake_blank_db, monkeypatch, tmp_path):
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "synthetic-canonical-worm").as_uri())
    yield from source.case.__wrapped__(intake_blank_db, monkeypatch)


def _rows(bundle):
    return CanonicalPostgresAuditLog(bundle.engine).list_events(source.TENANT)


def _jobs(bundle):
    return bundle.engine.query("SELECT * FROM durable_jobs WHERE job_type = ? ORDER BY created_at", (JOB_TYPE,))


def _drain(bundle):
    worker = ODayWorker(persistence=bundle, worker_id="synthetic-canonical-audit-worker")
    for _ in range(12):
        if not worker.run_once():
            return
    pytest.fail("The synthetic audit queue did not settle")


def test_committed_canonical_jobs_survive_restart_and_actual_worker_attaches_verified_objects(case):
    db, bundle, monkeypatch = case
    assert source._submit(bundle).status_code == 202
    before = source._snapshot(bundle)
    rows = _rows(bundle)
    assert len(rows) == len(_jobs(bundle)) == 3
    assert all(row["worm_object_uri"] is None for row in rows)
    bundle.engine.close()
    restarted = source._bundle(db, monkeypatch)
    try:
        _drain(restarted)
        after = _rows(restarted)
        assert all(row["worm_object_uri"] for row in after)
        assert all(job["status"] == "succeeded" for job in _jobs(restarted))
        for old, row in zip(rows, after, strict=True):
            assert canonical_audit_payload(row) == canonical_audit_payload(old)
            path = Path(unquote(urlsplit(row["worm_object_uri"]).path))
            import json
            envelope = json.loads(path.read_text())
            assert envelope["record_id"] == row["audit_event_id"]
            assert envelope["payload"] == canonical_audit_payload(row)
            assert envelope["checksum"] == row["event_sha256"] == sha256_hex(envelope["payload"])
        assert CanonicalPostgresAuditLog(restarted.engine).verify_chain(source.TENANT).ok
        assert source._snapshot(restarted) == before
    finally:
        restarted.engine.close()


def test_queue_failure_rolls_back_canonical_audit_business_outbox_and_replay(case):
    _, bundle, monkeypatch = case
    real_enqueue = bundle.job_queue.enqueue

    def fail(request, *, correlation_id):
        if request.job_type == JOB_TYPE:
            raise RuntimeError("synthetic canonical queue failure")
        return real_enqueue(request, correlation_id=correlation_id)

    with monkeypatch.context() as patch:
        patch.setattr(bundle.job_queue, "enqueue", fail)
        response = source._submit(bundle, key="synthetic-queue-rollback")
    assert response.status_code == 503, response.text
    assert all(not value for value in source._snapshot(bundle).values())
    assert _rows(bundle) == [] and _jobs(bundle) == []
    assert source._submit(bundle, key="synthetic-queue-rollback").status_code == 202


def test_external_outage_keeps_canonical_uri_pending_and_does_not_repeat_business(case):
    _, bundle, monkeypatch = case
    assert source._submit(bundle).status_code == 202
    before = source._snapshot(bundle)
    target = bundle.audit_log._worm_sink

    def unavailable(event):
        raise RuntimeError("synthetic canonical sink outage")

    worker = ODayWorker(persistence=bundle, worker_id="synthetic-canonical-outage")
    with monkeypatch.context() as patch:
        patch.setattr(target, "write_audit_event", unavailable)
        assert worker.run_once() is True
    assert _jobs(bundle)[0]["status"] == "queued"
    assert all(row["worm_object_uri"] is None for row in _rows(bundle))
    assert source._snapshot(bundle) == before
    _drain(bundle)
    assert all(row["worm_object_uri"] for row in _rows(bundle))
    assert source._snapshot(bundle) == before


def test_receipt_save_interruption_recovers_existing_object_without_overwrite(case):
    _, bundle, monkeypatch = case
    assert source._submit(bundle).status_code == 202
    writer = CanonicalPostgresAuditLog
    before = source._snapshot(bundle)

    def interrupted(*args, **kwargs):
        raise RuntimeError("synthetic receipt save interruption")

    worker = ODayWorker(persistence=bundle, worker_id="synthetic-canonical-interrupted")
    with monkeypatch.context() as patch:
        patch.setattr(writer, "attach_worm_receipt", interrupted)
        assert worker.run_once() is True
    sink_root = Path(unquote(urlsplit(bundle.audit_log._worm_sink.sink_id).path))
    objects = {p: p.read_bytes() for p in sink_root.rglob("*.json")}
    assert len(objects) == 1
    assert all(row["worm_object_uri"] is None for row in _rows(bundle))
    _drain(bundle)
    assert all(p.read_bytes() == data for p, data in objects.items())
    assert len(list(sink_root.rglob("*.json"))) == 3
    assert source._snapshot(bundle) == before


@pytest.mark.parametrize("tamper", ["sink", "hash", "tenant", "missing-record", "receipt"])
def test_delivery_cannot_attach_a_wrong_target_hash_or_receipt(case, tamper):
    _, bundle, monkeypatch = case
    assert source._submit(bundle).status_code == 202
    before = source._snapshot(bundle)
    target = bundle.audit_log._worm_sink
    writes = []
    original_write = target.write_audit_event

    def write(event):
        writes.append(event.event_id)
        receipt = original_write(event)
        return replace(receipt, object_uri="gs://unapproved-bucket/forged") if tamper == "receipt" else receipt

    monkeypatch.setattr(target, "write_audit_event", write)
    if tamper != "receipt":
        row = _jobs(bundle)[0]
        job = bundle.job_queue.get(row["job_id"])
        payload = deepcopy(job.payload)
        key, value = {
            "sink": ("sink_id", "gs://other-target"),
            "hash": ("event_sha256", "0" * 64),
            "tenant": ("tenant_id", str(uuid4())),
            "missing-record": ("audit_event_id", str(uuid4())),
        }[tamper]
        payload[key] = value
        import json
        bundle.engine.execute("UPDATE durable_jobs SET payload_json = ? WHERE job_id = ?", (json.dumps(payload), job.job_id))
    assert ODayWorker(persistence=bundle, worker_id="synthetic-canonical-denial").run_once() is True
    assert all(row["worm_object_uri"] is None for row in _rows(bundle))
    if tamper != "receipt":
        assert writes == []
    assert source._snapshot(bundle) == before


def test_gcs_response_loss_recovers_original_canonical_payload_after_database_restart(case):
    db, bundle, monkeypatch = case
    server = GcsWormProtocolStub()

    def configured_sink(*args, **kwargs):
        target = GcsWormEvidenceSink("gs://synthetic-bucket/canonical", token=server.writer_token, recovery_token=server.recovery_token)
        server.bind(target, monkeypatch)
        return target

    target = configured_sink()
    monkeypatch.setattr(bundle.audit_log, "_worm_sink", target)
    monkeypatch.setattr(bundle.assisted_intake_store._canonical_audit_sink.delivery, "sink", target)
    assert source._submit(bundle).status_code == 202
    before = source._snapshot(bundle)
    rows = _rows(bundle)
    server.drop_after_create_event_type = rows[0]["event_type"]
    bundle.engine.close()
    from shared.infrastructure.persistence import factory
    monkeypatch.setattr(factory, "build_audit_worm_sink_from_env", configured_sink)
    restarted = source._bundle(db, monkeypatch)
    try:
        worker = ODayWorker(persistence=restarted, worker_id="synthetic-canonical-gcs-worker")
        assert worker.run_once() is True
        assert _rows(restarted)[0]["worm_object_uri"] is None
        first_objects = deepcopy(server.objects)
        assert len(first_objects) == 1
        _drain(restarted)
        assert len(server.objects) == 3
        assert all(server.objects[name] == value for name, value in first_objects.items())
        assert all(row["worm_object_uri"].startswith("gs://synthetic-bucket/canonical/") for row in _rows(restarted))
        assert CanonicalPostgresAuditLog(restarted.engine).verify_chain(source.TENANT).ok
        assert source._snapshot(restarted) == before
    finally:
        restarted.engine.close()
        server.close()


def test_delivery_column_grant_keeps_immutable_audit_columns_and_delete_forbidden(canonical_audit, tmp_path):
    writer, db, url = canonical_audit
    row = writer.append(physical._fact())
    target = LocalAppendOnlyWormSink(tmp_path / "synthetic-column-grant")
    event = SimpleNamespace(event_id=row["audit_event_id"], sequence=row["sequence_no"], to_dict=lambda: canonical_audit_payload(row))
    receipt = target.write_audit_event(event)
    args = dict(expected_event_sha256=row["event_sha256"], sink_id=target.sink_id, receipt=receipt)
    with pytest.raises(errors.InsufficientPrivilege):
        writer.attach_worm_receipt(physical.TENANT_A, row["audit_event_id"], **args)
    role = urlsplit(url).username
    with db.connect() as conn:
        conn.execute(sql.SQL("GRANT UPDATE (worm_object_uri) ON audit.audit_events TO {}").format(sql.Identifier(role)))
    delivered = writer.attach_worm_receipt(physical.TENANT_A, row["audit_event_id"], **args)
    assert delivered["worm_object_uri"] == receipt.object_uri
    assert canonical_audit_payload(delivered) == canonical_audit_payload(row)
    for statement in ("UPDATE audit.audit_events SET event_type='forged'", "DELETE FROM audit.audit_events"):
        with pytest.raises(errors.InsufficientPrivilege):
            with writer.engine.transaction():
                writer.engine.query_one("SELECT set_config('app.tenant_id', ?, true)", (physical.TENANT_A,))
                writer.engine.execute(statement)
    assert writer.verify_chain(physical.TENANT_A).ok
