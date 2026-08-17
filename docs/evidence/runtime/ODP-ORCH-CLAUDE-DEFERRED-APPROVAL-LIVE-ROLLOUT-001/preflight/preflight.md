# Preflight capture
captured_at_utc: 2026-07-29T03:26:23Z

## Rollout source
merge_sha: 647970dae975f4008633a484cde1e63187035544
supervisor_py_blob: 4c33259cec94f1ecd72f5c0bd318080907be83e4
supervisor_py_sha256_target: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
task_worktree_head: 647970dae975f4008633a484cde1e63187035544
task_worktree_file_sha256: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8

## Deployed BEFORE rollout
live_runtime_root: /home/lupin/oday-plus-supervisor-live
live_supervisor_sha256: f954e4bf19e3ee7fea3a849c033de3b96f920a6280ca7e8ccb4a20013c7c6bfa
live_head: 63656fa7a43d58cb9aaea2f47e5af43e25efed61 (ops/ODP-LIVE-SUPERVISOR-20260726)
control_launch_tree: /home/lupin/oday-plus
control_supervisor_sha256: f954e4bf19e3ee7fea3a849c033de3b96f920a6280ca7e8ccb4a20013c7c6bfa
control_head: e3f0fb84b93e76d52c81b402890dbe08226e4e6f (main)
runtime_root_pointer: /home/lupin/oday-plus-supervisor-live

## Unrelated dirty files that must be preserved
### live runtime root
   M .orchestrator/permission_broker.py
   M ai-activity-log.jsonl
   M dashboard-bundle.json
   M docs-site/dashboard-bundle.json
  ?? .orchestrator/approval-queue.lock
  ?? .orchestrator/runtime/
  ?? .orchestrator/supervisor.lock
  ?? .orchestrator/supervisor.pid
  ?? .orchestrator/worktree-dirt-backups/
  ?? ai-task-archive/
  ?? archive/
  ?? current-work.md
  ?? docs-site/ai-activity-log.jsonl
  ?? docs-site/approval-queue.json
  ?? docs-site/current-work.md
  ?? docs-site/index.html
  ?? docs-site/orchestrator-state.json
  ?? docs-site/script.js
  ?? docs-site/style.css
### control launch tree (tracked modifications only)
   M .github/workflows/ci.yml
   M .github/workflows/promote-dev-to-main.yml
   M .gitignore
   M .orchestrator/adapters/__init__.py
   M .orchestrator/adapters/antigravity.py
   M .orchestrator/adapters/claude_cli.py
   M .orchestrator/adapters/codex.py
   M .orchestrator/adapters/copilot_cloud.py
   M .orchestrator/adapters/copilot_local.py
   M .orchestrator/adapters/file_inbox.py
   M .orchestrator/adapters/gemini.py
   M .orchestrator/adapters/qwen.py
   M .orchestrator/adapters/vscode_chat.py
   M .orchestrator/approval_queue.py
   M .orchestrator/auth_loader.py
   M .orchestrator/auto_commit_archive.py
   M .orchestrator/bin/codex
   D .orchestrator/bin/gemini
   D .orchestrator/bin/gemini2
   M .orchestrator/claude_permission_prompt_mcp.py
   M .orchestrator/common.py
   M .orchestrator/config.example.json
   M .orchestrator/coordination_file_watcher.py
   M .orchestrator/copilot_login_helper.py
   M .orchestrator/cross_repo_issue_mapper.py
   M .orchestrator/github_bus.py
   M .orchestrator/github_cloud_relay.py
   M .orchestrator/github_webhook_server.py
   M .orchestrator/multi_repo_registry.py
   M .orchestrator/permission_broker.py
   M .orchestrator/provider_permissions.py
   M .orchestrator/runtime_state.py
   M .orchestrator/sidecar_cleanup.py
   M .orchestrator/supervisor.py
   M .orchestrator/supervisor_watchdog.py
   M .orchestrator/sync_provider_permissions.py
   M .orchestrator/task_archive.py
   M .orchestrator/test_adapter_fallback_policy.py
   M .orchestrator/test_dispatch_policy.py
   M .orchestrator/test_github_bus.py
   M .orchestrator/test_provider_permissions.py
   M .orchestrator/test_runtime_state.py
   M .orchestrator/test_sidecar_cleanup.py
   M .orchestrator/test_supervisor.py
   M .orchestrator/test_supervisor_watchdog.py
   M .orchestrator/watch_events.py
   M .orchestrator/wave_guards.py
   M .orchestrator/worker_runner.py
   M README.md
   M apps/web/features/map/HeatZoneMap.tsx
   M apps/web/features/map/map.module.css
   M apps/web/next-env.d.ts
   M docs/design/ODAY_PLUS_CLAUDE_DESIGN_MASTER_BRIEF.md
  AM docs/evidence/PRODUCT_GRADE_E2E_FLEET_DISPATCH_QUEUE.json
  AM docs/evidence/fleet_dispatch/ODP-EXT-004.md
  AM docs/evidence/fleet_dispatch/ODP-EXT-005.md
  AM docs/evidence/fleet_dispatch/ODP-EXT-006.md
  AM docs/evidence/fleet_dispatch/ODP-EXT-007.md
  AM docs/evidence/fleet_dispatch/ODP-EXT-008.md
   M docs_archive/README.md
   M pyproject.toml
   M scripts/ai_status.py
  AM product_ops/external_data_backfill.py
   M scripts/run-supervisor-watchdog.sh
   M scripts/run-supervisor.sh
   M scripts/supervisor_runtime_health.py
   M scripts/supervisor_watchdog_install.py
   M scripts/test_ai_status.py
   M scripts/test_supervisor_runtime_health.py
   M scripts/test_supervisor_watchdog_install.py
   M tests/e2e/e2e-map.spec.ts
   M uv.lock

