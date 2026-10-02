# ODP-DEV-MIGRATION-RELEASE-REBUILD-001 — dev candidate rebuild and release gate reconciliation

- **Task ID**: `ODP-DEV-MIGRATION-RELEASE-REBUILD-001`
- **Owner**: Antigravity3 · **Reviewer**: Codex2
- **Date**: 2026-09-27 UTC
- **Candidate**: `355a94b52b14badc236be4b3e52eb936a7075549` (`origin/dev` tip)
- **Previous candidate**: `419e6bf4958269c5b9e94efcb80770e28cd54dda` (bound by `ODP-DEV-RELEASE-GATE-RECONCILIATION-006`, PR #1370)
- **Manifest digest**: `sha256:a1e3fcf6b765861dc269e3fd395b7bec6ac4b2b6f3b3207a3ffb623c6ca6dfaa`
- **Previous manifest digest**: `sha256:134cc712132155b0003d68063298d3044d5400d91244b8024b4448268c4fc678`
- **Candidate CI run**: [36311413006](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006)
- **Build run**: [Runtime Release 36313147910](https://github.com/alfloop-dev/odayplus/actions/runs/36313147910) (`success`)

## 1. Why the candidate had to move

Candidate `419e6bf4958269c5b9e94efcb80770e28cd54dda` was built prior to the fix for Dagster/ODay migration version history collision during dev rollout. The deployment attempt in run 36278150009 revealed that shared Alembic version tables caused migration lookup failure when Dagster tables were present.

The delta from `419e6bf4` to this candidate includes:

| PR | Task | What it changed |
|---|---|---|
| #1362 | `ODP-ORCH-AUTONOMOUS-RECOVERY-001` | Worker failure policy, clean worktree preservation, and autonomous recovery enhancements |
| #1372 | `ODP-MIGRATION-VERSION-TABLE-ISOLATION-001` | Alembic version table isolated from Dagster via `oday_plus_alembic_version` in `infra/db/migrations/env.py` and regression contract tests |

Because `infra/db/migrations/env.py` is a product and build-input path, the images built for `419e6bf4` cannot be reused: `check_candidate_ancestry` only tolerates evidence-path drift between the candidate and the deployed head. The artifact handoff was therefore rebuilt.

## 2. The build run

Runtime Release run [36313147910](https://github.com/alfloop-dev/odayplus/actions/runs/36313147910),
`workflow_dispatch`, `phase=build`, `environment=dev`,
`release_sha=355a94b52b14badc236be4b3e52eb936a7075549`, `initial_release_recovery=true`.

- `Validate release phase inputs` — success (10s)
- `Build once and publish the immutable artifact handoff` — success (8m5s)
- `Verify the Supervisor lease…`, `Deploy the admitted artifact…`, `Verify production watch…` — **skipped**

The three deploy jobs were skipped because no Supervisor lease was supplied. **Nothing was
deployed by this run.** `initial_release_recovery=true` was required because dev holds no
approved release; the build reads the target back and confirms all five Cloud Run targets are absent (see `initial-release-absence-readback.json`).

### Component images

| Component | Image |
|---|---|
| api | `oday-api@sha256:e68b616286c41f73fd2ef59d0b57ed9f627a0f4cc23881c8eb2c7a81fdf2b046` |
| web | `oday-web@sha256:4885d9b769ba523c03f38be69beec646561e6ecf5866569ef1d0204536e91e70` |
| worker | `oday-worker@sha256:e706224898d77d4c65e53ea70433ee1c81420d590eebc472034c9df3328965ca` |
| scheduler | `oday-scheduler@sha256:005520c0bfd4ecd7694def660919f4a56a93ff5343a946b8b17e3ea2c157e796` |
| migration | shares the `worker` image |

All four carry a Cosign `signature_refs` entry and a CycloneDX `sbom_refs` entry in the manifest.

### Artifacts committed from the run

| Artifact | Committed path | SHA-256 of the file as committed |
|---|---|---|
| `runtime-release-manifest-355a94b5…` | `docs/evidence/gates/RELEASE_MANIFEST.json` | `914b638840d99bcd2801a580b4dc041887a49e5432dc53f15a9692d50fe532a7` |
| `runtime-release-images-355a94b5…` | `runtime-release-images.json` | `732f80e4802ab186b88c24f47425209d46bc5f8df283ffef51f4f2e9a7159c5b` |
| `initial-release-absence-readback-355a94b5…` | `initial-release-absence-readback.json` | `2aa002d03085e86054f9fe592cb721f759164842e87cf7e8e0eace510dd9a954` |
| `release-environment-receipt-dev-build` | `release-environment-receipt.json` | `bc5f64a30159a41a6ba6bb02a806ecf7d2d43b20bbe8fee66120914d9a8744df` |
| `release-npm-audit-receipt-dev` | `npm-audit-receipt.json` | `b56ed3e63fce968ee993f63374d61c10aa7d24bbe5613970b49628230b172323` |
| `release-phase-receipt-dev-build` | `release-phase-receipt.json` | `ed849f9846331d3ab092dc0e159bd5599ce2ef2de13d797c35f5e8bd28fffe15` |

Note that `release.manifest_digest` in the registry is the manifest's own
`manifest_digest` field (`sha256:a1e3fcf6b765861dc269e3fd395b7bec6ac4b2b6f3b3207a3ffb623c6ca6dfaa`), which `release_manifest.compute_manifest_digest`
recomputes from the canonical payload — not the SHA-256 of the file on disk (`914b6388…`). Both are listed here so neither is mistaken for the other.

## 3. Gate disposition

Dev-admission gates gate-0 (Code) and gate-1 (Contract) are cleared against candidate `355a94b52b14badc236be4b3e52eb936a7075549`. Gate-4 (Security and Privacy) remains `blocked` pending candidate-specific human authorization and dev-toolchain vulnerability risk re-attestation. Gates 2, 3, 5, and 6 are bound to staging and production, keep their blockers, and stay `blocked`.

| Gate | Status | Receipt |
|---|---|---|
| gate-0 Code | `passed` | [`gate-0-receipt.md`](gate-0-receipt.md) |
| gate-1 Contract | `passed` | [`gate-1-receipt.md`](gate-1-receipt.md) |
| gate-4 Security and Privacy | `blocked` (dev) | [`gate-4-receipt.md`](gate-4-receipt.md) |
| gate-2 Data | `blocked` (staging) | — |
| gate-3 Model and Solver | `blocked` (production) | — |
| gate-5 E2E/Performance/UAT | `blocked` (production) | — |
| gate-6 Ops/Release/Audit | `blocked` (production) | — |

Because gate-4 is blocked and candidate-specific Human/Ops release signoff is pending, `release.decision` is fail-closed `no-go`.

## 4. The gate-4 disposition and authorization scope, stated plainly

1. **Dev-toolchain vulnerability risk acceptance**:
   The risk acceptance recorded in `docs/evidence/human-decisions/ODP-HUMAN-DECISION-RECORDS-001/2026-09-18-dev-toolchain-vulnerability-risk-acceptance.md` (covering 1 high and 2 moderate dev-only findings) is explicitly scoped to baseline `dc0eb370b29e50f2fc916e008bdab3d08e0a3ddc` and excludes other baselines. Candidate `355a94b52b14badc236be4b3e52eb936a7075549` requires a candidate-applicable human risk acceptance / waiver before gate-4 can be cleared.

2. **Candidate-specific release authorization**:
   The registry does not copy or redate human signoff without a candidate-applicable decision from Human/Ops. Release decision remains fail-closed `no-go`.

3. **OSS licence compliance**:
   The four LGPL cases (LGPL-SHARP-LIBVIPS, LGPL-PSYCOPG2, LGPL-PSYCOPG3, LGPL-MOOCORE) are conditionally approved by the named operator in `docs/evidence/human-decisions/ODP-HUMAN-DECISION-RECORDS-001/2026-09-18-oss-license-four-lgpl-cases.md`. An external authoritative receipt (H01) remains pending; `license_policy.json` remains `proposed` and `license_exemptions.json` remains empty.

The full reasoning and measurement details are in [`gate-4-receipt.md`](gate-4-receipt.md).

## 5. What this task did not do

- No deployment. No Supervisor lease was requested, issued or supplied.
- No edit to `docs/security/`.
- No change to any gate bound to staging or production.
- No database mutation, revision stamp reset, or live traffic change.
- No fabricated or extended human approvals: `release.decision` is `no-go` and gate-4 is blocked pending candidate-specific human inputs.
