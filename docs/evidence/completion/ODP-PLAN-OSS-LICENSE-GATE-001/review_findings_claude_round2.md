# ODP-PLAN-OSS-LICENSE-GATE-001 — Reviewer Findings (Claude, round 2)

- Task: 建立 license-aware SBOM 與 OSS release gate
- Owner: Antigravity5
- Reviewer: Claude
- Exact head reviewed: `3f581af1d21458a255533679de3afe5b2c53532b`
- Base compared against: `e496be62` (dev tip at branch point)
- Verdict: **CHANGES_REQUESTED**

## Commands run by the reviewer

```
git diff --stat e496be62..3f581af1
.venv/bin/python -m pytest -q tests -k "sbom or license or security"
.venv/bin/python -m pytest -q tests/security/test_supply_chain_security_gate.py
.venv/bin/python -m ruff check scripts/security tests/security .orchestrator
.venv/bin/python scripts/security/generate_sbom.py --verify
git diff --check
```

Environment note: `uv` is not on PATH in this worktree, so the repo's
`.venv/bin/python` (3.12.3, the same interpreter `uv sync --frozen`
produced) was used directly. This does not affect any finding below —
B1 is a static lint result and B2 is a deterministic path bug.

## What was re-verified as genuinely fixed from round 1

| ID | Claim | Reviewer result |
| --- | --- | --- |
| B2 | Release attestation fails closed, no synthetic fallback | CONFIRMED — `scripts/deploy_cloud_run_waji.sh:265-270` exits 1 on a non-`sha256:<64hex>` release digest; no `cosign-signed` literal remains |
| B3 | Test run no longer mutates the committed `sbom.json` | CONFIRMED — `git status --short` stayed clean across the full test run |
| S1 | `sbom-content-digest` is reproducible | CONFIRMED — `generate_sbom.py:463-468` hashes only components + dependencies; `git-sha` is no longer an input |
| S2 | `--readback` asserts expected digests | CONFIRMED — `generate_sbom.py:609-619`, with negative coverage in `test_sbom_readback_digest_assertion` |
| S3 | Policy doc Section 3 matches `license_policy.json` | CONFIRMED — allowed set matches exactly; LGPL decision is stated |
| N1 | Non-SPDX alias + case-insensitive matching | CONFIRMED — `normalize_spdx_license`, `evaluate_license_string:223-248` |
| N2 | `main()` prints properties by name | CONFIRMED — `generate_sbom.py:730` |
| N3 | `content_hash` covers the dependency graph | CONFIRMED in production code (see S2 caveat below on its test) |
| — | `--verify` against active lockfiles | PASSED (787 components, 389 dependency nodes) |
| — | `git diff --check` | Clean |

Composite-expression handling was spot-checked against the 34 distinct
license strings present in the committed SBOM. `MIT OR AFL-2.1` passes
on OR semantics (correct); `GPL-3.0 OR MIT` would still be rejected by
the denied-token pre-check (conservative, acceptable). Components with
an empty or malformed `licenses` entry resolve to `UNKNOWN` and fail
closed. The 8 `UNKNOWN` components are all first-party
`@oday-plus/*` workspace packages covered by named exemptions.

## BLOCKERS

### B1 — Unused import fails the `ruff check tests` CI job

`tests/security/test_supply_chain_security_gate.py:486`

```python
from scripts.security.generate_sbom import generate_sbom as current_generate_sbom
```

`current_generate_sbom` is never used. Reviewer result:

```
F401 [*] `scripts.security.generate_sbom.generate_sbom` imported but unused
   --> tests/security/test_supply_chain_security_gate.py:486:65
Found 1 error.
```

`.github/workflows/ci.yml:87` runs `uv run ruff check tests modules apps
shared models solver pipelines infra`, so this turns CI red on this
branch. The `Verified:` trailer on `3f581af1` records only
`ruff check .orchestrator scripts` (`ci.yml:36`), which does not cover
`tests/` — that is why the round-1 B1 fix was scored clean while the
lint error was still present in a different file.

Fix: delete the line.

### B2 — `test_check_notices_cli` fails; `--output` outside the repo root crashes the CLI

`scripts/security/generate_sbom.py:728`

```python
print(f"SBOM successfully generated at {args.output.relative_to(ROOT)}")
```

`Path.relative_to` raises `ValueError` for any path that is not under
`ROOT`. The round-1 B3 fix pointed the test at `tmp_path/sbom.json`
(under `/tmp/pytest-of-*/`), which is outside the worktree, so the CLI
now writes the SBOM successfully and then dies on the success message:

```
ValueError: '/tmp/pytest-of-lupin/pytest-1424/test_check_notices_cli0/sbom.json'
  is not in the subpath of '<repo root>'
assert 1 == 0
```

Reviewer run of the suite: **25 collected, 24 passed, 1 failed**. The
handoff note's "pytest 26 passed" is not reproducible at this head; the
failure is deterministic and path-derived, not environment-specific.

The same pattern exists at `verify_sbom` (`generate_sbom.py:663` and
`:666`), so `--verify --output <path outside repo>` crashes identically.

Fix: print `args.output` directly, or guard the relative form, e.g.
`args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output`,
and apply the same guard in `verify_sbom`.

