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
   that the dialect's own `SecretKeySelector` does not define. The members it
   *does* define must then say something usable:
   `_malformed_secret_selector_members` requires the selector to pick a
   resolvable Secret Manager version (Knative's `key` is required, v2's
   `version` optional but never blank), to leave Knative's `optional` absent or
   exactly `false` — a secret Cloud Run may resolve to nothing is not a
   mandatory binding — and to carry no deprecated `localObjectReference` beside
   `name`. "Resolvable" is Secret Manager's grammar, not an approximation of it:
   `_usable_secret_version` accepts the exact literal `latest`, a canonical
   version number, or an alias of at most 63 characters starting with a letter,
   and rejects the reserved words `latest` and `NEW` in every other case as well
   as any selector carrying surrounding whitespace.

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
| `valueFrom.secretKeyRef.optional: false` (a field the Knative selector really defines) | passes — the member rule is an allowlist of the API's own fields, not a two-key rule |
| `valueFrom.secretKeyRef.optional: true` | `secret_bindings` fails — the Secret or key need not exist, so this is not the mandatory binding the database and every selected provider secret require |
| `optional` set to anything that is not the boolean `false` (`"true"`, `"false"`, `1`, `0`, `null`) | same — a non-boolean is not the field the API defines, so no reader can be assumed to read it as `false` |
| Knative `secretKeyRef` with no `key` | `secret_bindings` fails — Cloud Run v1 documents `key` as required, and without it no version is selected |
| Knative `key` (or v2 `version`) blank, whitespace, non-string, `null`, `"0"`, a placeholder, or otherwise not a version selector | same — it names no resolvable Secret Manager version |
| Knative `key` (or v2 `version`) set to the exact literal `latest`, a canonical version number, or an alias of at most 63 characters | passes — those three are what Secret Manager resolves |
| Knative `key` (or v2 `version`) set to `latest` or `NEW` in any other case (`Latest`, `LATEST`, `new`, `New`, `NEW`) | `secret_bindings` fails — both are reserved words, not alias names, and only the lowercase `latest` literal resolves |
| an alias of 64 characters or more (up to the 255-character secret-*name* limit round 9 borrowed) | same — a version alias is capped at 63 characters |
| a selector with surrounding whitespace (`" latest "`, `"latest "`, `"\tlatest\n"`, `" 1 "`) | same — the description is the proof, so it is read as gcloud emitted it and never normalized |
| a padded version number (`"007"`) | same — versions are numbered from 1 and gcloud emits them canonically |
| v2 `secretKeyRef` with no `version` | passes — Cloud Run v2 leaves `version` optional; only v1's `key` is required |
| Knative `name` (or v2 `secret`) holding a character Secret Manager does not allow in a secret ID — a space, `.`, `/`, `!`, or non-ASCII | `secret_bindings` fails — a secret ID is letters, digits, `-` and `_` only, so this names no secret |
| a secret name of 256 characters or more | same — a secret ID is capped at 255 |
| a secret name with surrounding whitespace (`" oday-database-url "`, `"oday-database-url "`, `"\today-database-url\n"`) | same — the name is read exactly as gcloud emitted it, under the rule that already governs the version member |
| a secret name of at most 255 characters using letters, digits, `-` or `_` | passes — that is the secret ID grammar, so mixed case, digits, and underscores are all legitimate |
| `projects/<project>/secrets/<secret ID>`, with the project as a number or a 6–30 character project ID | passes — the documented cross-project form, and the deploy script takes every name from an operator-supplied `*_SECRET` variable |
| a path-shaped name with an empty segment (`projects//secrets/<id>`, `projects/<p>/secrets/`) or a longer `projects/<p>/secrets/<id>/versions/N` | `secret_bindings` fails — neither is the documented form, and a version path does not name the secret a binding references |
| `valueFrom.secretKeyRef.localObjectReference` | `secret_bindings` fails — Knative's superseded way of naming the same secret `name` names, so the selector names two |
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
  APIs really define rather than a two-key rule. `optional: false` on a Knative
  job still passes, pinned by
  `test_job_smoke_accepts_the_optional_members_each_dialect_defines`. What those
  members may *hold* was left unread, which is round 9 below.

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

## Round 9: the members were allowlisted by name and never read