## Service state
  Restart=always
  MainPID=1197865
  ExecMainStartTimestamp=Wed 2026-07-29 02:37:14 UTC
  KillMode=control-group
  ActiveState=active

## Active workers before rollout
      PID    PPID     ELAPSED COMMAND
  1197865    6884       49:08 python3 -u .orchestrator/supervisor.py --verbose
  1208697 1197865       41:52 [python3] <defunct>
  1249622 1197865       11:19 /usr/bin/python3 /home/lupin/oday-plus-supervisor-live/.orchestrator/worker_runner.py --run-id claude-20260729T031503Z-5e9cfd48 --he
  1249624 1249622       11:19 /home/lupin/.vscode-server/extensions/anthropic.claude-code-2.1.220-linux-x64/resources/native-binary/claude -p 你被喚醒了。

## STOP GATE 2 recheck (2026-07-29T04:0xZ, driver revision 3)
re_verified_deployed_live_sha256: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
re_verified_deployed_control_sha256: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
merge_commit_blob_sha256: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
bash_n_driver: OK
py_compile_assertion: OK
selftest_assertion: 11/11 PASS -> assertion-selftest.txt
selftest_driver_gates: 15/15 PASS -> driver-gate-selftest.txt (earlier notes said 16/16; the script has 15 `check` calls and the receipt has 15 `ok` lines - see the STOP GATE 3 recheck)
git_diff_check: RETRACTED - see the STOP GATE 3 recheck below. The command run
  was a bare `git diff --check`, which only inspects unstaged changes to tracked
  files; every file in this evidence directory was untracked at the time, so the
  clean exit said nothing about them.
live_state_unchanged_after_checks: MainPID=1197865 KillMode=control-group dropin=empty /tmp/odp-rollout-driver=absent

## Correction applied to this file (STOP GATE 3, driver revision 4)
Line 127 above - the captured `ps` line for the Claude worker - ended in two
literal spaces, because the worker's `-p` prompt argument ends in a full-width
`。` followed by trailing whitespace. Those two spaces were stripped so the
tree passes `git diff --check`. No other byte of the captured output was
altered; the command line itself, its pid and its elapsed time are as captured.

## STOP GATE 3 recheck (2026-07-29, driver revision 4)
scope_of_revision_4: documentation only - no gate logic, thresholds, exit codes
  or ordering changed relative to revision 3
