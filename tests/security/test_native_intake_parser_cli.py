"""In-process CLI contracts with mock runtime/owner; no SQL, children or network.

The partition API is supplied by the task packet and is intentionally mocked:
the frozen owner snapshot does not yet implement admit_partition.
"""

import argparse
import builtins
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, create_autospec

import pytest

from apps.worker.oday_worker import native_intake_parser as cli
from modules.external_data.application import native_parser_deployments as startup

TENANT = "c6cb4fe0-02df-4bd6-8697-9d1a3a35e581"
SOURCE = "native-parser-cli.synthetic-source"
RELEASE = "8b1657cd-d8dc-4949-b82f-2189f9d01576"
OTHER_RELEASE = "fa944a31-602c-45b9-b6d6-b32fa7fd272b"
PRIVATE = "postgresql://private-user:private-password@private-host/db /private/parser/provider.py"
BASE_ARGS = ["--tenant-id", TENANT, "--source-id", SOURCE]
LOAD_DEPLOYMENTS = startup.native_parser_deployments


@pytest.mark.parametrize("extra", [
    ["--match-completed"],
    ["--match-completed", "--matching-policy-version-id", "synthetic:" + TENANT, "--cursor", "parser-cursor"],
    ["--match-completed", "--matching-policy-version-id", "synthetic:" + TENANT, "--include-progress"],
    ["--matching-policy-version-id", "synthetic:" + TENANT],
])
def test_matching_partition_requires_explicit_authority_and_separate_parser_cursor(extra):
    with pytest.raises(SystemExit) as exit_info:
        cli.main([*BASE_ARGS, *extra])
    assert exit_info.value.code == 2


class _FutureOwner:
    """Only the future API; no invented request or withdrawal methods."""

    last_partition_progress = None

    def admit_partition(self, tenant, *, source_id, release_id, limit, cursor=None):
        raise AssertionError("Only the mock may be called")


class _Stream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushed = []

    def flush(self):
        self.flushed.append(self.getvalue())
        super().flush()


def _receipt(index=1):
    # Same public fields as the actual admission owner, with synthetic values.
    return {
        "job_id": f"00000000-0000-0000-0000-{index:012d}",
        "parser_run_id": "58fbfe5d-28fd-47f2-bef3-e46795dbf99f",
        "intake_id": "5a581975-c088-4ac5-8718-f98bd638f283",
        "source_snapshot_id": "10cbb9d8-25be-4f4a-a41c-6a1543a292bb",
        "parser_release_id": RELEASE,
        "admitted_intake_version": 2,
        "job_status_at_admission": "QUEUED",
        "timeout_at": "2026-10-18T14:00:00+00:00",
        "audit_event_id": "e197b32c-d6ce-453f-921a-e4cc2672b6ad",
    }


def _lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


@pytest.fixture
def flow(monkeypatch):
    owner = create_autospec(_FutureOwner, instance=True, spec_set=True)
    owner.admit_partition.return_value = iter(())
    owner.last_partition_progress = {
        "scanned": 0, "admitted": 0, "conflicts": 0,
        "has_more": False, "next_cursor": None, "conflict_examples": [],
    }
    engine = Mock(spec_set=["close"])
    factory = Mock(return_value=owner)
    bundle = SimpleNamespace(engine=engine, canonical_intake_parser=factory)
    bootstrap = Mock(return_value=bundle)
    # Importing the real API server would load unrelated runtime dependencies.
    # Install only its fixed bootstrap boundary; it never opens a real engine.
    server = ModuleType("apps.api.server")
    server.bootstrap_runtime = bootstrap
    monkeypatch.setitem(sys.modules, "apps.api.server", server)
    deployments = Mock(spec_set=["release_for_source"])
    deployments.release_for_source.return_value = RELEASE
    loader = Mock(return_value=deployments)
    monkeypatch.setattr(startup, "native_parser_deployments", loader)
    stdout, stderr = _Stream(), _Stream()
    original_main = cli.main

    def captured_main(argv=None):
        # Pytest resumes its own capture after fixture setup. Redirect at the
        # actual invocation so streaming/flush assertions observe real prints.
        with redirect_stdout(stdout), redirect_stderr(stderr):
            return original_main(argv)

    monkeypatch.setattr(cli, "main", captured_main)
    calls = Mock()
    calls.attach_mock(loader, "load")
    calls.attach_mock(deployments.release_for_source, "select")
    calls.attach_mock(bootstrap, "bootstrap")
    calls.attach_mock(factory, "factory")
    calls.attach_mock(owner.admit_partition, "admit")
    calls.attach_mock(engine.close, "close")
    return SimpleNamespace(
        owner=owner, engine=engine, bundle=bundle, factory=factory,
        bootstrap=bootstrap, deployments=deployments, loader=loader,
        stdout=stdout, stderr=stderr, calls=calls,
    )


