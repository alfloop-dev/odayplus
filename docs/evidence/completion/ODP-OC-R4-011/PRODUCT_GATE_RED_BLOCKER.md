# ODP-OC-R4-011 — Closeout Blocker: mandatory product-e2e-gate is RED

**Owner:** Claude · **Reviewer:** Antigravity4 · **Status at write time:** review_approved
**PR:** #293 (`task/ODP-OC-R4-011` → `dev`), `mergeStateStatus: UNSTABLE`
**Failing check:** `product-e2e-gate`
([run 29334799452](https://github.com/alfloop-dev/odayplus/actions/runs/29334799452/job/87091246395))
— **12 failed, 40 passed, 15 did not run**.

The owner cannot finalize this task to `done`:

1. The check this task itself makes mandatory (`product-e2e-gate`) is red, and it
   will not self-heal on re-run — the failures are deterministic (see below).
2. PR #293 is still open, so the branch HEAD is **not** an ancestor of `dev`;
   `scripts/ai-status.sh done` would be rejected by the ancestor gate regardless.

The reviewer approval was recorded against **static gates + local builds**
("All static gates and local builds verified green"), i.e. the standalone
Playwright harness (`playwright.config.ts`, in-memory API, default auth). It did
**not** exercise the dockerized `product-e2e-gate`
(`infra/docker/docker-compose.e2e.yml` + `scripts/e2e/run_product_e2e.sh`,
`ODP_ENV=e2e ODP_PERSISTENCE=durable`, seeded backend), which is the runner this
task promotes to mandatory.

## Root causes of the 12 failures (verified locally against the product boot config)

### A. Real accessibility violations — `operator-visual-a11y.spec.ts`
`R4 runtime surfaces render with no major a11y violations` fails at **both**
`desktop-1440x900` and `constrained-1024x768`:

```
Error: major a11y violations at Today 今日工作 @ constrained-1024x768
+   { "id": "color-contrast", "impact": "serious", "nodes": 3 }
```

This is a genuine, deterministic `color-contrast` (serious) violation inside
`[data-testid="operator-console"]` on the Today surface. Acceptance requires
"No major accessibility violations … at required viewports", so this is a real
product-quality gap, not a flake. Fix requires adjusting the offending
foreground/background tokens in the operator shell.

### B. Auth 403 → web fixture fallback — the `operator-network-*` specs
The operator console fetches `/api/v1/operator/network-listings` using the
**active role's** system role via `getSecurityHeaders` (OperatorConsole.tsx).
For the default `ops-lead` (system role `operations_manager`) the API returns
**403** — verified locally against the product boot env:

```
GET /api/v1/operator/network-listings  -> 403 {"detail":"role does not permit view on listing"}
GET /api/v1/operator/network-scoring   -> 403 {"detail":"role does not permit view on sitescore"}
GET /api/v1/operator/network-rebalance -> 403 {"detail":"role does not permit view on listing"}
```

On 403 the panel takes its documented fallback
(`NetworkFindAreasWorkspace.tsx`: "network-listings API unavailable; using
fixtures") and renders the **web** fixture `LST-440`
(`apps/web/features/operator/fixtures.ts`). The specs assert on the **API**
fixture `L-2024` / `L-2030`
(`modules/opsboard/application/network_listings.py`), so they fail:

```
expect(getByTestId('network-listing-table')).toContainText('L-2024')
actual: "仲介LST-440 · 信義區松仁路 88 號 1F …"
```

These specs were authored for the standalone harness, where the browser context
carries a broad role superset (`playwright.config.ts` extraHTTPHeaders:
`expansion_user,site_reviewer,…`) that *does* hold `listing:VIEW`. In the
productized flow the console's own per-role fetch headers win, so the same specs
403 → fixture-fallback → mismatch. bootstrap/today (`operator-shell-today`) are
in-memory-seeded and return **200** locally; their CI failure needs the API
container stack trace (below) to confirm, but the network family is a structural
auth/data mismatch.

### C. Server 500s — `e2e-operator-console` (Govern, Observability), `operator-shell-today`
CI shows `Failed to load resource: 500 (Internal Server Error)` on the Govern
and Observability surfaces, and the `operator-shell-today` API test fails ~43ms
(immediate error). These do **not** reproduce from a direct local API call
(bootstrap/today = 200), so the 500 is environment-specific to the dockerized
stack. The one operator surface that is genuinely DB-backed and **not** covered
by `seed_product_e2e_data.py` is `/api/v1/operator/store-ops/*`
(`bundle.store_ops_repository`); durable-mode empties/500s there are the leading
suspect. Confirming this requires the API container log from a compose run
(`.odp_data/e2e-diagnostics/compose-tail.log`), which is not in the CI artifact
tail captured here.

## Why this is a design decision, not a mechanical fix

Making the gate green requires one or more of:

- **A11y (B/blocking):** fix the `color-contrast` token(s) on the Today surface.
  Contained, owner-scoped.
- **Network auth/data (B):** either grant `operations_manager` `listing:VIEW`
  (an RBAC policy change owned by the security lane, not this task), or rewrite
  the `operator-network-*` specs to drive a listing-permitted role, or accept
  the fixture path. Each changes the substance the reviewer approved.
- **500s (C):** seed / harden `/operator/store-ops/*` (and any other durable
  surface) for the product env, or scope it out of the mandatory runner.

Because the fix set spans RBAC policy, cross-task web/API data sourcing, styling,
and a decision about which specs belong in the mandatory product runner, this is
handed back for a re-review / scoping decision rather than silently reworked on
an already-approved branch.

## Verification performed for this diagnosis
- `gh pr view 293` / `gh pr checks 293` → `product-e2e-gate` fail, PR open.
- Booted API with the product-gate env
  (`ODP_ENV=e2e ODP_PERSISTENCE=durable ODP_DB_PATH=…`): bootstrap/today = 200;
  network-listings/scoring/rebalance = 403 for `operations_manager`.
- Traced the web fixture-fallback path (`LST-440`) vs API fixture (`L-2024`).
