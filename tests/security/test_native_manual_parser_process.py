"""Exercise real isolated parser execution and owned process-group cleanup."""
import os
import subprocess
import sys
import time
from dataclasses import replace

import pytest

from modules.external_data.application import native_manual_parser_process as process
from modules.external_data.application.native_intake_process import NativeIORejected
from modules.listing.application.native_intake_assisted_entry_input import (
    NativeAssistedEntryFields,
    manual_evidence_document,
)
from tests.security.test_native_parser_artifacts import artifact_case as artifact_case
from tests.security.test_native_parser_output import case


def request_case(artifact_case, **changes):
    artifact, lineage, fields, _ = case(artifact_case, **changes)
    document = manual_evidence_document(
        **{name: getattr(lineage, name) for name in ("tenant_id", "intake_id", "request_id", "requested_by", "source_id")},
        fields=NativeAssistedEntryFields.model_validate(fields), correction_ids=dict(lineage.correction_ids))
    return document, {"lineage": lineage, "artifact": artifact, "original_fields": fields}


def track_children(monkeypatch):
    original = subprocess.Popen
    children, launches = [], []
    def launch(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        launches.append((args, kwargs))
        return child
    monkeypatch.setattr(subprocess, "Popen", launch)
    return children, launches


def assert_reaped(children):
    assert len(children) == 1
    assert children[0].poll() is not None
    with pytest.raises(ProcessLookupError):
        os.killpg(children[0].pid, 0)


@pytest.mark.parametrize("changes,status", [({}, "SUCCEEDED"), ({"floor": "unknown"}, "PARTIAL"),
    ({"address": "＜script＞"}, "PARTIAL")])
def test_actual_fixed_isolated_child_runs_attested_provider_and_reaps(artifact_case, monkeypatch, changes, status):
    data, kwargs = request_case(artifact_case, **changes)
    children, launches = track_children(monkeypatch)
    observed = []
    result = process.NativeManualParserProcess().parse(data, **kwargs, deadline=time.monotonic() + 3,
        guard=lambda: observed.append(time.monotonic()))
    assert result.status == status and result.facts.payloads()["parsed_payload"] == kwargs["original_fields"]
    assert result.facts.payloads()["field_confidence"] == {}
    assert len(observed) >= 5
    assert_reaped(children)
    args, settings = launches[0]
    assert args[0] == [sys.executable, "-I", "-S", str(process._CHILD)]
    assert settings["start_new_session"] and settings["close_fds"] and settings["pass_fds"] == ()
    assert set(settings["env"]) == {"LANG", "LC_ALL", "PYTHONUNBUFFERED"}


@pytest.mark.parametrize("mutation", ["document", "provider_digest", "content_digest"])
def test_actual_child_rejects_substitution_without_echoing_private_input(artifact_case, monkeypatch, mutation):
    data, kwargs = request_case(artifact_case)
    children, _ = track_children(monkeypatch)
    if mutation == "document":
        data = b'private-invalid-source-material'
    elif mutation == "provider_digest":
        kwargs["artifact"] = replace(kwargs["artifact"], installed_sha256=(("provider.py", "0" * 64),))
    else:
        kwargs["lineage"] = replace(kwargs["lineage"], content_sha256="0" * 64)
    with pytest.raises(NativeIORejected) as error:
        process.NativeManualParserProcess().parse(data, **kwargs, deadline=time.monotonic() + 3, guard=lambda: None)
    assert str(error.value) == "NATIVE_IO_PARSER_INPUT_REJECTED"
    assert_reaped(children)


def test_current_authority_guard_can_refuse_before_process_creation(artifact_case, monkeypatch):
    data, kwargs = request_case(artifact_case)
    children, _ = track_children(monkeypatch)
    def refuse():
        raise NativeIORejected("NATIVE_IO_AUTHORITY_LOST")
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_AUTHORITY_LOST$"):
        process.NativeManualParserProcess().parse(data, **kwargs, deadline=time.monotonic() + 3, guard=refuse)
    assert children == []


def test_actual_hanging_child_keeps_original_deadline_and_is_killed_and_reaped(tmp_path, monkeypatch):
    script = tmp_path / "owned-hanging-parser.py"
    script.write_text("import time\ntime.sleep(30)\n")
    class HangingChild(process.NativeManualParserChildRunner):
        def _command(self, credential_fd):
            assert credential_fd is None
            return [sys.executable, "-I", "-S", str(script)]
    children, _ = track_children(monkeypatch)
    started = time.monotonic()
    deadline = started + .4
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_DEADLINE_EXPIRED$"):
        HangingChild().run({"operation": "parse_manual"}, deadline=deadline, guard=lambda: None)
    elapsed = time.monotonic() - started
    assert .35 <= elapsed < 2
    assert_reaped(children)


def test_authority_loss_during_real_child_operation_prevents_result_and_reaps(tmp_path, monkeypatch):
    script = tmp_path / "owned-blocked-parser.py"
    script.write_text("import time\ntime.sleep(30)\n")
    class BlockedChild(process.NativeManualParserChildRunner):
        def _command(self, credential_fd):
            return [sys.executable, "-I", "-S", str(script)]
    children, _ = track_children(monkeypatch)
    def guard():
        if children:
            raise NativeIORejected("NATIVE_IO_AUTHORITY_LOST")
    with pytest.raises(NativeIORejected, match="^NATIVE_IO_AUTHORITY_LOST$"):
        BlockedChild().run({"operation": "parse_manual"}, deadline=time.monotonic() + 3, guard=guard)
    assert_reaped(children)
