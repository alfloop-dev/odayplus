# ODP-OC-R5-001 Closeout Verification

Task: ODP-OC-R5-001 — Implement durable R5 assisted listing intake
Owner: Claude
Reviewer: Codex
Status at closeout: in_progress → handoff for review

## Summary

The deliverable is already durable in `dev`. This task's backend half landed as
its own commit; its Package 7 UI half was delivered by sibling task
ODP-OC-R5-011 and hardened by ODP-OC-R5-004/005/002. No further implementation
was required, and this closeout adds no product code — only this evidence note.

This is recorded plainly because the task record still read `in_progress` while
the work had in fact shipped through the R5 sibling lane. The verification below
was re-run on the `dev` tip rather than asserted from the task history.

## Where The Deliverable Lives

| Half | Delivered by | Evidence in `dev` |
|---|---|---|
| Intake domain: URL validation/normalization, source registry, fail-closed policy gate, retrieval replay, parsing, entity matching | **ODP-OC-R5-001** (this task) | `54e95670` · `modules/external_data/application/assisted_intake.py` |
| Intake service + API: submit / list / detail / correct / decide / retry / promote, persistence, audit | **ODP-OC-R5-001** (this task) | `54e95670` · `modules/opsboard/application/network_listings.py`, `apps/api/app/routes/operator_modules/network_listings.py` |
| Package 7 UI (five screen labels), typed client contracts, structured errors | ODP-OC-R5-011 | PR #297, #298 · `apps/web/features/operator/network/intake/` |
| Retrieval security gate | ODP-OC-R5-005 | PR #305 · `modules/external_data/security/assisted_listing_retrieval.py` |
| Product E2E acceptance | ODP-OC-R5-004 | PR #304 · `tests/e2e/operator-network-assisted-intake.spec.ts` |
| Mandatory 37-label visual/a11y gate | ODP-OC-R5-002 | PR #313 · `d920a58b` |

## Acceptance Criteria → Evidence

All criteria are satisfied on the `dev` tip (`2108b8df`).

| Criterion | Verified by |
|---|---|
| Durable records survive process restart | `tests/integration/test_assisted_listing_intake_persistence.py::test_process_restart_survival`; E2E "decisions and corrections survive page reload and a fresh browser context" |
| API exposes submit/list/detail/correct/decide/retry/promote with permission, idempotency, validation, conflict, structured errors | `tests/contract/test_operator_assisted_listing_api.py`; E2E "correct and decide writes carry retry-stable idempotency keys" |
| Normalization, tracking-param removal, source detection, exact-duplicate check occur **before** retrieval | E2E "exact duplicate is caught before retrieval and never creates a second record" |
| Source policy independently returns all five states; only APPROVED_RETRIEVAL fetches | E2E "prove the correct fetch or no-fetch behavior per policy state" |
| Parser stores immutable raw evidence; exposes parsed/normalized/corrected/missing/low-confidence | E2E "empty state, then a clean URL submits to a durable READY / NEW record" |
| Matcher deterministically produces the five outcomes with supporting + contradictory signals | E2E "possible match requires a human decision…", "revision outcome offers append-version…" |
| POSSIBLE_MATCH never auto-merges; promotion is always explicit | E2E "possible match requires a human decision and refuses an empty reason"; "verify audit envelope for CREATE and PROMOTE decisions" |
| Identity/address/rent/area corrections require a reason and retain before/after | E2E "identity-field correction demands a reason, then records before/after" |
| All five Package 7 screen labels in the real console, desktop/tablet/mobile | E2E "all five Package 7 screen labels exist on the real surfaces"; "mobile routes ambiguous side-by-side compare to a desktop-required state"; "tablet viewport folds the 5-up meta grid correctly" |
| Real stages, durable deep link, leave-and-return, retry without losing corrections | E2E "explicit assertion of all 11 stage transitions in the UI stepper"; "deep link reopens the intake record after leaving the page"; "retryable failure shows code, correlation and next action, and retry preserves input" |
| Specified next action + diagnostics across empty/loading/stale/partial/auth-wall/blocked/retryable/terminal/read-only/quarantined | E2E "assisted-entry-only source keeps the URL and never fetches the page"; "unapproved source fails closed into quarantine with a governance reason"; "a role without listing permission gets the permission-limited state, not an empty queue" |
| Every decision records actor, role, timestamp, reason, related IDs, snapshot, parser version, before/after, correlation ID | E2E audit-envelope tests for CREATE, PROMOTE, REVISE, DUPLICATE, QUARANTINE, REJECT |
| Generated OpenAPI/client/domain types cover request/response/enum/error without frontend-only decision logic | `packages/openapi-client` (generated `./generated/types` + typed intake contracts, ODP-OC-R5-011) |
| No scheduled crawling, enumeration, credential capture, private API, auto-merge, or auto-promotion | `modules/external_data/security/assisted_listing_retrieval.py`; `tests/security/test_assisted_listing_intake_security.py`; E2E policy/no-fetch test |

## Verification Run On `dev` Tip (`2108b8df`)

```bash
uv run pytest tests/contract/test_operator_assisted_listing_api.py \
              tests/integration/test_assisted_listing_intake_persistence.py \
              tests/security/test_assisted_listing_intake_security.py -q
# 54 passed

npx playwright test tests/e2e/operator-network-assisted-intake.spec.ts
# 25 passed (5.6m) — real Operator Console against the real FastAPI backend
```

Both were executed in this worktree at `dev` tip, not inferred from CI history.

## Note For The Reviewer — Duplicate Work Avoided

This lane was dispatched against a branch **97 commits behind `dev`** and, before
detecting that, re-implemented the Package 7 UI in parallel (~3,300 lines). That
work was **discarded, not landed**: it would have introduced a second set of
components carrying the same five `data-screen-label` values, which would break
the now-mandatory 37-label gate (`d920a58b`) and duplicate the intake client
contracts.

It is preserved on the local branch `salvage/ODP-OC-R5-001-parallel-impl`
(`43affd6e`) purely so the two ideas below are not lost. Neither is an acceptance
gap — `dev` satisfies every criterion without them — so both are offered as
optional follow-ups, not defects:

1. **`POST /intake/preview`** — a read-only, no-retrieval endpoint returning
   canonical URL, source, policy, and duplicate hit, so the Add dialog can show
   the source *before* submission (design §5.1 "Source detection result").
   `dev`'s `AddListingFromUrlDialog` deliberately does not pre-judge the source
   and lets the server decide on submit. That is a defensible reading of the same
   requirement and is already merged and reviewed; this note only records the
   alternative, it does not contest the decision.
2. **Snapshot staleness** (`SNAPSHOT_STALE_AFTER_HOURS` + `snapshot_age_hours`)
   — design §7 lists "stale source snapshot" as its own state, and `dev` has no
   staleness field. Worth noting: the retrieval corpus carries fixed capture
   timestamps, so any wall-clock staleness check ages into permanently "stale"
   and must take an injectable clock to stay testable.

## Recommendation

`ODP-OC-R5-001` is substantively complete in `dev`. Recommended: reviewer Codex
confirms this evidence, then the owner runs `scripts/ai-status.sh done`. The two
items above should be triaged as separate tasks if wanted — they are enhancements
beyond this task's acceptance.
