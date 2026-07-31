# Execution Control Evidence: ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001

- **Task ID**: `ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001`
- **Title**: 補齊 Supervisor failure-loop 自動轉派覆蓋
- **Owner**: `Antigravity7`
- **Reviewer**: `Codex6`
- **Phase**: `P0 Execution Control`
- **Date**: `2026-07-31`

---

## 1. Executive Summary & Root Cause Context

### Background
During live supervision, task `ODP-PLAN-SITESCORE-OUTCOME-001` assigned to `Antigravity4` reached terminal failure threshold=2. While dispatch correctly stopped on `Antigravity4`, `.orchestrator/config.json` (and `config.example.json`) lacked explicit `owner_fallbacks` and `reviewer_fallbacks` entries for `Antigravity3` through `Antigravity7` and several Codex/Claude/Gemini aliases. As a result, the supervisor found no viable fallback target and could not auto-reassign the task, leaving it in a permanent failure-loop deadlock until manual intervention.

### Solution
1. **Full Configured Agent Matrix**: Expanded `config.example.json` and `supervisor.py` default fallback mappings to cover all enabled auto-dispatch agents (`Antigravity1-7`, `Claude1-3`, `Codex1-9`, `CodexCoordinator`, `Gemini1-2`, `Copilot`).
2. **Dynamic Fallback Derivation**: Implemented `get_agent_reassignment_candidates` in `.orchestrator/supervisor.py`. If an agent is missing explicit entries in `owner_fallbacks` or `reviewer_fallbacks`, the supervisor dynamically derives deterministic candidate fallbacks (prioritizing same-family agents then complementary families).
3. **Fail-Closed Safeguards**:
   - `Human/Ops` human-gate tasks and targets are strictly excluded from automated worker failure reassignment.
   - Target agents are verified to be dispatchable, active (not paused/quota-terminal/auth-paused), and distinct from owner and reviewer (`owner != reviewer`, `target != failing_agent`).
   - Task metadata, branch/worktree, and acceptance criteria are preserved; in-progress tasks return to status `todo` so replacement workers start fresh runs.

---

## 2. Complete Agent Fallback Matrix

| Agent Alias | Provider | Owner Fallbacks | Reviewer Fallbacks |
| :--- | :--- | :--- | :--- |
| **Antigravity** | `antigravity` | Antigravity2, Antigravity3, Antigravity4, Antigravity5, Antigravity6, Antigravity7, Codex2, Codex, Codex6, Claude2, Claude | Same as Owner |
| **Antigravity2** | `antigravity2` | Antigravity, Antigravity3, Antigravity4, Antigravity5, Antigravity6, Antigravity7, Codex2, Codex, Codex6, Claude2, Claude | Same as Owner |
| **Antigravity3** | `antigravity` | Antigravity4, Antigravity5, Antigravity6, Antigravity7, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Antigravity4** | `antigravity` | Antigravity3, Antigravity5, Antigravity6, Antigravity7, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Antigravity5** | `antigravity` | Antigravity6, Antigravity7, Antigravity4, Antigravity3, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Antigravity6** | `antigravity` | Antigravity5, Antigravity7, Antigravity4, Antigravity3, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Antigravity7** | `antigravity` | Antigravity6, Antigravity5, Antigravity4, Antigravity3, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Claude** | `claude` | Claude2, Claude3, Codex, Codex2, Codex6, Antigravity, Antigravity2, Antigravity3 | Same as Owner |
| **Claude2** | `claude` | Claude, Claude3, Codex, Codex2, Codex6, Antigravity, Antigravity2, Antigravity3 | Same as Owner |
| **Claude3** | `claude` | Claude2, Claude, Codex, Codex2, Codex6, Antigravity, Antigravity2, Antigravity3 | Same as Owner |
| **Codex** | `codex` | Codex2, Codex6, Codex3, Codex4, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Codex2** | `codex` | Codex, Codex6, Codex3, Codex4, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Codex3** | `codex` | Codex2, Codex6, Codex, Codex4, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity | Same as Owner |
| **Codex4** | `codex` | Codex2, Codex6, Codex, Codex3, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity | Same as Owner |
| **Codex5** | `codex` | Codex6, Codex2, Codex, Codex8, Codex9, Claude, Claude2, Antigravity3, Antigravity4 | Same as Owner |
| **Codex6** | `codex` | Codex2, Codex, Codex8, Codex9, Claude2, Claude, Antigravity3, Antigravity7 | Same as Owner |
| **Codex7** | `codex` | Codex6, Codex2, Codex, Codex8, Codex9, Claude, Claude2, Antigravity | Same as Owner |
| **Codex8** | `codex` | Codex9, Codex6, Codex2, Codex, Claude2, Claude, Antigravity3, Antigravity7 | Same as Owner |
| **Codex9** | `codex` | Codex8, Codex6, Codex2, Codex, Claude2, Claude, Antigravity3, Antigravity7 | Same as Owner |
| **CodexCoordinator** | `codex` | Codex6, Codex2, Codex, Codex8, Codex9, Claude2, Claude, Antigravity7 | Same as Owner |
| **Gemini** | `gemini` | Gemini2, Codex, Codex2, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Gemini2** | `gemini` | Gemini, Codex, Codex2, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Copilot** | `copilot` | Codex, Codex2, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Human/Ops** | `human` | *N/A (Fail-Closed Human Gate)* | *N/A (Fail-Closed Human Gate)* |

