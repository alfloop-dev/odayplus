"""Source-bound declaration gates; no SQL, source/storage IO or activation."""

import ast
import copy
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from modules.listing.application.native_intake_assisted_entry_identity import (
    assisted_delivery_identity,
)
from modules.listing.application.native_intake_assisted_entry_policy import (
    JOB_TYPE,
    SIGNING_KEY_ENV,
    NativeAssistedEntryAuthenticator,
    NativeAssistedEntryUnavailable,
)
from shared.domain.events import EventContractValidator
from shared.jobs.queue import JobFenceRejectedError

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "docs/events/ODAY_PLUS_ASSISTED_LISTING_INTAKE_EVENTS_V1_1_ADDENDUM.yaml"


def declared_audit_contract(document):
    """Check the actual scoped producers, including future manual modules."""
    directory = ROOT / "modules/listing/application"
    paths = set(directory.glob("native_intake_assisted_entry*.py")) | {
        directory / name for name in ("native_intake_source_policy_recheck.py", "native_intake_recheck_settlement.py",
            "native_intake_pending_commands.py", "native_intake_command_withdrawal.py")}
    actions = {node.value for path in paths for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.startswith("intake.") and node.value.endswith(".v1")}
    declared = set(document["rules"]["state_contract_labels"]["audit_action_codes"])
    assert actions, "The gate must inspect actual producer source"
    missing = sorted(actions - declared)
    if missing:
        raise AssertionError("Undeclared actual native audit actions: " + ", ".join(missing))
    return actions


def test_actual_manual_and_recheck_audit_literals_are_declared():
    actions = declared_audit_contract(yaml.safe_load(CATALOG.read_text()))
    assert {"intake.assisted_entry_admitted.v1", "intake.assisted_entry_attempt_accepted.v1"} <= actions


@pytest.mark.parametrize("missing", ["intake.assisted_entry_admitted.v1", "intake.assisted_entry_attempt_accepted.v1"])
def test_removing_a_real_emitted_audit_declaration_is_detected(missing):
    document = copy.deepcopy(yaml.safe_load(CATALOG.read_text()))
    document["rules"]["state_contract_labels"]["audit_action_codes"].remove(missing)
    with pytest.raises(AssertionError, match=re.escape(missing)):
        declared_audit_contract(document)


def test_closed_manual_routing_cannot_substitute_for_current_execution_authentication(monkeypatch):
    monkeypatch.delenv(SIGNING_KEY_ENV, raising=False)
    tenant, job = "3fc06c20-5718-41b8-bb17-39f433a8e2c1", "7729145e-7aac-4f3e-b56c-a1de35fedb8e"
    delivery = SimpleNamespace(job_type=JOB_TYPE, job_id=job,
        payload={"job_id": job, "tenant_id": tenant, "attempt_token": "1." + "a" * 64})
    assert assisted_delivery_identity(delivery) == (tenant, 1)
    with pytest.raises(NativeAssistedEntryUnavailable):
        NativeAssistedEntryAuthenticator().decode(delivery)
    delivery.job_type = "native-intake-source-policy-recheck"
    with pytest.raises(JobFenceRejectedError, match="ROUTING_IDENTITY_REJECTED"):
        assisted_delivery_identity(delivery)


def declared_event_contract(catalog, payloads):
    """Bind actual producer call sites to the effective catalog and schemas."""
    seen = set()
    for name in ("native_intake_assisted_entry_request", "native_intake_assisted_entry_results", "native_intake_assisted_entry_settlement",
                 "native_intake_source_policy_recheck"):
        module = importlib.import_module("modules.listing.application." + name)
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DomainEvent"):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords}

            def value(key, default=None, *, call_keywords=keywords, producer_module=module):
                if key not in call_keywords:
                    return default
                expression = call_keywords[key]
                return getattr(producer_module, expression.id) if isinstance(expression, ast.Name) else ast.literal_eval(expression)

            event_type = value("event_type")
            declaration = catalog.get(event_type)
            assert declaration is not None, "Undeclared emitted event: " + event_type
            for key in ("producer", "schema_ref", "aggregate_type", "sensitive_fields"):
                assert declaration[key] == value(key, [] if key == "sensitive_fields" else None), (event_type, key)
            schema = value("schema_ref").removeprefix("#/payloads/")
            assert schema in payloads, "Missing emitted payload schema: " + schema
            job = value("aggregate_type") == "job"
            assert declaration["partition_key"] == ("tenant_id:job_id" if job else "tenant_id:intake_id")
            assert declaration["ordering_scope"] == ("one_job" if job else "one_intake")
            assert declaration["consumers"], "Emitted event has no declared destination: " + event_type
            seen.add(event_type)
    assert {"intake.assisted_entry_requested", "intake.source_policy_recheck_requested",
        "intake.state_changed", "snapshot.manual_entry_created"} <= seen
    snapshot = catalog["snapshot.manual_entry_created"]
    assert snapshot["retention_days"] == 14 and snapshot["transaction_owner"] == "intake.source_snapshots"
    assert set(snapshot["consumers"]) == {"parser_orchestrator", "evidence_index", "reconciliation_service"}
    declared_cancellation_contract(catalog, payloads)


