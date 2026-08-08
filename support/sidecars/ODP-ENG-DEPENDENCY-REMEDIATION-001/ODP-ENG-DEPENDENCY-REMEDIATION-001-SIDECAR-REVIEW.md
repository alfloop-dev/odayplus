# ODP-ENG-DEPENDENCY-REMEDIATION-001 Sidecar Review Packet

- Sidecar task: `ODP-ENG-DEPENDENCY-REMEDIATION-001-SIDECAR-REVIEW`
- Parent task: `ODP-ENG-DEPENDENCY-REMEDIATION-001`
- Sidecar owner / reviewer: Claude2 / Claude (first pass authored by Codex2
  before the helper re-claim; second pass re-verified by Claude2)
- Parent owner / reviewer: Claude / CodexCoordinator
- Review target: PR [#708](https://github.com/alfloop-dev/odayplus/pull/708)
- Review SHA: `6fa073c0fd428086e8a6bff22bba13b5707e9d47`
- Remediation anchor: `888b6c077164a3775f6f5b7ef14b72e50662b692`
- Evidence snapshot: 2026-08-08T13:07:44Z (first pass);
  2026-08-08T13:22Z (second pass)
- Scope: support packet only; this document does not change dependency,
  runtime, registry, governance, or canonical truth.

## Executive Review Result

The six-file PR diff supports the claimed safe remediation: npm is clean,
the provider-gateway direct requirements audit is clean, the changed versions
match the evidence, the lockfile-derived SBOM gate passes, and the human
decision handback seal recomputes exactly.

This is **not evidence that all production Python dependencies are clean**.
The non-vacuous root audit still reports three `cryptography 48.0.1` findings,
the checked-in Python gate currently audits zero project packages because it
uses `pip-audit --local` inside a `uv run --with` overlay, and per-service
requirements are outside that gate. The parent packet discloses all three
facts correctly and does not sign a waiver. Review should therefore distinguish
approval of the safely remediable slice from the named human decision still
required for the remaining production findings and audit coverage defect.

## Fixed Review Boundary

GitHub reports exactly these files in PR #708:

1. `package-lock.json`
2. `uv.lock`
3. `services/provider-gateway/requirements.txt`
4. `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json`
5. `docs/evidence/completion/ODP-ENG-DEPENDENCY-REMEDIATION-001/completion_evidence.md`
6. `docs/evidence/completion/ODP-ENG-DEPENDENCY-REMEDIATION-001/HUMAN_DECISION_HANDBACK.md`

An old-base comparison (`956170de..6fa073c0`) also displays unrelated
orchestrator and sidecar files brought in by already-merged base commits. Those
files are not in GitHub's effective PR diff and are not part of this review.
`package.json` and `pyproject.toml` are also absent from the PR diff.

## Evidence Matrix

| Claim / acceptance | Independent observation at review SHA | Result |
| --- | --- | --- |
| npm findings safely remediated | `brace-expansion` resolves to `1.1.18` and `5.0.9`; `js-yaml` resolves to `4.3.1`; `package.json` is unchanged; both npm audit commands return zero findings | Supported |
| GitPython findings safely remediated | `uv.lock` resolves `gitpython` to `3.1.58`; `pyproject.toml` is unchanged | Supported |
| provider-gateway findings safely remediated | requirements contain `fastapi==0.138.1`, explicit `starlette>=1.3.1`, and unchanged `uvicorn[standard]==0.34.0`; direct requirements audit returns no known vulnerabilities | Supported, with coverage caveat below |
| SBOM matches the changed lockfiles | `tests/security/test_supply_chain_security_gate.py` passes all 13 cases; its SBOM assertion re-derives components from the live `package-lock.json` and `uv.lock` | Supported |
| remaining major-only findings have immutable handback | recorded prefix digest `ed6b3d5b...ad513` exactly matches the independently recomputed SHA-256; AI signature is explicitly absent | Supported |
| security evidence delivered | completion evidence and sealed handback are both present at review SHA | Supported |
| production dependency audit remains clean | Node and provider direct-input audits are clean, but the real root Python audit reports three `cryptography` findings | **Not literally satisfied; requires reviewer scope decision** |

## Independent Verification

Executed from the clean parent worktree at
`6fa073c0fd428086e8a6bff22bba13b5707e9d47`:

| Command / check | Observed result |
| --- | --- |
| `npm audit --omit=dev --audit-level=high` | exit 0; `found 0 vulnerabilities` |
| `npm audit` | exit 0; `found 0 vulnerabilities` |
| `uv run --with pip-audit pip-audit -r services/provider-gateway/requirements.txt --no-deps` | exit 0; `No known vulnerabilities found` |
| `uv run pytest tests/security/test_supply_chain_security_gate.py -q` | exit 0; all 13 collected tests passed |
| handback prefix SHA-256 recomputation | exact match: `ed6b3d5b1c412851b262e138df5d960af40d3fd6aa22e0e4eed87abdf65ad513` |
| parent `git status --short` after verification | clean |

The provider command emits pip-audit's warning that `--no-deps` does not audit
the transitive dependency graph. Making Starlette an explicit requirement
means the known Starlette issue is included, but this result must not be
generalized to complete transitive coverage.

## Second-Pass Re-verification (2026-08-08T13:22Z, owner Claude2)

The sidecar was helper-re-claimed after the first pass, so the new owner
re-checked the packet's load-bearing claims rather than inheriting them. The
sidecar branch was first base-advanced onto `origin/dev` (12 commits); that
range touches only frontend-build and API-health sidecar surfaces and has zero
overlap with the parent's six files, so the review boundary is unaffected.

| Re-check | Method | Result |
| --- | --- | --- |
| Review SHA still current | `gh pr view 708 --json headRefOid` | still `6fa073c0`; packet has not gone stale |
| PR file list still exactly six | `gh pr view 708 --json files` | matches the fixed boundary above |
| npm versions | `git show 6fa073c0:package-lock.json` | `brace-expansion` `1.1.18` and `5.0.9`, `js-yaml` `4.3.1` |
| GitPython version | `git show 6fa073c0:uv.lock` | `3.1.58` |
| provider requirements | `git show 6fa073c0:services/provider-gateway/requirements.txt` | `fastapi==0.138.1`, `starlette>=1.3.1`, `uvicorn[standard]==0.34.0` |
| manifests untouched | `git diff --name-only ..6fa073c0 -- package.json pyproject.toml apps/web/package.json` | empty; no declared-range change |
| handback seal | independent SHA-256 over the file content preceding the digest line | exact match to `ed6b3d5b…ad513`; also confirms the seal convention is byte-exact preceding-content, not a stripped variant |
| npm production audit | `npm audit --omit=dev --audit-level=high` | exit 0, `found 0 vulnerabilities` |
| `--local` gate coverage | `pip-audit --local -f json` vs the same without `--local` | 0 packages vs 247 — see finding 2 |
| real Python audit | `uv run --with pip-audit pip-audit -f json` | 247 packages; findings on `cryptography 48.0.1` and `gitpython 3.1.55` |
| supply-chain gate | `uv run pytest tests/security/test_supply_chain_security_gate.py -q` | 13 passed |

### Residual after merge is quantified, not just named

The non-vacuous audit was run on the sidecar branch, whose base is `dev`
**without** PR #708. It reports exactly two vulnerable packages:

- `cryptography 48.0.1` — PYSEC-2026-3552 / 3553 / 3554 (3 advisories)
- `gitpython 3.1.55` — GHSA-3f7w-8rr8-f37f, GHSA-p538-c434-8v24,
  GHSA-9rj7-rf2p-w77r, GHSA-4gmw-gg2m-w46p, GHSA-hh9p-6wh2-4mfc,
  GHSA-wvpp-8hx9-p66j, GHSA-jm78-9fvv-mhgr (7 advisories)

PR #708 moves `gitpython` to `3.1.58`, which closes all seven. So the reviewer
can treat the post-merge Python residual as **exactly the three `cryptography`
advisories already covered by the sealed handback** — no unnamed remainder is
hiding behind the vacuous gate. This is the strongest form of finding 1: the
gap is bounded and fully disclosed, not merely unmeasured.

### The SBOM test is a consistency check, not a remediation check

The 13 supply-chain tests also pass on this branch, where `uv.lock` still
carries the *pre*-remediation `gitpython 3.1.55`. That is correct behavior —
the test re-derives the SBOM from whatever lockfiles are live and asserts they
match — but it means a green run of that suite confirms SBOM/lockfile
*internal consistency* and must not be read as confirming that the remediation
itself landed. Version evidence, not the test result, carries that claim.

### Base advance did not move the gate

`dev` advanced the `Makefile` during this task, so the second pass re-checked
the target. The change is confined to `node-check` (adding
`npm run bundle:budget` and `&&` chaining); `dependency-audit` and its
`pip-audit --local` invocation are untouched. Findings 2 and 3 therefore still
hold against the current base.

## Review Findings And Residual Decisions

### 1. Root Python audit is known non-clean

The parent evidence records three open advisories on `cryptography 48.0.1`:
PYSEC-2026-3552, PYSEC-2026-3553, and PYSEC-2026-3554. The available fixes
require moving beyond the current `<49` constraint, which is also imposed by
`mlflow 3.14.0`. No AI waiver, ignore flag, or forced major upgrade appears in
the diff. This is the correct handback behavior, but it means a reviewer should
not approve a statement that the complete production Python audit is clean.

### 2. The repository Python gate is vacuous today

Both `make dependency-audit` (`Makefile:48`) and the supply-chain test
(`tests/security/test_supply_chain_security_gate.py:42`) use
`uv run --with pip-audit pip-audit --local`. The parent packet asserts this
combination sees zero project packages; the second pass reproduced it with a
hard count. `pip-audit --local -f json` reports **0 audited packages**, while
the same command without `--local` reports **247**. The gate is therefore not
merely weak — it is fully vacuous on the Python side, and it would keep
returning green no matter which advisories the project acquired.

Removing `--local` is prepared but deliberately withheld because it exposes
finding 1 and would require either a dependency decision or a human-approved,
time-bounded waiver.

### 3. Service requirements remain outside the normal gate and SBOM

The provider-gateway file was audited explicitly for this task, but normal CI
does not enumerate it or `infra/mlflow/requirements.txt`. The committed SBOM
is intentionally derived from `package-lock.json` and `uv.lock`; passing the
SBOM match test therefore does not establish coverage of those service files.
A follow-up coverage decision remains necessary.

### 4. FastAPI compatibility evidence is focused, not exhaustive

The bump from FastAPI `0.115.6` to `0.138.1` is a `0.x` minor move, for which
semantic-versioning compatibility is not guaranteed. Risk is reduced by the
narrow gateway API usage, the same target version already present in the root
environment, the owner's before/after `TestClient` parity checks, and the green
product/e2e CI. The reviewer should treat those as focused compatibility
evidence, not a universal compatibility guarantee.

## PR Gate Snapshot

At the evidence timestamp, PR #708 was open, GitHub reported it mergeable but
blocked, and its head exactly matched the review SHA. `orchestrator`, `product`,
`performance-gate`, and `product-e2e-gate` had succeeded. Only
`task-review-gate` remained pending, as expected before the parent reviewer
decision.

## Reviewer Handoff

Recommended disposition for Claude:

1. Confirm this packet faithfully summarizes the parent diff and pass it to
   the parent owner/reviewer flow as support material.
2. Do not represent the root Python production audit as clean.
3. Ask the named human authority to select and sign a handback outcome for the
   `cryptography` findings, then pair that outcome with removal of the vacuous
   `--local` gate and a decision on service-requirements coverage.
4. If the parent acceptance is interpreted narrowly as “close all safely
   remediable findings and hand back every major-only finding,” the submitted
   slice has evidence for approval. If “production dependency audit remains
   clean” is interpreted literally across Python production dependencies,
   approval should remain blocked pending the human outcome.
5. Second pass adds a decision-relevant fact for that choice: the unclean
   remainder is now measured, not merely acknowledged. A non-vacuous audit
   finds exactly three `cryptography` advisories left once PR #708's GitPython
   bump lands. A reviewer choosing the narrow reading is therefore accepting a
   bounded, named, sealed residual rather than an open-ended one.

This packet makes no approval decision for the parent task and introduces no
canonical implementation change.
