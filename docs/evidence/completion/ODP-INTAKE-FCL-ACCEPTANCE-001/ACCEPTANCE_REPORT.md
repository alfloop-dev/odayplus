---
task_id: ODP-INTAKE-FCL-ACCEPTANCE-001
artifact: independent-functional-acceptance-report
reviewed_commit: 361d0c8e8457f8c3b969f28d34b3cd8217ab00a5
reviewed_branch: task/ODP-INTAKE-FCL-INTEGRATION-001
reviewed_at: 2026-07-23
---

# Assisted Listing Intake Functional Acceptance

## Acceptance Decision

This Fleet did not implement the integrated change. The mandatory first
`git rev-parse HEAD` returned
`361d0c8e8457f8c3b969f28d34b3cd8217ab00a5`, exactly matching the requested
target on `task/ODP-INTAKE-FCL-INTEGRATION-001`.

The source trace contains 197 unique, contiguous rows from `FTR-001` through
`FTR-197`. Independent disposition counts are:

| Disposition | Count |
|---|---:|
| `PASS` | 197 |
| `FAIL` | 0 |
| `NOT_APPLICABLE` | 0 |
| `SKIP` | 0 |
| Total | 197 |

`ACCEPTANCE_MATRIX.json` is the machine-readable authority for each source
clause, prior integration claim, resolved production paths, resolved evidence
paths, exact-commit acceptance evidence, and rationale.

## Execution Method

Required commands that generate Next, Playwright, pytest, npm, or Python cache
material were run with the exact checkout exposed read-only and generated
outputs redirected to acceptance-owned temporary paths. Successful runs could
therefore exercise the checked-out production source without modifying
production code, tests, the requirement trace, execution tasks, or integration
evidence. Only retained acceptance artifacts are under
`docs/evidence/completion/ODP-INTAKE-FCL-ACCEPTANCE-001/`.

An early `uv run pytest --collect-only` preflight created an ignored `.venv`
before test collection. It was immediately removed. It changed no tracked file
and is absent from the final worktree. Two subsequent sandbox startup attempts
failed before collection or browser scenarios and are not counted as
verification runs.

## Required Reruns

| Verification | Exact result | Fail | Skip/fixme | Notes |
|---|---:|---:|---:|---|
| `python3 scripts/e2e/run_assisted_intake_functional_runtime.py` | 23/23 passed | 0 | 0 | 5.3m; real Next/API/worker |
| `python3 scripts/e2e/run_assisted_intake_functional_coverage.py` | 6/6 passed | 0 | 0 | 0 flaky, 0 retry; 2.7m |
| `npm test --workspace=@oday-plus/web` | 213/213 passed in 18 files | 0 | 0 | 12.97s |
| `npm run typecheck --workspace=@oday-plus/web` | PASS | 0 | 0 | TypeScript clean |
| `npm run build --workspace=@oday-plus/web` | PASS | 0 | 0 | Production routes emitted |
| `npx vitest run packages/openapi-client/src/index.test.ts` | 5/5 passed in 1 file | 0 | 0 | 671ms |
| `npm run typecheck --workspace=@oday-plus/openapi-client` | PASS | 0 | 0 | TypeScript clean |
| Backend contract/integration/unit selection below | 182 passed | 0 | 0 | 14 `requires_live_env` deselected |
| `python3 scripts/validate_assisted_listing_intake_design.py` | PASS | 0 | 0 | All evaluated checks passed |
| `python3 scripts/build_validate_assisted_listing_intake_openapi.py` | PASS | 0 | 0 | Effective OpenAPI 1.1.3 |
| Package 10 manifest SHA-256 check | 7/7 passed | 0 | 0 | All archived files verified |

The completed core runner result and retained responsive artifacts are recorded
in `reruns/runtime-command-result.json`. The coverage runner metadata records
the reviewed SHA, exit code 0, real API and real Next flags, and timestamps.
Its Playwright JSON contains six passed specs, zero errors, and retry 0. Source
inspection found no active skip, fixme, or expected-failure declaration in
either mandated browser spec or config.

The backend selection was rerun with:

```bash
python3 -m pytest -m 'not requires_live_env' \
  tests/contract/test_assisted_listing_intake_events.py \
  tests/contract/test_assisted_listing_intake_schema.py \
  tests/contract/test_assisted_listing_intake_states.py \
  tests/contract/test_assisted_listing_openapi.py \
  tests/contract/test_assisted_listing_operations.py \
  tests/contract/test_assisted_listing_promotion_api.py \
  tests/contract/test_assisted_listing_v1_runtime.py \
  tests/contract/test_operator_assisted_listing_api.py \
  tests/integration/test_assisted_listing_evidence_export.py \
  tests/integration/test_assisted_listing_functional_runtime.py \
  tests/integration/test_assisted_listing_identity.py \
  tests/integration/test_assisted_listing_intake_outbox.py \
  tests/integration/test_assisted_listing_intake_persistence.py \
  tests/integration/test_assisted_listing_intake_worker.py \
  tests/integration/test_assisted_listing_promotion.py \
  tests/integration/test_assisted_listing_snapshots.py \
  tests/unit/listing/test_intake_state_machines.py \
  tests/unit/listing/test_identity_graph.py \
  tests/unit/test_assisted_intake_durable_stream_merge.py \
  tests/unit/test_assisted_intake_failure_variants.py
```

