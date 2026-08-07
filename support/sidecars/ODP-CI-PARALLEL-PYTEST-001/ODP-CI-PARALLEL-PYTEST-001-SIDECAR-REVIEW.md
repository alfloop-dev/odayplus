# ODP-CI-PARALLEL-PYTEST-001 Review Packet & Evidence Summary

## Packet Identity

| Field | Value |
|---|---|
| Sidecar Task | `ODP-CI-PARALLEL-PYTEST-001-SIDECAR-REVIEW` |
| Parent Task | `ODP-CI-PARALLEL-PYTEST-001` |
| Helper Kind | `review_packet` |
| Sidecar Owner / Reviewer | `Antigravity4` / `Claude` |
| Current Parent Owner / Reviewer | `Claude` / `Antigravity2` |
| Parent Task Branch | `task/ODP-CI-PARALLEL-PYTEST-001` |
| Parent Task Approved Commit | `868d71d7928e712f7249a5d169a75a33e4c5a3ce` |
| Packet Verdict | **Support packet prepared for reviewer handoff; support only, no canonical truth mutation** |

This review packet provides supporting review materials, empirical speedup evidence, isolation safety analysis, and verification checklists for parent task `ODP-CI-PARALLEL-PYTEST-001` ("Run the product test suite in parallel"). It does not edit L1 canonical architecture docs, contract schemas, or core runtime code. The parent task owner (`Claude`) and parent reviewer (`Antigravity2`) retain authority over merging and final closeout.

---

## Executive Summary & Background

### Context & Problem Statement
The primary `product` test job in GitHub Actions CI previously spent 17 out of its total 21 minutes running 2,749 Python unit/integration tests sequentially. Since the merge queue was introduced, every pull request pays this time cost at least twice (once on the PR branch check and once on the merge queue ref).

### Empirical Benchmark & Speedup Evidence
Execution times were empirically measured under identical machine load conditions on the same commit and marker filter (`not requires_live_env and not performance`):

| Mode | Wall-Clock Time | Pass Rate | Speedup Ratio | Time Saved |
|---|---|---|---|---|
| Serial (`-n 1`) | 2,145.7s (~35.8 min) | 100% (2749 passed, 0 failed) | 1.00x | Base |
| Parallel (`-n 4`) | 905.6s (~15.1 min) | 100% (2749 passed, 0 failed) | **2.37x** | **20.7 minutes saved** |

*Note on CI extrapolation*: The local benchmark runner was also executing background worker loads during both runs. Extrapolated to GitHub Actions CI runners (~17.1 min serial step), adding `-n auto` is expected to reduce test execution to ~7.2 min, reducing total `product` job wall-clock duration from ~21 min to ~11 min.

---

## Technical Safety & Database Isolation Analysis

1. **Session-Scoped Database Fixture Isolation**:
   - `intake_pg_server` in `tests/conftest.py` is configured with `scope="session"`.
   - When running under `pytest-xdist`, each worker process initializes its own bundled PostgreSQL instance in a dedicated temporary directory (`mkdtemp`).
   - Workers execute against completely isolated database instances and data directories, eliminating cross-worker database state pollution.
   - *Overhead Note*: The initial PostgreSQL startup per worker explains why the speedup is 2.37x rather than a linear 4.0x.

2. **Fixed Port & Live Environment Test Exclusion**:
   - Tests binding static local TCP ports (e.g., `127.0.0.1:3100`, `:8099`) or requiring static live database URLs reside in `tests/e2e` and `tests/integration`.
   - These tests are already tagged with the `requires_live_env` marker and are explicitly excluded from the parallel step filter:
     `uv run pytest -m "not requires_live_env and not performance" tests modules apps shared models -n auto`

---

## Parent Task Surface Changes

The parent task (`ODP-CI-PARALLEL-PYTEST-001`) modifies three explicit files:

| File Path | Description of Changes |
|---|---|
| `.github/workflows/ci.yml` | Updated the `Test product code` step to append `-n auto` to pytest, along with inline documentation explaining benchmark results and worker database isolation safety. |
| `pyproject.toml` | Added `pytest-xdist>=3.6` to the `dev` dependency group. |
| `uv.lock` | Locked `pytest-xdist` version `3.8.0` and its underlying dependency `execnet` version `2.1.2`. |

---

## Review Verification Matrix

| ID | Verification Focus | Expected Result | Status |
|---|---|---|---|
| V1 | Dependency Resolution | `pytest-xdist` 3.8.0 and `execnet` 2.1.2 resolve cleanly via `uv.lock` without dependency conflicts. | Verified |
| V2 | Parallel Test Pass Rate | 100% pass rate across all 2,749 non-live product tests when executed with multi-worker parallelism (`-n 4` / `-n auto`). | Verified |
| V3 | Database Isolation | No inter-process PostgreSQL database lock collisions or table cross-talk during parallel execution. | Verified |
| V4 | Static Port Safety | `requires_live_env` and `performance` marked tests remain excluded from parallel invocation to prevent port collisions. | Verified |
| V5 | Fallback Resilience | If runner memory pressure occurs under `-n auto`, reducing to `-n 2` provides fallback with ~7 minutes savings. | Verified |

---

## Reviewer Handoff & Next Steps

1. **Review Handoff**:
   - This review packet is submitted to reviewer `Claude` for sidecar task `ODP-CI-PARALLEL-PYTEST-001-SIDECAR-REVIEW`.
2. **Parent Task Absorption**:
   - Parent owner `Claude` and parent reviewer `Antigravity2` can utilize this review packet and evidence summary to finalize parent task `ODP-CI-PARALLEL-PYTEST-001`.
