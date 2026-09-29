# ODP-DEV-LIVE-DEPLOY-EXECUTION-001 — evidence

Status: **blocked at lease issuance. Nothing has been deployed.** Round 2 (2026-09-29T15:19Z) used the one fresh release request that Human/Ops registered under `HUMANOPS-DEV-MIGRATION-20260929T053234Z`. The live Supervisor issuer rejected it before any lease existed: no GCS lease, no Secret Manager read, and no Runtime Release run. The newest `deploy-dev.yml` run is still the build run `36509055237` (2026-09-29T01:41:03Z).

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