Result: 182 passed, 14 deselected, 0 failed, and 0 runtime-skipped in 173.56s.
The 14 deselections are repository-marked live PostgreSQL cases: 13 schema
cases and one snapshot case. They are environment exclusions, not failures.
The sole warning is the existing Starlette/httpx deprecation.

## Production Graph

The production import graph is mounted and build-reachable:

- `/w/expansion/listings` mounts `ExpansionWorkspace`, `ListingsPage`, and
  `AssistedIntakeSection`.
- `/w/expansion/listings/intake/[intakeId]` mounts the durable
  `AssistedIntakeDetailPage`; the legacy intake route only preserves query and
  redirects to it.
- `/w/expansion/listings/[listingId]` mounts `ExistingListingDetailPage` for
  authoritative exact-duplicate navigation.
- `AssistedIntakeSection` mounts the Inbox, processing detail, parsed review,
  identity decision, assignment/SLA, evidence, receipts, audit, recovery,
  promotion, and SiteScore job surfaces.
- `intakeClient.ts` calls the generated `@oday-plus/openapi-client` operations
  for reads and commands. Its missing-client path fails explicitly and supplies
  no fixture or simulated production data.
- `apps/api/oday_api/main.py` mounts the assisted-intake router and injects the
  production listing service and operator intake repository.
- The worker consumes `assisted-listing-intake`, records a `RUN` receipt before
  stage work, enforces source policy, avoids retrieval for assisted-only and
  exact-duplicate paths, and persists failure/DLQ outcomes.

The retained production route manifest confirms the three canonical listing
routes. The successful production build emitted only pre-existing Autoprefixer
warnings in unrelated `designAligned`, `governance`, and
`networkFindAreas` CSS.

## Persisted Effects

`SqliteEngine.transaction()` uses `BEGIN IMMEDIATE`; `SqliteDocumentStore` and
`DurableAssistedIntakeRepository` perform atomic updates that union append-only
histories and prevent stale scalar projections from rolling state backward.
The concurrency regression uses separate SQLite engines and threads to prove
that API/worker receipts and history are not lost. Failure regressions cover
authentication walls, bot challenges, permanent parser failure, and stale
source observations.

The canonical integration runtime uses a real HTTP source server, production
worker, durable repository restart, and readback of Listing revision,
identity-edge, Candidate, SiteScore job, receipt, and audit histories.
Acceptance-owned browser readbacks additionally prove:

- Original and canonical URL separation, exact-duplicate Listing routing, and
  unchanged retrieval count for the exact-duplicate short path:
  `reruns/coverage/readback/add-url-matrix.json`.
- Merge, reversal, split/unmerge, edge lineage, versions, correlations, and
  receipts: `reruns/coverage/readback/identity-graph-readback.json`.
- `SCORE_FAILED` Candidate retention, same Candidate/job reuse, successful
  replay, and identical lost-response receipt:
  `reruns/coverage/readback/promotion-score-failure-replay.json`.
- Authoritative Inbox paging, filters, saved views, result/map identity,
  durable actions, and zero unsafe direct retries:
  `reruns/coverage/readback/inbox-authoritative-coverage.json`.
- Removed page, timeout, partial/permanent parser failure, authentication wall,
  bot challenge, stale source snapshot, persisted transitions, and reason
  codes: `reruns/coverage/readback/retrieval-parser-failure-matrix.json` and
  its per-variant readbacks.

## Previously Pending Rows

- `FTR-181` is `PASS`. Review 003 names Product, System Design, Frontend,
  Accessibility, and QA; records `APPROVED_WITH_CONDITIONS`; and explicitly
  states that missing discipline outcomes remain pending. It is truthful and
  does not impersonate approval. Its functional implementation conditions were
  rechecked by the exact-commit browser, accessibility, route, and persistence
  evidence.
- `FTR-193` is `PASS`. Mounted behavior is specified and independently testable
  through the production AppShell, typed client, API, worker, and readbacks
  without guessing or fixture fallback.
- `FTR-195` is `PASS`. This evidence records the exact commit, commands,
  request/response artifacts, persisted readbacks, production route manifest,
  and 390/1024/1440 screenshots.
- `FTR-196` is `PASS`. This independent Fleet did not author the implementation
  and has rerun and signed every source row.
- `FTR-197` is `PASS`. Rows 001 through 196 all pass and every required rerun is
  green.

## Residual Notes

