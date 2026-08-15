#!/usr/bin/env python3
"""Mutation harness for the protected-redirect smoke contract.

Round-1 review rejected this task for vacuous redirect assertions: tests that
pass whether or not the guard under test exists. This harness is the evidence
that the current tests are non-vacuous.

Each mutant disables exactly one guard in the reviewed surface
(``_effective_port``, ``_is_safe_protected_redirect``, ``_redact_location``,
``_is_plain_relative_path``) inside
``product_ops/deployment/validate_cloud_run_live_deployment.py``. A mutant is
KILLED when the focused test selection fails, and SURVIVES when the tests still
pass without the guard — a survivor is a coverage gap, not a nit.

The target file is restored from memory in a ``finally`` block, so an
interrupted run leaves the worktree clean.

Usage (from the repo root):

    python3 docs/evidence/runtime/ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001/mutation_harness.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[4]
TARGET = pathlib.Path("product_ops/deployment/validate_cloud_run_live_deployment.py")
TESTS = "tests/ops/test_cloud_run_live_deployment.py"
SELECTION = "protected_redirect or redact_location or effective_port"

# (id, description, anchor, replacement)
MUTANTS: list[tuple[str, str, str, str]] = [
    (
        "M01",
        "_effective_port ignores an explicit port",
        "        if parsed.port is not None:\n            return parsed.port",
        "        if parsed.port is not None:\n            pass",
    ),
    (
        "M02",
        "_effective_port does not fail closed on a malformed port",
        "    except ValueError:\n        return None\n    scheme = parsed.scheme.lower()",
        "    except ValueError:\n        pass\n    scheme = parsed.scheme.lower()",
    ),
    (
        "M03",
        "_effective_port invents 443 for a non-web scheme",
        '    if scheme == "http":\n        return 80\n    return None',
        '    if scheme == "http":\n        return 80\n    return 443',
    ),
    (
        "M04",
        "_effective_port uses the wrong http default port",
        '    if scheme == "http":\n        return 80',
        '    if scheme == "http":\n        return 443',
    ),
    (
        "M05",
        "redirect-status guard removed (200 accepted)",
        "    if web_status not in {302, 303, 307, 308} or not isinstance(location, str)"
        " or not location.strip():",
        "    if not isinstance(location, str) or not location.strip():",
    ),
    (
        "M06",
        "blank-Location guard removed",
        "    if web_status not in {302, 303, 307, 308} or not isinstance(location, str)"
        " or not location.strip():",
        "    if web_status not in {302, 303, 307, 308} or not isinstance(location, str):",
    ),
    (
        "M07",
        "userinfo guard removed entirely",
        "        if target_parsed.username or target_parsed.password"
        ' or "@" in target_parsed.netloc:\n            return False',
        "        if False:\n            return False",
    ),
    (
        "M08",
        'bare "@" netloc clause removed (username/password kept)',
        "        if target_parsed.username or target_parsed.password"
        ' or "@" in target_parsed.netloc:',
        "        if target_parsed.username or target_parsed.password:",
    ),
    (
        "M09",
        "fragment guard removed",
        "        if target_parsed.fragment:\n            return False",
        "        if False:\n            return False",
    ),
    (
        "M10",
        "empty-base-scheme guard removed",
        "        if not base_scheme or base_scheme != target_scheme:",
        "        if base_scheme != target_scheme:",
    ),
    (
        "M11",
        "scheme-equality guard removed (downgrade accepted)",
        "        if not base_scheme or base_scheme != target_scheme:",
        "        if not base_scheme:",
    ),
    (
        "M12",
        "empty-base-host guard removed",
        "        if not base_host or base_host != target_host:",
        "        if base_host != target_host:",
    ),
    (
        "M13",
        "host-equality guard removed (external origin accepted)",
        "        if not base_host or base_host != target_host:",
        "        if not base_host:",
    ),
    (
        "M14",
        "undefined-port fail-closed guards removed",
        "        if base_port is None or target_port is None or base_port != target_port:",
        "        if base_port != target_port:",
    ),
    (
        "M15",
        "port-equality guard removed",
        "        if base_port is None or target_port is None or base_port != target_port:",
        "        if base_port is None or target_port is None:",
    ),
    (
        "M16",
        "target-path guard removed",
        "        if target_parsed.path != target_path:\n            return False",
        "        if False:\n            return False",
    ),
    (
        "M17",
        "returnTo cardinality guard removed (duplicates accepted)",
        "        if not return_to_list or len(return_to_list) != 1:",
        "        if not return_to_list:",
    ),
    (
        "M18",
        "returnTo presence guard removed",
        "        if not return_to_list or len(return_to_list) != 1:",
        "        if len(return_to_list or []) > 1:",
    ),
    (
        "M19",
        "returnTo value guard removed (hostile returnTo accepted)",
        "        if return_to_list[0] != protected_path:\n            return False",
        "        if False:\n            return False",
    ),
    (
        "M20",
        "parse_qs keep_blank_values dropped",
        "        query_params = urllib.parse.parse_qs("
        "target_parsed.query, keep_blank_values=True)",
        "        query_params = urllib.parse.parse_qs(target_parsed.query)",
    ),
    (
        "M21",
        "Location whitespace no longer stripped",
        "        raw_location = location.strip()",
        "        raw_location = location",
    ),
    (
        "M22",
        "relative Location no longer resolved (the original defect)",
        "        resolved_url = urllib.parse.urljoin(request_url, raw_location)",
        "        resolved_url = raw_location",
    ),
    (
        "M23",
        "_redact_location echoes any returnTo value verbatim",
        '                    if key == "returnTo" and _is_plain_relative_path(value):',
        '                    if key == "returnTo":',
    ),
    (
        "M24",
        "_redact_location stops masking userinfo",
        '            userinfo = "<redacted>@" if "@" in parsed.netloc else ""',
        '            userinfo = ""',
    ),
    (
        "M25",
        "_redact_location stops masking non-returnTo values",
        '                        rendered.append(f"{key}=<redacted>")',
        '                        rendered.append(f"{key}={value}")',
    ),
    (
        "M26",
        "_is_plain_relative_path accepts protocol-relative values",
        '        and not value.startswith("//")',
        "        and True",
    ),
    (
        "M27",
        "_is_plain_relative_path accepts an embedded scheme",
        '        and "://" not in value',
        "        and True",
    ),
    (
        "M28",
        "_is_plain_relative_path accepts non-printable payloads",
        "        and all(ch.isprintable() for ch in value)",
        "        and True",
    ),
]

# Mutants that are behaviourally equivalent under the function's public
# contract. They are kept in the harness so the reasoning is auditable rather
# than silently dropped, and they must be justified, not merely tolerated.
EQUIVALENT_SURVIVORS: dict[str, str] = {
    "M06": (
        "The blank-Location short circuit is defensive redundancy: with it "
        "removed, an empty or whitespace-only Location still resolves through "
        "urljoin to the request URL itself, whose path is /operator, so the "
        "target-path guard rejects it anyway. No input distinguishes the two "
        "versions, so no test can kill this mutant."
    ),
}


def run_tests() -> tuple[bool, str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            TESTS,
            "-k",
            SELECTION,
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    lines = proc.stdout.strip().splitlines()
    return proc.returncode == 0, lines[-1] if lines else ""


def main() -> int:
    path = ROOT / TARGET
    original = path.read_text()

    passed, summary = run_tests()
    print(f"baseline: {'PASS' if passed else 'FAIL'} - {summary}")
    if not passed:
        print("baseline must pass before mutating; aborting")
        return 2

    survivors: list[str] = []
    try:
        for mutant_id, description, anchor, replacement in MUTANTS:
            matches = original.count(anchor)
            if matches != 1:
                print(f"{mutant_id} BAD-ANCHOR (matched {matches}x): {description}")
                survivors.append(f"{mutant_id} (bad anchor): {description}")
                continue
            path.write_text(original.replace(anchor, replacement))
            passed, summary = run_tests()
            if passed and mutant_id in EQUIVALENT_SURVIVORS:
                verdict = "EQUIVALENT"
            elif passed:
                verdict = "SURVIVED"
                survivors.append(f"{mutant_id}: {description}")
            else:
                verdict = "KILLED"
            print(f"{mutant_id} {verdict}: {description} - {summary}")
    finally:
        path.write_text(original)

    equivalent = sum(1 for m in MUTANTS if m[0] in EQUIVALENT_SURVIVORS)
    print()
    print(
        f"mutants: {len(MUTANTS)} - "
        f"killed: {len(MUTANTS) - len(survivors) - equivalent} - "
        f"equivalent: {equivalent} - survivors: {len(survivors)}"
    )
    for mutant_id, reason in EQUIVALENT_SURVIVORS.items():
        print(f"  EQUIVALENT {mutant_id}: {reason}")
    for survivor in survivors:
        print(f"  SURVIVOR {survivor}")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
