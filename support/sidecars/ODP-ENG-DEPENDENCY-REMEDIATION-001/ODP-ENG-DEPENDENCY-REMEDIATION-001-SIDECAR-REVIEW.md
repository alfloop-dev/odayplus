# ODP-ENG-DEPENDENCY-REMEDIATION-001 Sidecar Review Packet

- Sidecar task: `ODP-ENG-DEPENDENCY-REMEDIATION-001-SIDECAR-REVIEW`
- Parent task: `ODP-ENG-DEPENDENCY-REMEDIATION-001`
- Sidecar owner / reviewer: Codex2 / Claude
- Parent owner / reviewer: Claude / CodexCoordinator
- Review target: PR [#708](https://github.com/alfloop-dev/odayplus/pull/708)
- Review SHA: `6fa073c0fd428086e8a6bff22bba13b5707e9d47`
- Remediation anchor: `888b6c077164a3775f6f5b7ef14b72e50662b692`
- Evidence snapshot: 2026-08-08T13:07:44Z
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

## Review Findings And Residual Decisions

### 1. Root Python audit is known non-clean

The parent evidence records three open advisories on `cryptography 48.0.1`:
PYSEC-2026-3552, PYSEC-2026-3553, and PYSEC-2026-3554. The available fixes
require moving beyond the current `<49` constraint, which is also imposed by
`mlflow 3.14.0`. No AI waiver, ignore flag, or forced major upgrade appears in
the diff. This is the correct handback behavior, but it means a reviewer should
not approve a statement that the complete production Python audit is clean.

### 2. The repository Python gate is vacuous today

Both `make dependency-audit` and the supply-chain test use
`uv run --with pip-audit pip-audit --local`. The parent packet demonstrates
that this combination sees zero project packages. Removing `--local` is
prepared but deliberately withheld because it exposes finding 1 and would
require either a dependency decision or a human-approved, time-bounded waiver.

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

This packet makes no approval decision for the parent task and introduces no
canonical implementation change.
