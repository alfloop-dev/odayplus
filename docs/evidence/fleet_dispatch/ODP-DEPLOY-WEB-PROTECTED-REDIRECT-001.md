# ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001: Fix candidate Web protected redirect smoke contract

Owner: Antigravity2 · Reviewer: Claude · Phase: Live Runtime Deployment · 2026-07-29

Remediate the `smoke:web:/operator: status=307 protected_redirect=false` failure observed in Deploy Dev run 30436771086 without weakening unauthenticated access protection or modifying Package 10 visual components.

Full runtime detail:
`docs/evidence/runtime/ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001/` (README.md).

## 1. Root Cause Analysis

In Deploy Dev run [30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086), candidate Web `/operator` returned status 307 when queried by the deployment validator `scripts/deployment/validate_cloud_run_live_deployment.py`.

However, the validation check evaluated `protected_redirect` to `false`:
```
- smoke:web:/operator: status=307 protected_redirect=false
```

Investigation confirmed:
- `apps/web/src/middleware.ts` correctly enforced authentication in production mode: unauthenticated requests to `/operator` received HTTP 307 redirect to `/login?returnTo=%2Foperator`. Fail-closed protection was fully intact.
- The failure was caused by rigid string prefix matching in `scripts/deployment/validate_cloud_run_live_deployment.py`:
  ```python
  expected_login_prefix = f"{web_url.rstrip('/')}/login?"
  auth_redirect = (
      web_status in {302, 303, 307, 308}
      and isinstance(location, str)
      and location.startswith(expected_login_prefix)
      and "returnTo=" in location
  )
  ```
- When Cloud Run's frontend proxy terminates TLS and forwards the request to the candidate web container over internal HTTP, or when relative `Location` headers (`/login?returnTo=%2Foperator`) or absolute `http://` scheme URLs (`http://candidate-.../login?returnTo=%2Foperator`) are returned, the rigid `https://.../login?` prefix check evaluated to `False`.

## 2. What Changed

1. **Validator Contract Rework (`scripts/deployment/validate_cloud_run_live_deployment.py`)**:
   - Implemented strict `_is_safe_protected_redirect` helper enforcing fail-closed same-origin authentication validation:
     - **Status code**: Must be in `{302, 303, 307, 308}` (unauthenticated 200 OK fails closed).
     - **Credentials / Userinfo**: Rejects URLs containing username, password, or `@` in netloc.
     - **Fragments**: Rejects target URLs containing URL fragments.
     - **Scheme**: Must match request scheme exactly. Reject scheme downgrade (HTTPS base to HTTP target).
     - **Hostname**: Must match normalized base hostname.
     - **Effective Port**: Must match (including default vs nondefault port mismatches e.g. 443 vs 8443).
     - **Target Path**: Must strictly equal `/login`.
     - **returnTo Parameter**: Decoded `returnTo` parameter must strictly equal the intended local protected route (`/operator`). Rejects external URLs, hostile paths, or subpaths.

2. **Deterministic Regression Tests (`tests/ops/test_cloud_run_live_deployment.py`)**:
   - Expanded `test_is_safe_protected_redirect_contract()` covering:
     - Absolute HTTPS safe redirect.
     - Relative `/login?returnTo=%2Foperator` safe redirect.
     - Rejection of HTTPS -> HTTP scheme downgrade.
     - Rejection of effective port mismatches (443 vs 8443).
     - Rejection of userinfo in target location.
     - Rejection of target URL fragments.
     - Rejection of hostile external `returnTo` parameter values (`https://attacker.com`, `/evil-path`, `/operator/extra`).
     - Rejection of hostile external host redirects (`https://attacker.com/login?returnTo=%2Foperator`).
     - Rejection of hostile protocol-relative redirects (`//attacker.com/login?returnTo=%2Foperator`).
     - Rejection of unauthenticated 200 OK responses (fail-closed auth preservation).
     - Rejection of redirects to wrong target paths (e.g., `/dashboard`).
     - Rejection of redirects missing the `returnTo` parameter.

3. **Web Auth Middleware Verification (`apps/web/src/middleware.ts`)**:
   - Confirmed `middleware.ts` enforces production session checks and passes all vitest tests (`apps/web/src/lib/auth/__tests__/middleware.test.ts`).

## 3. Verification Summary

- `pytest tests/ops/test_cloud_run_live_deployment.py`: All 357 tests passed (including `test_is_safe_protected_redirect_contract`).
- `ruff check`: All checks passed clean on modified python files.
- `vitest`: All web middleware auth unit tests passed clean (2/2 passed).
- Zero Package 10 visual components, page layouts, design archives, or API business responses were modified.

