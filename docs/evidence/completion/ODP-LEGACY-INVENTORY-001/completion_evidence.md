# ODP-LEGACY-INVENTORY-001 — completion evidence

Freeze and exhaustively classify legacy odayplus external-data code.

- Owner: Claude
- Reviewer: Antigravity2
- Provides contract: `odayplus.legacy-external-data-disposition.v2`
- Source catalog: `alfloop-dev/oday-data-platform@75688bca257b98be119a2ae8d7e1686572ec0413`
  (`docs/design/emgi/v0.4.1/tasks/definitions/consumer-a.json`)

## Deliverables

| Path | Role |
| --- | --- |
| `docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml` | The disposition record: vocabulary, exhaustive classification rules, frozen surface inventories, blocked capabilities, allowed workflows, provider-reference declarations. |
| `scripts/validate_external_data_boundary.py` | Whole-tree fail-closed validator over that record (`classification`, `freeze`, `capabilities`, `provider_references`). |
| `tests/architecture/test_external_data_boundary.py` | 69 architecture tests: live-tree assertions plus synthetic regressions that prove each rule actually blocks. |

## Relationship to the v1 boundary gate

`delivery_toolchain/governance/emgi-consumer-boundary.json` (schema_version 1)
is diff-scoped: it checks the paths a pull request touched against a handful of
forbidden prefixes. Two gaps follow from that, and v2 closes both:

- a file no diff touches is never classified, so "is this producer code?" was
  only ever answered for known directories;
- a provider reference outside `modules/external_data/` — in
  `services/provider-gateway/`, `product_ops/`, terraform, or a CI workflow —
  was invisible to a prefix check.

v1 stays in place as the fast per-pull-request gate. v2 is the exhaustive
whole-tree record and is authoritative where the two disagree.

## Verification

Both commands from the task definition, run at the reviewed head:

```console
$ uv run python scripts/validate_external_data_boundary.py
contract: odayplus.legacy-external-data-disposition.v2
tracked files: 2457
  classified: 2457
  unclassified: 0
  by_disposition: {"archived": 77, "assisted_intake_workflow": 58,
    "delivery_and_governance": 75, "development_platform": 196,
    "documentation_and_evidence": 888, "frozen_legacy_producer": 32,
    "migrating_to_platform_client": 46, "product_consumer_owned": 611,
    "product_review_workflow": 146, "repository_metadata": 14,
    "shared_platform_support": 61, "verification_only": 253}
  frozen_files: 32
  capability_detections: 69
  provider_reference_hits: 227
external-data boundary: OK
# exit 0

$ uv run pytest tests/architecture/test_external_data_boundary.py -q
69 passed
# exit 0
```

`ruff check` is clean on both new Python files. (`uv` is unavailable in the
worker sandbox; the commands were run as `python3 scripts/... ` and
`python3 -m pytest ...`, which is what `uv run` invokes.)

### Code-boundary registration

The repo's own ownership manifest, `config/code-boundaries.yaml`, has no
catch-all either, so the new validator had to be registered before CI's
`check_code_boundaries.py` would pass:

```console
$ uv run python delivery_toolchain/governance/check_code_boundaries.py
- unclassified code file: scripts/validate_external_data_boundary.py
# exit 1
```

`scripts/validate_external_data_boundary.py` is classified as
`development_platform_system`. That is forced, not chosen: the
`development_platform` removal bundle claims `scripts/` as a root, and
`validate_removal_bundles` rejects any other scope under a bundle root as a
foreign scope. Every other `.py` under `scripts/` carries the same boundary.

`tests/architecture/test_external_data_boundary.py` needed no manifest edit —
it matches the existing `verification` include patterns — but both files are
new rows in the generated `docs/audits/code-boundary-inventory.csv`.

```console
$ uv run python delivery_toolchain/governance/check_code_boundaries.py
Code boundary checks passed for 819 files.
# exit 0
```

## Acceptance

### 1. Classify every tracked file and detected provider reference, not only known directories

All **2457** tracked files carry a disposition. The classification rule list has
**no catch-all** (asserted by `test_classification_has_no_catch_all_rule`), so a
genuinely new top-level surface fails validation until a human gives it an
explicit disposition rather than inheriting one by accident. Dead rules fail
too, so the record cannot drift from the tree in either direction.

Surfaces a directory-only inventory would have missed, now classified as
external-data surface:

| Path | Disposition |
| --- | --- |
| `services/provider-gateway/**` | `frozen_legacy_producer` |
| `shared/infrastructure/persistence/external_data.py` | `frozen_legacy_producer` |
| `product_ops/external_data_backfill.py` | `frozen_legacy_producer` |
| `packages/schemas/source_contracts/external/**` | `frozen_legacy_producer` |
| `apps/data_platform/**`, `modules/integration/connectors/**`, `apps/api/app/routes/external_data.py` | `migrating_to_platform_client` |

