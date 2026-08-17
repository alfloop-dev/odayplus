# ODP-CI-PARALLEL-PYTEST-001 Review Packet & Evidence Summary

## Packet Identity

| Field | Value |
|---|---|
| Sidecar Task | `ODP-CI-PARALLEL-PYTEST-001-SIDECAR-REVIEW` |
| Parent Task | `ODP-CI-PARALLEL-PYTEST-001` |
| Helper Kind | `review_packet` |
| Sidecar Owner / Reviewer | `Claude2` / `Antigravity4` |
| Current Parent Owner / Reviewer | `Claude` / `Antigravity2` |
| Parent Task Branch | `task/ODP-CI-PARALLEL-PYTEST-001` |
| Parent Task Approved Commit | `868d71d7928e712f7249a5d169a75a33e4c5a3ce` |
| Parent PR | [#680](https://github.com/alfloop-dev/odayplus/pull/680) — OPEN, `product` check **FAILING** |
| Packet Revision | Rev 2 (2026-08-07) — supersedes Rev 1, which was reopened for contradicting CI evidence |
| Packet Verdict | **NOT ready for parent finalization.** One blocking, deterministic CI failure is caused by this change (see § Blocking Issues). The speedup direction and isolation analysis are sound and confirmed in CI. |

This review packet provides supporting review materials, empirical speedup evidence, isolation safety analysis, and a verification matrix for parent task `ODP-CI-PARALLEL-PYTEST-001` ("Run the product test suite in parallel"). It does not edit L1 canonical architecture docs, contract schemas, or core runtime code. The parent task owner (`Claude`) and parent reviewer (`Antigravity2`) retain authority over merging and final closeout.

### What changed in Rev 2

Rev 1 was reopened because its verification matrix asserted `Verified` on claims that CI contradicts. Corrections applied:

- **V1** downgraded to **FAILED** — the `uv.lock` edit deterministically breaks the supply-chain SBOM gate. Remediation added.
- **V2** split into V2a (`-n 4` local, green) and V2b (`-n auto` CI, **red**). Rev 1 claimed both were verified; `-n auto` was never green in CI.
- **V5** downgraded to **Not verified** — the `-n 2` fallback saving was never measured.
- CI extrapolation replaced with **directly observed** CI step timings from two real runs.
- Test counts corrected: 2,749 local vs 2,772 collected in CI.
- Blocking Issues section added; the handoff section no longer implies the parent can finalize from this packet.

---

## Blocking Issues (must clear before parent finalization)

### B1 — Committed SBOM is stale relative to the new `uv.lock` (deterministic, caused by this change)

| Field | Value |
|---|---|
| Severity | **Blocking** |
| Failing test | `tests/security/test_supply_chain_security_gate.py::test_sbom_and_provenance_present_and_valid` |
| CI run | [31133848763](https://github.com/alfloop-dev/odayplus/actions/runs/31133848763), job `product` |
| Result | `1 failed, 2771 passed, 142 warnings in 551.63s (0:09:11)` |
| Assertion | `Committed sbom.json is stale and does not match the active package-lock.json or uv.lock. Run scripts/security/generate_sbom.py to regenerate it.` |

**Root cause, verified at commit `868d71d7`:**

- `scripts/security/generate_sbom.py::generate_sbom()` walks **every** `[[package]]` entry in `uv.lock` and emits one component per entry. It does not filter by dependency group, so dev-group additions are in scope.
- `uv.lock` at `868d71d7` adds `pytest-xdist 3.8.0` (line 3809) and `execnet 2.1.2` (line 1033).
- The committed SBOM at `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` is **unchanged** by this commit (`git diff --stat <merge-base> 868d71d7 -- docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` is empty) and contains **zero** occurrences of `pytest-xdist` or `execnet`.
- The gate compares `generate_sbom()["components"]` against the committed file's `components`, so the two lists now differ by exactly those two entries and the assertion fails closed.

**Not pre-existing:** the same SBOM on `origin/dev` also lacks both packages, but `dev` has no `pytest-xdist` in `uv.lock`, so `dev` is self-consistent and green (e.g. run [31137818667](https://github.com/alfloop-dev/odayplus/actions/runs/31137818667) at `bbed40dd`, success). The failure appears only on this branch.

**Remediation (parent owner):**

```bash
python3 scripts/security/generate_sbom.py
# then commit the regenerated artifact on task/ODP-CI-PARALLEL-PYTEST-001:
#   docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json
```

Note that the regenerated SBOM's `metadata.properties` embed the git SHA, so the file will differ in more than the two component entries. That is expected; the gate only compares `components`.

### B2 — Parent task state already reflects the failure

`ai-status.json` for `ODP-CI-PARALLEL-PYTEST-001` is `review_approved` with
`next: "CI checks for task ODP-CI-PARALLEL-PYTEST-001 failed; resolve failing checks before finalization."`
The approved head is frozen at `868d71d7`. Clearing B1 requires a new commit on the task branch, which will invalidate that frozen `approved_head`; the parent will need a re-review round rather than a direct `done`.

---

## Executive Summary & Background

### Context & Problem Statement
The primary `product` test job in GitHub Actions CI spent the bulk of its wall-clock in one step: running the non-live product test suite serially. Since the merge queue was introduced, every pull request pays this cost at least twice (once on the PR branch check and once on the merge-queue ref).

### Observed CI evidence (directly measured, not extrapolated)

Step-level durations for `product` → `Test product code`, taken from the GitHub Actions jobs API:

| Run | Commit | Mode | Step Duration | Outcome |
|---|---|---|---|---|
| [31133699607](https://github.com/alfloop-dev/odayplus/actions/runs/31133699607) (`dev`) | `44109779` | Serial (pre-change) | 00:11:34 → 00:29:53 = **1,099s (~18.3 min)** | success |
| [31133848763](https://github.com/alfloop-dev/odayplus/actions/runs/31133848763) (PR #680) | `868d71d7` | Parallel (`-n auto`) | 00:14:00 → 00:23:16 = **556s (~9.3 min)** | **failure** (B1 only) |

- Observed CI speedup on the step: **≈1.98x**, **≈9.0 minutes saved** per `product` job run.
- pytest's own summary on the parallel run: `551.63s (0:09:11)` for 2,772 collected tests.
- The parallel run's single failure is B1 (supply-chain SBOM gate). It is unrelated to parallelism — the remaining 2,771 tests passed under multi-worker execution.

### Local benchmark (reported by the parent task; not independently re-measured here)

Measured by the parent task owner on one machine under concurrent background worker load, same commit and marker filter (`not requires_live_env and not performance`):

| Mode | Wall-Clock Time | Pass Rate | Speedup Ratio |
|---|---|---|---|
| Serial (`-n 1`) | 2,145.7s (~35.8 min) | 100% (2,749 passed, 0 failed) | 1.00x |
| Parallel (`-n 4`) | 905.6s (~15.1 min) | 100% (2,749 passed, 0 failed) | **2.37x** |

Two caveats a reviewer should carry forward:

1. **Absolute times do not transfer.** The local box was running background worker loads during both runs; only the ratio is meaningful. The parent commit body states this explicitly.
2. **Test counts differ between environments.** Local runs collect **2,749** tests; CI collects **2,772** (2,771 passed + 1 failed). The 23-test delta is an environment/collection difference that this packet did not chase down. Any claim phrased as "all 2,749 tests pass in CI" is imprecise.

The parent commit's own extrapolation (`~17.1min → ~7.2min`) was optimistic; the observed CI figures above (`18.3min → 9.3min`) are the numbers to review against.

---

## Technical Safety & Database Isolation Analysis

1. **Session-scoped database fixture isolation**
   - `intake_pg_server` in `tests/conftest.py` is `scope="session"`.
   - Under `pytest-xdist`, each worker process is a separate session and initializes its own bundled PostgreSQL instance in a dedicated `mkdtemp` data directory.
   - Workers therefore execute against isolated database instances and data directories, so cross-worker database state pollution is structurally prevented rather than merely unobserved.
   - *Overhead note*: N workers pay N PostgreSQL startups, which is the stated reason the local speedup is 2.37x rather than a linear 4.0x, and why the CI speedup is ≈1.98x.

2. **Fixed-port and live-environment test exclusion**
   - Tests binding static local TCP ports (e.g. `127.0.0.1:3100`, `:8099`) or requiring static live database URLs live under `tests/e2e` and `tests/integration`.
   - These carry the `requires_live_env` marker and are excluded by the step's filter, verified by inspection of `.github/workflows/ci.yml:119` at `868d71d7`:
     `uv run pytest -m "not requires_live_env and not performance" tests modules apps shared models -n auto`
   - The separate `product-e2e-gate` job still covers those tests serially and was green on the PR run.

---

## Parent Task Surface Changes

The parent task (`ODP-CI-PARALLEL-PYTEST-001`) modifies three files at `868d71d7`
(`git diff --stat <merge-base> 868d71d7` → 3 files, 39 insertions, 1 deletion):

| File Path | Description of Changes |
|---|---|
| `.github/workflows/ci.yml` | Appends `-n auto` to the `Test product code` pytest invocation, plus inline comments explaining the benchmark result and worker database isolation safety. |
| `pyproject.toml` | Adds `pytest-xdist>=3.6` to the `dev` dependency group. |
| `uv.lock` | Locks `pytest-xdist 3.8.0` and its transitive dependency `execnet 2.1.2`. |

**Missing from this set:** `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json`, which the supply-chain gate requires to stay in sync with `uv.lock`. See B1.

---

## Review Verification Matrix

Status legend: **Verified** = confirmed by a run or artifact cited here · **Failed** = confirmed broken · **Not verified** = plausible but unmeasured.

| ID | Verification Focus | Expected Result | Status | Evidence |
|---|---|---|---|---|
| V1 | Dependency resolution & supply-chain gate | `pytest-xdist` 3.8.0 / `execnet` 2.1.2 resolve via `uv.lock` **and** the committed SBOM stays consistent with `uv.lock`. | **Failed** | Resolution itself is fine (`uv.lock` locks both). The SBOM half fails: `test_sbom_and_provenance_present_and_valid` errors with "Committed sbom.json is stale". Fix per B1. |
| V2a | Parallel pass rate, local `-n 4` | 100% pass across the non-live product suite under 4 workers. | Verified | Parent-reported: 2,749 passed / 0 failed in 905.6s. Recorded in the `868d71d7` commit trailer `Verified:`. Not independently re-measured by this packet. |
| V2b | Parallel pass rate, CI `-n auto` | 100% pass in the GitHub Actions `product` job. | **Failed** | Run 31133848763: `1 failed, 2771 passed in 551.63s`. The single failure is V1/B1, not a parallelism defect — but `-n auto` has never been green in CI, and must not be reported as verified until B1 is fixed and the job reruns. |
| V3 | Database isolation | No inter-process PostgreSQL lock collisions or table cross-talk under parallel execution. | Verified | Structural: session-scoped fixture + per-worker `mkdtemp`. Empirical: no isolation-class failures in either the local `-n 4` run or the CI `-n auto` run (2,771 passed). |
| V4 | Static port safety | `requires_live_env` and `performance` tests stay excluded from the parallel invocation. | Verified | By inspection of `.github/workflows/ci.yml:119` at `868d71d7`; `product-e2e-gate` remained green on run 31133848763. |
| V5 | Fallback resilience | If `-n auto` hits runner memory pressure, `-n 2` is a fallback saving ~7 min. | **Not verified** | No `-n 2` run exists locally or in CI; the ~7 min figure is a projection from the parent commit body, not a measurement. Mitigating context: `-n auto` completed on the standard runner without OOM or worker crashes, so the memory-pressure scenario has not materialized. Treat V5 as a contingency plan, not a validated result. |

---

## Reviewer Handoff & Next Steps

1. **Sidecar review handoff**
   - This packet (Rev 2) is handed to sidecar reviewer `Antigravity4` for `ODP-CI-PARALLEL-PYTEST-001-SIDECAR-REVIEW`.
   - Scope check for the reviewer: support artifact only, under `support/sidecars/`. No canonical truth, contract, runtime, registry, or governance file is touched.

2. **What the parent owner / reviewer can and cannot do with this packet**
   - **Can** use §"Observed CI evidence", §"Technical Safety & Database Isolation Analysis", and V3/V4 as review support — those are confirmed.
   - **Cannot** treat this packet as clearance to finalize. PR #680 is red and the parent's own `next` field already says "resolve failing checks before finalization."

3. **Suggested sequence for the parent task**
   1. Regenerate and commit `docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` on `task/ODP-CI-PARALLEL-PYTEST-001` (B1).
   2. Let CI rerun; confirm `product` goes green and record the new `Test product code` duration — that rerun is what promotes V1 and V2b from Failed to Verified.
   3. Because the new commit invalidates the frozen `approved_head` `868d71d7`, route the parent through `re_review` rather than straight to `done` (B2).
   4. Optional, non-blocking: run `-n 2` once to substantiate or drop V5, and reconcile the 2,749 vs 2,772 collection delta.
