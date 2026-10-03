from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
MLFLOW_RECEIPT = "mlflow-model-readback-20261002.json"

_spec = importlib.util.spec_from_file_location(
    "verify_recovery_evidence", HERE / "verify_recovery_evidence.py"
)
verifier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verifier)


@pytest.fixture()
def doc() -> dict:
    return json.loads((HERE / "recovery_evidence.json").read_text(encoding="utf-8"))


def _model(doc: dict, name: str) -> dict:
    return next(m for m in doc["models"] if m["model"] == name)


def _task(doc: dict, task_id: str) -> dict:
    return next(t for t in doc["original_tasks"] if t["task_id"] == task_id)


def _source(doc: dict, source_id: str) -> dict:
    return next(s for s in doc["sources"] if s["id"] == source_id)


def _offline(doc: dict, **kwargs) -> list[str]:
    kwargs.setdefault("git_blob", lambda ref: None)
    kwargs.setdefault("commit_time", lambda sha: None)
    return verifier.validate(doc, **kwargs)


def test_committed_evidence_is_valid(doc: dict) -> None:
    # Real git resolution: cited blobs and commit times are compared when present.
    committed = verifier.evidence_committed_at(REPO_ROOT)
    assert verifier.validate(doc, committed_at=committed) == []


def test_committed_evidence_keeps_facts_apart(doc: dict) -> None:
    forecast = _model(doc, "forecast_revenue_interval")
    assert forecast["registry"]["state"] == "absent"
    assert forecast["artifact"]["state"] == "artifact_missing"
    assert forecast["artifact"]["unobserved_scope"]
    assert forecast["approval"]["state"] == "approval_missing"
    assert forecast["approval"]["unobserved_scope"]
    assert forecast["history"]["state"] == "history_unknown"
    others = [m for m in doc["models"] if m is not forecast]
    for model in others:
        assert model["registry"]["state"] == "not_read_current"
        assert model["approval"]["state"] == "not_read_current"
    assert doc["claims"]["model_ready"] is False
    assert doc["claims"]["historical_state_bytes_recovered"] is False


def test_saved_receipt_hash_is_checked(doc: dict, tmp_path: Path) -> None:
    receipt = doc["saved_receipts"][0]
    target = tmp_path / receipt["path"]
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    assert any("sha256 mismatch" in e for e in _offline(doc, root=tmp_path))


def test_git_blob_mismatch_is_rejected(doc: dict) -> None:
    errors = _offline(doc, git_blob=lambda ref: b"other bytes")
    assert any("git blob" in e and "sha256 mismatch" in e for e in errors), errors


def test_git_blob_match_is_accepted(doc: dict) -> None:
    def resolve(ref: str) -> bytes:
        source = next(s for s in doc["sources"] if s["ref"] == ref)
        return (REPO_ROOT / source["saved_copy"]).read_bytes()

    assert _offline(doc, git_blob=resolve) == []
    for source in doc["sources"]:
        data = resolve(source["ref"])
        assert hashlib.sha256(data).hexdigest() == source["sha256"]


def test_ref_commit_time_mismatch_is_rejected(doc: dict) -> None:
    errors = _offline(doc, commit_time=lambda sha: datetime(2020, 1, 1, tzinfo=UTC))
    assert any("ref_committed_at differs" in e for e in errors), errors


def test_captured_after_commit_is_rejected(doc: dict) -> None:
    errors = _offline(doc, committed_at=datetime(2026, 10, 2, 23, 50, 31, tzinfo=UTC))
    assert any("after the commit" in e for e in errors), errors


def test_captured_in_future_is_rejected(doc: dict) -> None:
    errors = _offline(doc, now=datetime(2026, 10, 2, tzinfo=UTC))
    assert any("in the future" in e for e in errors), errors


def _break(doc: dict, mutate) -> list[str]:
    broken = copy.deepcopy(doc)
    mutate(broken)
    return _offline(broken)


