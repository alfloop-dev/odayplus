# Dev administration release profile (`dev-admin`)

Task: ODP-DEV-ADMIN-RELEASE-READINESS-001 · Owner: Claude · Reviewer: Codex

## 1. Problem

The runtime already reports core health and model readiness separately.
`apps/api/oday_api/main.py::runtime_modes` derives `data.liveReady` from
persistence, the provider posture and the operator repository only, and
`/readiness` answers 200 on those facts. Production model bindings are reported
on their own (`details.models.productionBindingsReady`, per-service
`capabilities[*].available/reasonCode`, and the
`PRODUCTION_MODEL_BINDINGS_UNVERIFIED` model blocking reason). Model-dependent
routes stay refused: Learning Hub answers 503 without the production MLflow
registry, and ForecastOps refuses execution without a resolved binding.

The final release gate, `delivery_toolchain/e2e/check_live_e2e_gate.py`, did
not keep these apart. It always required `models.mode=mlflow-production`,
ForecastOps `available=true`, and exactly one approved `production` alias with
lineage. Until a model is trained and approved, no dev release with healthy
administration could pass, so dev administration had no shippable path.

## 2. Decision

Add a named, immutable **release profile** that selects the acceptance scope.

| Profile | Where it can be admitted | What the gate requires about models | Extra proof |
|---|---|---|---|
| `full` (default) | dev, staging, production | Unchanged: production bindings, ForecastOps available, one approved alias with lineage and object-store artifact | – |
| `dev-admin` | **dev only** | Missing models are reported truthfully and refused (see §4) | Real Web password sign-in journey (see §5) |

`dev-admin` never relaxes a persistence, provider, auth, worker, audit,
data-origin or surrogate-marker check. It replaces only the "models must be
production-ready" assertions, and only while the runtime itself reports the
models as not ready. If the runtime does report verified bindings, a dev-admin
release is held to the full model assertions.

## 3. Immutable binding: from build to live validation

1. **Build** (`deploy-dev.yml` input `release_profile`, build phase only;
   `build_release_handoff.py --release-profile`). `full` writes nothing. Every
   existing manifest and digest stays the same. `dev-admin` writes
   `manifest.release_profile = {"name": "dev-admin", "target_environment":
   "dev", "model_readiness": "not_claimed"}` before the manifest is sealed. The
   build refuses an unknown profile, or `dev-admin` for a target other than
   dev, before it seals anything.
2. **Digest.** `release_profile` is part of the canonical payload, so
   `manifest_digest` covers it, and so does the Supervisor lease issued against
   that digest. Stripping, renaming or re-targeting the profile after sealing
   breaks the digest. `validate_manifest` rejects a malformed profile even when
   the manifest is re-sealed: unknown name, explicit `full`, wrong target,
   claimed model readiness, or extra keys.
3. **Admission** (`check_runtime_admission.py`). `release_profile_errors`
   binds the profile to the deploy environment inside `admit_release`. This runs
   before the lease is consumed and before any cloud mutation. A `dev-admin`
   manifest with a valid staging or production lease is refused, and the lease
   stays `issued`. The receipt records `release_profile` only for a manifest
   that verified against the digest.
4. **Deploy.** The admission job outputs `release_profile` from that receipt
   and refuses any unknown value. The deploy job exports it as
   `ODP_RELEASE_PROFILE`. **The deploy phase has no profile input of its own,**
   so a deploy cannot narrow an already built `full` release.
   `deploy_cloud_run_waji.sh` validates the profile again before the preflight
   and before any `gcloud`/`docker` call. It refuses an unknown profile,
   `dev-admin` outside dev, and `dev-admin` without its sign-in inputs. It then
   writes `ODP_RELEASE_PROFILE` into the API and Web runtime env payloads and
   passes `--release-profile` to the live gate.
5. **Runtime** (`runtime_mode.release_profile`). `/readiness` and `/health`
   publish `details.releaseProfile = {name, valid, modelReadinessClaimed,
   error}`. An unknown profile, or `dev-admin` serving a deployment other than
   dev, is `valid=false`. Readiness then answers 503 and, under live data, adds
   the `RELEASE_PROFILE_INVALID` blocking reason.
6. **Live gate.** `runtime:release_profile` requires the runtime to report
   exactly the admitted profile, `valid=true`, and `modelReadinessClaimed` true
   for `full` only. A mismatch in either direction blocks. Before any request,
   the gate refuses an unknown profile (`config:release_profile`) and
   `dev-admin` with an `--expected-deployment` other than dev
   (`config:release_profile_deployment`).