### B3 — Acceptance criterion 3 (vulnerability audits) is documentation only

Acceptance: *"prod/dev audits 納管且 high findings 修復或具名豁免"*.

`docs/security/LICENSE_AND_SUPPLY_CHAIN_POLICY.md` Section 5 declares:

```
- **Node Audit**: `npm audit --omit=dev --audit-level=high`
- **Python Audit**: `pip-audit --local`
```

Nothing implements this. A grep for `npm audit`, `pip-audit`,
`pip_audit`, and `osv` across `.github/workflows/`, `scripts/security/`,
and `docs/security/` returns only that prose block. There is no
workflow step, no script, no vulnerability-exemption file (the
`license_exemptions.json` schema is license-scoped: `package_name`,
`purl`, `reason`, `approved_by`), and no test. Nothing enforces
"high findings remediated or named-exempted".

Two secondary problems in the same section:

1. `npm audit --omit=dev` audits production dependencies only, which
   contradicts the acceptance wording "prod/dev audits".
2. `pip-audit --local` is not reachable via the project's locked
   environment as written; the repo resolves tooling through
   `uv run --frozen` (see `sast_scan.py` and `run_locked_python` in
   `deploy_cloud_run_waji.sh`).

Fix: either implement the gate (audit steps in `deploy-dev.yml` /
`deploy-staging.yml` covering both prod and dev dependency sets, a
vulnerability exemption record with approver and expiry, and a test),
or split it into a named follow-up task and narrow this task's
acceptance to the SBOM/license scope that was actually delivered. The
policy doc must not assert a gate that does not run.

## SHOULD-FIX

### S1 — Deploy overwrites a tracked evidence artifact mid-deploy

`scripts/deploy_cloud_run_waji.sh:275-286` runs `generate_sbom.py`
without `--output`, so it writes the default
`docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` — a committed
file — with live image/release digests, then copies it to
`.odp_data/deployment/sbom.json`. Every deploy therefore leaves the
checkout dirty and rewrites committed evidence with values specific to
one release. The same concern applies to the new
`Generate SBOM and Check Policy` steps in `deploy-dev.yml:117` and
`deploy-staging.yml:104`.

Fix: pass `--output .odp_data/deployment/sbom.json` on the deploy-time
runs and drop the `cp`. The committed artifact then stays the
`UNBOUND`-digest reference SBOM that `--verify` checks, and the
digest-bound SBOM lives only in the release artifact.

### S2 — `test_dependency_graph_tampering_alters_sbom_digest` is tautological

`tests/security/test_supply_chain_security_gate.py:470-491` mutates the
dependency list and then recomputes the expected digest by
re-implementing the production formula inline
(`sha256(f"{comp_json}:{dep_json}")`, then
`sha256(f"{content_hash}:UNBOUND:UNBOUND")`). It compares one
hand-computed hash against another, so it would still pass if
`generate_sbom` stopped hashing `dependencies` entirely — which is
exactly the N3 regression it is supposed to guard. This is also the
line that carries the B1 unused import.

Fix: extract the digest computation from `generate_sbom` into a helper
and have the test call that helper on the tampered structure.

## NITS

### N1 — `PYPI_LICENSE_FALLBACKS` is an unsourced hardcoded assertion set

`generate_sbom.py:28-52` hardcodes licenses for 23 PyPI packages with no
source reference, no recorded date, and no expiry. Reviewer check: of
the 240 packages in `uv.lock`, 6 are absent from `.venv` (the
platform-conditional ones) and all 6 are covered by this dict, so the
gate currently passes and is reproducible on Linux CI. The residual
risks are that an entry can drift silently if a package relicenses, and
that any newly added platform-conditional dependency will fail with
`UNKNOWN` and no diagnostic pointing at this dict. Recording a source
URL and check date per entry, and naming the dict in the failure
message, would make both cases self-explaining.

### N2 — Exemptions are self-approved and unbounded

All 9 entries in `docs/security/license_exemptions.json` carry
`"approved_by": "Antigravity5"`, the task owner, and none has a review
or expiry date. Separately, the `purl in exempt_purls` branch in
`check_license_policy:516` is an unconditional bypass, while the
adjacent `package_name` branch is correctly constrained to first-party
prefixes (`pkg:generic/oday-plus`, `pkg:npm/%40oday-plus/`) — so a
third-party purl added to that file would be waved through with no
scope check at all. Constraining or at least logging purl-based
exemptions would keep the two branches consistent.

### N3 — Commit author / trailer mismatch

`3f581af1` is authored by `Antigravity4` but carries
`LLM-Agent: Antigravity5`. Cosmetic, but it makes the audit trail
misleading.

## Required to clear this round

1. B1 — remove the unused import; confirm with
   `uv run ruff check tests modules apps shared models solver pipelines infra`
   (the CI-equivalent scope), not just `.orchestrator scripts`.
2. B2 — fix the `relative_to(ROOT)` crash in `main()` and `verify_sbom`;
   confirm `test_check_notices_cli` passes and report the real
   collected/passed counts.
3. B3 — implement the vulnerability audit gate, or split it out as a
   named follow-up and correct Section 5 of the policy doc so it does
   not claim an unenforced gate.
4. S1, S2 addressed or explicitly deferred with a stated reason.