bash_n_driver: OK
py_compile_assertion: OK
selftest_assertion: 11/11 PASS -> assertion-selftest.txt
selftest_driver_gates: 15/15 PASS -> driver-gate-selftest.txt (earlier notes said 16/16; the script has 15 `check` calls and the receipt has 15 `ok` lines - see the STOP GATE 3 recheck)
git_diff_check_against_merge_sha: `git diff --check 647970dae975f4008633a484cde1e63187035544` -> exit 0
live_state_unchanged_after_checks: see the tail of this file; the live driver
  has still never been executed

## STOP GATE 4 recheck (2026-07-29T04:2xZ, driver revision 5)
scope_of_revision_5: text only - reviewer corrected Codex4 -> Codex2 in the
  fleet brief, CONTINUATION.md and the driver's two capture-commit trailer
  templates, plus one residual `16-check` overclaim in ../README.md section 6.
  No gate logic, thresholds, exit codes or ordering changed relative to
  revision 3.
reviewer_config_check: `codex4` absent from the `agents` list of
  /home/lupin/oday-plus-supervisor-live/.orchestrator/config.json (claude,
  claude2, claude3, antigravity, antigravity2, codex, codex2, antigravity3-7)
  and of /home/lupin/oday-plus/.orchestrator/config.json (same without
  claude3); ai-status.json records owner=Claude2 reviewer=Codex2
bash_n_driver: OK
bash_n_selftest_assertion: OK
py_compile_assertion: OK
selftest_assertion: 11/11 PASS (11 `ok` lines this run)
selftest_driver_gates: 15/15 PASS (15 `ok` lines this run) -> SELFTEST PASS
git_diff_check_against_merge_sha: `git diff --check 647970dae975f4008633a484cde1e63187035544` -> exit 0
trailing_whitespace_scan: clean across the evidence dir and the fleet brief
live_state_unchanged_after_checks: MainPID=1197865 (ExecMainStartTimestamp Wed
  2026-07-29 02:37:14 UTC, ActiveState=active) KillMode=control-group
  dropin=empty /tmp/odp-rollout-driver=absent timeline/=empty
  odp-rollout transient units=none
deployed_sha256_live: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
deployed_sha256_control: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
approval_queues: live queue "No pending approvals"; control queue holds 124
  unrelated pre-existing lines and 0 matching this task id - no test approval
  has ever been created

## STOP GATE 7 recheck (2026-07-29T05:2xZ, driver revision 8)
scope_of_revision_8: one executable change, in the revision-7 clean-attempt gate
  only - `attempt_state_dirty()` was fail-open (timeline listed with
  `find -type f`, so directories and symlinks passed; signal dir probed for four
  hardcoded names, so probe-child.pid / probe-commands.txt / deadman files and
  every unknown entry passed). The allowlist is now exact and both roots are
  type-checked. Phases 1-9, the probe, the wait budget, the dead-man derivation,
  the exit codes and the phase ordering are untouched; the two new verdicts
  (`timeline-root:` / `signal-root:`) route to the existing 50 / 51.
fail_open_reproduction: revision-7 and revision-8 implementations extracted
  mechanically from git and from the working tree and run side by side over 12
  fixtures -> stop-gate-7-fail-open-reproduction.txt. Revision 7 reports CLEAN
  for 8 of them, including the reviewer's own repro
  (timeline/unexpected-receipts, signal/probe-commands.txt); revision 8 names
  the offender in every case.
phase_2_9_delta_vs_revision_3: 3 deltas, all non-executable, enumerated with a
  normalized diff -> rev3-phase-2-9-delta.txt. The "byte-identical" claim is
  withdrawn from ../README.md, ../runbook/CONTINUATION.md and the fleet brief.
bash_n_driver: OK
bash_n_selftest_assertion: OK
py_compile_assertion: OK
selftest_assertion: 11/11 PASS (11 `ok` lines this run) -> assertion-selftest.txt
selftest_driver_gates: 50/50 PASS (50 `ok` lines, 0 FAIL this run; 39 -> 50, the
  15 new checks are on the clean-attempt gate) -> driver-gate-selftest.txt
git_diff_check_against_merge_sha: `git diff --check 647970dae975f4008633a484cde1e63187035544`
  -> exit 0, run with the two new receipts already `git add`ed, i.e. tracked -
  the STOP GATE 3 finding-1 trap. The first run of it was exit 2: unified diff
  emits a lone space on blank context lines, so rev3-phase-2-9-delta.txt was
  regenerated through `sed 's/[[:space:]]*$//'`.
