# Isolated response-boundary repair after sixth review

Task: `ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001`
Base: rejected `a1294dbe62b6259d70823fb5e252ed476758cbc0`, containing
`origin/dev` `359e480a46df56940cebef1346dfd5f855626b9e`.

This is a foreground source-only repair on a separate branch, not owner
reenrollment, removal of the six-reopen Human/Ops hold, canonical resubmission,
independent approval or task completion. Original allocated worktree, PR1459,
rejected heads, owner/reviewer history and all operational HOLDs are unchanged.
The user's continuation request does not authorize live account/secret effects.

## Repairs

1. The production identity administration audit projection refuses malformed
   recovery root/capability/binding metadata with one static policy error,
   before subject filtering. It no longer silently removes a malformed extra
   quarantine/duplicate from an otherwise valid ACK history. The mounted route
   returns403 without partial events or credential-bearing metadata. Valid
   projection/decoder/gate behavior remains unchanged.
2. The canonical native urllib client captures raw Set-Cookie multiplicity
   before reading a response body. Normal and HTTPError responses share the
   same custody/failure handling. Timeout, incomplete-body, I/O and absolute
   timer failures return status0 with no body/exception-message echo, retaining
   safely known headers only for cleanup. Native ownership is retained before
   the deadline context's final check; expired/partial replies never acknowledge
   login. Every safely known distinct session gets at most one logout, original
   cookie revocation reads and sticky UNKNOWN. No later login or ordinary
   transport is permitted after uncertainty.

## Regression scope

- Actual service + mounted audit route + canonical decoder/evaluator: valid
  control, valid ACK plus extended quarantine, extended duplicate and false
  redaction. Only audit storage and network transport are synthetic.
- Actual isolated PostgreSQL audit and authenticated route: all three recovery
  event types, extended quarantine/duplicate, missing fields and false redaction;
  subject filters cannot hide corruption and identity state remains unchanged.
- Real urllib/header parser + actual canonical evaluator: single/duplicate
  cookies followed by TimeoutError, IncompleteRead, OSError or the real absolute
  timer, on normal and HTTPError paths. Socket/DNS access is forbidden. All
  known cookies are cleaned once; UNKNOWN and gate refusal remain sticky.
- Existing recovery, invitation, provisioning, gate, native workflow, identity
  administration and production Web/PostgreSQL-store regressions must be verified
  on the clean new repair head. No existing receipt is transferred to that SHA.

## Diagnostic failures retained (not final-head evidence)

Initial focused run exited1: the new HTTPError test double lacked the real
response's `.closed` property, causing an AttributeError before body I/O. The
fixture was corrected; all16 partial-response compositions then passed. Other
focused projection/PG cases passed in that initial run. Initial Ruff import-order
failure was corrected; changed-file Ruff and code-boundary checks then passed.
The first broad regression run was interrupted by the harness's300s timeout at
about60%, with no terminal suite result. It is NOT a passing suite receipt; a
fresh immutable-head run with a sufficient finite budget is required.

Frozen Python dependencies are installed with unchanged `uv.lock`. Existing
same-baseline Node dependency files are locally reused with matching unchanged
`package-lock.json`; workspace links point into this isolated checkout. Nothing
installs/upgrades dependencies or lowers the300kB budget. These local artifacts,
throwaway fixture databases and synthetic cookie values are not runtime evidence.

Final command/exit/duration/log hashes are recorded externally under
`support/handoffs/recovery-response-boundary-repair-20261012/` in the foreground
status root. These are explicitly standalone repair evidence, not canonical
Manager task-verification receipts or PR1459 approval. Required hosted CI and
independent whole-composition review remain separate and unclaimed.

No live recovery, credential rotation, session revocation, bundle PUT/ACK,
consumer dispatch, deployment, IAM/role mutation, source activation, data/model
acquisition or F11/business acceptance occurred. Old quarantine/bindingNULL and
original incident cleanup UNKNOWN remain unchanged.
