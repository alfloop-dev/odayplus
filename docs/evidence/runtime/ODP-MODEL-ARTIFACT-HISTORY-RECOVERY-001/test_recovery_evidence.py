from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]

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


def test_committed_evidence_is_valid(doc: dict) -> None:
    assert verifier.validate(doc, root=REPO_ROOT) == []


def test_committed_evidence_keeps_facts_apart(doc: dict) -> None:
    forecast = _model(doc, "forecast_revenue_interval")
    assert forecast["registry"]["state"] == "absent"
    assert forecast["artifact"]["state"] == "artifact_missing"
    assert forecast["approval"]["state"] == "approval_missing"
    assert forecast["history"]["state"] == "history_unknown"
    others = [m for m in doc["models"] if m is not forecast]
    assert {m["registry"]["state"] for m in others} == {"not_read_current"}
    assert doc["claims"]["model_ready"] is False


def test_saved_receipt_hash_is_checked(doc: dict, tmp_path: Path) -> None:
    receipt = doc["saved_receipts"][0]
    target = tmp_path / receipt["path"]
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    assert any("sha256 mismatch" in e for e in verifier.validate(doc, root=tmp_path))


def _break(doc: dict, mutate) -> list[str]:
    broken = copy.deepcopy(doc)
    mutate(broken)
    return verifier.validate(broken)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d["claims"].update(model_ready=True), "model_ready claimed"),
        (lambda d: d["claims"].update(live_done=True), "live_done"),
        (lambda d: d["claims"].update(deploy_go=True), "deploy_go"),
        (lambda d: d.update(mutation=True), "cloud write"),
        (lambda d: d["cloud_writes"].append("gs://x"), "cloud write"),
        (
            lambda d: d["risk_acceptance"].update(used_as_current_go=True),
            "risk acceptance",
        ),
        # Forecast 404 must not be generalised to another model.
        (
            lambda d: _model(d, "dealroom_avm")["registry"].update(
                state="absent",
                evidence="current_readback",
                readback_ref="mlflow-model-readback-20261002.json",
                readback_model_name="forecast_revenue_interval",
            ),
            "different model",
        ),
        # Historical 4-day count must not stand in for the current count.
        (
            lambda d: _model(d, "forecast_revenue_interval")["history"].update(
                current_row_count=1303
            ),
            "current row count",
        ),
        (
            lambda d: _model(d, "forecast_revenue_interval")["history"].update(
                state="history_current"
            ),
            "current readback ref",
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
            lambda d: _model(d, "heatzone_priority")["approval"].update(state="approved"),
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