**227** provider references were detected across `.py`, `.ts`, `.tsx`, `.yaml`,
`.tf`, `.sh`, `.json` and `.sql` by three signals — provider host, provider
credential env var, frozen-producer package import — and every one is covered
by a declaration naming both the matched text and a path glob. Declarations
that match nothing also fail, so the inventory cannot rot.

Notable finding: the 17 provider credential env vars reach well beyond
`modules/external_data/` — into `.github/workflows/deploy-{dev,staging}.yml`,
`infra/terraform/main.tf`, both `tfvars.example` files, and
`product_ops/deployment/`. That wiring is now frozen with the adapters it
serves.

One deliberate exclusion from *content* scanning: `docs/**`, `docs_archive/**`,
`docs-site/**`, `archive/**`, `support/**`, `.orchestrator/**` and lockfiles.
Design documents and runtime evidence *describe* provider integrations; treating
a completion log as a live reference would make the inventory noise rather than
signal. Those files are still fully classified.

### 2. Block new connectors, credentials, schedulers, raw evidence stores, canonical market writers and direct provider calls

Seven blocked capabilities cover the six named classes (direct provider calls
are split into *reference* and *fetch*, which have different scopes):

| Capability | Detection | Grandfathered |
| --- | --- | --- |
| `new_provider_connector` | producer path globs + `connector`/`provider` filename tokens | 15 |
| `new_provider_credential` | the closed `ODP_*_PROVIDER_*` secret pattern | 13 |
| `new_source_scheduler` | `scheduled_fetch` filename + `ExternalFetchScheduler` family | 4 |
| `new_raw_evidence_store` | `ListingFeed*Snapshot`/`*IngestionStore`, `intake.source_snapshots` | 5 |
| `new_canonical_market_writer` | SQL writes into `external_data.*` / `data_plane.*` | 2 |
| `direct_provider_reference` | provider hosts + frozen producer package imports | 25 |
| `direct_provider_fetch` | `httpx`/`requests`/`urllib.request` on external-data surfaces | 5 |

Every current occurrence is grandfathered **by exact path**, so the rules block
growth rather than re-flagging the frozen code they describe. A grandfathered
path that stops existing is itself a violation, so retiring legacy code must
shrink the list rather than orphan it.

Eight frozen surfaces record their exact file inventories (**32** files). Adding
a file under a frozen surface fails; deleting one without updating the record
also fails. Both directions are covered by synthetic tests
(`test_a_new_file_under_a_frozen_surface_is_rejected`,
`test_retiring_a_frozen_file_without_updating_the_record_is_rejected`).

Each capability is proven to fire by a parametrized synthetic case — for
example a new `modules/listing/infrastructure/rakuten_provider.py`, an
`ODP_LISTING_PROVIDER_API_KEY_V2` declaration, or an
`INSERT INTO external_data.real_estate_transactions` — so the suite fails if a
rule is ever weakened, not only if the tree regresses.

Two false positives were identified and excluded by design:
`www.googleapis.com` and `monitoring.googleapis.com` are Google Cloud platform
APIs the product legitimately calls, so only `maps.`/`places.googleapis.com`
count as data-source hosts.

### 3. Allow assisted intake and product review workflows to continue

Two `allowed_surfaces` carry per-capability exemptions:

- **`assisted_listing_intake`** — the intake domain, XLSX intake, security gate,
  worker, UI, migration, persistence, and release config. It is exempt from
  `new_provider_connector`, `new_raw_evidence_store`, `direct_provider_reference`
  and `direct_provider_fetch`, because the gate must *recognise*
  591/rakuya/housefun in order to refuse to fetch them — naming the host is the
  opposite of crawling it — and because `intake.*` is the product's own intake
  state, not a raw external evidence store.
- **`product_review_and_promotion`** — Operator Console review, promotion and
  decision surfaces, where odayplus owns product authorization and the final
  human decision.

The exemptions are narrow, and that narrowness is asserted:
`test_allowed_surface_exemptions_are_narrow` requires that no allowed surface
may ever waive `new_provider_credential`, `new_source_scheduler` or
`new_canonical_market_writer`, and `test_intake_may_not_grow_a_scheduler`
confirms that adding an `ExternalFetchScheduler` to the intake worker is still
rejected.

`tests/**` is out of blocked-capability scope on purpose — a test that drives
the frozen ingestion path is doing its job — but test files remain fully
classified and fully provider-reference scanned, so they are not a blind spot.

## Downstream

`ODP-LEGACY-FACADE-001` requires this contract. The
`consumer_coupling_pending_facade` declaration is that task's work list: every
remaining direct import of producer internals by consumer code
(`apps/api/oday_api/main.py`, `apps/worker/oday_worker/handlers.py`,
`modules/integration/connectors/`, `shared/infrastructure/persistence/factory.py`,
`product_ops/`, `delivery_toolchain/e2e/`), each of which must become a read
through the generated data-platform client.
