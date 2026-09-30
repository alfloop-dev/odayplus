# ODP-DEV-LIVE-DEPLOY-EXECUTION-001 — evidence

Status: **blocked on Human/Ops (round 6, 2026-09-30T02:06Z): the lease was issued and consumed, the migration ran and succeeded on dev, then `gcloud run deploy` refused to create the new `oday-api` service because the deploy script passes `--no-traffic`, which Cloud Run does not accept on service creation. No API, web, worker or scheduler workload exists on dev.** This is a code defect in `product_ops/deployment/deploy_cloud_run_waji.sh`, present in the candidate `ee06d1d8` and still present on `origin/dev`. Earlier rounds are kept below as recorded.

## Round 6 (2026-09-30T01:58Z – 02:10Z) — lease issued, deploy failed at API service creation

### Preconditions, measured before the issuer ran

| Check | Measured |
|---|---|
| Task | `in_progress`, no blocker; `release_lease_request` approval `HUMANOPS-DEV-MIGRATION-20260929T053234Z`, new nonce digest `sha256:e9d9dbde…` (round 5 was `sha256:a7065d07…`), `expires_at` 2026-09-30T05:32:34Z |
| Signing key, metadata only | `gcloud secrets versions describe latest --secret=odp-release-lease-private-key --project=767864276141 </dev/null` → `versions/1 ENABLED`, exit 0, account `deborah.lu@dev.cctech-support.com` |
| Supervisor | pid `2780187` (runtime `a631b8c7`, as in round 5) |

### What happened

