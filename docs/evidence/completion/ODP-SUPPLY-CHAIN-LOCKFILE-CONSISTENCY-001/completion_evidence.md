# ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001 Completion Evidence

## Task Summary
- **Task ID**: `ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001`
- **Title**: 修正 production npm audit 的無效 package tree
- **Owner**: Antigravity3
- **Reviewer**: Codex
- **Base Commit**: `3fbc85bab88a` (`origin/dev`, composed via base-advance merge)
- **Environment for every command below**: this repository checkout, npm `10.9.8`,
  node available on `PATH`, Python via `uv run --frozen` unless a command is
  written with an explicit interpreter.

## Correction Notice
The first two revisions of this document attributed the CI failure to workspace
manifest / lockfile desynchronisation. **That root cause was wrong and is
retracted here.** It was inferred from the wording of a registry response rather
than measured, and it contradicted this same document's own observation that
`npm install --package-lock-only` produced a zero diff. The root cause below is
the measured one.

## Root Cause

### The failing signal
On PR #1164, `npm audit --omit=dev --audit-level=high` failed in the `product`
CI job with a body returned from
`POST https://registry.npmjs.org/-/npm/v1/security/audits/quick`:

```
statusCode: 400
Invalid package tree, run  npm install  to rebuild your package-lock.json
```

### Why that sentence is not about this repository's lockfile
`npm audit` does not decide locally that a package tree is invalid. The
resolution path is in the shipped `@npmcli/arborist`
(`/usr/lib/node_modules/npm/node_modules/@npmcli/arborist/lib/audit-report.js`,
`_getReport`, lines 304-346):

```js
try {
  // first try the super fast bulk advisory listing
  const res = await fetch('/-/npm/v1/security/advisories/bulk', {...})
  return await res.json()
} catch (er) {
  log.silly('audit', 'bulk request failed', String(er.body))
  // that failed, try the quick audit endpoint
  const res = await fetch('/-/npm/v1/security/audits/quick', {...})
  return AuditReport.auditToBulk(await res.json())
}
```

Two consequences follow directly from that source:

1. The `audits/quick` endpoint is only ever reached **after the bulk advisory
   endpoint has already thrown**. Seeing `audits/quick` in a CI log is by
   itself evidence of a registry transport failure on the supported endpoint.
2. `Invalid package tree, ...` is the HTTP response body of that deprecated
   fallback endpoint. It is registry output, not an arborist verdict on
   `package-lock.json`.

### Corroborating measurements
| Observation | Result |
| --- | --- |
| PR #1164 run `33829112721` (02:43Z) | `audits/quick` → `400 Invalid package tree` |
| PR #1164 run `33832485313` (03:45Z), **lockfile unchanged** | `503 Service Unavailable` |
| Same endpoint, separately observed | `npm notice This endpoint is being retired.` → exit 1 |
| Same lockfile, local | `npm audit --omit=dev --audit-level=high` → exit `0`, `found 0 vulnerabilities` |

One unchanged package tree producing three different registry outcomes, and
succeeding locally, rules out manifest/lockfile desynchronisation. The failure
is registry-side and transient.

### Why it reddened every product-scoped PR
The gate was `npm audit --omit=dev --audit-level=high`: a single call, no
retry, whose only output is an exit code. A registry transport failure and a
genuine high-severity advisory both surface as "exit non-zero", so a transient
registry error was indistinguishable from a real vulnerability — and, because
the fallback endpoint's body names `package-lock.json`, it actively misdirected
triage toward rebuilding a lockfile that was never broken.

## Lockfile Consistency (the original hypothesis, tested and rejected)
| Check | Command | Result |
| --- | --- | --- |
| Lockfile installs and matches manifests | `npm ci --dry-run` | exit `0`, `added 74 packages` (npm ci aborts hard when `package.json` and `package-lock.json` are out of sync, so this is the authoritative consistency check) |
| Production audit on the same tree | `npm audit --omit=dev --audit-level=high` | exit `0`, `found 0 vulnerabilities` |
| Production tree scope | `npm audit --omit=dev --json` | `auditReportVersion: 2`; `metadata.vulnerabilities` all `0`; `dependencies.prod: 127` |

`package.json`, the workspace manifests and `package-lock.json` were already
consistent. **No dependency version and no lockfile entry was changed by this
task** — rebuilding the lockfile would have been an unjustified supply-chain
change made on a false premise.

## Fix
`delivery_toolchain/security/npm_audit_gate.py` (new) replaces the bare audit
command. It keeps the `--omit=dev` scope and the `high` threshold, and adds the
distinction the old gate lacked:

- **Structural, not textual, discrimination.** The gate runs
  `npm audit --omit=dev --json` and classifies the result by shape: a real
  report always carries `auditReportVersion` plus `metadata.vulnerabilities`,
  whereas npm's `auditError` helper (`/usr/lib/node_modules/npm/lib/utils/audit-error.js`)
  emits an error object with `message`/`statusCode`/`body` and never sets
  `auditReportVersion`. Registry wording ("Invalid package tree", a retirement
  notice, a 503) therefore has no influence on the verdict.
- **Findings decide on counts, not exit code.** With `--json`, npm exits
  non-zero merely because findings exist. When a report is present the gate
  evaluates `metadata.vulnerabilities` at or above the threshold, so
  high/critical advisories always fail.