def _forecast_registry(**changes):
    def mutate(d: dict) -> None:
        registry = _model(d, "forecast_revenue_interval")["registry"]
        registry.update({k: v for k, v in changes.items() if k != "readback"})
        registry["readback"].update(changes.get("readback", {}))

    return mutate


def _dealroom_absent_with_forecast_receipt(d: dict) -> None:
    # Saved Forecast 404 bytes unchanged, relabelled to claim Dealroom.
    _model(d, "dealroom_avm")["registry"] = {
        "state": "absent",
        "scope": "dev MLflow",
        "readback": {
            "receipt": MLFLOW_RECEIPT,
            "model_name": "dealroom_avm",
            "environment": "dev",
            "mlflow_host": "oday-mlflow-767864276141.asia-east1.run.app",
            "observed_at": "2026-10-02T19:17:36.245685+00:00",
            "http_status": 404,
        },
    }


def _history_current(receipt: str):
    def mutate(d: dict) -> None:
        _model(d, "forecast_revenue_interval")["history"].update(
            state="history_current",
            current_row_count=1303,
            current_readback={"receipt": receipt, "relation": "model_ready.forecast_training_view"},
        )

    return mutate


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["claims"].update(model_ready=True), "model_ready claimed"),
        (lambda d: d["claims"].update(live_done=True), "live_done"),
        (lambda d: d["claims"].update(deploy_go=True), "deploy_go"),
        (lambda d: d["claims"].update(historical_state_bytes_recovered=True), "state bytes"),
        (lambda d: d["claims"].update(cloud_authority="dev-sa"), "cloud authority"),
        (lambda d: d.update(mutation=True), "cloud write"),
        (lambda d: d["cloud_writes"].append("gs://x"), "cloud write"),
        (
            lambda d: d["risk_acceptance"].update(used_as_current_go=True),
            "risk acceptance",
        ),
        # Receipt binding: one model's 404 says nothing about another model.
        (_dealroom_absent_with_forecast_receipt, "names a different model"),
        # Registered contradicts the saved 404 bytes.
        (
            _forecast_registry(state="registered", readback={"http_status": 404}),
            "registered contradicts",
        ),
        (_forecast_registry(readback={"http_status": 200}), "status differs"),
        (_forecast_registry(readback={"observed_at": "2026-10-03T00:00:00Z"}), "time differs"),
        (_forecast_registry(readback={"environment": "prod"}), "environment"),
        (_forecast_registry(readback={"mlflow_host": "legacy.example"}), "host differs"),
        (_forecast_registry(scope=""), "observed scope"),
        # Historical 4-day count must not stand in for the current count.
        (
            lambda d: _model(d, "forecast_revenue_interval")["history"].update(
                current_row_count=1303
            ),
            "current row count",
        ),
        (_history_current("nonexistent.json"), "scoped DB readback"),
        (_history_current(MLFLOW_RECEIPT), "scoped DB readback"),
        (
            lambda d: _model(d, "forecast_revenue_interval")["history"]["last_measured"][
                "facts"
            ].update({"forecastops.eligible_count": 5000}),
            "not in source",
        ),
        # Scoped absence: unobserved legacy scope must stay listed.
        (
            lambda d: _model(d, "forecast_revenue_interval")["artifact"].update(
                unobserved_scope=[]
            ),
            "access_unknown",
        ),
        (
            lambda d: _model(d, "forecast_revenue_interval")["approval"].pop("observed_scope"),
            "observed_scope",
        ),
        (
            lambda d: _model(d, "forecast_revenue_interval")["approval"].pop("unobserved_scope"),
            "unobserved_scope",
        ),
        (
            lambda d: d.update(conclusion="The Forecast model never existed."),
            "unbounded absence",
        ),
        (
            lambda d: d.update(operation_plan_reason="Nothing to restore."),
            "unbounded absence",
        ),
        (
            lambda d: _model(d, "dealroom_avm")["approval"].update(
                historical_source="missing-source"
            ),
            "approval not_read_current",
        ),
        # Source / terminal evidence binding.
        (lambda d: _source(d, "registry-training").update(sha256="0" * 64), "sha256 mismatch"),
        (lambda d: _source(d, "avm-handback").update(sha256="0" * 64), "sha256 mismatch"),
        (lambda d: _source(d, "release-py").update(saved_copy="nope.txt"), "saved copy"),
        (
            lambda d: _task(d, "ODP-PRODUCTION-MODEL-REGISTRY-001")["terminal_evidence"][1][
                "facts"
            ].update({"execution.exit_code": 0}),
            "not in source",
        ),
        (
            lambda d: _task(d, "ODP-PRODUCTION-MODEL-REGISTRY-001")["terminal_evidence"][1][
                "facts"
            ].update({"failure.release_mutation_completed": True}),
            "not in source",
        ),
        (
            lambda d: _task(d, "ODP-PRODUCTION-MODEL-REGISTRY-001")["terminal_evidence"].append(
                {"source": "backfill-readme", "facts": {}}
            ),
            "not on branch_head",
        ),
        (
            lambda d: d["release_entrypoints"].update(import_existing_artifact_entrypoint=True),
            "import flag",
        ),
        # Board: historical absence stays historical; current holder from the readback.
        (
            lambda d: _task(d, "ODP-PRODUCTION-MODEL-REGISTRY-001")["board"]["current"].update(
                owner="Codex2"
            ),
            "owner differs",
        ),
        (
            lambda d: _task(d, "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001")["board"][
                "current"
            ].update(non_dispatchable=False),
            "non_dispatchable differs",
        ),
        (
            lambda d: _task(d, "ODP-PRODUCTION-MODEL-REGISTRY-001")["board"][
                "historical_observation"
            ].update(on_board_or_archive=True),
            "historical board absence",
        ),
        (
            lambda d: _task(d, "ODP-PRODUCTION-MODEL-REGISTRY-001")["board"][
                "historical_observation"
            ].update(observed_at="2026-10-03T00:07:00Z"),
            "time differs from its receipt",
        ),
        (
            lambda d: d["missing_inputs"][0].update(handback_holder={"owner": "Codex"}),
            "holder differs",
        ),
        # Chronology.
        (lambda d: d.update(captured_at="2026-10-02T23:58"), "invalid timestamp"),
        (lambda d: d.update(captured_at="2026-10-02T20:00:00Z"), "after captured_at"),
        (
            lambda d: d["saved_receipts"][0].update(observed_at="2026-10-02T19:00:00Z"),
            "differs from its contents",
        ),
        # Recovered without hash / approval / rollback.
        (
            lambda d: (
                _model(d, "forecast_revenue_interval")["artifact"].update(state="recovered"),
                _model(d, "forecast_revenue_interval").update(recovered=True),
            ),
            "needs a sha256",
        ),
        (
            lambda d: (
                _model(d, "forecast_revenue_interval")["artifact"].update(
                    state="recovered",
                    sha256="0" * 64,
                    uri="gs://bucket/model",
                    version="1",
                    training_ref="run",
                ),
                _model(d, "forecast_revenue_interval").update(recovered=True),
            ),
            "approval ref",
        ),
        (
            lambda d: _model(d, "forecast_revenue_interval")["approval"].update(state="approved"),
            "approved without approval_ref",
        ),
        (
            lambda d: _model(d, "sitescore_propensity")["artifact"].update(state="ok"),
            "unknown artifact state",
        ),
        (lambda d: d["models"].pop(), "four readiness models"),
        (lambda d: d["missing_inputs"][0].pop("responsible"), "needs responsible"),
        (lambda d: d.update(missing_inputs=[]), "need missing_inputs"),
        (lambda d: d.update(operation_plan={"step": "copy"}), "nothing recovered"),
        (lambda d: d["original_tasks"][0].update(branch_head="950b852c"), "full SHA"),
    ],
)
def test_negative_cases_are_rejected(doc: dict, mutate, expected: str) -> None:
    errors = _break(doc, mutate)
    assert any(expected in e for e in errors), errors