def _assert_no_runtime(flow):
    flow.bootstrap.assert_not_called()
    flow.factory.assert_not_called()
    flow.owner.admit_partition.assert_not_called()
    flow.engine.close.assert_not_called()


def _assert_error(flow, error_class):
    assert _lines(flow.stderr) == [{"error_class": error_class, "admission_complete": False}]
    assert PRIVATE not in flow.stdout.getvalue() + flow.stderr.getvalue()
    assert "Traceback" not in flow.stderr.getvalue()


def _forbid_runtime_imports(monkeypatch):
    original = builtins.__import__
    imports = []

    def guarded(name, *args, **kwargs):
        if name in {"apps.api.server", "modules.external_data.application.native_parser_deployments"}:
            imports.append(name)
            raise AssertionError("Argument handling must precede runtime imports")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    return imports


def test_help_needs_no_startup_or_bootstrap(flow, monkeypatch):
    imports = _forbid_runtime_imports(monkeypatch)
    with pytest.raises(SystemExit) as result:
        cli.main(["--help"])
    assert result.value.code == 0
    help_text = flow.stdout.getvalue()
    for flag in ("--tenant-id", "--source-id", "--limit", "--cursor", "--include-progress"):
        assert flag in help_text
    assert "--release-id" not in help_text
    assert flow.stderr.getvalue() == ""
    assert imports == []
    flow.loader.assert_not_called()
    _assert_no_runtime(flow)


@pytest.mark.parametrize("arguments", [
    [], ["--tenant-id", TENANT], ["--source-id", SOURCE],
    ["--tenant-id", "invalid", "--source-id", SOURCE],
    ["--tenant-id", "00000000-0000-0000-0000-000000000000", "--source-id", SOURCE],
    *[BASE_ARGS + ["--limit", value] for value in ("0", "101", "-1", "True", "False", "1.5", "", "nan")],
    *[["--tenant-id", TENANT, "--source-id", value] for value in (
        "", " ", " leading", "trailing ", "x" * 257, "bad\x00source", "bad\x1fsource",
        "bad\x7fsource", "bad\nsource", "bad\tsource", "\ud800", "\udfff",
    )],
    BASE_ARGS + ["--release-id", RELEASE],
    BASE_ARGS + ["--include-progress", "false"],
    BASE_ARGS + ["--lim", "2"],
])
def test_invalid_arguments_exit_before_startup_or_bootstrap(flow, monkeypatch, arguments):
    imports = _forbid_runtime_imports(monkeypatch)
    with pytest.raises(SystemExit) as result:
        cli.main(arguments)
    assert result.value.code == 2
    assert flow.stdout.getvalue() == ""
    assert "error:" in flow.stderr.getvalue()
    assert imports == []
    flow.loader.assert_not_called()
    _assert_no_runtime(flow)


@pytest.mark.parametrize("value", [True, False, 1, None, b"source"])
def test_source_and_limit_converters_reject_non_text_values(value):
    for converter in (cli._source_id, cli._limit):
        with pytest.raises(argparse.ArgumentTypeError):
            converter(value)


def test_source_converter_requires_strict_string():
    class Text(str):
        pass

    with pytest.raises(argparse.ArgumentTypeError):
        cli._source_id(Text(SOURCE))


@pytest.mark.parametrize("raw", [None, "", "{", "null", '{"version":true,"deployments":[]}'])
def test_absent_or_malformed_startup_rejected_before_bootstrap(flow, monkeypatch, raw):
    # Exercise the real loader only on inputs rejected before archive access.
    if raw is None:
        monkeypatch.delenv(startup.DEPLOYMENTS_ENV, raising=False)
    else:
        monkeypatch.setenv(startup.DEPLOYMENTS_ENV, raw)
    flow.loader.side_effect = LOAD_DEPLOYMENTS
    with pytest.raises(SystemExit) as result:
        cli.main(BASE_ARGS)
    assert result.value.code == 1
    expected = ("NativeParserDeploymentSelectionUnavailable" if raw is None
                else "NativeParserDeploymentConfigurationRejected")
    _assert_error(flow, expected)
    assert flow.stdout.getvalue() == ""
    flow.loader.assert_called_once_with()
    _assert_no_runtime(flow)


