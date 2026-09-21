#!/usr/bin/env python3
"""Regenerate the pinned Evidently 0.7.21 drift-monitoring baseline.

Run from the repository root with the locked toolchain::

    uv run --frozen --python 3.12 \
        python tests/models/fixtures/evidently_0_7_21/generate_baseline.py

Every value written here is read back from a live call into
``modules.learninghub.infrastructure.EvidentlyDriftMonitor``; nothing is
hand-authored. The generator never writes outside this fixture directory, so it
cannot touch an SBOM or another task's evidence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CASES_DIR = FIXTURE_DIR / "cases"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"

#: Packages whose exact versions determine the recorded statistics.
RECORDED_PACKAGES = (
    "evidently",
    "nltk",
    "pandas",
    "numpy",
    "scipy",
    "scikit-learn",
)


#: Manifest keys that describe *when and where* the baseline was produced. They
#: change on every run and every commit, so ``--check`` compares everything else
#: and leaves these as provenance only.
MANIFEST_PROVENANCE_KEYS = frozenset(
    {"generated_at", "source_sha", "source_branch", "source_worktree_clean"}
)


def _manifest_differences(on_disk: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    differences: list[str] = []
    for key in sorted((set(on_disk) | set(fresh)) - MANIFEST_PROVENANCE_KEYS):
        if on_disk.get(key) != fresh.get(key):
            differences.append(f"changed: manifest.json[{key!r}]")
    return differences


def _load_cases_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "evidently_0_7_21_baseline_cases", FIXTURE_DIR / "baseline_cases.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("could not load baseline_cases.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - defensive
        return None


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in RECORDED_PACKAGES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:  # pragma: no cover - defensive
            versions[name] = None
    return versions


def _environment() -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_compiler": platform.python_compiler(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor_architecture": platform.architecture()[0],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and fail if anything on disk differs",
    )
    arguments = parser.parse_args()

    cases = _load_cases_module()
    CASES_DIR.mkdir(parents=True, exist_ok=True)

    files: dict[Path, str] = {}
    receipts: dict[str, Any] = {}

    for case in cases.ALL_CASES:
        receipt = cases.input_receipt(case)
        golden: dict[str, Any] = {
            "case_id": case.case_id,
            "kind": case.kind,
            "description": case.description,
            "routing_expectation": case.routing_expectation,
        }
        raw_path = CASES_DIR / f"{case.case_id}.raw.json"
        try:
            result = cases.execute(case)
        except Exception as error:  # noqa: BLE001 - the failure is the fixture
            if not case.expects_failure:
                raise
            golden["outcome"] = "raised"
            golden["failure"] = cases.normalize_failure(error)
            receipt["raw_report_sha256"] = None
            if raw_path.exists():
                raw_path.unlink()
        else:
            if case.expects_failure:
                raise AssertionError(
                    f"case {case.case_id} was declared as a failure case but completed"
                )
            golden["outcome"] = "completed"
            golden.update(
                cases.normalize_result(
                    result, expects_auto_snapshot=case.expects_auto_snapshot
                )
            )
            raw_report = result.report_json
            files[raw_path] = raw_report
            receipt["raw_report_sha256"] = cases.sha256_text(raw_report)

        serialized = cases.canonical_json(golden)
        files[CASES_DIR / f"{case.case_id}.golden.json"] = serialized
        receipt["golden_sha256"] = cases.sha256_text(serialized)
        receipts[case.case_id] = receipt

    manifest = {
        "baseline_id": "ODP-NLTK-MONITORING-BASELINE-001",
        "generated_at": cases.utc_now_iso(),
        "generator": "tests/models/fixtures/evidently_0_7_21/generate_baseline.py",
        "case_definitions": "tests/models/fixtures/evidently_0_7_21/baseline_cases.py",
        "source_sha": _git("rev-parse", "HEAD"),
        "source_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "source_worktree_clean": _git("status", "--porcelain") == "",
        "environment": _environment(),
        "packages": _package_versions(),
        "rng_seed": cases.RNG_SEED,
        "rng_decimals": cases.RNG_DECIMALS,
        "normalization": cases.NORMALIZATION_POLICY,
        "case_count": len(cases.ALL_CASES),
        "cases": receipts,
    }
    files[MANIFEST_PATH] = cases.canonical_json(manifest)

    if arguments.check:
        differences: list[str] = []
        for path, content in files.items():
            relative = path.relative_to(REPO_ROOT)
            if not path.exists():
                differences.append(f"missing: {relative}")
                continue
            on_disk = path.read_text(encoding="utf-8")
            if path == MANIFEST_PATH:
                differences.extend(_manifest_differences(json.loads(on_disk), manifest))
            elif on_disk != content:
                differences.append(f"changed: {relative}")
        for difference in differences:
            print(difference)
        if differences:
            print(f"{len(differences)} baseline difference(s) against a fresh run")
            return 1
        print(
            f"baseline is reproducible: {len(files)} file(s) match "
            f"(manifest provenance keys {sorted(MANIFEST_PROVENANCE_KEYS)} are excluded by design)"
        )
        return 0

    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    print(f"wrote {len(files)} baseline file(s) under {FIXTURE_DIR.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
