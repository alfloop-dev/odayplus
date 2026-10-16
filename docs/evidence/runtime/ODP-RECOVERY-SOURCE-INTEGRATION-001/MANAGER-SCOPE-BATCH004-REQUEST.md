# Batch004 — one additional PostgreSQL HTTP fixture path

**SOURCE ONLY; NOT REVIEW-READY. Manager authorization/serialized lease required before editing.**

Pi run `pi-20261015T052543Z-a1757def` completed the assigned batch003 source anchors through `934a8b2f85b82bc7583e78458d54d03fd1064a9d` (tree `9ffb3b1b7f1d9c89aed107ee23bb80e2ee79df69`). Fetched admitted `origin/dev` remains `c0c8c8226acaab3cbe8bb76e79f01e5e6694abb3`. Canonical353-path scope was inspected: the following path is absent and has NOT been edited.

## Exact additional path requested

- `tests/integration/test_official_real_estate_postgresql.py`

Normal hosted CI [38689309039 / product-db116271342543](https://github.com/alfloop-dev/odayplus/actions/runs/38689309039/job/116271342543), head `e838d987f0688ea8f24678f714ebb6da4820f55d`, actually terminates exit1: **4 failed, 8 passed**. The four failures are:

1. `test_postgresql_adjust_concurrency_creates_single_replacement_and_rejects_collision`
2. `test_postgresql_adjust_interleaved_with_stop_or_version_update_conflict[stop]`
3. `test_postgresql_adjust_interleaved_with_stop_or_version_update_conflict[version]`
4. `test_postgresql_adjust_interleaved_with_stale_assign_or_stop_rejected`

All stop at Intervention creation: expected201, actual403. Their local `INTERVENTION_HEADERS` and separately constructed concurrent `auth_headers(...)` lack the newly mandatory authenticated tenant. Each test already creates an actual PostgreSQL tenant/store pair; bind only these explicit local principals/worker fixtures to that owning tenant using the supported authentication mode. Do not widen shared `tests/integration/_authz.py`, grant global tenant/auditor authority, waive the HTTP boundary, or edit source authorization to accommodate a tenantless test.

Preserve every independent engine/real PostgreSQL/schema/migration path, concurrency barrier, locked revalidation/CAS, exact200/409 and `STALE_UPDATE_CONFLICT`, audit winner/loser, version, relational/document lineage and no-duplicate/orphan assertion. Add foreign/anonymous refusal and persisted tenant/lineage checks as justified within this one fixture module. No numerical, transition, production-mode, real-artifact, skip, timeout, CI trigger or gate weakening. This request does not authorize edits itself.

## Other measured state, not additional requested paths

The same hosted E2E job116271342691 measures129 members: **3 failed / 98 passed / 28 not run**. The remaining POSSIBLE_MATCH failure was independently reproduced against a fresh throwaway API and an actual current standalone Web built with the Dockerfile's public E2E flags: successful200 submit/detail followed by `Cannot read properties of undefined (reading 'toLowerCase')`. The response projection had removed the canonical field key together with private metadata. Already-owned `intake_authorization.py` now synthesizes only safe schema key/label, keeping all private values null and untrusted metadata absent. The unchanged canonical3 browser flow passes locally; full129 and normal successor CI are still owed. No E2E test/UI/runner/workflow path extension is requested, and this single browser pass is not closure of all three failures or28 unexecuted members.

PR1478 readback at934 is OPEN with `autoMergeRequest=null`; successor normal CI38691575647 is in progress, not accepted. Parent e838 overall CI/unit remains nonterminal at last readback, despite terminal failed DB/E2E leaves. See `pi-batch003-diagnostics.json` for head/dirty-state-bound author diagnostics and raw-log hashes, including genuine failed attempts. Do not transfer parent results to the request/documentation successor.

After Manager authorization renew the exclusive owner context, repair the one leased fixture and finish owning controls; then fresh final exact-head14 canonical receipts, all17 supplemental selections, normal terminal hosted CI and independent configured Codex2 supplied-text assessment (explicit omissions/late measurements/whole-composition gap) remain required. Human/Ops remains final reviewer/PENDING. Original FULL blocked/six-reopen task is verified unchanged; failed epochs/refusals/quota, operational HOLD/quarantine/bindingNULL/cleanupUNKNOWN and goalfalse remain. No submission, approval, merge arming, merge, done, deployment or live credential/recovery/account/session/cloud/data effect.