def test_unmapped_source_fails_before_bootstrap_without_fallback(flow):
    flow.deployments.release_for_source.side_effect = startup.NativeParserDeploymentSelectionUnavailable(PRIVATE)
    with pytest.raises(SystemExit) as result:
        cli.main(BASE_ARGS)
    assert result.value.code == 1
    _assert_error(flow, "NativeParserDeploymentSelectionUnavailable")
    assert flow.stdout.getvalue() == ""
    flow.deployments.release_for_source.assert_called_once_with(SOURCE)
    _assert_no_runtime(flow)


@pytest.mark.parametrize("release", [RELEASE, OTHER_RELEASE])
@pytest.mark.parametrize("limit", [None, "1", "100"])
def test_explicit_startup_release_and_default_or_boundary_limits(flow, release, limit):
    flow.deployments.release_for_source.return_value = release
    arguments = BASE_ARGS if limit is None else BASE_ARGS + ["--limit", limit]
    assert cli.main(arguments) is None
    assert flow.calls.mock_calls == [
        call.load(), call.select(SOURCE), call.bootstrap(), call.factory(),
        call.admit(TENANT, source_id=SOURCE, release_id=release, limit=25 if limit is None else int(limit)),
        call.close(),
    ]
    assert flow.stdout.getvalue() == ""
    assert flow.stderr.getvalue() == ""


@pytest.mark.parametrize("cursor", ["opaque-partition-cursor", ""])
def test_cursor_forwarded_exactly_and_tenant_canonicalized(flow, cursor):
    cli.main(["--tenant-id", TENANT.upper(), "--source-id", SOURCE, "--limit", "7", "--cursor", cursor])
    flow.owner.admit_partition.assert_called_once_with(
        TENANT, source_id=SOURCE, release_id=RELEASE, limit=7, cursor=cursor,
    )
    flow.engine.close.assert_called_once_with()


@pytest.mark.parametrize("source", ["x" * 256, "來源.資料", "source with internal spaces"])
def test_valid_source_text_is_preserved_exactly(flow, source):
    cli.main(["--tenant-id", TENANT, "--source-id", source])
    flow.deployments.release_for_source.assert_called_once_with(source)
    flow.owner.admit_partition.assert_called_once_with(TENANT, source_id=source, release_id=RELEASE, limit=25)


def test_main_reads_process_arguments_when_no_explicit_list_is_supplied(flow, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["native_intake_parser", *BASE_ARGS])
    cli.main()
    flow.owner.admit_partition.assert_called_once_with(TENANT, source_id=SOURCE, release_id=RELEASE, limit=25)
    flow.engine.close.assert_called_once_with()


def test_receipts_are_flushed_before_iterator_advances_and_survive_later_failure(flow):
    receipts = [_receipt(1), _receipt(2)]

    def admitted():
        for index, receipt in enumerate(receipts):
            assert len(_lines(flow.stdout)) == index
            yield receipt
            expected = [
                {"phase": "native_parser_admission_only", "admission_receipt": item}
                for item in receipts[:index + 1]
            ]
            assert _lines(flow.stdout) == expected
            assert flow.stdout.flushed[-1] == flow.stdout.getvalue()
        raise RuntimeError(PRIVATE)

    flow.owner.admit_partition.return_value = admitted()
    with pytest.raises(SystemExit) as result:
        cli.main(BASE_ARGS)
    assert result.value.code == 1
    assert _lines(flow.stdout) == [
        {"phase": "native_parser_admission_only", "admission_receipt": receipt}
        for receipt in receipts
    ]
    _assert_error(flow, "RuntimeError")
    flow.engine.close.assert_called_once_with()


@pytest.mark.parametrize("requested,has_more,conflicts", [
    (False, False, 0), (True, False, 0), (False, True, 0),
    (False, False, 1), (True, True, 1),
])
def test_progress_conditions_and_admission_only_receipt_order(flow, requested, has_more, conflicts):
    receipt = _receipt()
    progress = {
        "scanned": 1 + conflicts, "admitted": 1, "conflicts": conflicts,
        "has_more": has_more, "next_cursor": "next-position" if has_more else None,
        "conflict_examples": [{"intake_id": receipt["intake_id"], "error_class": "ValueError"}] if conflicts else [],
    }

    def admitted():
        yield receipt
        # Progress is published by the owner only after the iterator finishes.
        flow.owner.last_partition_progress = progress

    flow.owner.admit_partition.return_value = admitted()
    cli.main(BASE_ARGS + (["--include-progress"] if requested else []))
    expected = [{"phase": "native_parser_admission_only", "admission_receipt": receipt}]
    if requested or has_more or conflicts:
        expected.append({"phase": "native_parser_scan", "scan_receipt": progress})
    assert _lines(flow.stdout) == expected
    assert flow.stdout.flushed[-1] == flow.stdout.getvalue()
    assert flow.stderr.getvalue() == ""
    flow.engine.close.assert_called_once_with()