Unchanged and still enforced: candidate SHA and image digests, signing, SBOM,
lease and registry admission, first-release absence readback, normal
migrations, sources-off `ALL_TRAFFIC` and default-deny egress, and rollback on a
red gate (the gate still runs before `DEPLOYMENT_COMMITTED`).

## 4. Missing models stay refused (dev-admin)

When the runtime reports `productionBindingsReady != true`, the gate requires:

- `runtime:model_limitation_reported`: `productionBindingsReady=false`,
  `autoSeeded=false`, mode not `mlflow-production`, a non-empty composition
  `error`, and the `PRODUCTION_MODEL_BINDINGS_UNVERIFIED` blocking reason.
- `runtime:model_capability:<service>`: ForecastOps is either really bound
  (`available=true`, no reason) or truthfully unavailable (`available=false`
  with its `reasonCode`). The governed-disabled services keep their full
  receipt-backed evidence checks unchanged.
- `models:registry_refused`: `GET /api/v1/learninghub/models` must answer
  **503**. A 200 response means model versions were served without the approved
  registry, which counts as a manufactured state. This holds even for an empty
  list. A 401 or 403 is reported as an `auth` problem.

No model version, alias, lineage, approval or fallback prediction is created
anywhere. Model training, backfill and promotion tasks stay open. A passing
dev-admin gate cannot close them, and its receipt says so:
`release_profile.model_readiness_claimed=false` and
`full_product_acceptance_claimed=false`.

## 5. Administration proof: the Web password journey (dev-admin)

The gate signs in through the deployed Web origin the way a browser does. It
uses the password form endpoint, the sealed session cookie, and the BFF proxy
that swaps the cookie for the server-side session bearer. It never injects a
bearer, role or tenant header. Each step is a separate named check:

| # | Operation | Check | Fails on |
|---|---|---|---|
| 1 | anonymous `/auth/session`, anonymous `/api/v1/operator/bootstrap` | `session:anonymous_session_denied`, `session:anonymous_api_denied` | either served |
| 2 | `POST /login` with a wrong password | `session:invalid_credentials_refused` | anything but 401 `AUTH_INVALID_CREDENTIALS`, or any session cookie issued |
| 3 | `POST /login` (JSON, Web `Origin`) as the provisioned account | `session:password_login` | not 200 `ok` for that subject, or no session cookie (for example, the PostgreSQL session store or throttle is down: 503) |
| 4 | `GET /auth/session` with the cookie | `session:session_resolves_account` | subject is not the signed-in account |
| 5 | `GET /api/v1/operator/bootstrap` through the session | `session:operator_bootstrap` | not 200 live provenance, or a surrogate marker |
| 6 | the same read with `x-operator-role` set to a role the account does not hold | `session:wrong_role_denied` | not 403 |
| 7 | `POST /api/v1/jobs` (`external-fetch`, tenant bound from the session, idempotency key), then `GET /api/v1/jobs/{id}` | `session:job_enqueue`, `session:job_readback` | not 202 created with an audit event id; not read back from the durable queue |
| 8 | `POST /api/v1/jobs` with `payload.tenant_id` set to another tenant | `session:cross_tenant_denied` | not 403 `TENANT_SCOPE_MISMATCH` |
| 9 | `GET /api/v1/audit/events?correlation_id=<journey>` | `session:audit_persisted` | missing the hash-chained accepted enqueue for the job, or missing the denied enqueue |
| 10 | `POST /auth/logout`, then replay the old cookie on `/auth/session` and the API | `session:logout`, `session:revoked_session_refused`, `session:revoked_api_refused` | logout not durable (503) or cookie not cleared; revoked cookie still served |

These are the supported core operations the live verifier exercises. All of
them run against the candidate that `runtime:release_profile` and
`release:platform_version` (exact release SHA) bind. The existing core checks
also run unchanged: PostgreSQL persistence, the operator repository and live
data origin, the provider posture, worker enqueue/idempotent replay/drain and
terminal state, the durable audit receipt and its integrity, and ingestion
receipts.

The password comes only from a secret. The report redacts it, and it never
appears in a check detail.

## 6. Operator / coordinator handoff

**Changed release interface**

