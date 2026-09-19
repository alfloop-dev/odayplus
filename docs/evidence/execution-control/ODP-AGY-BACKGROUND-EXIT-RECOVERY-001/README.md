# agy background command recovery

The old single-prompt CLI could return exit 0 after terminating a background
command. Owner reassignment and dispatch notes could then be mistaken for task
progress, while the same dirty checkout was repeatedly handed back.

## Implementation

The Antigravity adapter now invokes `agy_session.py` from its immutable runtime.
It preserves the selected binary, model, account environment, permission options,
and native print timeout. The transport uses agy 1.2.7's supported
`--input-format stream-json --output-format stream-json --print=` interface and
keeps stdin open until the result and terminal tool events are observed. It
closes the stream only after that point. A prematurely yielded result can receive
at most two requests to await existing handles; commands are not relaunched.

The session saves lifecycle-only receipts next to the worker runner status as
`<status-path>.agy.json`. Each command exit code comes from the native session's
terminal header, not assistant prose or tool stdout. Unknown/cancelled command
results, missing results and malformed events cannot become successful worker
exits. A known nonzero command exit remains recorded as nonzero; it need not fail
a whole development session in which the agent subsequently fixes the test.

Progress excludes owner, notes, title and priority. Real head/artifact/PR changes
and lifecycle decisions still count. Reassignment preserves failure history;
real sealed progress clears it. Repeated identical head/dirt/reason handoffs
share a budget across owner aliases and use the existing `after_attempts` setting.
Files are neither discarded nor admitted for review without a clean handoff.

Failed native stream session results preserve provider quota/auth diagnostics.
Tool output and successful response quotations are excluded from that authority.

## Evidence and limits

`native-command-canary.json` records actual short success, a 12-second success,
and an intentional command exit 7. `native-cancel-canary.json` records a cancelled
native command: the CLI itself returned 0, while the transport returned 75 and
classified its unavailable terminal command result as interrupted.

These are local transport probes. They do not assert that a product acceptance
passed or that the production Supervisor has already loaded this patch. Runtime
promotion and live task progression are separate post-merge checks.

Local verification: the full required tooling suite completed with 3,119 tests
and 689 subtests passing (6 skipped, 10 deselected), exit 0 in 382.87 seconds.
Ten session subprocess regressions also passed after cleanup hardening. Ruff,
config schema and all 190 config wiring checks passed. `local-verification.json`
records commands and content hashes; GitHub CI remains the immutable final-head
validation required before merge. A subsequent provider-error compatibility
check passed all 91 failure-policy tests and 23 subtests in 18.67 seconds.

## Deployment

Merge the existing PR #1347 using the repository's pure-development-tooling scope
and required CI gates. Prepare a clean source at the merged `origin/dev`, then use
`scripts/orchestrator/rollout_supervisor_runtime.py` with the existing canonical
status root, stable runtime symlink and watchdog PID file. Do not modify a running
runtime or install a second Supervisor. Verify the loaded SHA, heartbeat and a
new agy worker's session receipt. Retain the previous runtime for rollback.
