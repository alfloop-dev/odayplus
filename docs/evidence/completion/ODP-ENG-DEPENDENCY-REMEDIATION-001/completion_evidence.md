# ODP-ENG-DEPENDENCY-REMEDIATION-001 — Completion Evidence

- Task: Remediate dependency findings not requiring risk acceptance
- Owner: Claude
- Reviewer: CodexCoordinator
- Branch: `task/ODP-ENG-DEPENDENCY-REMEDIATION-001`
- Remediation anchor commit: `888b6c077164a3775f6f5b7ef14b72e50662b692`
- Evidence produced: 2026-08-08

## 1. Scope And Rule Applied

A finding was treated as **safely remediable** only when it could be closed by
a lockfile bump or an in-range manifest bump that does not force a breaking
major upgrade. Anything whose only fix is a major version bump was routed to
[`HUMAN_DECISION_HANDBACK.md`](HUMAN_DECISION_HANDBACK.md) instead. No waiver,
`--ignore-vuln`, allowlist, or risk acceptance was signed by an AI agent in
this task.

## 2. Findings Closed

### 2.1 Node — `package-lock.json` (lockfile-only)

Baseline `npm audit` reported 2 high findings across 546 dependencies:

| Package | Was | Now | Advisories |
| --- | --- | --- | --- |
| `brace-expansion` (root) | 1.1.16 | 1.1.18 | GHSA-mh99-v99m-4gvg, GHSA-rgw5-rvv9-x895 |
| `brace-expansion` (under `@typescript-eslint/typescript-estree`) | 5.0.7 | 5.0.9 | GHSA-mh99-v99m-4gvg, GHSA-rgw5-rvv9-x895 |
| `js-yaml` | 4.3.0 | 4.3.1 | GHSA-5p4m-2wfm-xmqj |

Applied with `npm audit fix --package-lock-only` — no `package.json` range,
`overrides` entry, or `engines` value was changed, and the diff is 10 changed
lines confined to `package-lock.json`.

```bash
$ npm audit --omit=dev --audit-level=high
found 0 vulnerabilities

$ npm audit
found 0 vulnerabilities
```

### 2.2 Python (root) — `uv.lock` (lockfile-only)

| Package | Was | Now | Advisories |
| --- | --- | --- | --- |
| `gitpython` (transitive, via mlflow) | 3.1.55 | 3.1.58 | GHSA-3f7w-8rr8-f37f, GHSA-p538-c434-8v24, GHSA-9rj7-rf2p-w77r, GHSA-4gmw-gg2m-w46p, GHSA-hh9p-6wh2-4mfc, GHSA-wvpp-8hx9-p66j, GHSA-jm78-9fvv-mhgr |

Applied with `uv lock --upgrade-package gitpython` — no `pyproject.toml`
constraint was changed, and the diff is 3 changed lines in `uv.lock`.

### 2.3 Python (service) — `services/provider-gateway/requirements.txt`

The gateway pinned `fastapi==0.115.6`, whose `starlette>=0.40.0,<0.42.0`
constraint held `starlette` at 0.41.3 with **9 known advisories**
(PYSEC-2026-161, -248, -249, -1941, -1942, -2280, -2281). No fix version is
reachable inside the old FastAPI pin: the lowest advisory fix is starlette
0.47.2, which is above that ceiling.

Change applied:

```
fastapi==0.115.6            ->  fastapi==0.138.1
                            +   starlette>=1.3.1   (explicit security floor)
uvicorn[standard]==0.34.0   ->  unchanged
```

This was classified as safely remediable rather than a forced breaking
upgrade because:

1. It is a `0.x` **minor** bump, not a major bump.
2. The same repository's main API already runs `fastapi 0.138.1` /
   `starlette 1.3.1` under the root `pyproject.toml` (`fastapi>=0.115`), so the
   target version is already exercised here.
3. `services/provider-gateway/app.py` uses only the long-stable FastAPI
   surface — `FastAPI`, `HTTPException`, `Request`, `Response` and plain route
   decorators. It declares no Pydantic models, no `Depends`, no middleware, and
   no `on_event`/`lifespan` hooks.
4. Behaviour was verified before and after (§3.2).

The explicit `starlette>=1.3.1` floor is required for durability: FastAPI
0.138.1 only requires `starlette>=0.46.0`, so without the floor a future
resolve could legally land back on a vulnerable starlette.