def declared_cancellation_contract(catalog, payloads):
    """The shared emitter resolves event/aggregate from the actual closed lane map."""
    module = importlib.import_module("modules.listing.application.native_intake_command_withdrawal")
    tree = ast.parse(Path(module.__file__).read_text())
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "DomainEvent"]
    assert len(calls) == 1
    keywords = {kw.arg: kw.value for kw in calls[0].keywords}
    assert isinstance(keywords["event_type"], ast.Name) and keywords["event_type"].id == "event_type"
    assert isinstance(keywords["aggregate_type"], ast.Name) and keywords["aggregate_type"].id == "aggregate"
    assert any(isinstance(node, ast.Assign) and isinstance(node.value, ast.Subscript)
        and isinstance(node.value.value, ast.Name) and node.value.value.id == "LANES"
        and isinstance(node.value.slice, ast.Name) and node.value.slice.id == "request_kind"
        and any(isinstance(target, ast.Tuple) and [item.id for item in target.elts] ==
            ["_", "aggregate", "job_type", "event_type", "audit_action"] for target in node.targets)
        for node in ast.walk(tree))
    assert set(module.LANES) == {"ASSISTED_ENTRY", "SOURCE_POLICY_RECHECK"}
    for kind, (_, aggregate, _, event_type, _) in module.LANES.items():
        declaration = catalog.get(event_type)
        assert declaration is not None, "Undeclared cancellation event: " + event_type
        assert declaration["aggregate_type"] == aggregate
        for key in ("producer", "schema_ref"):
            assert declaration[key] == ast.literal_eval(keywords[key]), (event_type, key)
        assert declaration["schema_ref"].removeprefix("#/payloads/") in payloads
        sensitive_fields = ast.literal_eval(keywords["sensitive_fields"]) if "sensitive_fields" in keywords else []
        assert declaration["sensitive_fields"] == sensitive_fields
        assert declaration["partition_key"] == "tenant_id:intake_id"
        assert declaration["ordering_scope"] == "one_intake"
        assert declaration["retention_days"] == 30
        assert set(declaration["consumers"]) == ({"assisted_entry_service", "listing_intake_service"}
            if kind == "ASSISTED_ENTRY" else {"listing_intake_service"})


def test_actual_request_and_completion_events_match_effective_catalog_and_schemas():
    validator = EventContractValidator()
    declared_event_contract(validator.catalog, validator.payloads)


@pytest.mark.parametrize("mutation", ["event", "schema", "producer", "consumers", "deadletter"])
def test_missing_or_wrong_actual_manual_snapshot_declarations_fail(mutation):
    validator = EventContractValidator()
    catalog, payloads = copy.deepcopy(validator.catalog), copy.deepcopy(validator.payloads)
    snapshot = catalog["snapshot.manual_entry_created"]
    if mutation == "event":
        del catalog["snapshot.manual_entry_created"]
    elif mutation == "schema":
        del payloads["SnapshotCreatedV1"]
    elif mutation == "producer":
        snapshot["producer"] = "synthetic-wrong-owner"
    elif mutation == "deadletter":
        del catalog["job.dead_lettered"]
    else:
        snapshot["consumers"] = []
    with pytest.raises(AssertionError):
        declared_event_contract(catalog, payloads)


@pytest.mark.parametrize("event_type", ["intake.assisted_entry_request_cancelled", "intake.source_policy_recheck_cancelled"])
@pytest.mark.parametrize("mutation", ["event", "schema", "producer", "aggregate", "consumers", "partition", "ordering", "retention"])
def test_cancellation_declaration_counters_cover_both_actual_dynamic_lanes(event_type, mutation):
    validator = EventContractValidator()
    catalog, payloads = copy.deepcopy(validator.catalog), copy.deepcopy(validator.payloads)
    declaration = catalog[event_type]
    if mutation == "event":
        del catalog[event_type]
    elif mutation == "schema":
        del payloads["IntakeCommandCancellationV1"]
    elif mutation == "consumers":
        declaration["consumers"] = []
    elif mutation == "retention":
        declaration["retention_days"] = 1
    else:
        field = {"producer": "producer", "aggregate": "aggregate_type", "partition": "partition_key", "ordering": "ordering_scope"}[mutation]
        declaration[field] = "synthetic-wrong-contract"
    with pytest.raises(AssertionError):
        declared_event_contract(catalog, payloads)


def cli(arguments):
    return subprocess.run([sys.executable, "-m", "apps.worker.oday_worker.native_intake_assisted_entry", *arguments],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=10)


def test_manual_partition_CLI_help_requires_no_runtime_or_account():
    result = cli(["--help"])
    assert result.returncode == 0 and "--tenant-id" in result.stdout and "--cursor" in result.stdout


@pytest.mark.parametrize("arguments", [[], ["--tenant-id", "00000000-0000-0000-0000-000000000000"],
    ["--tenant-id", "3fc06c20-5718-41b8-bb17-39f433a8e2c1", "--limit", "101"]])
def test_manual_partition_CLI_refuses_missing_zero_or_unbounded_partition_before_bootstrap(arguments):
    result = cli(arguments)
    assert result.returncode == 2 and "required" in result.stderr.lower()
