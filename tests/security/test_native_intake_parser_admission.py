"""Routing is readable without positive settings and never authenticates work."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from modules.listing.application.native_intake_parser import NativeIntakeParserOwner
from modules.listing.application.native_intake_parser_job_proof import parser_delivery_identity
from modules.listing.application.native_intake_parser_policy import (
    JOB_TYPE,
    SIGNING_KEY_ENV,
    NativeParserAuthenticator,
    NativeParserUnavailable,
)
from shared.jobs.queue import JobFenceRejectedError, JobRecord


def delivery():
    job_id, tenant = str(uuid4()), str(uuid4())
    return JobRecord(JOB_TYPE, {"job_id": job_id, "tenant_id": tenant,
        "attempt_token": "1." + "a" * 64}, str(uuid4()), job_id=job_id)


def test_forged_well_formed_token_supplies_routing_but_fails_actual_authentication(monkeypatch):
    monkeypatch.setenv(SIGNING_KEY_ENV, "synthetic-current-parser-own-key-" * 2)
    item = delivery()
    tenant, generation = parser_delivery_identity(item)
    assert (tenant, generation) == (item.payload["tenant_id"], 1)
    with pytest.raises(JobFenceRejectedError, match="NATIVE_PARSER_TOKEN_REJECTED"):
        NativeParserAuthenticator().decode(item)


def test_missing_positive_settings_does_not_block_owner_construction_or_closed_routing(monkeypatch):
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    graph = SimpleNamespace(engine=SimpleNamespace(), queue=SimpleNamespace())
    owner = NativeIntakeParserOwner(graph)
    item = delivery()
    assert owner.artifact_registry is None
    assert parser_delivery_identity(item) == (item.payload["tenant_id"], 1)
    with pytest.raises(NativeParserUnavailable):
        owner.auth.decode(item)


@pytest.mark.parametrize("token", [None, 1, "", "0." + "a" * 64, "01." + "a" * 64,
    "-1." + "a" * 64, "1." + "A" * 64, "1." + "a" * 63,
    "1." + "a" * 65, str(2**63) + "." + "a" * 64,
    "1." + "a" * 64 + "\n", "1." + "a" * 64 + ".extra"])
def test_noncanonical_or_unbounded_attempt_routing_is_rejected(token):
    item = delivery()
    item = replace(item, payload={**item.payload, "attempt_token": token})
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_ROUTING_IDENTITY_REJECTED$"):
        parser_delivery_identity(item)


@pytest.mark.parametrize("defect", ["other_purpose", "extra_key", "wrong_job", "tenant_alias",
    "nil_tenant", "missing_token", "payload_array"])
def test_foreign_purpose_or_open_routing_envelope_cannot_enter_parser_owner(defect):
    item = delivery()
    data = dict(item.payload)
    if defect == "other_purpose":
        item = replace(item, job_type="native-intake-assisted-entry")
    elif defect == "extra_key":
        data["current_grant"] = True
    elif defect == "wrong_job":
        data["job_id"] = str(uuid4())
    elif defect == "tenant_alias":
        data["tenant_id"] = "tenant-a"
    elif defect == "nil_tenant":
        data["tenant_id"] = "00000000-0000-0000-0000-000000000000"
    elif defect == "missing_token":
        data.pop("attempt_token")
    elif defect == "payload_array":
        data = []
    with pytest.raises(JobFenceRejectedError, match="^NATIVE_PARSER_ROUTING_IDENTITY_REJECTED$"):
        parser_delivery_identity(replace(item, payload=data))


def test_routing_preserves_actual_maximum_generation_without_resetting_it():
    item = delivery()
    item = replace(item, payload={**item.payload, "attempt_token": str(2**63 - 1) + "." + "a" * 64})
    assert parser_delivery_identity(item) == (item.payload["tenant_id"], 2**63 - 1)