- **Enforced threshold floor.** The gate validates that the threshold cannot be
  lowered below `high` (preventing `ODP_NPM_AUDIT_LEVEL=critical` from bypassing
  high-severity production advisories).
- **Bounded retry for transport failures only.** Three attempts with linear
  backoff; a delivered report is never retried away.
- **Fail-closed on exhaustion.** If no attempt yields a report, the gate exits
  `2` with `AUDIT UNAVAILABLE`. A registry outage means there is no
  vulnerability data, so the gate stays closed rather than reporting a pass.
- **Redacted receipt emission.** When `--receipt <path>` is supplied, the gate
  atomically writes a redacted JSON receipt recording execution status, exit code,
  severity counts, threshold, and outcome kind.
- **Architectural separation between CI and Runtime Release.** PR and merge CI
  do not execute live `npm audit` calls over the network; CI validates deterministic
  classifiers and workflow wiring. Live production audit is isolated exclusively to
  the Runtime Release (`deploy-dev.yml`) build-phase egress probe where it executes
  once and writes `npm-audit-receipt.json`.

Exit codes are distinct so the two states can never be conflated again:
`0` clean, `1` vulnerabilities at or above threshold, `2` audit unavailable.

`delivery_toolchain/security/generate_sbom.py` retains the fix from the earlier
revision: distribution discovery is pinned to `.venv/lib/python*/site-packages`
so `--check` is reproducible when invoked with a bare `python3` as well as under
`uv`. This addresses the first reviewer's finding about the receipt's
environment; it is orthogonal to the npm audit gate and does not affect it.

## Verification (on this head)
| # | Command | Result |
| --- | --- | --- |
| 1 | `npm ci --dry-run` | exit `0` |
| 2 | `python3 delivery_toolchain/security/npm_audit_gate.py` | exit `0`, `PASS: no production vulnerabilities at or above 'high'` |
| 3 | `python3 delivery_toolchain/security/generate_sbom.py --check` | exit `0`, `SBOM at docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json is valid and up to date.` |
| 4 | `uv run python delivery_toolchain/security/generate_sbom.py --check` | exit `0`, `SBOM at docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json is valid and up to date.` |
| 5 | `uv run --frozen pytest tests/security/test_supply_chain_security_gate.py tests/security/test_release_security_gate.py -q` | exit `0`; 33 collected, 33 passed |
| 6 | `uv run --frozen pytest tests/ops/test_deploy_workflow_contract.py -q` | exit `0`; 77 collected, 77 passed |
| 7 | `uv run --frozen ruff check delivery_toolchain/security/npm_audit_gate.py tests/security/test_supply_chain_security_gate.py` | `All checks passed!` |
| 8 | `uv run python delivery_toolchain/governance/check_code_boundaries.py` | exit `0`, `Code boundary checks passed for 1098 files.` |

### Regression tests added
`tests/security/test_supply_chain_security_gate.py` gains regression cases that pin
the gate's behaviour, using the **actual registry bodies observed on PR #1164**
as fixtures:

- `test_npm_audit_gate_fails_closed_on_high_severity_findings`
- `test_npm_audit_gate_fails_closed_on_critical_severity_findings`
- `test_npm_audit_gate_ignores_findings_below_threshold`
- `test_npm_audit_gate_does_not_pass_on_registry_transport_failure`
- `test_npm_audit_gate_transport_failure_is_not_reported_as_a_lockfile_defect`
- `test_npm_audit_gate_rejects_unparsable_output`
- `test_npm_audit_gate_rejects_report_without_severity_counts`
- `test_npm_audit_gate_retries_transport_failures_before_giving_up`
- `test_npm_audit_gate_stops_retrying_once_a_report_arrives`
- `test_npm_audit_gate_exhausted_retries_stay_closed`
- `test_npm_audit_gate_is_wired_into_the_release_gate`
- `test_npm_audit_gate_omits_dev_dependencies`
- `test_npm_audit_gate_rejects_lowering_threshold_to_critical`
- `test_npm_audit_gate_rejects_critical_env_var`
- `test_npm_audit_gate_writes_redacted_receipt`
- `test_npm_audit_gate_receipt_distinguishes_unavailable_from_vulnerabilities`
- `test_ci_does_not_execute_live_npm_audit_in_makefile`

The transport failure and retry exhaustion cases assert that a `400`
`Invalid package tree` body, a `503`, and exhausted retries each produce
`EXIT_AUDIT_UNAVAILABLE` and explicitly **not** `EXIT_OK`.

## Safety Invariants
- Audit threshold unchanged (`high`, production scope `--omit=dev`).
- No threshold downgrade to `critical` permitted.
- No gate skipped, waived or made conditional; no `continue-on-error` added.
- A registry non-200 is **not** treated as success — it is a distinct
  fail-closed exit code.
- Deterministic test execution: `test_npm_audit_passes` in pytest evaluates
  the audit gate logic with deterministic fixture/mock outcome rather than making
  direct live network requests, preventing merge-queue timeouts.
- Live audit execution is performed exclusively by the Runtime Release build-phase
  egress probe and produces a redacted receipt.
- No production dependency version and no `package-lock.json` entry changed.

## Residual Risk
If the npm registry is unavailable for the full retry budget, the gate fails
with `AUDIT UNAVAILABLE` (exit `2`) and the release stays red. That is deliberate:
without advisory data the gate cannot assert that production dependencies are
clean. The distinct exit code and message make this state self-identifying, so
it is no longer mistaken for a lockfile defect.