```bash
$ uv run --with pip-audit pip-audit -r services/provider-gateway/requirements.txt --no-deps
No known vulnerabilities found
```

### 2.4 SBOM

`docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json` was regenerated so
the committed CycloneDX 1.5 SBOM matches the updated lockfiles.

```bash
$ python3 scripts/security/generate_sbom.py
Total components cataloged: 778
SBOM Content Digest: sha256:592e60bb11525286d55e4bdae3bdda43b122517d6a6ed0423d1e0b6b1bbf245f
```

`tests/security/test_supply_chain_security_gate.py::test_sbom_and_provenance_present_and_valid`
re-derives the SBOM from the live `package-lock.json` and `uv.lock` and asserts
an exact component match, so this is enforced rather than asserted.

## 3. Verification

### 3.1 Audits

| Command | Result |
| --- | --- |
| `npm audit --omit=dev --audit-level=high` | `found 0 vulnerabilities` |
| `npm audit` (incl. dev, 547 packages) | `found 0 vulnerabilities` |
| `uv run --with pip-audit pip-audit -r services/provider-gateway/requirements.txt --no-deps` | `No known vulnerabilities found` |
| `uv run --with pip-audit pip-audit` (root environment, 247 dists) | 3 findings, all `cryptography` — see §4 |
| `uv run --with pip-audit pip-audit --local` (the command wired into `make dependency-audit`) | `No known vulnerabilities found` — **vacuous, see §4.2** |

### 3.2 Provider-gateway behavioural parity

Both runs load `services/provider-gateway/app.py` through
`fastapi.testclient.TestClient` in a clean throwaway virtualenv:

| Request | Before (`fastapi 0.115.6` / `starlette 0.41.3`) | After (`fastapi 0.138.1` / `starlette 1.5.0`) |
| --- | --- | --- |
| `GET /healthz` | `200 {'status': 'ok', 'geocode': 'unconfigured', 'poi': 'unconfigured', 'admin_boundary': 'unconfigured'}` | identical |
| `GET /admin-boundary?lat=25.03&lon=121.56` | `503 {'detail': 'gateway unconfigured'}` | identical |
| `POST /geocode` | `503 {'detail': 'geocode gateway unconfigured'}` | identical |

### 3.3 Regression suite

```bash
$ uv run pytest tests/security
230 passed, 5 warnings in 463.38s (0:07:43)
```

This includes the fail-closed negative tests for stale lockfiles, SBOM drift,
vulnerable fixtures, unsigned images, invalid provenance, and leaked test
secrets.

## 4. Not Closed — Routed To Human Decision

Two items are deliberately **not** fixed in this task and are recorded in
[`HUMAN_DECISION_HANDBACK.md`](HUMAN_DECISION_HANDBACK.md). They are coupled:
turning on the real audit (4.2) makes the unfixed finding (4.1) fail the gate,
so they must be decided together.

### 4.1 `cryptography` 48.0.1 — major-only, blocked upstream

PYSEC-2026-3552 (fix 50.0.0), PYSEC-2026-3553 and PYSEC-2026-3554 (fix
49.0.0). `pyproject.toml` declares `cryptography>=45,<49`, and that ceiling
mirrors an upstream one: **`mlflow==3.14.0` requires `cryptography>=43.0.0,<49`**.
Every fix is therefore a major bump that is not reachable without moving
mlflow, which makes this a risk-acceptance decision rather than a remediation.

### 4.2 `pip-audit --local` audits zero packages

`make dependency-audit` (Makefile:48) and
`tests/security/test_supply_chain_security_gate.py:42` both run
`uv run --with pip-audit pip-audit --local`. Under `uv run --with`, pip-audit
executes in an ephemeral overlay environment, and `--local` restricts the audit
to that overlay's own site-packages — so the project's dependencies are never
examined:

```bash
$ uv run --with pip-audit pip-audit --local --format=json   # dependencies audited: 0
$ uv run --with pip-audit pip-audit --format=json           # dependencies audited: 247
```

The Python half of the supply-chain gate has been reporting a vacuous pass.
Removing `--local` is a one-line fix, but it immediately turns the gate red on
§4.1 — which is exactly the decision being handed back.