- `deploy-dev.yml` adds the build-phase input `release_profile` (`full` |
  `dev-admin`, default `full`), the admission job output `release_profile`,
  and the deploy job env `ODP_RELEASE_PROFILE`, which comes from admission
  only. The Cloud Run deploy step now reads three more values:
  `vars.ODP_DEV_ADMIN_USERNAME`, `secrets.ODP_DEV_ADMIN_PASSWORD` and
  `vars.ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE`.
- `build_release_handoff.py --release-profile`. Its GitHub output adds
  `release_profile`.
- The manifest gains the optional field `release_profile`. Admission receipts
  gain the field `release_profile`.
- `check_live_e2e_gate.py --release-profile` (default: `$ODP_RELEASE_PROFILE`,
  otherwise `full`). It reads `ODP_DEV_ADMIN_USERNAME`,
  `ODP_DEV_ADMIN_PASSWORD` and `ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE`.
- The API and Web runtime env carry `ODP_RELEASE_PROFILE`, and the API
  `/readiness` carries `details.releaseProfile`.

**Prerequisites in the `dev` GitHub environment** (vars are environment-scoped)

- `ODP_DEV_ADMIN_USERNAME`: an existing operator account in `identity` on dev
  PostgreSQL. It needs a local password, Operator Console access,
  `external-fetch` job create/view and `audit:view`. Provision it through the
  existing account bootstrap. Do not seed a fixed password.
- `ODP_DEV_ADMIN_PASSWORD` (environment **secret**): that account's password.
- `ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE`: an Operator Console role id the account
  does **not** hold, for example `cs-lead` for an account without it. If the
  account holds every role, the wrong-role probe cannot be proven and the gate
  stays red. Use a narrower account in that case.

**Candidate.** The build, deploy and gate code runs from the release SHA's own
tree. A dev-admin release therefore needs a candidate built from a SHA that
contains this change. The existing candidate cannot be relabelled. It keeps
its `full` digest and lease.

**One build, then an exact-tuple deploy** (for the coordinator to approve; this
task performs neither):

```
gh workflow run deploy-dev.yml --ref dev \
  -f phase=build -f environment=dev -f release_sha=<C> \
  -f task_id=<candidate task> -f release_profile=dev-admin [first-release/rollback inputs as today]
# Supervisor issues the lease against the build's manifest_digest (it now covers release_profile)
gh workflow run deploy-dev.yml --ref dev \
  -f phase=deploy -f environment=dev -f release_sha=<C> -f task_id=<candidate task> \
  -f manifest_run_id=<build run> -f manifest_digest=<digest> -f release_lease=<lease> \
  -f api_image=... -f web_image=... -f worker_image=... -f scheduler_image=...
```

**Supervisor runtime.** Adopting this code is not required for safety. The
Supervisor (`.orchestrator/release_lease.py` → `load_manifest`) already accepts
the new top-level field. The lease binds the digest, which covers the profile,
and admission at the dispatch SHA enforces the dev-only binding before the
lease is consumed. It is still **recommended** to adopt it before issuing a
dev-admin lease. Issuance then validates the profile structure as well, and a
malformed profile is refused at issuance rather than at admission.

## 7. Verification

Offline regression suites (fixtures are test inputs, never live evidence):

- `tests/release/test_release_profile.py`: build and seal; digest tamper
  (strip, rename, retarget, narrow a full manifest); malformed profiles;
  staging/production admission refusal; lease left `issued`; handoff refusals;
  deploy entrypoint refusals before any `gcloud`/`docker`/`uv` call; the same
  profile vocabulary across manifest, runtime and gate.
- `tests/e2e/test_live_e2e_gate_dev_admin.py`: the positive dev-admin path
  with missing models; the full profile still rejecting the same runtime;
  scope negatives refused before any request (unknown profile,
  staging/production, mismatches in both directions, missing inputs);
  manufactured model states; a registry that does not refuse; every step of
  the session journey broken in turn (unauthenticated, wrong password, session
  store down, wrong account, wrong role, cross-tenant write, failed job
  persistence, unaudited denial, non-chained audit, audit store down,
  non-durable logout, revocation); password redaction; and the **real API
  composition** under `dev-admin` with no MLflow binding, which satisfies the
  dev-admin limitation checks and fails the full binding checks.
- `tests/e2e/test_live_e2e_gate.py`: the existing full-profile suite,
  unchanged except that the readiness fixture now carries the `releaseProfile`
  block the runtime emits.

Exact commands, exit codes and the head they ran at are recorded in
`docs/evidence/runtime/ODP-DEV-ADMIN-RELEASE-READINESS-001/README.md` and in
the task note. A live dev run is the coordinator's next step and is not
claimed here.
