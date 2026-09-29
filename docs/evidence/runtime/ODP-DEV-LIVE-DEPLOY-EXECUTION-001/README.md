# ODP-DEV-LIVE-DEPLOY-EXECUTION-001 — evidence

Status: **blocked before the lease request. Nothing has been deployed.** No release request was registered, no nonce was minted, no lease exists, and no Runtime Release run was dispatched. Authorization `HUMANOPS-DEV-MIGRATION-20260929T053234Z` (expires 2026-09-30T05:32:34Z) is still unused.

## Step 1: admission against the real origin/dev — passed

`registry_admission_errors(environment="dev")` was run with `release_sha` = `git rev-parse origin/dev` after `git fetch origin dev`. The result was `0d5699401d3c70d2a55f23c12e9e2ee19668a77a` (PR #1107 merge). The registry and manifest were read with `git show 0d569940:<path>`, and the ancestry check ran in a checkout at that same commit.

Result: `errors: []`, measured at 2026-09-29T08:02:35Z. Registry: `decision=go`, `admission_target=dev`, candidate `ee06d1d8`, manifest `sha256:8ee919d6…`. Manifest raw sha256 is `1d34ada7…`, the same value the remediation README reports.

Receipt: `admission-origin-dev-0d569940.json`.

## Step 2: what the lease issuer would actually read — mismatched, so stopped here

The live Supervisor issuer (`/home/lupin/oday-plus-supervisor-runtime-af923aa58d33/.orchestrator/release_lease_integration.py`, runtime commit `af923aa5`) reads `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` and `RELEASE_MANIFEST.json` from `config_path(config, "status_file").parent`. That is the working tree of the canonical checkout `/home/lupin/odayplus`, not origin/dev.

In that working tree the two files are uncommitted local modifications that still bind the superseded candidate `a31e02ae` / `sha256:499110d0…` / build `36333397898`. They were last synced for the a31 round and never synced to ee06d1d8.

I called the issuer's own input and binding functions in-process, read-only, with a hypothetical request for `ee06d1d8` / `sha256:8ee919d6…` / `36509055237`. None of these calls registered a request, used a nonce, or touched GCS or Secret Manager.

| Input root | `_read_release_inputs` | `_exact_binding_errors` | `_build_run_binding_errors` |
|---|---|---|---|
| `/home/lupin/odayplus` (live issuer input) | `manifest.candidate_sha does not match release.candidate_sha; the manifest is for a different candidate` | 4 errors (candidate and manifest mismatch vs both registry and manifest) | `candidate_rebind does not bind the requested candidate_sha` |
| checkout at origin/dev `0d569940` | `[]` | `[]` | `[]` |

Receipts: `signer-input-probe-canonical-checkout.json` and `signer-input-probe-origin-dev-0d569940.json`.

### Why I stopped instead of registering

- The authorization allows **one** fresh release request.
- The issuer never retries a blocked fingerprint.
- A blocked request's nonce digest stays in issuance history, so the same nonce cannot be reused.

Registering now would be rejected at `_exact_binding_errors` and would use up the only authorized request. The acceptance criterion is to stop and report when conditions do not match.

## Required Human/Ops actions (human gate)

These are outside the worker's authorized scope. The authorization covers one request, one lease, and the deploy phase only. It does not cover mutating the shared control-plane checkout.

1. **Sync the issuer's two input files.** In `/home/lupin/odayplus`, sync `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` and `docs/evidence/gates/RELEASE_MANIFEST.json` to `origin/dev` (`0d569940`). Back up the current a31 copies first. Expected sha256 after sync:
   - registry: `f0e59b2c338938a6e10ce3aa19761d29ecd17c40418c3af145544150dca0cb1d`
   - manifest: `1d34ada707d40bbb25f8053955e9736b30cc16f229563a7ec594e3598a209ec0`
2. **Resolve the blocker and move the task back to in_progress.** Reopen is limited to owner Claude2 or reviewer Codex2.
3. **Register one fresh `release_lease_request` with a fresh nonce.** Use `approval_id=HUMANOPS-DEV-MIGRATION-20260929T053234Z`, candidate `ee06d1d8294464f1eb7231f2b06505348a61cba2`, manifest `sha256:8ee919d67fc89768c7ae8912ecfce706dc1b2a0fbb2e3da1a14b5e87c1fae80e`, `manifest_run_id=36509055237`, target `dev`, action `deploy`. `expires_at` must not be later than 2026-09-30T05:32:34Z. `ai_status.py` has no subcommand that writes this field, so the worker cannot register it.

### Disclosed risk, not a precondition

The live Supervisor runtime `af923aa5` (2026-09-27) does **not** contain PR #1377 (`c9d60c78`, stale-CAS safe lease recovery, merged 2026-09-28). The a31 request on 2026-09-28T00:47:26Z stayed in `state=issuing` with no lease on that same runtime. If this request also sticks in `issuing`, the likely cause is the missing fix, not the request.

## Not claimed

- No deploy, no Cloud Run / migration / egress / IAM live readback. Those steps depend on a lease that does not exist.
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