trailing_whitespace_scan: 0 hits across the evidence dir and the fleet brief
live_state_unchanged_after_checks: MainPID=1197865 (ExecMainStartTimestamp Wed
  2026-07-29 02:37:14 UTC, ActiveState=active) KillMode=control-group
  dropin=0 entries /home/lupin/.config/systemd/user=7 entries
  /tmp/odp-rollout-driver=absent timeline/={README.md, attempt-1-*/}
  odp-* transient units loaded=0 deadman timer/service=inactive/inactive
  watchdog timer=active
deployed_sha256_live: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
deployed_sha256_control: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
approval_queues: live queue "No pending approvals"; control queue 124 unrelated
  pre-existing lines, 0 matching this task id - no test approval has ever been
  created
window_state: still closed. The driver has never been executed past the 04:31Z
  phase-1 abort, and revision 8 must not be executed until the exact head below
  is re-cleared.

## STOP GATE 8 recheck (2026-07-29T05:4xZ, driver revision 9)
scope_of_revision_9: one executable change, confined to the phase-1 probe -
  every systemd operation there now goes through `probe_cmd` (mutating,
  PROBE_CMD_TIMEOUT_S=30) or `probe_query` (read-only, PROBE_QUERY_TIMEOUT_S=10),
  both of which preserve stdout and append a receipt line carrying label, rc,
  stdout and stderr. Receipt completeness (every required label present, count
  within the declared budget) is an input to PROBE_VERDICT. The probe no longer
  calls the shared unbounded readers at all. Phases 2-9, the clean-attempt
  allowlist, the probe's assertions, the exit codes and the phase ordering are
  untouched.
raw_call_reproduction: the revision-9 scanner run over both revisions' probe
  regions -> stop-gate-8-raw-probe-call-reproduction.txt. Revision 8:
  `raw=4 reader=6` - `show -p ControlGroup`, `reset-failed` (stderr to
  /dev/null), two `show -p MainPID`, plus six calls into load_state /
  active_state / sub_state. Revision 9: `raw=0 reader=0`. Both regions extracted
  mechanically (rev 8 out of git by line number, rev 9 between its sentinels)
  and the scanner sed-extracted from the driver, never retyped.
scanner_is_not_vacuous: --selftest runs the same scan over a fixture holding one
  raw call and one reader call and requires `raw=1 reader=1`, and over a file
  with no sentinels and requires a non-zero exit.
wait_budget: PROBE_QUERY_TIMEOUT_S and the exact call counts (PROBE_MUTATING_CALLS=7,
  PROBE_QUERY_CALLS=36) are declared and folded into the derivation:
  TOTAL_BOUNDED_WAIT_S 1388 -> 1808, DEADMAN_DELAY_S 2288 -> 2708.
  `abort_deadman_budget` (42) still enforces delay > budget.
bash_n_driver: OK
bash_n_selftest_assertion: OK
py_compile_assertion: OK
selftest_assertion: 11/11 PASS -> assertion-selftest.txt (byte-identical to the
  committed receipt, so it is not re-committed)
selftest_driver_gates: 63/63 PASS (63 `ok` lines, 0 FAIL this run; 50 -> 63 - 7
  receipt-completeness checks and 6 on the static scan) -> driver-gate-selftest.txt
probe_residue_after_selftest: `pgrep -af odp-killmode-probe` reports exactly one
  match, and it is this shell's own command line (the pattern is in its argv) -
  the same substring artefact the token-exact unmanaged-supervisor check exists
  for. `ps -eo pid,cmd | grep -c '[o]dp-killmode-probe'` counts 2: that shell and
  its grep. No probe process, unit or cgroup survives.
live_state_unchanged_after_checks: MainPID=1197865 (ExecMainStartTimestamp Wed
  2026-07-29 02:37:14 UTC, ActiveState=active) KillMode=control-group
  dropin=0 entries /home/lupin/.config/systemd/user=7 entries
  /tmp/odp-rollout-driver=absent timeline/={README.md, attempt-1-*/}
  odp-* units loaded=0 deadman timer/service=not-found/inactive
  watchdog timer=active