No functional acceptance gaps remain. Review 003 remains truthfully
`APPROVED_WITH_CONDITIONS`; its missing discipline sign-offs are a separate
release-process condition and are not represented as approved here. The 14
live-PostgreSQL marker exclusions and unrelated build warnings are recorded
above and do not represent failed or skipped functional cases.

## Row Dispositions

Every row below has a corresponding full source clause, production path set,
evidence path set, and rationale in `ACCEPTANCE_MATRIX.json`.

| Requirement | Disposition | Acceptance evidence |
|---|---|---|
| `FTR-001` | `PASS` | `A-BUILD`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-002` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-003` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-004` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-005` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-006` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-007` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-008` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-009` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-010` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-011` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-012` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-013` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-014` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-DESIGN`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-015` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-016` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-017` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH` |
| `FTR-018` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-019` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-020` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-021` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-022` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-023` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-024` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-025` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-026` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-027` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-028` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-029` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-030` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-031` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-032` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-033` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH` |
| `FTR-034` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-035` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-036` | `PASS` | `A-BACKEND`, `A-BUILD`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-037` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-038` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-039` | `PASS` | `A-BACKEND`, `A-BUILD`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-040` | `PASS` | `A-BACKEND`, `A-BUILD`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-041` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-042` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-043` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-044` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH` |
| `FTR-045` | `PASS` | `A-COMMIT`, `A-COVERAGE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-046` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-047` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-048` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-049` | `PASS` | `A-COMMIT`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-050` | `PASS` | `A-COMMIT`, `A-COVERAGE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-051` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-052` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-053` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-054` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-055` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-056` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-057` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-058` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-059` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-060` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-061` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-062` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-063` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-064` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-065` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-066` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-067` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-068` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-069` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-070` | `PASS` | `A-COMMIT`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-071` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-072` | `PASS` | `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-073` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-074` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-075` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-076` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-077` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-078` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-079` | `PASS` | `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-080` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-081` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-082` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-083` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-084` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-085` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-086` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-087` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-088` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-089` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-090` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-091` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-092` | `PASS` | `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-093` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-094` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-095` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-096` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-097` | `PASS` | `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-098` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-099` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-100` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-101` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-102` | `PASS` | `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-103` | `PASS` | `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-104` | `PASS` | `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-105` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-106` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-107` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-108` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-109` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-110` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-111` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-112` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-113` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-114` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-115` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-116` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-117` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-118` | `PASS` | `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-119` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-120` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-121` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-122` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-123` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-124` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS`, `A-WEB` |
| `FTR-125` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-126` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-127` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-128` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-129` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH` |
| `FTR-130` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-131` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-132` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-133` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-134` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-DESIGN`, `A-PERSISTENCE`, `A-ROUTE-GRAPH` |
| `FTR-135` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-136` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-137` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-138` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-139` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-140` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-141` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH` |
| `FTR-142` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-143` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-144` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-145` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-146` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-147` | `PASS` | `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-148` | `PASS` | `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-149` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-150` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-151` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-152` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-153` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-154` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-155` | `PASS` | `A-COMMIT`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-156` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-DESIGN`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-157` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-DESIGN`, `A-PERSISTENCE`, `A-ROUTE-GRAPH` |
| `FTR-158` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-159` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-160` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-161` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-162` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-163` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-164` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-165` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-166` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-167` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-168` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-169` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-170` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-171` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-172` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-173` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-174` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-175` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-176` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-177` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-178` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-179` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-180` | `PASS` | `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-181` | `PASS` | `A-ACCEPTANCE`, `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-182` | `PASS` | `A-BUILD`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-183` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-184` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-185` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-186` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-187` | `PASS` | `A-BACKEND`, `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH` |
| `FTR-188` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-189` | `PASS` | `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH` |
| `FTR-190` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH` |
| `FTR-191` | `PASS` | `A-BUILD`, `A-COMMIT`, `A-CORE-E2E`, `A-ROUTE-GRAPH`, `A-WEB` |
| `FTR-192` | `PASS` | `A-BACKEND`, `A-BUILD`, `A-CLIENT`, `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-193` | `PASS` | `A-ACCEPTANCE`, `A-BUILD`, `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH` |
| `FTR-194` | `PASS` | `A-BACKEND`, `A-CLIENT`, `A-COMMIT`, `A-DESIGN`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-195` | `PASS` | `A-ACCEPTANCE`, `A-BUILD`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-196` | `PASS` | `A-ACCEPTANCE`, `A-BUILD`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |
| `FTR-197` | `PASS` | `A-ACCEPTANCE`, `A-BUILD`, `A-CLIENT`, `A-COMMIT`, `A-CORE-E2E`, `A-COVERAGE-E2E`, `A-PERSISTENCE`, `A-ROUTE-GRAPH`, `A-VALIDATORS` |

Final disposition: FUNCTIONALLY_COMPLETE