---

## 3. Failure-Loop Drill & Test Verification Receipts

### Test Execution Command
```bash
PYTHONPATH=.orchestrator python3 -m unittest test_supervisor test_model_rotation
python3 .orchestrator/doctor.py
git diff --check
```

### Verification Output
```text
Ran 259 tests in 1.103s - OK

Supervisor Doctor:
- Workspace & Providers verified clean (claude, gemini, codex, copilot, grok)
- Exit code: 0

Git Diff Check:
- Clean (no whitespace or line-ending anomalies)
```

### Key Test Drill Assertions Verified
1. `test_full_agent_matrix_coverage_all_configured_agents`: Validates non-empty fallback lists for all 23 agent display names with no self-references or `Human/Ops` inclusion.
2. `test_dynamic_fallback_derivation_when_agent_missing_from_config`: Proves that an unlisted agent dynamically derives same-family and complementary-family fallbacks.
3. `test_failure_loop_reassignment_drill_owner`: Proves that a task reaching terminal failure on `Antigravity4` reassigns ownership to a viable target, resets task status to `todo`, and clears failure streaks.
4. `test_failure_loop_reassignment_drill_reviewer`: Proves that a task reaching terminal failure on `Antigravity7` reassigns reviewer role to an eligible target without corrupting owner or status.
5. `test_fail_closed_human_ops_gate_never_auto_reassigned`: Proves `Human/Ops` tasks return `None` and are never auto-reassigned.
6. `test_fail_closed_never_reassigns_to_human_ops`: Proves `Human/Ops` is never selected as a target.
7. `test_fail_closed_skips_paused_agents`: Proves paused/disabled agents are skipped during fallback selection.

---

## 4. Post-Drain Supervisor Rollout & Restart Protocol

> [!IMPORTANT]
> Do NOT restart the live Supervisor while active workers (`OSS`, `Observability`, `Control-Pack review`, or `SiteScore`) are running. First prepare code/config/tests and exact-head PR handoff.

### Post-Drain Runtime Rollout Sequence
1. **Worker Drain Check**: Verify all active worker tasks have completed or reached safe checkpoints.
2. **Merge & Pull**: Merge task PR into `dev` tip.
3. **Deploy Config Update**: Copy updated `owner_fallbacks` and `reviewer_fallbacks` from `.orchestrator/config.example.json` into `/home/lupin/oday-plus-supervisor-live/.orchestrator/config.json`.
4. **Restart Supervisor**:
   ```bash
   sudo systemctl restart pantheon-supervisor
   ```
5. **Verify Live PID & Health**:
   ```bash
   systemctl status pantheon-supervisor
   python3 scripts/supervisor_runtime_health.py
   ```
