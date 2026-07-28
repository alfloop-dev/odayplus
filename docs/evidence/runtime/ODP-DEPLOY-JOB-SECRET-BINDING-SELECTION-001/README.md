# ODP-DEPLOY-JOB-SECRET-BINDING-SELECTION-001 evidence

## The failure, from the exact receipts

Deploy Dev run [30376737123](https://github.com/alfloop-dev/odayplus/actions/runs/30376737123)
at `dda726155a399487474ae148b4dc1c3294ea9463` built and cosign-signed the API,
worker, and scheduler images. The immutable migration candidate
`oday-migration-r-dda726155a39` was deployed and **executed successfully** as
`oday-migration-r-dda726155a39-ndb4l`.

The deployment then aborted inside the migration gate on exactly one check:

```text
jobs-smoke:migration:secret_bindings
```

Rollback restored API revision `oday-api-00005-gin` and Web revision
`oday-web-00008-ws4`.

The check failed for a configuration that was correct. `cloud_run_job_checks`
proved secret bindings like this:

```python
required_secret_envs = (
    "oday_database_url",
    "odp_listing_provider_api_key",     # <-- always demanded
    "odp_poi_provider_api_key",
    "odp_geocode_provider_api_key",
    "odp_admin_boundary_provider_token",
)
all(name in description_text for name in required_secret_envs)
```

`description_text` is `json.dumps(job_description).lower()` — a substring scan
over the whole job description. The release selected

```text
ODP_PRODUCTION_PROVIDER_IDS=poi.commercial_api,geocode.primary_api,admin_boundary.official_dataset
```

`listing.partner_feed` is not in that set, so `scripts/deploy_cloud_run_waji.sh`
never adds `ODP_LISTING_PROVIDER_API_KEY` to `API_SECRET_BINDINGS`, and the
string `odp_listing_provider_api_key` never appears in the job description. The
gate demanded a secret for a provider the release deliberately does not deploy.

That is reproduced mechanically by
`test_job_smoke_reproduces_run_30376737123_secret_binding_failure`, which
asserts on the run's own receipt shape that

```python
assert "odp_listing_provider_api_key" not in json.dumps(job).lower()
```

and then requires every check to pass.

## Delivered boundary

The requirement is now derived, not hardcoded, and it is structural rather than
textual. `job_secret_binding_checks` does four things:

1. **Reads the job's own env, from the authoritative task template only.**
   `_authoritative_job_containers` resolves containers at exactly one of the two
   canonical paths — Knative `spec.template.spec.template.spec.containers` or v2
   `template.template.containers` — and rejects a description whose containers
   are absent, declared at both paths, or accompanied by a `containers` key
   anywhere off that path. Migration, worker, and scheduler jobs all use the
   same reader.
2. **Reads the selection from the deployed job.** The plaintext
   `ODP_PRODUCTION_PROVIDER_IDS` env entry of the job under test is the
   authority for what that job actually selected, and it must occur exactly once
   across the authoritative container set.
3. **Derives the required secrets from the provider registry.**
   `required_job_secret_env_vars` returns `ODAY_DATABASE_URL` plus every
   `required_in_live` credential `env_var` of each selected provider, read from
   `modules.external_data.connectors.provider_registry`. Adding, renaming, or
   re-scoping a provider credential updates the deploy gate with no second copy
   of the mapping to drift.
4. **Proves each binding is exactly one secret reference.**
   `_secret_binding_proof` requires the required env var to be declared by
   **exactly one** env entry, that entry to declare **exactly one** secret
   source, and that source to be this job's own dialect —
   `valueFrom.secretKeyRef.name` (Knative) or `valueSource.secretKeyRef.secret`
   (v2), and only when the reference is not a placeholder. The container path
   picks the dialect (`_JOB_API_SCHEMAS` holds the two paths with their pairs),
   and `_foreign_secret_binding_locations` rejects anything else the entry
   carries: the other dialect's env source, a `secretKeyRef` hoisted to the top
   level, or the other dialect's reference key inside this dialect's own source.
   "Exactly one source" also holds *inside* the accepted source:
   `_unsupported_secret_source_members` rejects any member of `valueFrom` /
   `valueSource` other than `secretKeyRef` — `configMapKeyRef` first of all,
   which Cloud Run v1 does not support — and any member inside `secretKeyRef`
   that the dialect's own `SecretKeySelector` does not define.

Substring scanning is gone for this check: a job that merely mentions
`ODAY_DATABASE_URL` in a label or an argument no longer satisfies it.

## Fail-closed matrix

| Job under test | Outcome |
| --- | --- |
| selection excludes `listing.partner_feed`, key not bound | **passes** (run 30376737123's case) |
| selection includes `listing.partner_feed`, key not bound | `secret_bindings` fails, naming `ODP_LISTING_PROVIDER_API_KEY` |
| selection includes `listing.partner_feed`, key bound | passes |
| `ODAY_DATABASE_URL` not bound | `secret_bindings` fails, for every selection |
| any single selected provider secret not bound | `secret_bindings` fails, naming it |
| selected provider secret set as a plaintext `value` | `secret_bindings` fails; the literal is never echoed into the detail or report |
| env entry with no `valueFrom`/`valueSource` | `binding declares no usable secretKeyRef` |
| empty `valueFrom` / `valueSource` / `secretKeyRef` | same |
| `secretKeyRef` naming a placeholder (`placeholder`, `changeme`, …) | same |
| `secretKeyRef` at the top level of the env entry | same — not a Cloud Run schema |
| `valueFrom.secretKeyRef.secret` (v2 key in the Knative source) | same — not a Cloud Run schema |
| `valueSource.secretKeyRef.name` (Knative key in the v2 source) | same — not a Cloud Run schema |
| a required secret env declared twice, naming different secrets | `secret_bindings` fails — the authoritative binding is ambiguous |
| a required secret env declared twice, naming the same secret | same — a repeated env var has no defined winner |
| a required secret env bound once and set to a plaintext value once | same |
| a valid binding beside a conflicting off-dialect source (`valueSource` on a Knative job, or the reverse) | `secret_bindings` fails, naming the off-schema location and this job's dialect |
| a valid binding beside a top-level `secretKeyRef` | same |
| `valueFrom.secretKeyRef` carrying both `name` and `secret` | same |
| a valid binding beside another member of its **own** env source (`valueFrom.configMapKeyRef`, `valueFrom.fieldRef`, `valueFrom.resourceFieldRef`, and the `valueSource` mirrors) | `secret_bindings` fails, naming the member and the only source Cloud Run resolves — v1 does not support `configMapKeyRef` |
| `secretKeyRef` carrying a member its own dialect does not define (`valueFrom.secretKeyRef.value`, `valueSource.secretKeyRef.key`) | same — a planted field cannot ride along inside a valid reference |
| `valueFrom.secretKeyRef.optional` (a field the Knative selector really defines) | passes — the member rule is an allowlist of the API's own fields, not a two-key rule |
| a valid binding beside an empty off-dialect source (`"valueSource": {}`) | same — a source gcloud does not emit |
| a valid binding beside a blank or non-string literal (`"value"` set to `""`, `"   "`, `0`, `false`, `[]`, `{}`, or `null`) | same — an env entry carries a literal or a secret source, never both |
| plaintext `ODP_PRODUCTION_PROVIDER_IDS` on an entry that also declares an off-dialect secret source | `provider_selection` **and** `secret_bindings` fail: the selection is unprovable |
| plaintext `ODP_PRODUCTION_PROVIDER_IDS` on an entry that also declares an empty **same-dialect** source (`"valueFrom": {}` on a Knative job, `"valueSource": {}` on a v2 job) | same — a declared source makes the literal unreadable, empty or not |
| Knative container path, every secret in the v2 `valueSource.secretKeyRef.secret` schema | `secret_bindings` fails, naming each env var and the required `valueFrom.secretKeyRef.name` |
| v2 container path, every secret in the Knative `valueFrom.secretKeyRef.name` schema | `secret_bindings` fails, naming each env var and the required `valueSource.secretKeyRef.secret` |
| no plaintext `ODP_PRODUCTION_PROVIDER_IDS` in the job | `provider_selection` **and** `secret_bindings` both fail: the selection is unprovable |
| `ODP_PRODUCTION_PROVIDER_IDS` supplied only as a secret reference | same — an unreadable selection proves nothing |
| `ODP_PRODUCTION_PROVIDER_IDS` declared twice, with different values | same — the effective selection is ambiguous |
| `ODP_PRODUCTION_PROVIDER_IDS` declared twice, identically | same — nothing proves which one the runtime reads |
| secret refs planted at `metadata.containers` (or any off-path `containers`) | same — the description is rejected before any binding is read |
| containers declared at both the Knative and the v2 path | same — the authoritative task template is ambiguous |
| the task template declares more than one container (secrets or selection in a sidecar) | same — the authoritative task container is ambiguous |
| no containers at either canonical path, or an empty/non-object container list | same |
| selection names a provider the registry does not know | `provider_selection` and `secret_bindings` fail |
| provider registry cannot be imported | both fail with the import error |
| job selection ≠ release `ODP_PRODUCTION_PROVIDER_IDS` | `selected_provider_release_match` fails |
| release allowlist present but empty | `selected_provider_release_match` fails |
| a provider secret bound but not selected | passes; reported under `unselected_provider_secret_env_vars` |

The empty-`valueSource` rows are a deliberate tightening: the previous fixtures
used `{"name": "...", "valueSource": {}}`, which is not a binding gcloud emits
and which proves nothing about Secret Manager.

The three off-schema `secretKeyRef` rows close a gap Codex6 found at head
`d6bb605a`: the reference lookup had walked
`(valueFrom, valueSource, entry)` × `(secret, name)`, so six shapes resolved
where only two are real. All three off-schema shapes are now parametrized
regression cases in `test_job_smoke_rejects_malformed_secret_binding`
(9 cases total).

## Round 3: the two exploits Codex6 found at head `76063434`

Both were real bypasses of a check that reported `ok`. Both now fail closed, and
each has a regression that fails against the pre-fix validator.

### 1. Containers were located by shape, so any mapping could carry the proof

`_iter_job_containers` walked the whole description and yielded every list under
a `containers` key. A description whose real Knative task template bound **zero**
secrets therefore satisfied `secret_bindings` by planting the refs at
`metadata.containers` — a path Cloud Run never runs anything from.

Containers are now read only from the two canonical paths, one of which must be
present and unambiguous, and a `containers` key found anywhere else rejects the
whole description rather than contributing env entries. Off-path locations are
named in the failure detail (`metadata.containers`), so a genuinely new gcloud
schema surfaces as an explicit rejection to be reviewed, not as a silent pass.

Regressions: `test_job_smoke_rejects_secret_refs_planted_outside_the_task_template`
(planted beside a real template, and planted with no real template at all) and
`test_job_smoke_rejects_an_ambiguous_or_unreadable_task_template`.

### 2. The selection was the first plaintext hit, so a wider one could hide

`_job_plaintext_env` took the first nonempty `value` for each env name and
`break`ed. A description declaring `ODP_PRODUCTION_PROVIDER_IDS` twice — the
three normal providers first, then `...,listing.partner_feed` — was validated
against the narrow first value and passed without
`ODP_LISTING_PROVIDER_API_KEY`, while nothing establishes which occurrence the
runtime reads.

`_job_selected_provider_ids` replaces it: the env var must occur **exactly once**
inside the authoritative task container and be readable plaintext. A duplicate
(conflicting or identical, same container or a sibling), a secret-bound
occurrence, and a missing or blank value all leave the selection unprovable and
fail `provider_selection` and `secret_bindings` together. Uniqueness rather than
consistency is the rule because a repeated env var has no defined winner, and
`gcloud run jobs deploy --set-env-vars` emits it once.

Regression: `test_job_smoke_rejects_a_duplicate_provider_selection` (3 cases:
conflicting, identical, plaintext-beside-secret) and
`test_job_smoke_rejects_a_selection_declared_by_a_second_container`.

The scheduler fixture in
`test_job_smoke_rejects_failed_execution_and_missing_provider_secrets` was moved
onto the canonical Knative path; it had sat at `spec.template.containers` and
would otherwise have been rejected as off-path before reaching the missing-secret
assertion it exists to make.

## Round 4: the same planting exploit, one level down

Round 3 fixed *where* containers come from but still merged env across **every**
container in the authoritative task template. That left the identical bypass
inside the canonical path: a job whose real task container binds nothing passes
`secret_bindings` as long as a **sidecar** in the same template carries
`ODAY_DATABASE_URL` and all three selected provider secrets. Verified against
head `49e65382` — the crafted description fails **no** check at all
(`_failed_names(checks) == set()`).

Nothing in a Cloud Run job description says which container runs the task
(`image`/`args` are attacker-controlled in the same payload), so a second
container makes the question unanswerable rather than merely harder.
`_authoritative_task_container` therefore requires the task template to declare
**exactly one** container and reads env only from it; anything else fails
`provider_selection` and `secret_bindings` with
`job task template declares N containers`. This matches what
`scripts/deploy_cloud_run_waji.sh` creates — `gcloud run jobs deploy` with one
image and no `--container` sidecars — so a sidecar arriving later is a
deliberate deployment change that must be reviewed here, not silently trusted.

Regressions: `test_job_smoke_rejects_secrets_bound_only_by_a_sidecar_container`
(the exploit above) and `test_job_smoke_rejects_a_selection_declared_by_a_second_container`
(unchanged intent, now rejected at the container count).

## Round 5: the schema pair was accepted independently of the container path

Round 4 established *which* container is authoritative but still resolved secret
references by trying **both** schema pairs on every entry. The container path and
the secret schema are one fact — the API version that places containers at
`spec.template.spec.template.spec.containers` is the same one that writes
`valueFrom.secretKeyRef.name` — and treating them as two independent facts let a
whole description cross over. Verified against head `5b9c430a`: a Knative job
whose four required secrets are all written in the v2
`valueSource.secretKeyRef.secret` schema failed **no** check, and a v2 job whose
secrets are all written in the Knative `valueFrom.secretKeyRef.name` schema
failed none either — `_failed_names(checks) == set()` for both. Neither shape is
one `gcloud run jobs describe` emits, so both were passing on a binding that
proves nothing about Secret Manager, contradicting the evidence contract this
file states above and the malformed-bindings-fail-closed acceptance.

The two dialects are now a single record, `_JobApiSchema`, holding the container
path together with its env source key and secret reference key.
`_authoritative_job_containers` returns the schema it matched rather than
discarding it, `_authoritative_task_container` and `_job_env_entries` thread it
through, and `_secret_reference_name(entry, schema)` accepts only that schema's
pair. The failure detail names the pair the description owes
(`binding declares no usable valueFrom.secretKeyRef.name`), so a genuinely new
gcloud dialect surfaces as an explicit rejection naming what it violated, not a
silent pass. Cross-dialect keys *within* one source (`valueFrom.secretKeyRef.secret`)
were already rejected in round 2 and stay rejected — this round closes the case
where the entire description is consistently in the other dialect.

The selection read is bound the same way: `ODP_PRODUCTION_PROVIDER_IDS` supplied
through the other dialect's secret source is no longer recognised as a secret
binding, but it is not readable plaintext either, so it still fails
`provider_selection` and `secret_bindings` together rather than being read.

Regressions: `test_job_smoke_rejects_a_knative_job_whose_secrets_use_the_v2_schema`,
`test_job_smoke_rejects_a_v2_job_whose_secrets_use_the_knative_schema`, and
`test_job_smoke_accepts_each_dialect_at_its_own_container_path`, which pins that
the discriminator does not break the two shapes gcloud really emits.

## Round 6: a binding was well formed but not unique

Rounds 3–5 established *which container* and *which dialect* the proof reads.
Neither made the binding itself singular, so the check could report `ok` for a
description that names two different secrets for the same env var. Verified
against head `dd4acb0b` — all four crafted descriptions below fail **no** check
at all (`_failed_names(checks) == set()`), while the unmodified receipt fixture
still passes:

| crafted description at `dd4acb0b` | result |
| --- | --- |
| `ODP_POI_PROVIDER_API_KEY` bound twice, to `odp-poi-provider-api-key` and to `attacker-controlled-secret` | passed |
| one entry with valid `valueFrom.secretKeyRef.name` **and** conflicting `valueSource.secretKeyRef.secret` | passed |
| one `valueFrom.secretKeyRef` carrying both `name` and a conflicting `secret` | passed |
| valid `valueFrom.secretKeyRef.name` beside a top-level `secretKeyRef` naming another secret | passed |

Both defects are the same mistake in two places: the proof *read* one binding
and *ignored* whatever else the entry or the env list declared. Ignoring is not
rejecting. A description could therefore name one secret to this gate and a
different one to any reader that prefers the other occurrence or the other
dialect, which is exactly the crossed/off-schema shape the fail-closed matrix
above claims is rejected.

Two rules close it:

- **One entry per required env.** `_secret_binding_proof` now fails when the
  authoritative task container declares a required env var more than once,
  whatever the extra entries say — different secret, identical secret,
  plaintext, or malformed. Uniqueness rather than agreement, matching the
  `ODP_PRODUCTION_PROVIDER_IDS` rule from round 3: a repeated env var has no
  defined winner, and `gcloud run jobs deploy --set-secrets` emits it once.
- **One secret source per entry.** `_foreign_secret_binding_locations(entry,
  schema)` returns every secret-binding location outside this job's dialect —
  the other dialect's env source, a top-level `secretKeyRef`, and the other
  dialect's reference key inside this dialect's own source. Any hit rejects the
  binding and the detail names both the off-schema location and the dialect the
  description owes (`binding declares off-schema secret sources (valueSource);
  this job's dialect is valueFrom.secretKeyRef.name`). The same predicate guards
  the selection read, so a plaintext `ODP_PRODUCTION_PROVIDER_IDS` that also
  carries an off-dialect secret source is unprovable rather than read.

An empty off-dialect source (`"valueSource": {}` on a Knative job) is rejected
too: gcloud emits one dialect per description, so the key's presence is the
defect, not its contents.

A uniqueness rule can only be a tightening if the real deployment cannot trip
it, so that was checked against `scripts/deploy_cloud_run_waji.sh` rather than
assumed. Each job is deployed with `--env-vars-file="${API_ENV_FILE}"` **and**
`--set-secrets="${API_SECRET_BINDINGS}"`, which is the one way a required env
var could arrive twice. It cannot: the env file is written from an explicit
`keys` allowlist (`ODAY_RELEASE_SHA`, `ODP_PRODUCTION_PROVIDER_IDS`, the deploy
mode flags, and the selected providers' URL/auth-status keys) that contains no
secret env var, while `API_SECRET_BINDINGS` is a comma list naming
`ODAY_DATABASE_URL`, `ODP_AUTH_PRINCIPAL_MAP`, and each selected provider
credential exactly once. The two sets are disjoint, and
`ODP_PRODUCTION_PROVIDER_IDS` is supplied only by the env file, so the selection
entry carries no secret source either.

Regressions: `test_job_smoke_rejects_a_duplicate_required_secret_binding`
(4 cases), `test_job_smoke_rejects_an_entry_mixing_secret_binding_dialects`
(4 cases), `test_job_smoke_rejects_a_v2_entry_mixing_secret_binding_dialects`
(the mirror on the v2 container path), and
`test_job_smoke_rejects_a_selection_entry_carrying_an_off_dialect_secret`. All
ten assert that neither the conflicting secret name nor the plaintext key
reaches the detail or the report.

## Round 7: presence of a conflicting key was judged by its payload

Round 6 made the binding singular but decided *whether a second source exists*
by looking at what that source contained. Two predicates asked the wrong
question, and both reported `ok` for descriptions that declare an env var
twice. Verified against head `15e7ec64` — every crafted description below
fails **no** check at all (`_failed_names(checks) == set()`):

| crafted description at `15e7ec64` | result |
| --- | --- |
| valid `valueFrom.secretKeyRef.name` on an entry that also sets `"value": ""` (also `"   "`, `"\t\n"`, `0`, `false`, `[]`, `{}`, `null`) | passed |
| the same eight literals on the v2 path beside a valid `valueSource.secretKeyRef.secret` | passed |
| plaintext `ODP_PRODUCTION_PROVIDER_IDS` on an entry that also declares `"valueFrom": {}` (also `{"secretKeyRef": {}}`, `{"secretKeyRef": {"key": "latest"}}`) | passed |
| the same three sources on the v2 path as `"valueSource"` | passed |

- `_secret_binding_proof` tested `isinstance(value, str) and value.strip()`, so
  only a *truthy string* literal counted as a literal. Every falsy or
  non-string payload left the entry looking like a pure secret binding while
  the description still carried a `value` key for any reader that prefers it.
- `_declares_any_secret_binding` — which guarded the selection read — returned
  true only when this dialect's reference **resolved** or an **off-dialect**
  location was present. A same-dialect source that declares a binding without
  resolving one (`"valueFrom": {}`) was therefore invisible: the gate read the
  plaintext selection and validated the required secret set against a value the
  runtime resolves from Secret Manager instead.

Both are now presence rules, matching the round-6 empty-`valueSource` decision
that the key's presence is the defect rather than its contents:

- **The literal is the `value` key.** `_secret_binding_proof` fails on
  `"value" in entry` whatever the payload, with a detail that names the key and
  not its contents (`declares a literal value key beside its secret source;
  gcloud emits one or the other, never both`). A truthy string keeps its
  existing `bound to a plaintext value` detail, so the round-2 redaction
  assertions are unchanged.
- **A source is any declared source.** `_declared_secret_source_locations`
  replaces `_declares_any_secret_binding` and reports this dialect's env source
  key by presence, on top of every off-dialect location round 6 already
  reported. `_job_selected_provider_ids` fails on any hit and names the
  locations (`ODP_PRODUCTION_PROVIDER_IDS declares secret sources (valueFrom)
  beside its literal value`), so `provider_selection` and `secret_bindings`
  fail together as they already do for the unreadable-selection cases.

The real deployment cannot trip either rule, checked against
`scripts/deploy_cloud_run_waji.sh` rather than assumed, and it is the same
disjointness round 6 established: `--env-vars-file` writes an explicit non-secret
`keys` allowlist and `--set-secrets` names each secret env exactly once, so no
required secret env var ever receives a `value` key and the
`ODP_PRODUCTION_PROVIDER_IDS` entry never receives a secret source. `gcloud run
jobs describe` emits one or the other per env entry, so a description carrying
both is not a shape the deploy path can produce.

Regressions: `test_job_smoke_rejects_a_secret_binding_carrying_a_literal_value_key`
(8 literal payloads, each asserted on the Knative **and** the v2 container path)
and `test_job_smoke_rejects_a_selection_entry_declaring_an_empty_same_dialect_source`
(3 same-dialect sources, both dialects). The v2 mirrors go through a new
`_v2_job_with_envs` helper that exposes the env knobs `_knative_job` already had.

## Round 8: the accepted source was never read past `secretKeyRef`

Rounds 6 and 7 made the env *entry* carry exactly one secret source. Nothing
looked **inside** that source. `_secret_reference_name` reads `secretKeyRef` and
`_foreign_secret_binding_locations` reports only the *other dialect's* keys, so
a member sitting beside `secretKeyRef` in the accepted dialect's own source was
invisible. Verified against head `d3dfeb13` — every crafted description below
fails **no** check at all (`_failed_names(checks) == set()`):

| crafted description at `d3dfeb13` | result |
| --- | --- |
| required secret with a valid `valueFrom.secretKeyRef.name` **and** a `valueFrom.configMapKeyRef` | passed |
| the same with `valueFrom.fieldRef` and with `valueFrom.resourceFieldRef` | passed |
| the v2 mirror: valid `valueSource.secretKeyRef.secret` **and** `valueSource.configMapKeyRef` (also `fieldRef`) | passed |
| `valueFrom.secretKeyRef` carrying a planted `value` beside a valid `name` (v2: a `key` beside a valid `secret`) | passed |

`configMapKeyRef` is the load-bearing case Codex6 named. Knative's
`EnvVarSource` defines it, **Cloud Run v1 does not support it**, and an entry
declaring it names a second value for one env var — exactly the ambiguity round
6 rejected across dialects and round 7 rejected for `value` keys, one level
further in. The planted-member row is the same fail-open inside the reference:
a `value` beside `name` is not the other dialect's key, so the round-6 rule
never saw it.

The fix is one more presence rule, `_unsupported_secret_source_members`, applied
in `_secret_binding_proof` after the off-dialect check:

- **A secret source declares only `secretKeyRef`.** Any other member of
  `valueFrom` / `valueSource` fails closed, named in the detail (`binding
  declares env source members Cloud Run does not resolve
  (valueFrom.configMapKeyRef); the only supported source is
  valueFrom.secretKeyRef.name`).
- **A reference declares only its own dialect's selector fields.**
  `_JobApiSchema` gains `reference_members` — Knative's `SecretKeySelector`
  (`name`, `key`, `optional`, the deprecated `localObjectReference`) and Cloud
  Run v2's (`secret`, `version`) — so the rule is an allowlist of fields the
  APIs really define rather than a two-key rule. `optional` on a Knative job
  still passes, pinned by
  `test_job_smoke_accepts_the_optional_members_each_dialect_defines`.

Cross-dialect keys inside `secretKeyRef` (`valueFrom.secretKeyRef.secret`) stay
the round-6 rule's business: the off-dialect check runs first, so those details
and their round-6 assertions are unchanged.

The real deployment cannot trip either rule, checked against
`scripts/deploy_cloud_run_waji.sh` rather than assumed:
`gcloud run jobs deploy --set-secrets` is the only way secrets reach these jobs,
and it emits `secretKeyRef` alone — there is no `--set-config-maps` or
equivalent anywhere in the deploy path, and Cloud Run has no ConfigMap resource
to bind. Nothing in the script can produce a second source member.

Regressions: `test_job_smoke_rejects_a_knative_source_member_beside_secret_key_ref`
(3 members), `test_job_smoke_rejects_a_v2_source_member_beside_secret_key_ref`
(2 members on the v2 container path),
`test_job_smoke_rejects_a_member_planted_inside_the_secret_key_ref` (both
dialects), and the `optional` control. Each asserts the planted name never
reaches the detail or the report.

## Check and report surface

`jobs-smoke:<kind>:secret_bindings` keeps its name, so the deploy gate and any
existing triage against run 30376737123 still refer to the same check. Two
checks are added:

- `jobs-smoke:<kind>:provider_selection` — the job declares a readable,
  registry-known provider allowlist.
- `jobs-smoke:<kind>:selected_provider_release_match` — emitted only when the
  validating process has `ODP_PRODUCTION_PROVIDER_IDS` (the deploy script
  always exports it, since it writes the same value into the job env file); the
  deployed job's selection must equal the release's, compared as sets.

The report gains `selected_provider_ids`, `required_secret_env_vars`,
`secret_bound_env_vars`, `unselected_provider_secret_env_vars`, and (when
cross-checked) `release_provider_ids`. All are env-var and provider **names**;
`secret_values_redacted` remains `true` and
`test_job_smoke_rejects_plaintext_provider_secret` asserts that a plaintext key
placed in the job description never reaches the detail text or the report.

## Unchanged by this task

- `scripts/deploy_cloud_run_waji.sh`, both deploy workflows, and the job proof
  capture path (`capture_latest_execution`, `resolve-latest-execution`).
- The `jobs-smoke` CLI surface: same subcommand, same required arguments.
- `release_sha`, `entrypoint`, `execution`, and `execution_receipt` checks.
- Preflight, smoke, compatibility-smoke, traffic, and scheduler rollback logic.
- API, Package 10, model registry, and OperatorStateService scope.

## Focused verification

Executed from the task branch on the round-8 tree (parent `d3dfeb13`), with
`export PATH="$HOME/.local/bin:$PATH"`:

```text
python3 -m pytest tests/ops/test_cloud_run_live_deployment.py -p no:randomly   # 125 passed
python3 -m pytest tests/ops -p no:randomly                                     # 180 passed, 20 skipped
python3 -m ruff check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
python3 -m ruff format --check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
git diff --check
```

Ruff check, ruff format `--check`, and `git diff --check` all passed. Both pytest
runs are fully green on this worker; the single environmental failure earlier
rounds reported,
`test_deploy_preflight_imports_runtime_dependencies_via_locked_python`, needs
`uv` on `PATH` and now passes because `uv` is present
(`/home/lupin/.local/bin/uv`).

The focused-file count by round was 87 at `76063434` (round 2), 93 at `49e65382`
(round 3, six new regressions), 94 at `5b9c430a` (round 4, the sidecar test), 97
at `dd4acb0b` (round 5, the two crossed-whole-schema regressions plus the
both-dialects control), 107 at `15e7ec64` (round 6, ten uniqueness and
mixed-dialect regressions), 118 at `d3dfeb13` (round 7, eleven presence-rule
cases, each asserting on both container paths), and 125 now — round 8 adds six
source-member regressions plus the `optional` control that pins the allowlist
against over-tightening.

Each round's regressions fail against that round's pre-fix validator, verified by
restoring the parent commit's
`scripts/deployment/validate_cloud_run_live_deployment.py` under the new test
file: `6 failed` for round 3 against `76063434`; for round 4 against `49e65382`
the sidecar test fails with `assert 'jobs-smoke:migration:provider_selection' in
set()`; for round 5 against `5b9c430a` both crossover tests fail with
`assert 'jobs-smoke:<kind>:secret_bindings' in set()`; and for round 6 against
`dd4acb0b` exactly the ten new cases fail — `assert
'jobs-smoke:migration:secret_bindings' in set()` for the eight binding cases and
`assert 'jobs-smoke:migration:provider_selection' in set()` for the selection
case — the pre-fix validator passing every crafted description with zero failing
checks, reproducing the `[]` the round-6 review reported. For round 7 against
`15e7ec64` the same restore run reports `11 failed, 107 passed`: the eight
literal-payload cases fail with
`assert 'jobs-smoke:migration:secret_bindings' in set()` and the three
same-dialect source cases with
`assert 'jobs-smoke:migration:provider_selection' in set()`, which is the
independent reproduction of both fail-opens Codex6 reported at that head. For
round 8 against `d3dfeb13` the restore run reports `6 failed, 119 passed`:
exactly the six new source-member cases fail, each with
`assert 'jobs-smoke:<kind>:secret_bindings' in set()` — the pre-fix validator
returning zero failing checks for a required secret that declares
`configMapKeyRef` beside a valid `secretKeyRef`, on both container paths, which
is the reproduction of the round-8 review finding. The seventh new test, the
`optional` control, passes against both validators, so the tightening is proven
narrow. The control tests
(`test_job_smoke_accepts_each_dialect_at_its_own_container_path` and the run
30376737123 receipt) pass against both validators.

Exact-head CI and an independent Codex6 review are required before merge. After
merge, ODP-P10-DEV-REDEPLOY-VERIFY-001 must re-run from the exact merged `dev`
SHA; that rerun is the live proof that run 30376737123's migration gate now
clears with the same provider selection.

## Merge blocker: `product` fails on an unrelated runner-bound perf budget

At head `ef048b0f`, CI run
[30380735899](https://github.com/alfloop-dev/odayplus/actions/runs/30380735899)
fails the required `product` check on one test, twice (original and
`gh run rerun --failed`):

```text
FAILED tests/performance/test_load_and_soak.py::test_concurrency_and_soak_execution
  AssertionError: P95 latency 7.518s exceeded budget of 3.0s   # 17:12Z
  AssertionError: P95 latency 6.956s exceeded budget of 3.0s   # 17:29Z
1 failed, 1968 passed, 68 deselected
```

This task cannot be its cause:

- The whole diff is `scripts/deployment/validate_cloud_run_live_deployment.py`
  (a standalone CLI never imported by the API), `tests/ops/`, and this file.
- `tests/performance/test_load_and_soak.py` imports only
  `apps.api.oday_api.main`, `shared.infrastructure.persistence.factory`, and
  `tests.integration._authz`. There is no import path from the diff to the test.
- The previous head of this same branch, `d6bb605a`, passed `product` at 16:37Z
  (run 30379120952), and `dev` at the shared base `dda72615` passed at 16:26Z.
  The `d6bb605a → ef048b0f` delta is 54 lines across those same three files.

Re-run on the exact failing head, on the worker host:

```text
export PATH="$HOME/.local/bin:$PATH"
python3 -m pytest tests/performance/test_load_and_soak.py -q      # 1 passed
p50=0.499s  p95=1.138s  p99=1.304s  success=150  failure=0  throughput=35.96 req/s
```

The test drives 150 requests at 10/20/50-way thread concurrency against one
SQLite file and asserts a wall-clock p95, so it measures the runner's CPU and IO
contention as much as the application. p95 is 1.138s locally against a 3.0s
budget; the hosted runner overshot by more than 2x in the 17:12–17:30Z window.

The perf budget belongs to ODP-PGAP-RELIABILITY-001, not to this task, so it is
not retuned here. `product` must go green on the exact head before merge —
re-run it rather than merging around it.
