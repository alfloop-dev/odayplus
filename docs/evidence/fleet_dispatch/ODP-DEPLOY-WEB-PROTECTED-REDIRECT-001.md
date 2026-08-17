# ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001: Fix candidate Web protected redirect smoke contract

Owner: Antigravity2 · Reviewer: Claude · Phase: Live Runtime Deployment · 2026-07-29

Remediate the `smoke:web:/operator: status=307 protected_redirect=false` failure observed in Deploy Dev run 30436771086 without weakening unauthenticated access protection or modifying Package 10 visual components.

Full runtime detail:
`docs/evidence/runtime/ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001/` (README.md).

## 1. Root Cause Analysis

In Deploy Dev run [30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086), candidate Web `/operator` returned status 307 when queried by the deployment validator `product_ops/deployment/validate_cloud_run_live_deployment.py`.

However, the validation check evaluated `protected_redirect` to `false`:
```
- smoke:web:/operator: status=307 protected_redirect=false
```

Investigation confirmed (coordinator live no-follow probe of the preserved candidate revision):
- The real Location header returned by the candidate was relative: `/login?returnTo=%2Foperator`.
- `apps/web/src/middleware.ts` is correct and unchanged — it calls `new URL("/login", request.url)`, which produces a same-origin relative Location when Next.js runs in standalone mode.
- The failure was caused entirely by rigid string prefix matching in `product_ops/deployment/validate_cloud_run_live_deployment.py`:
  ```python
  expected_login_prefix = f"{web_url.rstrip('/')}/login?"
  auth_redirect = (
      web_status in {302, 303, 307, 308}
      and isinstance(location, str)
      and location.startswith(expected_login_prefix)
      and "returnTo=" in location
  )
  ```
- A relative Location `/login?returnTo=%2Foperator` does not start with the full `https://candidate-...a.run.app/login?` absolute prefix, so the prefix check evaluates to `False` — incorrectly rejecting a valid same-origin redirect as unsafe. Root cause: validator-only.

## 2. What Changed

1. **Validator Contract Rework (`product_ops/deployment/validate_cloud_run_live_deployment.py`)**:
   - Implemented strict `_is_safe_protected_redirect` helper using `urllib.parse.urljoin` to resolve relative and absolute Location headers against the candidate request URL before validation:
     - **Status code**: Must be in `{302, 303, 307, 308}` (unauthenticated 200 OK fails closed).
     - **urljoin resolution**: Relative `/login?returnTo=%2Foperator` and absolute same-origin URLs are both resolved and validated identically.
     - **Credentials / Userinfo**: Rejects URLs containing username, password, or `@` in netloc.
     - **Fragments**: Rejects target URLs containing URL fragments.
     - **Scheme**: Must match request scheme exactly. Reject scheme downgrade (HTTPS base to HTTP target).
     - **Hostname**: Must match normalized base hostname.
     - **Effective Port**: Must match (including default vs nondefault port mismatches e.g. 443 vs 8443). Malformed non-numeric ports (e.g. `:bad`) and out-of-range ports (e.g. `:99999`) raise `ValueError`; caught and fail-closed (`False`).
     - **Target Path**: Must strictly equal `/login`.
     - **returnTo Parameter**: `urllib.parse.parse_qs` already URL-decodes values once. The decoded parameter must strictly equal the intended local protected route (`/operator`). Rejects external URLs, hostile paths, subpaths, or double-encoded values (`%252Foperator`).
   - `smoke_checks()` now captures the raw Location header in `report["web_operator_redirect"]` and the check detail for diagnosability.

2. **Deterministic Regression Tests (`tests/ops/test_cloud_run_live_deployment.py`)**:
   - Expanded `test_is_safe_protected_redirect_contract()` covering:
     - Absolute HTTPS safe redirect.
     - Relative `/login?returnTo=%2Foperator` safe redirect (the real failing case).
     - Rejection of HTTPS → HTTP scheme downgrade.
     - Rejection of effective port mismatches (443 vs 8443).
     - Rejection of malformed non-numeric ports (`:bad`).
     - Rejection of out-of-range ports (`:99999`).
     - Rejection of userinfo in target location.
     - Rejection of target URL fragments.
     - Rejection of hostile external `returnTo` parameter values (`https://attacker.com`, `/evil-path`, `/operator/extra`, `%252Foperator`).
     - Rejection of hostile external host redirects (`https://attacker.com/login?returnTo=%2Foperator`).
     - Rejection of hostile protocol-relative redirects (`//attacker.com/login?returnTo=%2Foperator`).
     - Rejection of unauthenticated 200 OK responses (fail-closed auth preservation).
     - Rejection of redirects to wrong target paths (e.g., `/dashboard`).
     - Rejection of redirects missing the `returnTo` parameter.

3. **Middleware Not Changed**:
   - `apps/web/src/middleware.ts` reverted to origin/dev `new URL("/login", request.url)` — middleware is correct and produces the valid relative Location that urljoin now handles.
   - `apps/web/src/lib/auth/__tests__/middleware.test.ts` restored to origin/dev 2-test baseline; no fabricated proxy-header tests added.

## 3. Verification Summary (Round 2)

- `pytest tests/ops/test_cloud_run_live_deployment.py::test_is_safe_protected_redirect_contract`: Passed.
- `pytest tests/ops/test_cloud_run_live_deployment.py`: 1 pre-existing env failure (`test_deploy_preflight_imports_runtime_dependencies_via_locked_python`, `uv` not installed in sandbox), unrelated to this task.
- `ruff check --diff product_ops/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py`: Clean.
- `git diff --check`: Clean.
- Zero Package 10 visual components, page layouts, design archives, or API business responses were modified.
- Middleware unchanged; no fabricated evidence introduced.