Round 8 closed the reference to the members each dialect defines. An allowlist
of *names* says which members may appear, never what they may hold, so a member
could be present, defined, inside the allowlist, and still cancel the binding it
sits in. Verified against head `a2a0106b` — every crafted description below
fails **no** check at all (`_failed_names(checks) == set()`):

| crafted description at `a2a0106b` | result |
| --- | --- |
| required secret with a valid `valueFrom.secretKeyRef.name` and `"optional": true` | passed |
| the same with `optional` set to `"true"`, `"false"`, `1`, `0`, or `null` | passed |
| `valueFrom.secretKeyRef` with a valid `name` and **no** `key` | passed |
| `key` set to `""`, `"   "`, `"0"`, `"-1"`, `"latest version"`, `1`, `null`, or `"placeholder"` | passed |
| the v2 mirror: `valueSource.secretKeyRef.version` set to `""`, `"  "`, `"0"`, `"latest version"`, `1`, or `null` | passed |
| valid `name` beside a `localObjectReference` naming `attacker-controlled-secret` | passed |
| `ODAY_DATABASE_URL` itself bound with `"optional": true` | passed |

The `optional: true` row is the load-bearing one Codex6 named. Cloud Run v1
defines `optional` as *whether the Secret or its key must be defined*: with it
set, a missing secret is not an error and the env var is simply absent at
runtime. A binding that Cloud Run is free to resolve to nothing is not the
mandatory binding this proof exists to assert, so accepting it contradicted the
"database and every selected provider secret remain mandatory" acceptance
directly — including for `ODAY_DATABASE_URL`. The `key` rows contradict
"malformed missing or plaintext secret bindings fail closed" the same way: v1
documents `key` as **required**, and a missing, blank, or unusable one selects
no Secret Manager version, so nothing about the reference resolves.

The fix reads the members the dialect defines instead of only naming them.
`_JobApiSchema` gains the semantic half of each dialect beside
`reference_members`, because the *meaning* of a selector member is as much an
API-version fact as its name:

- `version_key` / `version_required` — Knative selects the version with `key`,
  which Cloud Run v1 documents as required; Cloud Run v2 selects it with
  `version`, which the v2 API leaves optional. The asymmetry is the API's, and
  it is recorded rather than flattened: a v2 selector with no `version` still
  passes, pinned by
  `test_job_smoke_accepts_a_v2_selector_without_a_declared_version`.
- `mandatory_flag_key` — Knative's `optional`, which v2 does not define.
- `deprecated_members` — Knative's `localObjectReference`, the pre-`name` way of
  naming the same secret.

`_malformed_secret_selector_members` applies them in `_secret_binding_proof`
after the reference name resolves:

- **A mandatory secret may not be optional.** `optional` must be absent or the
  boolean `false` exactly. `"false"`, `0`, and `null` are not the field the API
  defines, so no reader can be assumed to treat them as `false`, and they fail
  closed alongside `true`.
- **A binding selects a usable version.** `_usable_secret_version` accepts what
  Secret Manager resolves — `latest`, a version number ≥ 1, or a version alias
  (leading letter, then letters, digits, `_`, `-`) — and rejects blanks,
  non-strings, `0`, and the placeholder values `_configured` already rejects for
  secret names. Knative's `key` must additionally be present. Round 9 wrote that
  grammar too loosely; round 10 below is the correction, and it is the rule in
  force.
- **A selector names one secret.** `localObjectReference` is rejected on
  presence: it is superseded by `name`, so a selector carrying both has two
  names for one binding and gcloud emits neither shape.

The detail names member paths only, never member payloads
(`binding declares an unusable valueFrom.secretKeyRef
(valueFrom.secretKeyRef.optional must be absent or exactly false); a mandatory
secret must select a usable version and may not be optional`), so the round-2
redaction assertions extend to this round unchanged.

The real deployment cannot trip these rules, checked against
`scripts/deploy_cloud_run_waji.sh` rather than assumed. Secrets reach these jobs
only through `--set-secrets="${API_SECRET_BINDINGS}"`, whose entries are
`ENV=<secret-ref>` built from the `*_SECRET` deployment variables. `gcloud run
jobs deploy --set-secrets` requires a version in each reference and emits it as
`key`; it has no flag that emits `optional` or `localObjectReference` at all.
Every binding the deploy path produces therefore carries a usable `key` and
neither rejected member.

