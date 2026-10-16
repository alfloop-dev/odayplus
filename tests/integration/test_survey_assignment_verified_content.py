"""Real native/SQL/signed HTTP with explicit owned object-storage doubles.

These controls do not access Google Cloud, verify workload IAM, complete field
work or promote observations. The fake transport returns only declared test
bytes; production constructors still require the real GCS/ADC path.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import pickle
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from modules.market_survey.application.assignment_content import (
    SurveyGcsObjectStore,
    SurveyWorkloadCredentials,
    VerifiedSurveyObjectContent,
)
from modules.market_survey.infrastructure.assignment_repository import SurveyOwnerUnavailable
from modules.opsboard.auth import encode_compact_jwt
from shared.auth import Role, Scope
from shared.identity.store import Account
from shared.infrastructure.object_store.client import InMemoryObjectStore, ResidencyDeniedError
from shared.infrastructure.persistence.factory import _durable_bundle
from tests.integration.test_canonical_survey_assignments import (
    ACTOR,
    ASSIGNEE,
    BASE,
    CID,
    TENANT,
    _business,
    _payload,
)
from tests.integration.test_canonical_survey_assignments import (
    context as context,
)

BYTES = b"\xff\xd8\xff\xe0owned-synthetic-photo-bytes\xff\xd9"
BUCKET = "taiwan-snapshots"


class OwnedFileObjectStore(InMemoryObjectStore):
    """Test-only persisted actual bytes/generation; never a production provider."""
    def __init__(self, path):
        super().__init__()
        self.path = path
        if path.exists():
            self._objects = pickle.loads(path.read_bytes())  # noqa: S301 - owned fixture only

    def upload_object(self, *args, **kwargs):
        result = super().upload_object(*args, **kwargs)
        self.path.write_bytes(pickle.dumps(self._objects))
        return result

    def corrupt_bytes(self):
        row = next(iter(next(iter(self._objects.values())).values()))
        row["data"] = b"deliberate-test-corruption"
        self.path.write_bytes(pickle.dumps(self._objects))


@pytest.fixture
def media(context, tmp_path, monkeypatch):
    # Explicit local engineering injection, not a fallback from production.
    monkeypatch.setenv("ODP_ENV", "local")
    monkeypatch.delenv("ODAY_ENV", raising=False)
    store = OwnedFileObjectStore(tmp_path / "owned-object-fixture.pickle")
    context.factory.object_store, context.factory.content_bucket = store, BUCKET
    response = context.client.post(BASE + "/assignments", json=_payload(),
        headers={**context.headers(), "Idempotency-Key": "verified-content-assignment"})
    assert response.status_code == 201, response.text
    return SimpleNamespace(context=context, store=store, identity=response.json()["assignment"]["assignment_id"])


def _upload_payload(**changes):
    return {"expectedRevision": 1, "dataBase64": base64.b64encode(BYTES).decode(),
        "contentType": "image/jpeg", "mediaKind": "PHOTO", "capturedAt": "2026-10-15T08:00:00+00:00",
        "caption": "Explicit synthetic attachment", "reason": "Attach real bytes for the selected native assignment", **changes}


def _upload(media, *, payload=None, key="one-attachment", subject=ASSIGNEE):
    return media.context.client.post(f"{BASE}/assignments/{media.identity}/attachments", json=payload or _upload_payload(),
        headers={**media.context.headers(subject), "Idempotency-Key": key})


def _submit_payload(**changes):
    return {"expectedRevision": 1, "location": {"latitude": 25.03, "longitude": 121.56, "srid": 4326},
        "surveyedAt": "2026-10-15T08:00:00+00:00", "attributes": {"siteObservation": "Explicit engineering field input"},
        "reason": "Submit the selected assignment as pending field evidence", **changes}


def _submit(media, payload, key="one-submission", subject=ASSIGNEE):
    return media.context.client.post(f"{BASE}/assignments/{media.identity}/submit", json=payload,
        headers={**media.context.headers(subject), "Idempotency-Key": key})


def test_signed_http_actual_bytes_head_generation_download_and_native_submission_are_one_true_chain(media):
    context = media.context
    upload = _upload(media)
    assert upload.status_code == 201, upload.text
    attachment = upload.json()["attachment"]
    assert attachment["sha256"] == hashlib.sha256(BYTES).hexdigest()
    assert attachment["sizeBytes"] == len(BYTES) and attachment["uploadedBy"] == ASSIGNEE
    assert "storage_uri" not in attachment and "object_uri" not in attachment
    response = context.client.get(f"{BASE}/assignments/{media.identity}/attachments/{attachment['mediaId']}/content", headers=context.headers())
    assert response.status_code == 200 and response.content == BYTES
    assert response.headers["x-content-sha256"] == hashlib.sha256(response.content).hexdigest()
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith('attachment; filename="survey-media-')
    assert response.headers["cache-control"] == "private, no-store"
    payload = _submit_payload(mediaIds=[attachment["mediaId"]])
    response = _submit(media, payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["assignment"]["status"] == "SUBMITTED" and result["assignment"]["revision"] == 2
    assert result["surveyId"] and result["observationId"] and result["auditEventIds"]
    read = context.client.get(f"{BASE}/assignments/{media.identity}/evidence", headers=context.headers())
    assert read.status_code == 200, read.text
    evidence = read.json()
    assert evidence["survey_id"] == result["surveyId"] and evidence["observation_id"] == result["observationId"]
    assert evidence["submitter_id"] == ASSIGNEE and evidence["assignment_id"] == media.identity
    assert evidence["review_status"] == "PENDING_REVIEW" and evidence["promotion_status"] == "NOT_PROMOTED"
    assert evidence["confidence"] is None  # no default measured 100% confidence
    assert evidence["media_attachments"] == [attachment]
    before = _business(context.bundle.engine)
    replay = _submit(media, payload)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["surveyId"] == result["surveyId"]
    assert _business(context.bundle.engine) == before
    conflict = _submit(media, {**payload, "reason": "Different input under the same submission key"})
    assert conflict.status_code == 409 and _business(context.bundle.engine) == before


def test_zero_photos_is_not_an_invented_mandatory_gate_but_the_native_surveyor_and_expiry_still_are(media):
    before = _business(media.context.bundle.engine)
    denied = _submit(media, _submit_payload(), subject=ACTOR)
    assert denied.status_code == 403 and _business(media.context.bundle.engine) == before
    response = _submit(media, _submit_payload(mediaIds=[]))
    assert response.status_code == 200, response.text
    evidence = media.context.bundle.survey_assignment_repository_for_tenant(TENANT).get_evidence(response.json()["surveyId"])
    assert evidence.media_attachments == [] and evidence.confidence is None
    assert evidence.review_status.value == "PENDING_REVIEW"


def test_same_upload_key_replays_exact_bytes_without_second_metadata_audit_or_generation(media):
    payload = _upload_payload()
    first = _upload(media, payload=payload)
    assert first.status_code == 201, first.text
    before = _business(media.context.bundle.engine)
    second = _upload(media, payload=payload)
    assert second.status_code == 201 and second.json()["replayed"] is True
    assert second.json()["attachment"] == first.json()["attachment"]
    assert _business(media.context.bundle.engine) == before
    assert len(media.store.list_objects(TENANT, BUCKET)) == 1
    changed = _upload(media, payload={**payload, "dataBase64": base64.b64encode(b"different").decode()})
    assert changed.status_code == 409 and _business(media.context.bundle.engine) == before
    uri = "gs://" + BUCKET + "/" + media.store.list_objects(TENANT, BUCKET)[0]
    assert media.store.head_object(TENANT, uri)["generation"] == 1
    assert media.store.download_object(TENANT, uri, generation=1) == BYTES


def test_lost_upload_response_is_not_committed_and_exact_retry_recovers_owned_immutable_bytes(media, monkeypatch):
    actual = media.store.upload_object
    attempts = 0
    def lost_reply(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        value = actual(*args, **kwargs)
        if attempts == 1:
            raise OSError("Explicit owned storage lost reply")
        return value
    monkeypatch.setattr(media.store, "upload_object", lost_reply)
    payload = _upload_payload()
    before = _business(media.context.bundle.engine)
    first = _upload(media, payload=payload)
    assert first.status_code == 503 and _business(media.context.bundle.engine) == before
    assert len(media.store.list_objects(TENANT, BUCKET)) == 1
    retry = _upload(media, payload=payload)
    assert retry.status_code == 201, retry.text
    assert len(media.store.list_objects(TENANT, BUCKET)) == 1


def test_sql_audit_failure_leaves_no_metadata_receipt_or_native_evidence_and_same_key_recovers_object(media, monkeypatch):
    audit = media.context.bundle.audit_log
    original = audit.record
    def fail(event):
        if event.event_type == "market_survey.attachment_uploaded.v1":
            raise RuntimeError("Owned transactional audit failure")
        return original(event)
    monkeypatch.setattr(audit, "record", fail)
    payload = _upload_payload()
    before = _business(media.context.bundle.engine)
    response = _upload(media, payload=payload)
    assert response.status_code == 503 and _business(media.context.bundle.engine) == before
    assert len(media.store.list_objects(TENANT, BUCKET)) == 1  # external write was not a SQL rollback
    monkeypatch.setattr(audit, "record", original)
    retry = _upload(media, payload=payload)
    assert retry.status_code == 201, retry.text
    assert len(media.store.list_objects(TENANT, BUCKET)) == 1
    owner = media.context.bundle.survey_assignment_repository_for_tenant(TENANT)
    assert len(owner.list_media(media.identity)) == 1


def test_native_submit_audit_failure_rolls_back_assignment_evidence_and_receipt(media, monkeypatch):
    attachment = _upload(media).json()["attachment"]
    original = media.context.bundle.audit_log.record
    def fail(event):
        if event.event_type == "market_survey.survey_submitted.v1":
            raise RuntimeError("Owned submit audit failure")
        return original(event)
    monkeypatch.setattr(media.context.bundle.audit_log, "record", fail)
    before = _business(media.context.bundle.engine)
    response = _submit(media, _submit_payload(mediaIds=[attachment["mediaId"]]))
    assert response.status_code == 503 and _business(media.context.bundle.engine) == before
    owner = media.context.bundle.survey_assignment_repository_for_tenant(TENANT)
    assert owner.get_assignment(media.identity).status.value == "ASSIGNED"
    assert owner.get_assignment(media.identity).survey_id is None


@pytest.mark.parametrize("corruption", ["head", "bytes", "generation"])
def test_no_attachment_is_committed_until_real_head_generation_and_bytes_match(media, monkeypatch, corruption):
    head, download = media.store.head_object, media.store.download_object
    if corruption == "bytes":
        monkeypatch.setattr(media.store, "download_object", lambda *a, **k: b"wrong bytes")
    else:
        def altered(*args, **kwargs):
            value = head(*args, **kwargs)
            return {**value, "sha256": "0" * 64} if corruption == "head" else {**value, "generation": value["generation"] + 1}
        monkeypatch.setattr(media.store, "head_object", altered)
    before = _business(media.context.bundle.engine)
    response = _upload(media)
    assert response.status_code == 503 and _business(media.context.bundle.engine) == before
    monkeypatch.setattr(media.store, "download_object", download)


def test_current_authorized_corruption_is_failclosed_and_not_a_successful_download_audit(media):
    attachment = _upload(media).json()["attachment"]
    media.store.corrupt_bytes()
    before = _business(media.context.bundle.engine)
    response = media.context.client.get(f"{BASE}/assignments/{media.identity}/attachments/{attachment['mediaId']}/content", headers=media.context.headers())
    assert response.status_code == 503 and _business(media.context.bundle.engine) == before


def test_unknown_and_foreign_assignment_share_404_without_any_object_storage_access(media, monkeypatch):
    attachment = _upload(media).json()["attachment"]
    foreign_tenant, foreign_subject = str(uuid4()), str(uuid4())
    context = media.context
    context.bundle.identity_store.save_account(Account(account_id=UUID(foreign_subject), tenant_id=UUID(foreign_tenant),
        username=foreign_subject, email=foreign_subject + "@example.invalid"))
    context.bundle.identity_store.set_account_roles(foreign_subject, [Role.EXPANSION_USER])
    context.bundle.identity_store.set_account_scope(foreign_subject, Scope(tenant_id=foreign_tenant,
        modules=context.principal.scope.modules, clearance=context.principal.scope.clearance))
    session = context.bundle.session_service.create_session(account_id=UUID(foreign_subject), provider="local_password")
    boundary = context.client.app.state.survey_test_boundary
    key = boundary._config.local_signing_keys["owned-survey"]
    now = datetime.now(UTC)
    token = encode_compact_jwt({"sub": foreign_subject, "sid": str(session.session_id), "tenant_id": foreign_tenant,
        "iss": "urn:odp:identity:local", "aud": "owned-survey", "iat": now.timestamp(),
        "exp": (now + timedelta(minutes=10)).timestamp()}, key)
    def forbidden_io(*args, **kwargs):
        pytest.fail("A non-admitted assignment must not access object storage")
    monkeypatch.setattr(media.store, "head_object", forbidden_io)
    monkeypatch.setattr(media.store, "download_object", forbidden_io)
    before = _business(context.bundle.engine)
    unknown = context.client.get(f"{BASE}/assignments/unknown/attachments/{attachment['mediaId']}/content", headers=context.headers())
    foreign = context.client.get(f"{BASE}/assignments/{media.identity}/attachments/{attachment['mediaId']}/content",
        headers={"Authorization": "Bearer " + token, "x-correlation-id": CID})
    assert unknown.status_code == foreign.status_code == 404
    assert unknown.json() == foreign.json() == {"detail": "SURVEY_ASSIGNMENT_NOT_FOUND"}
    assert _business(context.bundle.engine) == before


def test_actual_parent_revocation_denies_media_and_submission_before_storage(media, monkeypatch):
    attachment = _upload(media).json()["attachment"]
    metadata = media.context.metadata.get_listing_metadata("listing-owned")
    media.context.metadata.save_listing_metadata("listing-owned", {**metadata, "storeId": "other-store"})
    scope = replace(media.context.principal.scope, store_ids=frozenset({"store-owned"}))
    media.context.bundle.identity_store.set_account_scope(ASSIGNEE, scope)
    def forbidden_io(*args, **kwargs):
        pytest.fail("Revoked current parent must not access content")
    monkeypatch.setattr(media.store, "head_object", forbidden_io)
    before = _business(media.context.bundle.engine)
    response = media.context.client.get(f"{BASE}/assignments/{media.identity}/attachments/{attachment['mediaId']}/content", headers=media.context.headers(ASSIGNEE))
    assert response.status_code == 404
    assert _submit(media, _submit_payload(mediaIds=[attachment["mediaId"]])).status_code == 404
    assert _business(media.context.bundle.engine) == before


def test_own_sql_metadata_and_owned_fixture_bytes_survive_reopening_without_model_artifact_namespace(media):
    attachment = _upload(media).json()["attachment"]
    reopened = _durable_bundle(media.context.path)
    try:
        record = reopened.survey_assignment_repository_for_tenant(TENANT).get_media(attachment["mediaId"], media.identity)
        storage = VerifiedSurveyObjectContent(OwnedFileObjectStore(media.store.path), BUCKET)
        assert record.assignment_id == media.identity and storage.read_verified(record) == BYTES
        assert "model_registry" not in record.object_uri
    finally:
        reopened.engine.close()


@pytest.mark.parametrize("claim,value", [("storage_uri", "gs://caller/claim"), ("sha256", "0" * 64),
                                          ("actor_id", ACTOR), ("tenant_id", TENANT)])
def test_upload_http_never_accepts_caller_identity_or_storage_claim_authority(media, claim, value):
    before = _business(media.context.bundle.engine)
    response = _upload(media, payload=_upload_payload(**{claim: value}))
    assert response.status_code == 422 and _business(media.context.bundle.engine) == before
    assert media.store.list_objects(TENANT, BUCKET) == []


def test_production_without_actual_bucket_or_with_memory_provider_is_unavailable_not_uploaded(media, monkeypatch):
    monkeypatch.setenv("ODP_ENV", "production")
    before = _business(media.context.bundle.engine)
    assert _upload(media).status_code == 503
    assert _business(media.context.bundle.engine) == before and media.store.list_objects(TENANT, BUCKET) == []
    monkeypatch.delenv("ODP_SOURCE_SNAPSHOT_BUCKET", raising=False)
    with pytest.raises(SurveyOwnerUnavailable, match="SURVEY_CONTENT_BUCKET_UNAVAILABLE"):
        VerifiedSurveyObjectContent.configured(credentials=SurveyWorkloadCredentials(credentials=SimpleNamespace(valid=False, token=None)))


def test_workload_adc_refresh_and_expiry_never_borrow_an_audit_writer_token(monkeypatch):
    class Credentials:
        valid = False
        token = None
        def refresh(self, request):
            request("https://oauth2.googleapis.com/token", method="POST")
            self.valid, self.token = True, "synthetic-workload-access-token"
    calls = []
    credentials = Credentials()
    provider = SurveyWorkloadCredentials(credentials=credentials, refresh_request=lambda *a, **k: calls.append((a, k)))
    monkeypatch.setenv("ODP_AUDIT_WORM_GCS_TOKEN", "synthetic-writer-only-token")
    store = SurveyGcsObjectStore(provider)
    assert store._get_token() == "synthetic-workload-access-token" and len(calls) == 1
    assert store._get_token() == "synthetic-workload-access-token" and len(calls) == 1
    credentials.valid = False
    assert store._get_token() == "synthetic-workload-access-token" and len(calls) == 2
    def failed(*args, **kwargs):
        raise RuntimeError("Explicit mocked refresh failure")
    credentials.valid = False
    credentials.refresh = failed
    with pytest.raises(SurveyOwnerUnavailable, match="SURVEY_WORKLOAD_CREDENTIAL_UNAVAILABLE"):
        store._get_token()


def test_gcs_adapter_real_multipart_metadata_and_generation_pinned_read_use_refreshed_token_before_mock_transport():
    digest = hashlib.sha256(BYTES).hexdigest()
    key = f"tenants/{TENANT}/market-survey/assignments/asgn-test/attachments/media-test"
    requests = []
    class Response(io.BytesIO):
        pass
    class Transport:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 10.0 and request.get_header("Authorization") == "Bearer synthetic-current-token"
            if request.method == "POST":
                assert "uploadType=multipart&ifGenerationMatch=0" in request.full_url
                assert BYTES in request.data and digest.encode() in request.data and key.encode() in request.data
                assert request.get_header("Content-type").startswith("multipart/related; boundary=")
                return Response(json.dumps({"generation": "7"}).encode())
            if "alt=media&generation=7" in request.full_url:
                return Response(BYTES)
            return Response(json.dumps({"generation": "7", "size": str(len(BYTES)),
                "metadata": {"sha256": digest}, "contentType": "image/jpeg"}).encode())
    provider = SurveyWorkloadCredentials(credentials=SimpleNamespace(valid=True, token="synthetic-current-token"))
    store = SurveyGcsObjectStore(provider, opener=Transport())
    owner = VerifiedSurveyObjectContent(store, BUCKET)
    uri, generation = owner.put_verified(tenant=TENANT, key=key, data=BYTES, content_type="image/jpeg")
    assert uri == f"gs://{BUCKET}/{key}" and generation == 7
    assert len(requests) == 4  # upload, verification HEAD, pinned-download HEAD, bytes
    before = len(requests)
    with pytest.raises(ResidencyDeniedError):
        store.download_object("foreign", uri, generation=7)
    assert len(requests) == before


@pytest.mark.parametrize("mismatch", ["other-assignment", "unknown", "other-surveyor"])
def test_submission_attachment_and_actual_surveyor_join_cannot_be_replaced_by_an_id_claim(media, mismatch):
    attachment = _upload(media).json()["attachment"]
    context = media.context
    selected = media.identity
    subject = ASSIGNEE
    media_id = attachment["mediaId"]
    if mismatch == "other-assignment":
        created = context.client.post(BASE + "/assignments", json=_payload(),
            headers={**context.headers(), "Idempotency-Key": "different-actual-assignment"})
        assert created.status_code == 201
        media.identity = created.json()["assignment"]["assignment_id"]
    elif mismatch == "unknown":
        media_id = "unknown-media-id"
    else:
        subject = ACTOR
    before = _business(context.bundle.engine)
    response = _submit(media, _submit_payload(mediaIds=[media_id]), subject=subject)
    assert response.status_code == (403 if mismatch == "other-surveyor" else 404)
    assert _business(context.bundle.engine) == before
    assert context.bundle.survey_assignment_repository_for_tenant(TENANT).get_assignment(selected).status.value == "ASSIGNED"


@pytest.mark.parametrize("bad", ["not-base64", ""])
def test_invalid_uploaded_bytes_do_not_create_uri_claims_metadata_or_objects(media, bad):
    before = _business(media.context.bundle.engine)
    response = _upload(media, payload=_upload_payload(dataBase64=bad))
    assert response.status_code == 422 and _business(media.context.bundle.engine) == before
    assert media.store.list_objects(TENANT, BUCKET) == []


def test_default_adc_resolver_uses_real_google_credentials_refresh_api_with_mocked_http_only(monkeypatch):
    import google.auth
    from google.oauth2.credentials import Credentials

    credentials = Credentials(token=None, refresh_token="owned-fixture-refresh", token_uri="https://oauth2.googleapis.com/token",
        client_id="owned-fixture-client", client_secret=uuid4().hex)
    scopes_seen, calls = [], []
    def resolve(*, scopes):
        scopes_seen.append(scopes)
        return credentials, "owned-fixture-project"
    def transport(url, method="GET", **kwargs):
        calls.append((url, method))
        assert url == "https://oauth2.googleapis.com/token" and method == "POST"
        return SimpleNamespace(status=200, data=json.dumps({"access_token": "owned-mocked-refreshed-token",
            "token_type": "Bearer", "expires_in": 3600}).encode(), headers={})
    monkeypatch.setattr(google.auth, "default", resolve)
    provider = SurveyWorkloadCredentials(refresh_request=transport)
    assert provider.token() == "owned-mocked-refreshed-token"
    assert scopes_seen == [["https://www.googleapis.com/auth/devstorage.read_write"]]
    assert calls == [("https://oauth2.googleapis.com/token", "POST")]
    assert provider.token() == "owned-mocked-refreshed-token" and len(calls) == 1
    # Real google Credentials now reports expiry and the same actual refresh
    # method receives a second mocked HTTP exchange; no GCS/IAM is inferred.
    credentials.expiry = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    assert provider.token() == "owned-mocked-refreshed-token" and len(calls) == 2


# Every owned-file object double in this module is explicit local engineering.
# Keep inherited host deployment flags out of that fixture premise; individual
# production admission controls below set the actual server-owned marker.
@pytest.fixture(autouse=True)
def _explicit_local_object_double_mode(monkeypatch):
    for name in ("ODP_DEPLOY_ENV", "ODAY_ENV", "ODP_ENV", "APP_ENV", "ENVIRONMENT",
                 "ODP_REQUIRE_LIVE_DATA", "ODP_PRODUCT_MODE", "NODE_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ODP_ENV", "local")


@pytest.mark.parametrize("name,value", [
    ("ODP_REQUIRE_LIVE_DATA", "true"), ("ODP_PRODUCT_MODE", "production"),
    ("ODP_PRODUCT_MODE", "live"), ("ODP_DEPLOY_ENV", "production"),
    ("ODP_DEPLOY_ENV", "staging"), ("ODAY_ENV", "production"),
    ("ODP_ENV", "production"), ("APP_ENV", "production"),
    ("ENVIRONMENT", "production"), ("NODE_ENV", "production"),
])
@pytest.mark.parametrize("provider_kind", ["memory", "base-gcs-audit-client"])
def test_all_actual_live_modes_require_the_survey_refreshable_content_provider(monkeypatch, name, value, provider_kind):
    from shared.infrastructure.object_store.client import GcsObjectStore

    monkeypatch.delenv("ODP_ENV", raising=False)
    monkeypatch.setenv(name, value)
    store = InMemoryObjectStore() if provider_kind == "memory" else GcsObjectStore()
    with pytest.raises(SurveyOwnerUnavailable, match="SURVEY_PRODUCTION_GCS_REQUIRED"):
        VerifiedSurveyObjectContent.configured(store=store, bucket=BUCKET)


@pytest.mark.parametrize("name,value", [
    ("ODP_REQUIRE_LIVE_DATA", "true"), ("ODP_PRODUCT_MODE", "production"),
    ("ODP_DEPLOY_ENV", "production"), ("NODE_ENV", "production"),
])
def test_live_modes_accept_the_actual_survey_adc_provider_without_claiming_iam_or_fetching_a_token(monkeypatch, name, value):
    monkeypatch.delenv("ODP_ENV", raising=False)
    monkeypatch.setenv(name, value)
    provider = SurveyWorkloadCredentials(credentials=SimpleNamespace(valid=False, token=None))
    def no_token_during_configuration():
        pytest.fail("Configuration is not an authorized object operation or an IAM check")
    monkeypatch.setattr(provider, "token", no_token_during_configuration)
    store = SurveyGcsObjectStore(provider)
    configured = VerifiedSurveyObjectContent.configured(store=store, bucket=BUCKET)
    assert configured.store is store and configured.store.provider is provider
    assert configured.bucket == BUCKET


def test_explicit_local_engineering_object_double_remains_available_without_becoming_production_fallback():
    store = InMemoryObjectStore()
    configured = VerifiedSurveyObjectContent.configured(store=store, bucket=BUCKET)
    assert configured.store is store and configured.bucket == BUCKET
