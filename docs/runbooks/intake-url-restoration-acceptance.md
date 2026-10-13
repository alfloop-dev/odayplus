# Intake inbox and detail URL restoration

Requirement: VDC-004 in
`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`.
The R7 continuous detail composition and existing controls are retained.

The production container owns one Next navigation state. The inbox receives
controlled filters rather than maintaining another global-history writer.
The first API request uses the direct link's filters. Later responses from an
older filter or client cannot overwrite the current queue.

The shell remains the sole main landmark. Detail and its live summaries have
distinct region names. Inbox rows retain pointer opening while the existing
record ID provides a native keyboard open control beside the independent
review/retry action; interactive controls are not nested.

## Navigation fields

| Field | Contract |
| --- | --- |
| `search`, `intakeMethod`, `intakeStage`, `matchOutcome`, `slaState`, `heatZoneId` | Inbox filters. Changing a filter resets `page` to 1. The API's heat-zone query is `selectedHeatZoneId`. |
| `savedView` | `all`, `needsReview`, `awaitingEntry`, `blocked`, `processing`, `ready`. |
| `viewMode` | `list` or `map`; defaults to `list`. |
| `page`, `pageSize` | Positive safe integers; page size at most 100. Invalid values use the existing defaults 1 and 10. |
| `sortBy`, `sortOrder` | Existing column keys and `asc`/`desc`; defaults `updatedAt`/`desc`. |
| `selected`, `dialog` | Exact selected intake and existing add/detail/fix/decide/assignmentSla context. Conflicting repeated IDs do not pick the first record. |
| `section` | Existing continuous detail section. Clicking/focusing within a section replaces the current URL; direct load/reload restores its scroll target. Unknown values do not become CSS selectors. |

Legacy `stage`, `outcome`, `hz`, `view`, and known `sort` values are read.
When an inbox control changes, these aliases become the deterministic long
fields. Default values are omitted. Unrelated and repeated query parameters
are preserved, including shell workspace/tab and legacy source context.
`source` remains navigation context: the current inbox API has no source-ID
filter, so this change does not claim one.
An explicit inbox `heatZoneId` takes precedence over the surrounding workspace
selection; without an explicit inbox value, the workspace selection applies.

The API currently implements `id`, `sourceId`, `stage`, and `updatedAt`
ordering. Older `submitted_at_desc`/`due_at_asc` strings and unknown long sort
keys use that API's existing default. They are not represented as captured
time or lexical SLA-state order: neither is the requested timestamp. Actual
submission/deadline sort admission remains a separate API/data requirement.

`/intake/{id}` takes precedence over query selection. A legacy
`#intake/{encoded-id}` is converted using replace navigation when no explicit
selection/path is present. Malformed or ambiguous links never choose another
record as a fallback. Other hash values and framework history state survive.

## Verification and release limits

Run the focused React suite:

```sh
npm run test --workspace=@oday-plus/web -- features/operator/network/intake
```

Run the supplemental three-width browser proof on unused loopback ports:

```sh
OPSBOARD_PORT=35402 ODP_API_PORT=35401 \
  ODP_API_BASE_URL=http://127.0.0.1:35401 \
  npx playwright test --config tests/visual/operator-intake-url.config.ts
```

For compiled runtime proof, build the web workspace with the same explicit
synthetic E2E environment as the config and set `ODP_INTAKE_URL_COMPILED=1`
on the browser command. This starts `next start` rather than the development
compiler. It does not deploy the artifact. Scroll assertions include the
existing sticky-header `scroll-padding-top` and the container's maximum
scroll range; the target must remain fully visible.

This suite uses the actual Next UI and FastAPI intake routes with explicitly
synthetic submissions and test principals. It verifies navigation at 1440,
1024, and 390 pixels, including a fresh browser context. It uses one worker
and zero retries and does not replace the fixed acceptance inventory.

Local navigation evidence is not six real-role UAT, independent VDC-005
approval, complete 40-screen visual signoff, or release authority. Formal
approval records and live runtime evidence remain pending until their actual
owners supply them. This runbook grants no production rollout.

## PostgreSQL startup regression found during verification

Parent CI also detected runtime DDL competing with an active eight-connection
approval flow. Runtime bootstrap now stores an exact SQL SHA256 marker with
the complete migration in one transaction, under the existing migration
advisory lock. Engines seeing that exact marker skip repeated DDL; production
schema validation remains enabled independently. This marker records applied
technical SQL, not workflow approval or release authority.

Changed migration bytes still require applying the migration, with the
deployment's approved migration window and privileges. This optimization does
not authorize schema changes against a live workload. The regression proof
holds a real job-table DML lock while another engine starts, and retains the
eight-connection approval/audit-chain check.

The Intake relation-only fixture now lists all required names. AVM fixtures
use the deployment's full Alembic schema, including the real OSS execution
cases that share that helper. These changes preserve the production boot
guard rather than removing missing-table checks.