Regressions: `test_job_smoke_rejects_an_unusable_knative_secret_selector`
(16 selectors), `test_job_smoke_rejects_an_unusable_v2_secret_selector`
(6 on the v2 container path),
`test_job_smoke_rejects_an_optional_database_secret_binding` (the same defect on
`ODAY_DATABASE_URL`, which is required for every selection), and two controls
that pin the tightening as narrow:
`test_job_smoke_accepts_every_usable_knative_version_selector` (`latest`, `1`,
`42`, `prod_pinned`, `prod-v1`) and
`test_job_smoke_accepts_a_v2_selector_without_a_declared_version`. The round-8
`optional: false` control is unchanged and still passes.

## Round 10: the version grammar was borrowed from the wrong resource

Round 9 started reading the selector members instead of only naming them, but it
wrote the version grammar from memory rather than from Secret Manager's, and
three mistakes fell out of that. Verified against head `39cf252c` — every
selector below fails **no** check at all (`_failed_names(checks) == set()`):

| crafted description at `39cf252c` | result |
| --- | --- |
| `key` / `version` set to `"NEW"`, `"new"`, or `"New"` | passed |
| `key` / `version` set to `"Latest"` or `"LATEST"` | passed |
| a 64-character alias, and a 255-character one | passed |
| `" latest "`, `" latest"`, `"latest "`, `"\tlatest\n"`, `" 1 "` | passed |
| `"007"` | passed |

Each row is a selector Secret Manager does not resolve, so each one is a job
whose "mandatory" secret binds to nothing while
`jobs-smoke:<kind>:secret_bindings` reports zero failed checks — the same
fail-open the acceptance "malformed missing or plaintext secret bindings fail
closed" forbids, and a direct contradiction of round 9's own claim that only
resolvable versions pass.

The three defects, and what replaces them:

- **The length cap came from the wrong resource.** `{0,254}` is the tail of the
  255-character limit on a secret *name*. A version **alias** is capped at 63
  characters, so every length from 64 to 255 was accepted. The pattern is now
  `[A-Za-z][A-Za-z0-9_-]{0,62}`, and a 63-character alias is kept as an explicit
  boundary control on both dialects so the correction cannot drift into
  over-tightening.
- **The reserved words were not reserved.** `latest` is a reserved selector, not
  an alias: Secret Manager refuses both `latest` and `NEW` as alias names, in any
  case. Round 9 let the alias pattern swallow the literal, which made `Latest`,
  `LATEST`, `new`, `New`, and `NEW` all alias-shaped and therefore accepted. The
  literal is now matched exactly (`value == "latest"`), and
  `_RESERVED_SECRET_VERSION_ALIASES` rejects both reserved words case-insensitively
  before the alias pattern is consulted.
- **The validator normalized the proof it was checking.** `value.strip()` ran
  before validation, so ` latest ` was judged as `latest`. The description *is*
  the proof: whitespace around a selector is a defect in what gcloud emitted, not
  something this validator may fix on the deployment's behalf. A selector that is
  not identical to its own `strip()` is now rejected outright.

A version number is also read canonically now (`[1-9][0-9]*` instead of `[0-9]+`
plus an `int()` cast, which accepted `007`). Secret Manager numbers versions from
1 and `gcloud run jobs describe` never emits a padded number, so a padded one is
a description this proof should not vouch for.

The real deployment still cannot trip any of this, checked against
`scripts/deploy_cloud_run_waji.sh` rather than assumed: bindings reach these jobs
only through `--set-secrets="${API_SECRET_BINDINGS}"`, and gcloud emits the
version it resolved — `latest` or a canonical number — with no surrounding
whitespace and no reserved-word alias.

Regressions: 13 new cases on `test_job_smoke_rejects_an_unusable_knative_secret_selector`
and the same 13 on `test_job_smoke_rejects_an_unusable_v2_secret_selector`, so
both container paths are pinned. Controls: `_USABLE_VERSION_SELECTORS` now
carries the 63-character alias beside `latest`, `1`, `42`, `prod_pinned`, and
`prod-v1`, and it drives both
`test_job_smoke_accepts_every_usable_knative_version_selector` and the new
`test_job_smoke_accepts_every_usable_v2_version_selector` — the acceptance
boundary is a Secret Manager fact, so both dialects must keep accepting it.
`test_job_smoke_accepts_a_v2_selector_without_a_declared_version` is unchanged
and still passes.

