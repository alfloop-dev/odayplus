# Transfer target fail-closed increment

Pi · reviewer Codex2 · `ODP-UI-NETWORK-TABS-PARITY-001` · existing PR #1440

**Bounded safety increment, NOT full parity acceptance or review resubmission.**
No genuine Operator Assignment/SLA provisioning, scoped identity directory,
server target authorization, successful Transfer/Pause mutation or durable reload
is supplied here. This does not resolve those remaining acceptance conditions.

## Source and repair

Resumed clean `6994d1dc616197e61dcb0b600f84cd0405818182` on the expected task
branch; live canonical brief/status/review findings read. Fetched `origin/dev`
`2fe3ef933794d0bb293449b74dffe9602d53f767` remains unchanged at final fetch.
Checked canonical Operator and UUID route source rather than inferring repository
truth from the isolated worktree. Product/test anchor:
`945db9c95d9f41126481adfa506fc8c2ff81ba0e`; supplemental harness anchor:
`7a8dc49d51280e8f05f17af350cbc54c03a8d143`. `source.sha256` binds tested files.

- Removed runtime static `actor-mgr`, `actor-steward`, `actor-staff` and
  `gov-queue` transfer targets. Explicit caller-supplied targets default to empty;
  the production caller currently has no authoritative directory integration.
- Missing targets visibly show `TRANSFER_TARGETS_UNAVAILABLE` and disable
  selection, risk acknowledgement and submission, even with a valid Assignment
  token. A display person/queue is not an authorized UUID target.
- UUID shape, nonblank metadata and uniqueness are checked. These are **shape
  guards, not identity/tenant/scope authorization**. The future directory must
  supply fresh resource-scoped results, and backend must validate on write.
- A disappearing choice is not silently replaced with the first remaining
  person. Handoff draft survives; consent resets on target ID/name/role or Intake
  change, and when target authority disappears. Same-target resource-token
  refresh still preserves consent and draft, including the existing 409 flow.
- Container receives explicit scoped-target options, checks exact ID/role and
  current permission again before issuing a request. Mounted success/request
  regressions now explicitly inject fixture targets instead of using defaults.

## Paired screen evidence: mocked-read UI only

`operator-transfer-targets-parity.spec.ts` deliberately intercepts **Operator
Intake GETs** with `IN-TARGET-UI-FIXTURE`, an explicit UUID Assignment and its own
v14 token versus Intake v71. It blocks all Assignment mutations; no successful
mutation is mocked or attempted. This is necessary to mount the modal while real
Operator resource provisioning is absent. It is **not** an actual API read-model,
directory, durable write or restart proof.

Before captures use exact pre-edit product files from `6994d1dc`, with an EXIT
trap restoring the anchored product files. `before-source.sha256` binds those
two files. Design captures navigate unchanged archived Package 10 locally;
external reference requests are aborted. No prototype content/actors are edited.

Pi inspected all six `shots/{design,before,after}-transfer-{1440,390}.png` images.
The after state removes invented people, displays the unavailable note and keeps
handoff/risk sections in the same design-language family. Geometry assertions
cover document/dialog/controls/footer containment and max height. Reference
Transfer visibly includes a resume-time field; it is **not copied**, preserving
VDC-001 Transfer/Pause separation. Risk disclosure, explicit resource authority
and unavailable-state explanation are later-spec additions, not pixel identity.
Reference role-switch toast and independently hydrated shell counters remain in
screenshots; background counters are not acceptance evidence.

| Modal measurement | 1440 | 390 |
| --- | ---: | ---: |
| Reference width / height | 460 / 342.75 | 350 / 342.75 |
| Before width / height | 460 / 478.58 | 350 / 510.44 |
| After width / height | 460 / 548.97 | 350 / 564.56 |
| After document scrollWidth | 1440 | 390 |
| Radius | 14 | 14 |

After scoped axe reports have **zero violations** at both widths. Native keyboard
Tab/Shift-Tab wraps enabled modal controls; Escape returns to detail. These are
bounded modal checks, not whole-console certification or independent VDC approval.

`real-api-negative/` is a **separate genuine local API probe**, without resource
injection or intercepted API responses. It resets isolated fixture API, submits
synthetic URL, reads actual `IN-3001`, and tests Transfer/Pause deep links at
1440/390. Actual Operator read still lacks resource IDs/versions, so both modals
remain absent and no resource POST occurs. Do not conflate these receipts with
the explicitly mocked-read modal pairs above.

## Verification: original terminal exits

All commands completed synchronously through their original tool invocation;
`logs/*.exit` stores the shell exit receipt. No summary-based waits or liveness
inference were used. Existing unit jsdom ECONNREFUSED diagnostics remain in logs;
the terminal exits below are 0.

| Check | Result |
| --- | --- |
| focused TransferTargets / AssignmentSlaSummary / IntakeProcessingDetail | exit 0; 56 tests |
| before paired UI/design captures | exit 0; 2 tests |
| after paired UI/geometry/axe/keyboard | exit 0; 2 tests |
| genuine local API missing-authority probe | exit 0; 2 tests / four negative states |
| full web unit | exit 0; 73 files / 797 tests |
| web typecheck / lint / code boundaries / diff check | exit 0 each |
| root business inventory | exit 0; unchanged 125 tests / 18 files |

No API suite, build, full business E2E, cloud action, remote CI or independent
VDC approval was run/claimed in this increment. Supplemental spec is outside
exact business inventory. Reproduce after pairs with:

```sh
CI=1 OPSBOARD_PORT=3310 ODP_API_PORT=8310 ODP_API_BASE_URL=http://127.0.0.1:8310 \
ODP_DEPLOY_ENV=e2e ODP_E2E_MODE=true ODP_PRODUCT_MODE=poc ODP_DATA_BINDING_MODE=fixture \
NETWORK_PARITY_EVIDENCE_DIR=/tmp/transfer-target-ui \
npx playwright test --config tests/visual/operator-network-tabs-parity.config.ts \
  operator-transfer-targets-parity --workers=1 --retries=0 --project=chromium
npm test --workspace=@oday-plus/web
npm run typecheck --workspace=@oday-plus/web
npm run lint --workspace=@oday-plus/web
.venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
npx playwright test --list
```

## Required next work

Implement genuine resource provisioning and shared authoritative Operator
read-model with identity-backed, tenant/resource-scoped target directory and
server validation. Do not treat this optional UI prop or UUID shape guard as an
identity authority. Then verify actual Transfer/Pause writes/reload and successful
modal/permission/conflict pairs. Existing Spatial split/permission, Find Areas
reload-before-back, remaining tab states/density, independent VDC and final-scope
verification remain open. **Task stays in_progress; no task_finalize/handoff/
re_review/done for this increment.**
