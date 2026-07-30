# ODP-PLAN-OSS-LICENSE-GATE-001 — Reviewer Findings (Claude, round 3)

- Task: 建立 license-aware SBOM 與 OSS release gate
- Owner: Antigravity5
- Reviewer: Claude
- Exact head reviewed: `7c364ed68a74b1455d03c06d578e07ecf47a5402`
- Previous reviewed head: `3f581af1` (round 2)
- Verdict: **CHANGES_REQUESTED**

This round re-verifies the round-2 blockers, independently re-runs the
CodexCoordinator audit of 2026-07-30T19:39:47Z at the exact head, and
adds three reviewer-original findings in the new vulnerability gate.

## Commands run by the reviewer

```
python3 -m pytest -q tests -k "sbom or license or security"      # 246 tests: 238 passed, 8 skipped, 0 failed
                                                                  # (all 8 skips are intake RLS tests needing
                                                                  #  INTAKE_TEST_DATABASE_URL; unrelated to this task)
                                                                  # tests/security/test_supply_chain_security_gate.py: 28 tests
python3 -m ruff check tests scripts modules apps shared models solver pipelines infra .orchestrator
python3 scripts/security/generate_sbom.py --help                 # exit 0
python3 scripts/security/generate_sbom.py --verify                # PASSED
python3 scripts/security/vulnerability_scan.py                    # PASSED
git diff --check                                                  # clean
```

All of the task's own `Verification` commands pass at this head. The
findings below are not test failures — they are gaps between what the
gates assert and what the acceptance criteria require.

## Round-2 findings: re-verified

| ID | Round-2 finding | Reviewer result at `7c364ed6` |
| --- | --- | --- |
| B1 | `F401` unused import turns `ruff check tests` red | **FIXED** — CI-equivalent ruff scope reports `All checks passed!` |
| B2 | `relative_to(ROOT)` crashes on `--output` outside repo | **FIXED** — guarded helper at `generate_sbom.py:32`; `test_check_notices_cli` passes |
| B3 | Vulnerability audit gate is documentation only | **PARTIAL** — `scripts/security/vulnerability_scan.py` now exists and runs; wiring and receipts still incomplete (see C3, R1–R3) |
| S1 | Deploy overwrites committed evidence SBOM | **FIXED** — `deploy_cloud_run_waji.sh:276` and both deploy workflows now pass `--output .odp_data/deployment/sbom.json` |
| S2 | Digest-tampering test was tautological | **FIXED** — `test_dependency_graph_tampering_alters_sbom_digest` now calls the production `compute_sbom_digest` helper |
| N2 (2nd half) | `purl` exemptions bypass the first-party scope check | **NOT FIXED** — `generate_sbom.py:807` still short-circuits on `purl in exempt_purls` with no prefix constraint, while the adjacent `name in exempt_names` branch is prefix-constrained |

## Coordinator audit items: independently re-verified

### C1 — Exemption approvers are unverifiable (CONFIRMED)

`docs/security/license_exemptions.json` (9 entries) and
`docs/security/vulnerability_exemptions.json` (1 entry) all carry
`"approved_by": "Human/Ops"`. No named authority, no `issued_at`, no
decision reference. The license file has no `expires_at` at all.

The validator is a single negative regex, identical in both scripts
(`generate_sbom.py:26`, `vulnerability_scan.py:18`):

```python
AI_AGENT_PATTERN = re.compile(r"^(Antigravity|Claude|Codex|Gemini|Copilot|GPT|LLM)\d*$", re.IGNORECASE)
```

Reviewer probe against both modules:

| `approved_by` value | rejected? |
| --- | --- |
| `Human/Ops` | no |
| `Legal/Ops` | no |
| `asdf` | no |
| `x` | no |
| `TBD` | no |
| `.` | no |
| `ClaudeCode` | no |
| `Antigravity Team` | no |

Any string that is not exactly an agent handle is accepted, including
the two AI-agent spellings in the last two rows. The gate does not
establish that a human approved anything; it only blocks one literal
spelling of one class of names. `Human/Ops` is not an approver — it is
a placeholder that happens to survive the regex.

### C2 — Coordinate-derived hashes presented as content hashes (CONFIRMED)

Three code paths emit `SHA-256` component hashes computed from package
coordinates rather than artifact bytes:

- `generate_sbom.py:582` — root component: `sha256(b"oday-plus-root")`
- `generate_sbom.py:614` — linked npm workspace packages: `sha256(pkg_name)`
- `generate_sbom.py:633` — npm packages with no lockfile `integrity`:
  `sha256("npm:{name}:{version}:{sorted dep names}")`
- `generate_sbom.py:706` — Python packages with no sdist/wheel hash:
  `sha256("pypi:{name}:{version}:{sorted dep names}")`

Reviewer measurement on a live `generate_sbom()` run: 787 components, of
which **8** (`@oday-plus/design-tokens`, `domain-types`,
`openapi-client`, `schemas`, `testkit`, `ui`, `ui-domain`, `web`) carry
the name-only surrogate, plus the root component.

A consumer reading `hashes[].alg == "SHA-256"` will treat these as
artifact digests and will fail any attempt to verify them against real
bytes. Two of the surrogate formulas are also derived from the same
inputs already recorded in `purl` and `dependencies`, so they add no
integrity signal while claiming one.

