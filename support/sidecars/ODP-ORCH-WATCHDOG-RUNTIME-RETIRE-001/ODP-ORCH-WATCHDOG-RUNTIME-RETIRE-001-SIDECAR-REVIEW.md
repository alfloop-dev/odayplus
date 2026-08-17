# Review Packet: ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001

- Sidecar task: `ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001`
- Sidecar owner: `Antigravity6`
- Assigned sidecar reviewer / parent reviewer: `Claude2`
- Evidence captured: `2026-08-07` UTC
- Parent branch: `origin/task/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001`
- Exact reviewed parent HEAD: `0b6419f4246674a97ff3c7875d3c14df0322ea95`
- Scope: review packet & evidence summary only; no L1 canonical truth or core supervisor runtime implementation modified

## Executive Disposition

The retirement of the legacy second watchdog runtime checkout (`oday-plus-supervisor-runtime-945a8366`) and the alignment of freshness monitoring for parent task `ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001` have been thoroughly audited and verified.

The parent task successfully addresses the architectural drift between the main supervisor runtime and the watchdog runtime by:
1. **Single Runtime Consolidation**: Re-pointing `pantheon-supervisor-watchdog.service` to `oday-plus-supervisor-runtime-current`, so a single rollout advances both services simultaneously.
2. **Freshness Alarm Noise Elimination**: Updating `pantheon-runtime-freshness.service` to monitor only `runtime-current`, removing an unactionable, ever-growing failure alarm (which previously drifted up to 420 commits behind).
3. **Uncommitted Work Audit & Preservation**: Preserving all 9 uncommitted files (604 lines of diff) from `945a8366` in version-controlled evidence (`docs/evidence/runtime/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/uncommitted-945a8366.diff`) after confirming that critical items (`REBASE_HEAD`, `_refresh_reused_worker_worktree`, `config_path_arg`) were already merged into `origin/dev` via PR #602.
4. **Documentation & Runbook Alignment**: Updating `docs/runbooks/supervisor-runtime-rollout.md` (§9.3 and §9.4) and creating comprehensive evidence docs under `docs/evidence/runtime/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/`.

Independent verification confirms that the parent branch operates strictly within ops documentation, systemd unit definitions, and evidence artifacts without mutating L1 canonical documents or core runtime contracts.

## Reviewed Change Surface

Compared with `origin/dev`, parent branch `origin/task/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001` touches 4 support, evidence, and runbook files:

| File Path | Change Type | Role & Audit Observation |
| --- | --- | --- |
| `docs/evidence/runtime/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/README.md` | Added | Architectural summary of watchdog retirement, rationale for freshness monitor removal, uncommitted work audit table, and operational lessons learned. |
| `docs/evidence/runtime/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/pantheon-runtime-freshness.service.txt` | Added | Reference copy of the updated systemd service unit monitoring only `runtime-current`. |
| `docs/evidence/runtime/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/uncommitted-945a8366.diff` | Added | Version-controlled 604-line diff preserved from the retired `945a8366` checkout prior to service decommission. |
| `docs/runbooks/supervisor-runtime-rollout.md` | Modified | Updated sections 9.3 and 9.4 to document the single-checkout model for watchdog, pointer fixes to version-controlled evidence, and retirement of `945a8366`. |

No L1 canonical document (`TARGET_ARCHITECTURE.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, etc.), core supervisor module (`supervisor.py`), or production runtime code was modified in this slice.

## Evidence Verification Matrix

| Verification Aspect | Expected Outcome | Verification Status | Artifact / Command Receipt |
| --- | --- | --- | --- |
| **Watchdog Unit Target** | `ExecStart` points to `runtime-current` | PASS | `systemctl --user status pantheon-supervisor-watchdog.service` |
| **Freshness Unit Target** | `ExecStart` passes only `--runtime runtime-current` | PASS | `systemctl --user status pantheon-runtime-freshness.service` |
| **Installed Unit Parity** | `~/.config/systemd/user/pantheon-runtime-freshness.service` matches evidence | PASS | `diff -u ~/.config/systemd/user/pantheon-runtime-freshness.service docs/evidence/runtime/.../pantheon-runtime-freshness.service.txt` |
| **Uncommitted Diff Preservation** | 604-line diff saved under version control | PASS | `docs/evidence/runtime/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/uncommitted-945a8366.diff` |
| **Dev Alignment Audit** | Critical fixes confirmed merged in `dev` via PR #602 | PASS | `docs/evidence/runtime/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/README.md` |
| **Runbook Pointers** | Runbook §9.4 points to version-controlled diff path | PASS | `docs/runbooks/supervisor-runtime-rollout.md` |
| **Git Hygiene** | No trailing whitespace or diff errors | PASS | `git diff --check` |

## Independent Verification at Exact Parent HEAD

The following checks were verified on the live system at parent HEAD `0b6419f4246674a97ff3c7875d3c14df0322ea95`:

```bash
# 1. Systemd service status check
systemctl --user status pantheon-supervisor-watchdog.service pantheon-runtime-freshness.service

# 2. Installed unit vs checked-in unit diff
diff -u ~/.config/systemd/user/pantheon-runtime-freshness.service \
  docs/evidence/runtime/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/pantheon-runtime-freshness.service.txt

# 3. Clean diff check
git diff --check
```

All commands executed cleanly.

## Reviewer Attention Points

1. **Exact Parent HEAD Stamp**: Stamped against exact parent HEAD `0b6419f4246674a97ff3c7875d3c14df0322ea95`.
2. **Support & Evidence Scope**: Changes are strictly confined to support sidecar markdown artifact `support/sidecars/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001/ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001-SIDECAR-REVIEW.md`. No L1 canonical files, contracts, or main runtime files were altered.
3. **Audit Trail Integrity**: The lesson "audit artifacts must live under version control, not in the audited system" has been strictly honored.

## Recommended Disposition & Handoff

- **Recommendation**: Approved review packet ready for absorption into parent task workflow.
- **Handoff Target**: `Claude2` (assigned reviewer).
- **Next Step**: Hand off task `ODP-ORCH-WATCHDOG-RUNTIME-RETIRE-001-SIDECAR-REVIEW` to `Claude2` for review.
