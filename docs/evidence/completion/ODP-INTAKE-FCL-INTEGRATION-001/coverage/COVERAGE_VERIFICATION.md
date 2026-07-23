# Assisted Listing Intake Functional Coverage Verification

Status: `VERIFIED`

## Runtime

- Worktree: `/tmp/oday-plus-intake-functional-integration-20260723`
- Branch: `task/ODP-INTAKE-FCL-INTEGRATION-001`
- Recorded HEAD: `47daa5918400e9823a3a2dd3bf758c46a13cab42`
- API: real Uvicorn runtime
- Web: real Next.js runtime
- Worker: real durable queue worker
- Persistence: fresh durable SQLite database per run
- Synthetic retrieval: deterministic corpus injected at the approved worker
  retrieval fetcher boundary
- Playwright workers: `1`
- Playwright retries: `0`
- Skipped/fixme/expected-failure tests: `0`

This is a shared integration worktree. Production changes were supplied by the
implementation Fleets and were not modified by the coverage Fleet.

## Command and Result

Complete supplemental functional coverage suite:

```text
ODP_INTAKE_COVERAGE_API_PORT=18389 \
ODP_INTAKE_COVERAGE_WEB_PORT=13389 \
python3 scripts/e2e/run_assisted_intake_functional_coverage.py
```

Result: `6 passed, 0 failed, 0 skipped, 0 flaky (4.1m Playwright;
5m36s runner lifecycle)`.

## Coverage Results

| Functional coverage | Result | Duration |
|---|---:|---:|
| Inbox filters, cursor history, saved view, selection, and direct workflow links | PASS | 35.902s |
| Inbox loading, transport error, read-only, and no-results states | PASS | 37.294s |
| Add URL validation, canonicalization, unsupported source, request lock, and receipt | PASS | 61.755s |
| Merge, split, unmerge, reversal, redirects, and superseded lineage | PASS | 12.377s |
| Promotion `SCORE_FAILED`, Candidate retention, replay, and lost response | PASS | 23.690s |
| Retrieval/parser failure matrix and durable error recovery | PASS | 69.675s |

## Authoritative Readbacks

### Exact duplicate

```text
historical intake match_outcome = NEW
submission receipt type = EXACT_SOURCE_IDENTITY
existing_listing_id = L-2036
navigation_target = /w/expansion/listings/L-2036
RETRIEVING transitions before/after duplicate submission = 0 / 0
```

The duplicate submission uses the authoritative receipt and existing Listing
target without rewriting historical intake outcome or starting retrieval.

### Failure and recovery matrix

```text
page removed:
  state = FAILED
  issue = ODP-INTAKE-RETRIEVAL-404
  terminal transition = MAX_RETRIES_EXHAUSTED
  next_action = REPLAY_FROM_CHECKPOINT
  worker history = RUNNING -> DEAD_LETTER

retrieval timeout:
  state = FAILED
  issue = ODP-INTAKE-RETRIEVAL-TIMEOUT
  terminal transition = MAX_RETRIES_EXHAUSTED
  next_action = REPLAY_FROM_CHECKPOINT
  worker history = RUNNING x4 -> DEAD_LETTER

parser partial:
  state = AWAITING_ASSISTED_ENTRY
  processing lineage = PARSER_PARTIAL
  UI issue = ASSISTED_ENTRY_REQUIRED
  next_action = ENTER_DATA
  worker history = RUNNING

authentication wall:
  state = FAILED
  issue = AUTH_WALL_ENCOUNTERED
  terminal transition = MAX_RETRIES_EXHAUSTED
  worker history = RUNNING -> DEAD_LETTER

bot challenge:
  state = FAILED
  issue = BOT_CHALLENGE_ENCOUNTERED
  terminal transition = MAX_RETRIES_EXHAUSTED
  worker history = RUNNING -> DEAD_LETTER

parser permanent:
  state = FAILED
  issue = PARSER_PERMANENT_FAILURE
  terminal transition = MAX_RETRIES_EXHAUSTED
  worker history = RUNNING/CHECKING_IDENTITY -> DEAD_LETTER/PARSING

stale source:
  state = NEEDS_REVIEW
  issue = STALE_SOURCE_SNAPSHOT
  next_action = REFRESH_SOURCE_OR_REVIEW
  worker history = RUNNING
```

Each code was verified in persisted API lineage and in the durable detail error
surface. The parser-partial assertion intentionally distinguishes its
diagnostic lineage code from its actionable UI issue. The schema-compatible
parser corpus and persisted worker `RUN` history are present in the generated
readbacks.

### Promotion recovery

```text
final promotion status = COMPLETED
candidate_site_id = 8b4db67e-ad6b-44a4-9af6-2ba36ba204c2
replayed job_id = 2adc3c0e-bae1-4d76-bbda-63d664b8658a
same idempotency-key response = identical durable receipt
duplicate Candidate created = no
```

## Durable Evidence

- `playwright-results.json`: canonical six-test result, exact counts/durations
- `run-metadata.json`: ports, timestamps, runtime mode, HEAD, and exit code
- `readback/`: 15 authoritative API and workflow readbacks
- `screenshots/`: six browser screenshots, one per coverage scenario
- `runtime-logs/`: API, web, and worker logs plus bounded tails
- `playwright-artifacts/`: Playwright completion metadata

No production functional defect remained in the six required coverage
scenarios at the tested shared-worktree state.