Fix: omit `hashes` when no authentic digest exists, or hash the actual
file bytes and record the origin explicitly. Do not derive a hash from
coordinates.

### C3 — Audits are prod-only at the canonical entrypoints (CONFIRMED, with a correction)

- `package.json:11` — `"audit:security": "npm audit --omit=dev --audit-level=high"` (prod only)
- `Makefile:40-45` — `dependency-audit` calls `npm run audit:security` (prod only) + `pip-audit --local`
- `.github/workflows/deploy-dev.yml:118`, `deploy-staging.yml:105`,
  `scripts/deploy_cloud_run_waji.sh:276` — invoke `generate_sbom.py`
  only; **no deploy path invokes `vulnerability_scan.py`**

Correction to the coordinator's wording: `vulnerability_scan.py` *is*
reachable in CI, but only indirectly. `ci.yml:110` runs `make security`,
whose `pytest tests/security` step includes
`test_vulnerability_audit_script_passes`, which subprocess-runs the
script at default `--scope all`. That is an incidental assertion inside
a test, not a declared gate step: it produces no receipt, is invisible in
the workflow log as a security gate, and is absent from every deploy
path. A release can be cut today with no vulnerability audit run at all.

Fix: add explicit `vulnerability_scan.py --scope prod` and
`--scope full` steps to the canonical gate (Makefile target + deploy
workflows), publish the two receipts separately, and stop relying on a
pytest assertion as the enforcement point.

### C4 — Exemption schema is unenforced (CONFIRMED)

Neither loader validates structure. `load_license_policy`
(`generate_sbom.py:463-479`) reads only `approved_by`, `purl`,
`package_name`. `load_vulnerability_exemptions`
(`vulnerability_scan.py:21-58`) reads only `package_name`,
`approved_by`, `expires_at`. There is no required-field check for
`issued_at`, `expires_at` (license side), scope, rationale, or an
approval reference, and no negative test for a generic or arbitrary
approver — `test_ai_approver_rejection_negative`
(`tests/security/test_supply_chain_security_gate.py:502`) covers only
the literal `Antigravity5`, which is the one case the regex already
catches.

## Reviewer-original findings in the new vulnerability gate

### R1 — A package-name exemption suppresses every future advisory

`vulnerability_scan.py:63`

```python
if pkg_name in exempt_pkgs:
    return True
```

`vulnerability_id` is recorded but never required to match on the direct
path. Reviewer demonstration: with the committed
`brace-expansion` / `GHSA-mh99-v99m-4gvg` receipt loaded, a synthetic
**CRITICAL** advisory `GHSA-ZZZZ-9999-0000` in the same package is
reported as exempted. The receipt is written as if it were
advisory-scoped; it behaves as a permanent package-wide mute.

Same defect on the Python path at `vulnerability_scan.py:149`
(`if pkg in exempt_pkgs or vid in exempt_ids`).

Fix: require the `(package, vulnerability_id)` pair to match.

### R2 — The `scope` field on a vulnerability exemption is never read

The committed receipt declares `"scope": "dev"`. `load_vulnerability_exemptions`
never reads it, and `main()` (`vulnerability_scan.py:174,179`) passes the
same `exemptions` list to both the `prod` and `full` audit runs, so
`exempt_pkgs` is identical in each. A dev-scoped receipt silently
suppresses the identical finding in the production audit.

This is directly load-bearing for the acceptance criterion
"prod/dev audits 納管": the two audits are not independently gated.

Fix: filter exemptions by scope per audit run.

### R3 — `npm audit` failure is fail-open

`vulnerability_scan.py:99-101`

```python
res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
if not res.stdout.strip():
    return True, []
```

`res.returncode` is never inspected. A registry or network failure that
writes to stderr and exits non-zero with empty stdout returns
`(True, [])` — the gate reports PASSED having audited nothing. The
task's own acceptance language is "fail closed"; this path fails open.

Fix: treat a non-zero exit with unparseable stdout as a violation.

## Required to clear this round

1. **C1 + C4** — replace the negative-regex approver check with a
   positive schema: named individual or role-holder, `issued_at`,
   `expires_at`, scope, rationale, and an approval reference. Until
   authoritative receipts exist, the affected components must stay
   `review_required` / fail-closed rather than be waved through by a
   placeholder string.
2. **C2** — remove all four coordinate-derived hash paths. Omit the
   hash, or compute it from real bytes with the source recorded.
3. **C3 + R2** — wire explicit prod and full/dev audit invocations into
   the canonical gate and both deploy paths, with separate receipts, and
   make exemption `scope` actually select which audit it applies to.
4. **R1, R3** — require advisory-ID match on exemptions; make an
   `npm audit` execution failure a violation.
5. **N2 (round 2)** — constrain or log the `purl`-based license
   exemption bypass at `generate_sbom.py:807`.
6. Add negative tests for: generic `Human/Ops` and arbitrary approvers,
   a missing schema field, a coordinate-derived hash substituted for a
   real digest, a dev-scoped exemption leaking into the prod audit, a
   non-matching advisory ID, and `npm audit` exiting non-zero with empty
   stdout.

Reviewer will re-run independently at the exact reworked head.