| Time (UTC) | Event |
|---|---|
| 02:01:22 | `release_lease_issuance_reserved`, fingerprint `sha256:5e915602…` |
| 02:01:35 | `release_lease_issued`, `lease_id=lease-410e6cf6a181ebb31e8b91fb14177bed`, key `ed25519:f2b35469…`, `dispatch_ref_sha=a631b8c7` |
| 02:01:38 | Deploy Dev run [36657889962](https://github.com/alfloop-dev/odayplus/actions/runs/36657889962) dispatched on `dev` @ `a631b8c7` (`release_lease_runtime_release_dispatched`) |
| 02:01–02:06 | Jobs: validate inputs ✅, lease verification ✅, build skipped (deploy phase), **deploy ❌**, watch skipped |
| 02:03:55 | Live deployment preflight passed |
| 02:04:12 | Migration job `oday-migration-r-ee06d1d82944` created with the manifest images (`oday-api@sha256:07b3c1f3…`, `oday-worker@sha256:9aeb6784…`, `oday-scheduler@sha256:bd4efa0d…`, `oday-web@sha256:9939e4d0…`) |
| 02:05:34 | Migration execution `oday-migration-r-ee06d1d82944-76fh9` exit 0; receipt `status=succeeded`, `target_revision=head`, `runtime_schema_verified=true`, assisted-intake steps 001–004 `verified` |
| 02:05:46 | Migration job smoke and bootstrap compatibility passed |
| 02:05:49 | `ERROR: (gcloud.run.deploy) --no-traffic not supported when creating a new service.` on `oday-api` |
| 02:05:49 | Script: `previous-release state could not be determined; no recovery mode is claimed` → first-release recovery deletes the candidate job and holds zero traffic |
| 02:05:56 | Migration candidate job deleted |

Raw step log: `deploy-run-36657889962-failed-step.log`. Live readback (services, jobs, migration Cloud Logging entries, run job conclusions): `live-readback-run-36657889962.json`.

The migration failure from the previous rollout (`Can't locate revision identified by '29b539ebc72a'`) did not recur: this execution upgraded to `head` and verified the schema.

### Live state after the run (read-only readback)

- Cloud Run services in `odayplus-runtime-20260825` / `asia-east1`: `oday-mlflow`, `oday-staging-mlflow` only. No `oday-api`, no `oday-web`.
- Cloud Run jobs: none. The migration candidate job was deleted by the script's first-release recovery.
- The dev database schema **was** changed: the migration ran to `head`. Nothing was reset, stamped or deleted.
- Egress and IAM on the API and web services cannot be read back because the services were never created. The 16 external sources stay off (`ODP_EXTERNAL_PROVIDER_MODE: disabled` in the step environment).
- The lease is consumed. The approval expires at 05:32:34Z today, and this request cannot be retried.

### Root cause: the deploy script cannot create a service

`product_ops/deployment/deploy_cloud_run_waji.sh` deploys the API (line 783) and the web service (line 964) with `--tag=… --no-traffic`. Cloud Run accepts `--no-traffic` only when the service already exists. This is the first release into this target, so the first `gcloud run deploy` always fails. `tests/ops/test_cloud_run_live_deployment.py` asserts that both blocks contain `--no-traffic` (lines 2281 and 6061/6067), so the contract tests require the defect. The same lines are on `origin/dev`. No open task or PR changes them.

The deploy job checks out the candidate SHA (`Assert exact release SHA is checked out`). So the fix cannot reach this candidate. The fix has to land on `dev` first, followed by a new build, a new registry binding and a new approval.

### Required Human/Ops actions (human gate)

1. Open a product task to fix the first-release path in `deploy_cloud_run_waji.sh`. Probe the service with `gcloud run services describe`. If it is absent, create it without `--no-traffic`: either `--no-traffic` omitted with ingress kept private and no public invoker, or a creation step that puts zero traffic on a placeholder. Update the two contract tests to match.
2. After that fix merges, rebuild the candidate and rebind the registry. The fix is outside `docs/evidence/`, so `ee06d1d8` cannot be reused.
3. Issue a new deploy authorization for the new candidate. `HUMANOPS-DEV-MIGRATION-20260929T053234Z` expires at 05:32:34Z today.
4. Account for the dev schema: it is already at the `ee06d1d8` head. The next candidate's migration will start from that state.

## Round 5 (2026-09-30T00:32Z – 00:36Z) — issuer blocked at the signing key

### Preconditions, measured before the issuer ran

| Check | Measured | Source |
|---|---|---|
| Running Supervisor | pid `2780187`, started 2026-09-30T00:12:41Z, `/proc/2780187/cwd` = `oday-plus-supervisor-runtime-a631b8c793b2`, exe `/usr/bin/python3.12` | `ps`, `readlink` |
| `oday-plus-supervisor-runtime-current` | → `a631b8c793b2` | `readlink -f` |
| `origin/dev` | `a631b8c793b27842f28f54f663a33b4af49ee824`, unchanged since round 2, so `admission-origin-dev-a631b8c7.json` (`errors: []`) still applies | `git fetch origin dev; git rev-parse origin/dev` |
| Task | `in_progress`, no blocker, `release_lease_request` present: approval `HUMANOPS-DEV-MIGRATION-20260929T053234Z`, round-2 binding, `expires_at` 2026-09-30T05:32:34Z, nonce digest `sha256:a7065d07…` (round 2 was `sha256:4caac1a3…`) | live `ai-status.json` |
| Full pre-GCS issuer check on the **registered** request, a631 code | every list `[]`, `dispatch_ref_sha` = `a631b8c7` | `issuer-precheck-registered-request-round5.json` (the round-3 script with the live request instead of a hypothetical one; read-only) |

### What the issuer did

| Time (UTC) | Event |
|---|---|
| 00:35:21 | `release_lease_issuance_reserved`, fingerprint `sha256:ebac97f0…`, state `issuing` |
| 00:35:34 | `release_lease_issue_blocked`, `lease_id=null`, error `Secret Manager signing key is unavailable`, `dispatch_ref_sha` = `a631b8c7` |

Receipt: `issuance-blocked-receipt-round5.json` (verbatim request with the nonce replaced by its sha256, the issuance record and both events).

In `process_release_lease_issuance` this error is recorded only in the `private_key_loader(...)` branch, which runs after the ancestry, nonce, admission and GCS lease-store steps. The ancestry block from round 2 is fixed, and the GCS lease store is reachable. The only remaining failure is the key load.

### Root cause: the host gcloud login needs re-authentication

`load_private_key_from_secret_reference` runs `gcloud secrets versions access latest --project 767864276141 --secret odp-release-lease-private-key` as a subprocess and discards stderr. The Supervisor has `HOME=/home/lupin` and no `CLOUDSDK_CONFIG` or `GOOGLE_APPLICATION_CREDENTIALS`, so it uses the host gcloud config (active account `deborah.lu@dev.cctech-support.com`). A metadata-only call from the same HOME on the same secret, which reads no key material, fails like this:

```
ERROR: (gcloud.secrets.versions.describe) There was a problem refreshing your current auth tokens: Reauthentication failed. cannot prompt during non-interactive execution.
EXIT=1
```

Transcript: `secret-access-probe-round5.txt`. `gh run list --workflow deploy-dev.yml` still shows `36509055237` (build, 2026-09-29T01:41:03Z) as the newest run, so nothing was dispatched.

### Why I stopped

The issuer does not retry a blocked fingerprint. The request's nonce is now recorded in issuance history, and `_nonce_reuse_errors` rejects it if it is registered again. Acceptance requires stopping on failure. Getting a working login needs an interactive `gcloud auth login`, which a background worker cannot do.

### Required Human/Ops actions, deadline 2026-09-30T05:32:34Z

1. On the Supervisor host, as `lupin`, restore a non-interactive gcloud login for an account that has `secretmanager.versions.access` on `projects/767864276141/secrets/odp-release-lease-private-key`, for example `gcloud auth login deborah.lu@dev.cctech-support.com`. Check that `gcloud secrets versions describe latest --secret odp-release-lease-private-key --project 767864276141 </dev/null` exits 0. The key loader runs gcloud per call, so no Supervisor restart is needed for this.
2. Return the task to `in_progress` and resolve the round-5 blocker.
3. With the user's consent (this would be the third request under this approval id), register one fresh `release_lease_request` with a new nonce, the same binding, and `expires_at` ≤ 2026-09-30T05:32:34Z.

After the deadline this authorization cannot be reused, and a new one is needed.

## Round 4 (2026-09-29T15:44Z) — task parked as blocked on Human/Ops

Re-measured at 15:44:12Z. Nothing has changed since round 3: pid `630708` is still running with `/proc/630708/cwd` = `oday-plus-supervisor-runtime-af923aa58d33`, `oday-plus-supervisor-runtime-current` still points to `af923aa58d33`, `task.release_lease_request` is `null`, and `task.release_lease_issuance.state` is still the round-2 `blocked` record.

The 15:39Z round left only a note, and the orchestrator recorded it as `worker_failed` (no progress). While the Supervisor runs `af923aa5`, every `owned_in_progress_dispatch` round can only re-measure. For that reason this round moves the task to `blocked` with `waiting_for=Human/Ops`. A Human/Ops blocker is never auto-recovered (`blocked_task_auto_recovery_eligible`), so the re-dispatch loop stops until an operator acts.

Operator order, deadline 2026-09-30T05:32:34Z:

1. Point `oday-plus-supervisor-runtime-current` at `oday-plus-supervisor-runtime-a631b8c793b2`, restart the Supervisor, and confirm with `readlink /proc/<new pid>/cwd`.
2. Return the task to `in_progress` and resolve the open blocker. `request_errors` rejects a blocked task or a task with an open blocker.
3. Register one fresh `release_lease_request` under `HUMANOPS-DEV-MIGRATION-20260929T053234Z`. Use the round-2 binding, a new nonce, and `expires_at` ≤ 2026-09-30T05:32:34Z.

If the deadline passes first, the authorization cannot be reused, and this task stays blocked until a new authorization exists.

## Round 3 (2026-09-29T15:33Z – 15:36Z) — precheck only, no request registered

The 15:33:00Z reopen said: "Supervisor runtime rolled to current dev … Second release_lease_request with a fresh nonce registered". Both claims were measured against the live host and the live board.

### Live state

| Check | Measured | Source |
|---|---|---|
| Running Supervisor | pid `630708`, started 2026-09-27T08:15:36Z, `/proc/630708/cwd` = `/home/lupin/oday-plus-supervisor-runtime-af923aa58d33` | `ps -eo pid,lstart,args`, `readlink /proc/630708/cwd` |
| `oday-plus-supervisor-runtime-current` | → `oday-plus-supervisor-runtime-af923aa58d33` | `readlink` |
| New runtime dir | `oday-plus-supervisor-runtime-a631b8c793b2` exists, HEAD `a631b8c7`, created 15:32:51Z, **not running** | `ls --time-style=full-iso`, `git rev-parse HEAD` |
| Worker launch path for this round | `/home/lupin/oday-plus-supervisor-runtime-af923aa58d33/.orchestrator/bin/claude` | `worker_started` event at 15:33:11Z |
| `task.release_lease_request` | `null` | `$PANTHEON_STATUS_ROOT/ai-status.json` at 15:34:01Z and 15:35:08Z |
| `task.release_lease_issuance.state` | still the round-2 `blocked` record (fingerprint `sha256:6b5c80ed…`) | same |

The runtime directory was created, but the running Supervisor is still on `af923aa5`. The "second request" is not on the board. A Human/Ops `assign` at 15:33:50Z came after the reopen and may have cleared the field. Whatever the cause, the issuer has no request to process, and nothing will be issued until one is registered.

### Full pre-GCS issuer check, both runtimes

For this round the probe covers every check that `process_release_lease_issuance` runs before `LeaseStateStore`: `request_errors`, `_read_release_inputs`, `_exact_binding_errors`, `_build_run_binding_errors`, `check_dispatch_ref_errors` (real `resolve_ref_sha` against the remote `dev`), `_nonce_reuse_errors` and `issuance_errors`. It uses the live config and live status root `/home/lupin/odayplus`. The request is hypothetical (`approval_id=HYPOTHETICAL-PROBE-NOT-REGISTERED`, throwaway nonce, `expires_at` = the authorization deadline) and was never written to the board. No GCS, Secret Manager or dispatch call was made.

| Runtime code | Every check except dispatch ref | `check_dispatch_ref_errors` (dev = `a631b8c7`) |
|---|---|---|
| `a631b8c793b2` (staged, not running) | `[]` | `[]` |
| `af923aa58d33` (running) | `[]` | `…intervening commits touch non-evidence paths: docs/audits/code-boundary-inventory.csv` |

Receipts: `issuer-full-precheck-runtime-a631b8c7.json`, `issuer-full-precheck-runtime-af923aa5.json`. The script is under Reproduce → Round 3 full issuer precheck.

### Consequence

If a request is registered while pid `630708` is still running, it gets blocked on the same ancestry error as round 2, and that request is used up. If the Supervisor is running `a631b8c7`, every check before GCS passes. After that, only the GCS lease store, the Secret Manager key and the deploy itself remain, and none of them can be dry-run.

### Required Human/Ops actions, in this order

1. Point `oday-plus-supervisor-runtime-current` at `oday-plus-supervisor-runtime-a631b8c793b2` and restart the Supervisor. Confirm with `readlink /proc/<new pid>/cwd`.
2. Only then register one fresh `release_lease_request`. Use the same binding as the round-2 request, a new nonce, and `expires_at` ≤ 2026-09-30T05:32:34Z. The task must stay `in_progress` with no open blocker, because `request_errors` rejects any other state.

## Round 2 (2026-09-29T15:19Z – 15:24Z)

### Base advance and admission on the real origin/dev — passed

`origin/dev` moved to `a631b8c793b27842f28f54f663a33b4af49ee824` (PR #1380, NFR evidence). I merged it into the task branch; the merge tree equals `git merge-tree --write-tree` (`c612ba50…`). I then re-ran `registry_admission_errors(environment="dev", release_sha=a631b8c7)` in a detached checkout at `a631b8c7`, using the Step 1 script below. Result: `errors: []`. Registry sha256 `f0e59b2c…` and manifest sha256 `1d34ada7…` are unchanged. The canonical checkout's two issuer input files now have exactly these hashes, which means the round-1 gap is closed.

Receipt: `admission-origin-dev-a631b8c7.json`.

### Lease issuance — blocked by the issuer

| Time (UTC) | Event | Source |
|---|---|---|
| 15:19:07 | Codex2 reopen; fresh `release_lease_request` (nonce digest `sha256:4caac1a3…`) | activity log |
| 15:22:54 | `release_lease_issuance_reserved`, state `issuing` | activity log |
| 15:23:04 | `release_lease_issue_blocked`, state `blocked`, `lease_id=null` | activity log, `task.release_lease_issuance` |

Raw issuer error:

```
release.candidate_sha 'ee06d1d8294464f1eb7231f2b06505348a61cba2' is an ancestor of expected SHA 'a631b8c793b27842f28f54f663a33b4af49ee824', but intervening commits touch non-evidence paths: docs/audits/code-boundary-inventory.csv
```

Receipt: `issuance-blocked-receipt.json`. It is a verbatim copy of the request, the issuance record, and both activity events. The raw nonce is replaced by its sha256, and that sha256 matches the issuer's `approval_nonce_digest`.

### Root cause: the issuer runs pre-fix code

The error comes from `check_dispatch_ref_errors` → `check_candidate_ancestry(candidate, dispatch_ref_sha, root)`, with `dispatch_ref=dev` resolved to `a631b8c7`. The issuer imports this function from its runtime `af923aa5` (2026-09-27).

- `docs/audits/code-boundary-inventory.csv` was added to the evidence allowlist by `3cc85636` (ODP-RUNTIME-RELEASE-ANCESTRY-INVENTORY-002, merged via PR #1379 = `ee06d1d8`, 2026-09-28T14:22Z). That is the candidate commit itself.
- `af923aa5` does not contain `3cc85636`, and neither does any `oday-plus-supervisor-runtime-*` directory on this host.
- Between `ee06d1d8` and `a631b8c7`, four commits touch the CSV: `c04ce808`, `bd65dccc` and `276b0938` (remediation), and `00b8a049` (NFR). Adding a `.py` file forces that CSV to change, so evidence-only PRs will keep touching it.

I called the same function from both code versions, read-only and in-process, against the same git root and SHAs:

| Code version | `check_candidate_ancestry(ee06d1d8, a631b8c7)` |
|---|---|
| issuer runtime `af923aa5` | the exact error above |
| origin/dev `a631b8c7` | `[]` |

Receipt: `issuer-ancestry-probe-a631b8c7.json`. The script is under Reproduce → Round 2 issuer ancestry probe.

### Correction to round 1

The round-1 issuer probe covered `_read_release_inputs`, `_exact_binding_errors` and `_build_run_binding_errors`. It did **not** cover `check_dispatch_ref_errors`. The remediation commits were already on dev's first-parent history at `0d569940`, so this block would have happened in round 1 as well. Round 1's "binding checks pass" did not mean the issuer would issue.

### Why I stopped

- The issuer never retries a blocked fingerprint.
- The approval allows one fresh request, and it is now used.
- Acceptance requires stopping on failure. Retrying would need a new request and nonce, which is outside this authorization.

## Required Human/Ops actions (human gate)

1. **Roll the Supervisor runtime forward** to a commit that contains `3cc85636` (and PR #1377 `c9d60c78`, stale-CAS lease recovery). Any current `origin/dev` qualifies. Then restart the Supervisor, because config and code load only at startup. Mutating the control-plane runtime is outside this worker's authorization.
2. **Issue a new deploy authorization.** It needs a new approval id or an explicit extension, and one new `release_lease_request` with a fresh nonce for candidate `ee06d1d8294464f1eb7231f2b06505348a61cba2`, manifest `sha256:8ee919d67fc89768c7ae8912ecfce706dc1b2a0fbb2e3da1a14b5e87c1fae80e`, `manifest_run_id=36509055237`, target `dev`, action `deploy`. The request for `HUMANOPS-DEV-MIGRATION-20260929T053234Z` was used and ended `blocked`. Reusing its nonce is refused by `_nonce_reuse_errors`.
3. **Before registering, dry-run the issuer's full gate list**, not only the binding checks. From the rolled-forward runtime, run `check_dispatch_ref_errors(settings, candidate, root)` together with the Step 2 probe.

## Round 1 (2026-09-29T08:02Z) — kept as recorded

### Step 1: admission against the real origin/dev — passed

`registry_admission_errors(environment="dev")` was run with `release_sha` = `git rev-parse origin/dev` after `git fetch origin dev`. The result was `0d5699401d3c70d2a55f23c12e9e2ee19668a77a` (PR #1107 merge). The registry and manifest were read with `git show 0d569940:<path>`, and the ancestry check ran in a checkout at that same commit.

Result: `errors: []`, measured at 2026-09-29T08:02:35Z. Registry: `decision=go`, `admission_target=dev`, candidate `ee06d1d8`, manifest `sha256:8ee919d6…`. Manifest raw sha256 is `1d34ada7…`, the same value the remediation README reports.

Receipt: `admission-origin-dev-0d569940.json`.

### Step 2: what the lease issuer would actually read — mismatched, so stopped here

The live Supervisor issuer (`/home/lupin/oday-plus-supervisor-runtime-af923aa58d33/.orchestrator/release_lease_integration.py`, runtime commit `af923aa5`) reads `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` and `RELEASE_MANIFEST.json` from `config_path(config, "status_file").parent`. That is the working tree of the canonical checkout `/home/lupin/odayplus`, not origin/dev.

In that working tree the two files are uncommitted local modifications that still bind the superseded candidate `a31e02ae` / `sha256:499110d0…` / build `36333397898`. They were last synced for the a31 round and never synced to ee06d1d8.

I called the issuer's own input and binding functions in-process, read-only, with a hypothetical request for `ee06d1d8` / `sha256:8ee919d6…` / `36509055237`. None of these calls registered a request, used a nonce, or touched GCS or Secret Manager.

| Input root | `_read_release_inputs` | `_exact_binding_errors` | `_build_run_binding_errors` |
|---|---|---|---|
| `/home/lupin/odayplus` (live issuer input) | `manifest.candidate_sha does not match release.candidate_sha; the manifest is for a different candidate` | 4 errors (candidate and manifest mismatch vs both registry and manifest) | `candidate_rebind does not bind the requested candidate_sha` |
| checkout at origin/dev `0d569940` | `[]` | `[]` | `[]` |

Receipts: `signer-input-probe-canonical-checkout.json` and `signer-input-probe-origin-dev-0d569940.json`.

#### Why I stopped instead of registering

- The authorization allows **one** fresh release request.
- The issuer never retries a blocked fingerprint.
- A blocked request's nonce digest stays in issuance history, so the same nonce cannot be reused.

Registering now would be rejected at `_exact_binding_errors` and would use up the only authorized request. The acceptance criterion is to stop and report when conditions do not match.

### Round 1 required actions (completed by Human/Ops and Codex2 at 15:19Z)

These are outside the worker's authorized scope. The authorization covers one request, one lease, and the deploy phase only. It does not cover mutating the shared control-plane checkout.

1. **Sync the issuer's two input files.** In `/home/lupin/odayplus`, sync `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` and `docs/evidence/gates/RELEASE_MANIFEST.json` to `origin/dev` (`0d569940`). Back up the current a31 copies first. Expected sha256 after sync:
   - registry: `f0e59b2c338938a6e10ce3aa19761d29ecd17c40418c3af145544150dca0cb1d`
   - manifest: `1d34ada707d40bbb25f8053955e9736b30cc16f229563a7ec594e3598a209ec0`
2. **Resolve the blocker and move the task back to in_progress.** Reopen is limited to owner Claude2 or reviewer Codex2.
3. **Register one fresh `release_lease_request` with a fresh nonce.** Use `approval_id=HUMANOPS-DEV-MIGRATION-20260929T053234Z`, candidate `ee06d1d8294464f1eb7231f2b06505348a61cba2`, manifest `sha256:8ee919d67fc89768c7ae8912ecfce706dc1b2a0fbb2e3da1a14b5e87c1fae80e`, `manifest_run_id=36509055237`, target `dev`, action `deploy`. `expires_at` must not be later than 2026-09-30T05:32:34Z. `ai_status.py` has no subcommand that writes this field, so the worker cannot register it.

#### Disclosed risk, not a precondition

The live Supervisor runtime `af923aa5` (2026-09-27) does **not** contain PR #1377 (`c9d60c78`, stale-CAS safe lease recovery, merged 2026-09-28). The a31 request on 2026-09-28T00:47:26Z stayed in `state=issuing` with no lease on that same runtime. If this request also sticks in `issuing`, the likely cause is the missing fix, not the request.

## Not claimed

- No deploy, no Cloud Run / migration / egress / IAM live readback. No lease was issued in either round.
- No change to any database, to staging or production, or to the 16 external sources.

## Reproduce

Step 1, run from a checkout at origin/dev:

```
uv run --frozen --python 3.12 python <script>
```

```python
import json, subprocess, sys, datetime
from pathlib import Path
sys.path.insert(0, ".")
from delivery_toolchain.release.check_runtime_admission import registry_admission_errors
root = Path(".").resolve()
dev_sha = subprocess.check_output(["git","rev-parse","origin/dev"], text=True).strip()
head = subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip()
reg_raw = subprocess.check_output(["git","show",f"{dev_sha}:docs/evidence/gates/RELEASE_GATE_REGISTRY.json"])
man_raw = subprocess.check_output(["git","show",f"{dev_sha}:docs/evidence/gates/RELEASE_MANIFEST.json"])
reg = json.loads(reg_raw); man = json.loads(man_raw)
errors = registry_admission_errors(reg, release_sha=dev_sha, environment="dev",
    expected_manifest_digest=man.get("manifest_digest"), root=root)
import hashlib
out = {
  "measured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
  "function": "delivery_toolchain.release.check_runtime_admission.registry_admission_errors",
  "code_checkout_head": head,
  "release_sha": dev_sha, "release_sha_source": "git rev-parse origin/dev after git fetch origin dev",
  "environment": "dev",
  "registry_sha256": hashlib.sha256(reg_raw).hexdigest(),
  "manifest_raw_sha256": hashlib.sha256(man_raw).hexdigest(),
  "registry_release": {k: reg["release"].get(k) for k in ("decision","stage","admission_target","candidate_sha","manifest_digest")},
  "expected_manifest_digest": man.get("manifest_digest"),
  "errors": errors,
}
print(json.dumps(out, indent=2))
sys.exit(1 if errors else 0)
```

Step 2 (`python3 <script> [root]`; with no argument it reads `/home/lupin/odayplus`):

```python
import json, sys, datetime
from pathlib import Path
RT = Path("/home/lupin/oday-plus-supervisor-runtime-af923aa58d33/.orchestrator")
sys.path.insert(0, str(RT))
import release_lease_integration as rli
root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/lupin/odayplus")
req = {"candidate_sha": "ee06d1d8294464f1eb7231f2b06505348a61cba2",
       "manifest_digest": "sha256:8ee919d67fc89768c7ae8912ecfce706dc1b2a0fbb2e3da1a14b5e87c1fae80e",
       "manifest_run_id": "36509055237"}
registry, manifest, input_errors = rli._read_release_inputs(root, req["candidate_sha"])
out = {
  "probed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
  "signer_module": str(RT / "release_lease_integration.py"),
  "signer_runtime_commit": "af923aa58d33da4971e09f6e87ea2d1a026f6580",
  "signer_input_root": str(root),
  "hypothetical_request": req,
  "note": "read-only in-process call of the signer's own input/binding checks; no request registered, no nonce minted, no GCS/secret access",
  "signer_registry_release": {k: (registry or {}).get("release", {}).get(k) for k in ("decision","admission_target","candidate_sha","manifest_digest")},
  "signer_registry_candidate_rebind_build_run": ((registry or {}).get("candidate_rebind") or {}).get("build_run"),
  "input_errors": input_errors,
  "exact_binding_errors": rli._exact_binding_errors(req, registry, manifest),
  "build_run_binding_errors": rli._build_run_binding_errors(req, registry),
}
print(json.dumps(out, indent=2, ensure_ascii=False))
```

Round 2 issuer ancestry probe (`python3 <script>`, run from the task worktree; the dev-side module must be byte-identical to `a631b8c7`):

```python
import json, sys, subprocess, datetime, importlib.util
from pathlib import Path
root = Path("/tmp/pantheon-worker-worktrees/pantheon/odp-dev-live-deploy-execution-001")
cand = "ee06d1d8294464f1eb7231f2b06505348a61cba2"
ref = "a631b8c793b27842f28f54f663a33b4af49ee824"
def load(path):
    spec = importlib.util.spec_from_file_location("m" + str(abs(hash(path))), path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
rt = "/home/lupin/oday-plus-supervisor-runtime-af923aa58d33/delivery_toolchain/e2e/check_release_gate_registry.py"
out = {
  "probed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
  "note": "read-only in-process call of check_candidate_ancestry (the function the issuer's check_dispatch_ref_errors uses) from two code versions; no request, nonce, GCS or secret access",
  "candidate_sha": cand, "dispatch_ref": "dev", "dispatch_ref_sha": ref,
  "git_root": str(root),
  "issuer_runtime": {
    "module": rt, "runtime_commit": "af923aa58d33da4971e09f6e87ea2d1a026f6580",
    "contains_3cc85636": subprocess.run(["git","merge-base","--is-ancestor","3cc85636670e2d514f49c9d2975890d3442c4185","af923aa58d33da4971e09f6e87ea2d1a026f6580"],cwd=root).returncode == 0,
    "errors": load(rt).check_candidate_ancestry(cand, ref, root),
  },
  "origin_dev_code": {
    "module": "delivery_toolchain/e2e/check_release_gate_registry.py @ " + ref,
    "errors": None,
  },
  "csv_touching_commits_candidate_to_ref": subprocess.check_output(["git","log","--format=%H %s",f"{cand}..{ref}","--","docs/audits/code-boundary-inventory.csv"],cwd=root,text=True).splitlines(),
}
tmp = root / "delivery_toolchain/e2e/check_release_gate_registry.py"
assert subprocess.run(["git","diff","--quiet",ref,"--",str(tmp)],cwd=root).returncode == 0
out["origin_dev_code"]["errors"] = load(str(tmp)).check_candidate_ancestry(cand, ref, root)
print(json.dumps(out, indent=2, ensure_ascii=False))
```

Round 3 full issuer precheck (`python3 <script> <runtime dir>`, read-only; run once per runtime):

```python
import json, sys, hashlib, datetime, subprocess
from pathlib import Path
from datetime import UTC
RT_ROOT = Path(sys.argv[1])
RT = RT_ROOT / ".orchestrator"
sys.path.insert(0, str(RT_ROOT)); sys.path.insert(0, str(RT))
import common, release_lease_integration as rli
from release_lease import issuance_errors
STATUS_ROOT = Path("/home/lupin/odayplus")
config = common.load_config_for_status_root(STATUS_ROOT)
settings, serr = rli.issuer_settings(config)
status = common.load_status(config)
task = rli._task_index(status, "ODP-DEV-LIVE-DEPLOY-EXECUTION-001", config=config)
now = datetime.datetime.now(UTC)
req = {"kind": rli.REQUEST_KIND, "status": "approved", "task_id": "ODP-DEV-LIVE-DEPLOY-EXECUTION-001",
       "approved_by": "Human/Ops", "approval_id": "HYPOTHETICAL-PROBE-NOT-REGISTERED",
       "nonce": "probe-" + hashlib.sha256(now.isoformat().encode()).hexdigest()[:16],
       "candidate_sha": "ee06d1d8294464f1eb7231f2b06505348a61cba2",
       "manifest_digest": "sha256:8ee919d67fc89768c7ae8912ecfce706dc1b2a0fbb2e3da1a14b5e87c1fae80e",
       "manifest_run_id": "36509055237", "target_environment": "dev", "action": "deploy",
       "approved_at": now.isoformat(), "expires_at": "2026-09-30T05:32:34+00:00"}
root = common.config_path(config, "status_file").parent
archive_dir = root / "ai-task-archive/tasks"
fp = rli.request_fingerprint(task["id"], req)
out = {"probed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
       "code_runtime": str(RT_ROOT),
       "code_runtime_head": subprocess.check_output(["git","-C",str(RT_ROOT),"rev-parse","HEAD"],text=True).strip() if (RT_ROOT/".git").exists() else None,
       "issuer_input_root": str(root), "settings_errors": serr,
       "dispatch_ref": settings and settings.get("dispatch_ref"),
       "hypothetical_request": {k: v for k, v in req.items() if k != "nonce"},
       "live_task_status": task.get("status"), "live_task_has_request": task.get(rli.REQUEST_FIELD) is not None}
out["request_errors"] = rli.request_errors(status, task, req, now=now)
registry, manifest, input_errors = rli._read_release_inputs(root, req["candidate_sha"])
out["read_release_inputs_errors"] = input_errors
out["exact_binding_errors"] = rli._exact_binding_errors(req, registry, manifest)
out["build_run_binding_errors"] = rli._build_run_binding_errors(req, registry)
ref_sha, ref_errors = rli.check_dispatch_ref_errors(settings, req["candidate_sha"], root)
out["dispatch_ref_sha"] = ref_sha; out["dispatch_ref_errors"] = ref_errors
out["nonce_reuse_errors"] = rli._nonce_reuse_errors(status, task["id"], fp, rli._safe_digest(req["nonce"]), archive_dir=archive_dir, config=config)
out["issuance_errors"] = issuance_errors(status=status, registry=registry, manifest=manifest, manifest_errors=input_errors,
    task_id=task["id"], target_environment="dev", release_sha=req["candidate_sha"], archive_dir=archive_dir, root=root)
print(json.dumps(out, indent=2, ensure_ascii=False))
```