## Round 11: the rule round 10 wrote was true of one member and false of the one beside it

Round 10 fixed the version member and stated the rule that made the old
behaviour a defect: *the description is the proof*, so the validator may not
normalize what it is checking. That rule was never applied to the other selector
member. `_secret_reference_name` still read the secret **name** through
`value.strip()` and tested it against no grammar at all — only `_configured`,
which rejects an empty string and the placeholder words. Found by self-review at
head `d9f2f007` rather than by the reviewer, and reproduced there before the fix:
every name below fails **no** check on **both** container paths
(`_failed_names(checks) == set()`).

| crafted `secretKeyRef.name` / `.secret` at `d9f2f007` | result |
| --- | --- |
| `" oday-database-url "`, `" oday-database-url"`, `"oday-database-url "` | passed |
| `"\today-database-url\n"` | passed |
| `"oday database url"` (inner space) | passed |
| `"oday-database-url!"`, `"oday.database.url"`, `"oday/database/url"` | passed |
| a 256-character name | passed |
| `"資料庫"` | passed |
| `"."` | passed |
| `"projects//secrets/<id>"`, `"projects/<p>/secrets/"` | passed |
| `"projects/<p>/secrets/<id>/versions/1"` | passed |

Each row is a name Secret Manager does not resolve, so each is a job whose
mandatory database or selected-provider secret binds to nothing while
`jobs-smoke:<kind>:secret_bindings` reports zero failed checks — the same
fail-open the acceptance "malformed missing or plaintext secret bindings fail
closed" forbids. The whitespace rows are the sharper failure: round 10 rejected
` latest ` on exactly the reasoning that `.strip()` on the proof is the
validator repairing the deployment's description for it, and then left the same
`.strip()` in place one member over.

`_usable_secret_name` now decides the name, under the grammar the API documents
rather than an assumed one:

- **A bare secret ID** is `[A-Za-z0-9_-]{1,255}`. Secret Manager allows letters,
  digits, `-` and `_` and nothing else, so a space, `.`, `/`, `!`, or non-ASCII
  character names no secret, and 256 characters is one past the cap.
- **A cross-project secret** is `projects/<project>/secrets/<secret ID>`. The
  project segment is a project number (never written with a leading zero) or a
  project ID — 6 to 30 characters, opening with a lowercase letter and never
  closing with a hyphen. Both spellings stay accepted on purpose:
  `scripts/deploy_cloud_run_waji.sh` takes every name from an operator-supplied
  `*_SECRET` variable (`ODAY_DATABASE_URL=${ODAY_DATABASE_URL_SECRET}`, and one
  per selected provider), so a cross-project secret is a supported deployment
  and rejecting the path form would over-tighten a schema this task must keep
  supporting. A path with an empty segment, or the longer `.../versions/N` path
  — which names a version, not the secret a binding references — is not that
  form and is rejected.
- **The name is read exactly as gcloud emitted it.** A value that is not
  identical to its own `strip()` is rejected outright, for the same reason a
  version is.

The failure surfaces through the existing
`binding declares no usable <dialect>.secretKeyRef.<name|secret>` detail, so no
check is added or renamed and the planted name never reaches the detail or the
report.

Regressions: 16 cases on `test_job_smoke_rejects_an_unusable_knative_secret_name`
and the same 16 on `test_job_smoke_rejects_an_unusable_v2_secret_name`. Controls:
six usable names — the deployment's own ID, an underscored one, a mixed-case
alphanumeric one, the 255-character boundary, and both cross-project path
spellings — drive `test_job_smoke_accepts_every_usable_knative_secret_name` and
`test_job_smoke_accepts_every_usable_v2_secret_name`, so the acceptance boundary
is pinned on both dialects exactly as round 10 pinned the version boundary.

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

Executed from the task branch on the round-11 tree (parent `d9f2f007`), with
`export PATH="$HOME/.local/bin:$PATH"`:

```text
python3 -m pytest tests/ops/test_cloud_run_live_deployment.py                  # 230 passed, 1 deselected
python3 -m pytest tests/ops                                                    # 285 passed, 20 skipped, 1 deselected
python3 -m ruff check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
python3 -m ruff format --check scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py
git diff --check
```