deployed_sha256_live: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
deployed_sha256_control: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
window_state: still closed. The driver has never been executed past the 04:31Z
  phase-1 abort, and revision 9 must not be executed until the exact head below
  is re-cleared.

## STOP GATE 9 recheck (2026-07-29T05:5xZ, driver revision 10)
scope_of_revision_10: one change, confined to the `--selftest`-only static scan
  added by revision 9. `probe_region_scan()` now returns the verdict: 0 clean,
  2 at least one violation (each still printed), 1 the region could not be
  located. Detection logic, the phase-1 probe, its receipts and budget, the
  clean-attempt allowlist, phases 2-9, the exit codes and the phase ordering are
  untouched, and the scan is still never called on the live path.
finding_confirmed_as_named: revision 9's scanner raised SystemExit only for a
  missing region. With the sentinels present it printed every violation and
  exited 0 - so the header, ../README.md, the runbook and the revision-9 commit
  message described a contract the code did not honour, and
  `probe_region_scan "$f" || fail` would have read revision 8's ten bypasses as
  clean. The committed reproduction recorded it in plain sight:
  `raw=4 reader=6` followed by `rc=0`.
exit_code_reproduction: revision 9's scanner (sed-extracted from git 0d04080c)
  and revision 10's (sed-extracted from the working tree) run over the same
  three inputs - revision 8's probe region, the current probe region, a file
  with no sentinels -> stop-gate-9-scanner-exit-code-reproduction.txt.
  Counts identical in every case; rc=0 vs rc=2 on the violating region, rc=0 vs
  rc=0 on the clean one, rc=1 vs rc=1 with no sentinels.
stop_gate_8_receipt_regenerated: stop-gate-8-raw-probe-call-reproduction.txt
  re-run against the revision-10 working tree; same extraction commands, same
  `raw=4 reader=6` / `raw=0 reader=0`, and it now ends in rc=2 where it ended in
  rc=0.
selftest_return_code_coverage: the scan's rc is asserted directly, not inferred
  from the counts - this driver 0, a routed-only fixture 0, a raw call alone 2,
  an unbounded reader alone 2, both together 2, a file with no sentinels 1. The
  routed-only fixture also pins the other direction: a call routed through
  probe_cmd must NOT be reported, or the scan would be unusable.
bash_n_driver: OK
bash_n_selftest_assertion: OK
py_compile_assertion: OK
selftest_assertion: 11/11 PASS, output identical to the committed receipt
  (assertion-selftest.txt), so it is not re-committed
selftest_driver_gates: 71/71 PASS (71 `ok` lines, 0 FAIL; 63 -> 71, all eight
  new checks on the static scan's return code) -> driver-gate-selftest.txt.
  The captured output is passed through `sed 's/[[:space:]]*$//'`, as the
  previous receipts were: `check` pads its label column, so the two lines whose
  asserted value is the empty string would otherwise carry trailing spaces and
  fail `git diff --check` once tracked (the STOP GATE 3 lesson). Nothing else is
  altered.
rev3_phase_2_9_delta: re-generated against revision 10 - slice still 479 -> 492
  raw and 389 / 389 normalized, the raw diff reproduces byte for byte, and the
  only normalized difference is still the two `Reviewer: Codex4` -> `Codex2`
  trailer lines. Revision 10 changes nothing after the phase-2 banner.
probe_residue_after_selftest: `pgrep -af odp-killmode-probe` reports exactly one
  match, this checking shell's own command line - the same substring artefact
  the token-exact unmanaged-supervisor check exists for. No probe process, unit
  or cgroup survives.
live_state_unchanged_after_checks: MainPID=1197865 (ExecMainStartTimestamp Wed
  2026-07-29 02:37:14 UTC, ActiveState=active) KillMode=control-group
  dropin=0 entries /home/lupin/.config/systemd/user=7 entries
  /tmp/odp-rollout-driver=absent timeline/={README.md, attempt-1-*/}
  odp-* units loaded=0 deadman timer/service=not-found/inactive
  watchdog timer=active
deployed_sha256_live: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
deployed_sha256_control: f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
window_state: still closed. The driver has never been executed past the 04:31Z
  phase-1 abort, and revision 10 must not be executed until this exact head is
  cleared by the coordinator.
