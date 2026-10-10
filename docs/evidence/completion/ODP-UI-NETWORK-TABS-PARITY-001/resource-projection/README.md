# Assignment / SLA contract read-token increment

Task `ODP-UI-NETWORK-TABS-PARITY-001` · Pi · reviewer Codex2 · existing PR #1440

**Bounded foundational increment, not full acceptance or review resubmission.**
This does not provision Operator Assignment/SLA resources, bridge `IN-*` to UUID
Intakes, supply transfer targets, prove durable persistence, or complete modal
1440/390 design-before-after/geometry/negative pairs. No new screenshots are
claimed for this read-contract/type layer; all full-task visual requirements remain.

## Source and changes

Resumed clean `a490e17a0bc022cabc160c6ce11c43ed8039d362` on the expected task
branch. Compared source/config with fetched `origin/dev`
`2fe3ef933794d0bb293449b74dffe9602d53f767`; the final fetch is unchanged.
Implementation and regression cases are anchored in
`02a44df9c0fcea53b939237bd7c17c5c12a83088`. `source.sha256` binds tested files.

- `/api/v1/intakes/:uuid` detail now returns nullable `assignment_version` and
  `sla_version` from the linked resources. Intake `version`/ETag remain separate;
  absent resources or absent resource tokens do not inherit the Intake token.
- The read joins require both Intake ID and resource tenant, excluding foreign
  and unscoped historical resource rows. Completed Assignments remain excluded.
  Existing Intake authorization/masking and action gates are unchanged.
- The normative IntakeDetail schema and generated effective bundle/client expose
  the optional positive resource tokens. The separate Operator `AssistedIntake`
  DTO explicitly declares optional camel-case tokens; existing safe-integer,
  matching-receipt-ID and snake-case compatibility guards are retained.
  A type declaration does **not** make the Operator service supply these fields.

## API regression scope and limits

The new HTTP contract cases exercise URL submission, real Assignment creation,
claim, detail reread, stale Intake-token transfer rejection (409), transfer with
the reread Assignment token, and another detail read. Assignment versions advance
without pretending the unchanged Intake ETag is a resource token. Other-tenant
Intake detail remains denied.

The SLA case explicitly seeds a linked process-local **fixture resource** because
this API has no SLA provisioning endpoint, then performs actual pause/resume HTTP
calls and verifies detail exposes resource versions 23 → 24 → 25 while the Intake
version is unchanged. Three unavailable cases cover foreign tenant, missing tenant
and same-tenant historical resources without versions. These are contract tests,
not Operator browser flow, production SLA creation or restart/durable API proof.

## Original terminal receipts

All accepted commands below completed through their original tool invocation and
shell exit receipt in `logs/*.exit`. No summary-polling/wait loops were used.

| Command | Receipt |
| --- | --- |
| `.venv/bin/python -m pytest tests/contract/test_assisted_listing_operations.py -q` | `api-final.exit`: 0 |
| `.venv/bin/python -m pytest tests/contract/test_assisted_listing_openapi.py -q` | `openapi-tests.exit`: 0; runtime schema/generation drift included |
| `.venv/bin/python delivery_toolchain/openapi/build_validate_assisted_listing_intake.py` | `openapi.exit`: 0 |
| `.venv/bin/python -m ruff check apps/api/app/routes/listings.py tests/contract/test_assisted_listing_operations.py` | `ruff.exit`: 0 |
| `npm test --workspace=@oday-plus/web -- features/operator/network/intake/__tests__/IntakeProcessingDetail.test.tsx` | `web-focused.exit`: 0; 34 tests |
| `npm run typecheck --workspace=@oday-plus/openapi-client` | `client-typecheck.exit`: 0 |
| `npm run typecheck --workspace=@oday-plus/web` | `web-typecheck.exit`: 0 |
| `npm run lint --workspace=@oday-plus/web` | `web-lint.exit`: 0 |
| `.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py` | `boundaries.exit`: 0 |
| `git diff --check` | `diff.exit`: 0 |

The first API invocation hit the original tool's 240s timeout; no child exit
receipt exists. Its `api-timeout.log` and `.status` are retained as **UNKNOWN**,
not passed. A separate full verification invocation with a 1200s tool bound
completed exit 0. This rerun established completion, not a test count; quiet API
logs have no count summary and no count is claimed. Web log includes existing
jsdom ECONNREFUSED navigation diagnostics but original terminal exit is 0.

`uv` was unavailable (observed shell exit 127); system Python lacked jsonpath_ng
and pytest. Used only the existing repository `.venv/bin/python`, with no host
filesystem search or dependency installation. Regeneration command:
`.venv/bin/python delivery_toolchain/openapi/generate_assisted_listing_intake_client.py`.

## Required next work

Implement genuine Assignment/SLA provisioning and shared authoritative Operator
read-model projection, with validated target identity/scope, tenant/RBAC, UUID,
If-Match, idempotency and audit gates. Do not use static targets or derive UUID
resources from `IN-*`. Then verify real writes/reload and Transfer/Pause visual
pairs/negative states; complete the existing Spatial split, Find Areas
reload-before-back, tab state/density and independent VDC checklist before final
verification and `task_finalize.sh`. This increment does not supersede earlier
resource-authority negative browser receipts or any remaining task acceptance.