Ruff check, ruff format `--check`, and `git diff --check` all passed. The one
deselected case is
`test_deploy_preflight_imports_runtime_dependencies_via_locked_python`, the
environmental failure earlier rounds reported: it shells out to `uv`, which is
absent from this worker's `PATH` on the round-11 run, so it fails with
`Error: required command 'uv' is not installed.` before reaching any assertion
about this task's code. It is untouched by this branch — the whole diff is the
validator, this test file's secret-binding cases, and this document — and it
passes wherever `uv` is installed, which includes CI. Every other case in both
runs passes.

The focused-file count by round was 87 at `76063434` (round 2), 93 at `49e65382`
(round 3, six new regressions), 94 at `5b9c430a` (round 4, the sidecar test), 97
at `dd4acb0b` (round 5, the two crossed-whole-schema regressions plus the
both-dialects control), 107 at `15e7ec64` (round 6, ten uniqueness and
mixed-dialect regressions), 118 at `d3dfeb13` (round 7, eleven presence-rule
cases, each asserting on both container paths), 125 at `a2a0106b` (round 8, six
source-member regressions plus the `optional` control that pins the allowlist
against over-tightening), 154 at `39cf252c` (round 9, 23 unusable-selector
regressions plus six controls — five usable Knative version selectors and the
v2 no-`version` case), 187 at `d9f2f007` (round 10, 26 version-grammar
regressions — 13 per dialect — plus seven controls: the 63-character alias on
the Knative side, and the whole usable-selector set re-run on the v2 container
path), and 231 now — round 11 adds 32 secret-name regressions (16 per dialect)
plus 12 controls (six usable names on each dialect). The 231 counts the
`uv`-dependent preflight case, which the round-11 run deselects; 230 ran.

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
narrow. For round 9 against `a2a0106b` the restore run — the round-9 test file
copied into a detached worktree at `a2a0106b` — reports `23 failed` out of the
29 newly selected cases: the 16 unusable Knative selectors, the six unusable v2
selectors, and the optional `ODAY_DATABASE_URL` binding all fail with
`assert 'jobs-smoke:<kind>:secret_bindings' in set()`, the pre-fix validator
returning zero failing checks for every one of them, which is the independent
reproduction of the probes Codex6 ran at that head. The remaining six selected
cases are the round-9 controls, and they pass against both validators. For
round 10 against `39cf252c` the same restore method — the round-10 test file
copied into a detached worktree at `39cf252c` — reports `26 failed, 161 passed`:
exactly the 26 new version-grammar cases fail, 13 with
`assert 'jobs-smoke:migration:secret_bindings' in set()` and 13 with
`assert 'jobs-smoke:worker:secret_bindings' in set()`, the pre-fix validator
returning **zero** failing checks for `NEW`/`new`/`New`, `Latest`/`LATEST`, the
64- and 255-character aliases, `' latest '` and its whitespace variants, `' 1 '`,
and `'007'` on both container paths. That is the independent reproduction of the
three defects Codex6 named at that head. The seven new controls pass against
both validators, so this round's tightening is proven narrow too. For round 11
against `d9f2f007` the same restore method reports `28 failed, 202 passed`: 14
of the 16 crafted names fail on each dialect, every one with
`assert 'jobs-smoke:<kind>:secret_bindings' in _failed_names(checks)` against a
`set()`, which is the reproduction of the fail-open above. The two that already
failed closed before the fix are the non-string names (`1` and `None`) — the old
`isinstance(value, str)` guard caught those and nothing else — so 14 per dialect
is the honest size of this round's hole, not 16. All 12 round-11 controls pass
against both validators, so the name grammar does not narrow what the
deployment may legitimately reference. The control tests
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

**Cleared at head `d9f2f007`.** Run
[30394458105](https://github.com/alfloop-dev/odayplus/actions/runs/30394458105)
passed `product` (19m22s), `product-e2e-gate` (7m51s), `performance-gate`
(1m32s), and `orchestrator` (33s) with nothing retuned and nothing merged
around, which confirms the diagnosis above: the failure was the hosted runner's
contention in the 17:12–17:30Z window, not this diff. `task-review-gate` was the
only red check, and it reports task status rather than code — it stays red until
the task moves to review. Round 11 moves the head past `d9f2f007`, so CI must go
green again on the new exact head before merge; that rerun is the one the
reviewer should read.