def test_requested_progress_is_printed_for_empty_scan(flow):
    cli.main(BASE_ARGS + ["--include-progress"])
    assert _lines(flow.stdout) == [{
        "phase": "native_parser_scan", "scan_receipt": flow.owner.last_partition_progress,
    }]
    flow.engine.close.assert_called_once_with()


@pytest.mark.parametrize("stage", ["load", "select", "bootstrap", "factory", "admit", "iterate", "progress", "close"])
def test_failures_emit_only_class_and_close_every_built_engine(flow, stage):
    failure = RuntimeError(PRIVATE)
    boundaries = {
        "load": flow.loader, "select": flow.deployments.release_for_source,
        "bootstrap": flow.bootstrap, "factory": flow.factory,
        "admit": flow.owner.admit_partition, "close": flow.engine.close,
    }
    if stage in boundaries:
        boundaries[stage].side_effect = failure
    elif stage == "iterate":
        class BrokenIterator:
            def __iter__(self):
                return self

            def __next__(self):
                raise failure

        flow.owner.admit_partition.return_value = BrokenIterator()
    else:
        class BrokenProgress(dict):
            def __getitem__(self, key):
                raise failure

        flow.owner.last_partition_progress = BrokenProgress()
    with pytest.raises(SystemExit) as result:
        cli.main(BASE_ARGS)
    assert result.value.code == 1
    assert result.value.__suppress_context__ is True
    _assert_error(flow, "RuntimeError")
    assert flow.stdout.getvalue() == ""
    if stage in {"load", "select", "bootstrap"}:
        flow.engine.close.assert_not_called()
        flow.factory.assert_not_called()
    else:
        flow.engine.close.assert_called_once_with()


@pytest.mark.parametrize("failure", [ValueError(PRIVATE), OSError(PRIVATE), ImportError(PRIVATE)])
def test_primary_error_wins_if_engine_close_also_fails(flow, failure):
    flow.factory.side_effect = failure
    flow.engine.close.side_effect = RuntimeError("private cleanup connection string")
    with pytest.raises(SystemExit) as result:
        cli.main(BASE_ARGS)
    assert result.value.code == 1
    _assert_error(flow, type(failure).__name__)
    assert "cleanup" not in flow.stderr.getvalue()
    flow.engine.close.assert_called_once_with()


def test_close_failure_preserves_receipts_and_scan_progress(flow):
    receipt = _receipt()
    flow.owner.admit_partition.return_value = iter([receipt])
    flow.owner.last_partition_progress.update(scanned=1, admitted=1)
    flow.engine.close.side_effect = OSError(PRIVATE)
    with pytest.raises(SystemExit) as result:
        cli.main(BASE_ARGS + ["--include-progress"])
    assert result.value.code == 1
    assert _lines(flow.stdout) == [
        {"phase": "native_parser_admission_only", "admission_receipt": receipt},
        {"phase": "native_parser_scan", "scan_receipt": flow.owner.last_partition_progress},
    ]
    _assert_error(flow, "OSError")
    flow.engine.close.assert_called_once_with()


def test_progress_serialization_failure_does_not_leak_or_erase_prior_receipt(flow):
    class PrivateValue:
        def __repr__(self):
            return PRIVATE

    receipt = _receipt()
    flow.owner.admit_partition.return_value = iter([receipt])
    flow.owner.last_partition_progress["next_cursor"] = PrivateValue()
    with pytest.raises(SystemExit) as result:
        cli.main(BASE_ARGS + ["--include-progress"])
    assert result.value.code == 1
    assert _lines(flow.stdout) == [{"phase": "native_parser_admission_only", "admission_receipt": receipt}]
    _assert_error(flow, "TypeError")
    flow.engine.close.assert_called_once_with()


@pytest.mark.parametrize("module", ["modules.external_data.application.native_parser_deployments", "apps.api.server"])
def test_runtime_import_errors_are_bounded_without_bootstrap(flow, monkeypatch, module):
    original = builtins.__import__

    def rejected(name, *args, **kwargs):
        if name == module:
            raise ImportError(PRIVATE)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", rejected)
    with pytest.raises(SystemExit) as result:
        cli.main(BASE_ARGS)
    assert result.value.code == 1
    _assert_error(flow, "ImportError")
    _assert_no_runtime(flow)
