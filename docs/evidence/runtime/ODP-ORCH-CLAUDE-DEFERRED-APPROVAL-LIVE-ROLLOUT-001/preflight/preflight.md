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
  AM scripts/external_data_backfill.py
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
