---
task_id: ODP-INTAKE-UX-QA-001
title: Assisted Listing Intake UI QA Completion Evidence
status: awaiting-independent-review
owner: Codex
reviewer: Antigravity5
base_anchor: 82ccc4a0c119e93ed7dc967048a7745264118084
runtime_tested_commit: a482a0eec3e72b94954df09d397316b699663b28
updated_at: 2026-07-22
---

# Assisted Listing Intake UI QA Completion Evidence

## 1. Scope and Baseline

This evidence closes the runtime QA gates for the Claude Design Package 10
implementation. It does not replace the product, system-design, or visual
contracts and does not claim that production rollout gates are approved.

Package 10 is archived at:

`docs_archive/00_source_zips/operator_console/r7-20260720-package-10/`

| Artifact | SHA-256 |
|---|---|
| Source zip | `d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454c983645d7f8` |
| Claude Design interactive HTML | `cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d` |
| Standalone HTML | `1aefb8068faa39666599ceeafe74ba24f1ddc8abd57ba9a6513a724abaee7d0f` |

The binding visual review is
`ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`
with disposition `APPROVED_WITH_CONDITIONS`. This task verifies the Package 10
conditions assigned to QA, especially `VDC-002` and `VDC-003`, and exercises
the composed implementation delivered by the six feature fleets.

## 2. Exact Runtime Target

| Item | Value |
|---|---|
| Base anchor | `82ccc4a0c119e93ed7dc967048a7745264118084` |
| Runtime-tested commit | `a482a0eec3e72b94954df09d397316b699663b28` |
| Playwright | `1.61.1` |
| Browser | Google Chrome for Testing `149.0.7827.55` |
| API | real FastAPI routes at `127.0.0.1:8099` |
| Web | Next.js runtime at `127.0.0.1:3100` |

The evidence-document commit is expected to be a documentation-only
descendant of the runtime-tested commit. Runtime conclusions remain bound to
the exact commit above.

## 3. Verification Results

| Verification | Result |
|---|---|
| `npm run typecheck --workspace=@oday-plus/web` | PASS |
| `npm run build --workspace=@oday-plus/web` | PASS |
| Four focused Vitest files for detail, promotion, assignment/SLA, and identity decisions | PASS, 58 tests |
| Assisted-listing API, intake security, and operator-shell security pytest suites | PASS |
| Three required Playwright specifications, one worker, no retries | PASS, 18 tests in 3.8 minutes |
| Existing operator-shell persona regression suite | PASS, 4 tests |
| `git diff --check origin/dev...HEAD` | PASS |

The required Playwright run used:

```bash
CI=1 PATH="$PWD/.venv/bin:$PATH" npx playwright test \
  tests/e2e/operator-assisted-listing-intake.spec.ts \
  tests/e2e/operator-assisted-listing-intake-mobile.spec.ts \
  tests/e2e/operator-assisted-listing-intake-a11y.spec.ts \
  --workers=1 --retries=0 --reporter=line
```

No Assisted Listing Intake route was stubbed by Playwright. Tests submit and
read through the real API client and assert durable IDs and persisted state.

## 4. Canonical Flow Coverage

| Flow | Runtime assertion | Result |
|---|---|---|
| Exact duplicate | Exact URL/source identity is intercepted before retrieval and opens the existing Listing | PASS |
| Assisted entry | `ASSISTED_ENTRY_ONLY` preserves the URL and validates durable manual input | PASS |
| Possible match | Human reason and risk acknowledgement are required; no automatic merge occurs | PASS |
| Promotion | An independent reviewer creates one Candidate and receives durable Candidate/SiteScore receipts | PASS |
| Score failure | `SCORE_FAILED` retains the committed Candidate and exposes the failed score job | PASS |
| Replay | Reusing the same idempotency key recovers the result and does not create a second Candidate or queue duplicate score work | PASS |

Additional real-route assertions cover approved, blocked, and unknown source
policy outcomes; retryable retrieval failure; correction persistence; read-only
governance access; unrelated-role denial; field masking; self-review denial;
version, owner, and review conflicts; stale snapshot; quarantine; retry
exhaustion/DLQ; and durable decision, listing, Candidate, SiteScore, correlation,
and audit receipts.

## 5. Responsive and Accessibility Evidence

| Gate | Result |
|---|---|
| 390 px queue and detail | No page-level horizontal overflow; complex comparison routes to desktop-required state |
| 1024 px detail | No page-level horizontal overflow; metadata and actions reflow without overlap |
| 1440 px detail | No page-level horizontal overflow; full side-by-side comparison remains visible |
| Keyboard | Dialog launch, Tab/Enter completion, Escape close, and focus return pass |
| Screen reader | Landmarks, accessible names, canonical stage labels, and comparison summary pass |
| Reduced motion | `prefers-reduced-motion` behavior passes |
| Axe | Queue and detail report zero serious or critical violations |

Screenshots:

- `screenshots/intake-390-inbox.png`
- `screenshots/intake-390-detail.png`
- `screenshots/intake-1024-detail.png`
- `screenshots/intake-1440-detail.png`

## 6. Defects Found and Closed

The QA pass found and repaired the following implementation or test defects:

1. Broad global Playwright role headers could make role-specific scenarios pass falsely.
2. The Expansion Manager test identity lacked the `site_reviewer` capability required by the approved segregation contract.
3. Fixture replay still depended on external DNS after SSRF validation.
4. The legacy Listing adapter discarded normalized address and H3 data required by promotion gates.
5. Promotion test actors used identifiers incompatible with persisted UUID contracts.
6. Promotion proposer initialization could overwrite durable reviewer state.
7. Masked fields and governance read-only behavior were not explicit enough in the UI.
8. The governance persona could not enter the Network workspace even though its backend evidence scope was read-only.
9. Read-only rows still exposed mutation-oriented action copy.
10. Responsive CSS caused page-level overflow at 1024 px and content-box overflow at 1440 px.
11. The `cs-lead` console persona was rejected by the API role-selection boundary.

Regression coverage was added for each affected contract boundary.

## 7. Build Note

One initial build attempt encountered a missing `.next` manifest while a
duplicate orchestrator worker was running Playwright and Next.js in the same
worktree. The duplicate worker was stopped, the generated `.next` cache was
removed, and the build was rerun without concurrent writers. The clean rerun
passed. This was an orchestration/cache race, not a product-code build failure.

## 8. Review Disposition

Independent review is pending from `Antigravity5`. The task must not be marked
`done` or used as release approval until the reviewer records a commit-bound
disposition. Production rollout remains separately gated by
`ODP-INTAKE-RELEASE-001`.
