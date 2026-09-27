# Gate 0 (Code Gate) — receipt for candidate 355a94b52b14badc236be4b3e52eb936a7075549

- **Task**: `ODP-DEV-MIGRATION-RELEASE-REBUILD-001`
- **Recorded by**: Antigravity3 (rebuild owner)
- **Gate owner of record**: Codex2 / reviewer Claude
- **Result**: pass
- **Candidate**: `355a94b52b14badc236be4b3e52eb936a7075549`
- **CI run**: [36311413006](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006) on that exact head
- **Build run**: [36313147910](https://github.com/alfloop-dev/odayplus/actions/runs/36313147910)

## Measurement on this candidate

CI run [36311413006](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006) completed with conclusion `success` on candidate `355a94b52b14badc236be4b3e52eb936a7075549`.

`product-node` job [108597973671](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006/job/108597973671):
- ESLint and node checks pass
- Edge Runtime warnings: **0**
- `product-lint-unit` job [108597973644](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006/job/108597973644): success
- `product-db` job [108597973668](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006/job/108597973668): success (including `tests/ops/test_alembic_version_isolation.py`)

## Required checks

| Criterion | Evidence on this candidate |
|---|---|
| lint and format checks pass | `product-node`: `✔ No ESLint warnings or errors`; `product-lint-unit`: success |
| static/type checks pass | `npm run typecheck` inside `make node-check`: success |
| unit tests pass for changed backend and domain logic | `product-lint-unit`: success |
| component tests pass for changed frontend surfaces | `npm run test --workspace=apps/web` inside `make node-check`: success |
| build artifacts immutable and traceable to the candidate SHA | Runtime Release run 36313147910 published `RELEASE_MANIFEST.json` with `candidate_sha` = `355a94b52b14badc236be4b3e52eb936a7075549` and four image digests; see `runtime-release-images.json` |
| **C1: zero build warnings** | **0 Edge Runtime warnings, `✓ Compiled successfully`** |

## What this receipt does not claim

It covers the dev admission boundary only. It says nothing about staging or production
admission, and it is not a deployment receipt — nothing was deployed by the run it cites.
