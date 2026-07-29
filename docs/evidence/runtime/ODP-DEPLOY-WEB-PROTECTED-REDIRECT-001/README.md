# ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001 — runtime evidence

Task: Fix candidate Web protected redirect smoke contract.
Owner: Antigravity2 · Reviewer: Claude · Phase: Live Runtime Deployment · 2026-07-29

Scope guard: This task changes only the deployment validator protected redirect contract logic and its corresponding ops unit tests. No Package 10 visual components, page layouts, design archives, or API business responses are touched.

## 1. Observed Incident (Deploy Dev run 30436771086)

In Deploy Dev run [30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086), the candidate Web `/operator` check failed during release-aware smoke:
```json
{
  "detail": "status=307 protected_redirect=false",
  "name": "smoke:web:/operator",
  "ok": false
}
```

The candidate web service responded with HTTP 307 (Temporary Redirect), but `validate_cloud_run_live_deployment.py` flagged `protected_redirect=false`.

## 2. Root Cause Analysis

1. **Authentication Behavior**:
   `apps/web/src/middleware.ts` enforces fail-closed authentication for protected routes in production:
   ```typescript
   const loginUrl = new URL("/login", request.url);
   loginUrl.searchParams.set("returnTo", safeReturnTo(`${request.nextUrl.pathname}${request.nextUrl.search}`));
   return NextResponse.redirect(loginUrl);
   ```
   An unauthenticated request to `/operator` correctly receives a 307 redirect to `/login` with `returnTo=/operator`.

2. **Validator Defect**:
   `scripts/deployment/validate_cloud_run_live_deployment.py` evaluated the redirect using strict prefix matching:
   ```python
   expected_login_prefix = f"{web_url.rstrip('/')}/login?"
   auth_redirect = (
       web_status in {302, 303, 307, 308}
       and isinstance(location, str)
       and location.startswith(expected_login_prefix)
       and "returnTo=" in location
   )
   ```
   `web_url` in deployment runs is `https://candidate-93ae1b2e75e1056c---oday-web-7sxbjoeozq-de.a.run.app`.
   When candidate Web returns a relative `Location` header (`/login?returnTo=%2Foperator`) or an absolute HTTP header (`http://candidate-.../login?returnTo=%2Foperator`) due to Cloud Run frontend TLS termination, `location.startswith("https://candidate-.../login?")` returned `False`.

## 3. Remediation & Fail-Closed Protection

We implemented `_is_safe_protected_redirect` and `_effective_port` in `scripts/deployment/validate_cloud_run_live_deployment.py`:

```python
def _effective_port(parsed: urllib.parse.ParseResult) -> int | None:
    if parsed.port is not None:
        return parsed.port
    scheme = parsed.scheme.lower()
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _is_safe_protected_redirect(
    web_url: str,
    web_status: int,
    location: str | None,
    *,
    protected_path: str = "/operator",
    target_path: str = "/login",
) -> bool:
    if web_status not in {302, 303, 307, 308} or not isinstance(location, str) or not location.strip():
        return False

    raw_location = location.strip()
    request_url = f"{web_url.rstrip('/')}{protected_path}"
    base_parsed = urllib.parse.urlparse(request_url)
    resolved_url = urllib.parse.urljoin(request_url, raw_location)
    target_parsed = urllib.parse.urlparse(resolved_url)

    # Reject userinfo / credentials in target URL
    if target_parsed.username or target_parsed.password or "@" in target_parsed.netloc:
        return False

    # Reject fragments in target URL
    if target_parsed.fragment:
        return False

    # Scheme must match base scheme (reject scheme downgrade, e.g. https -> http)
    base_scheme = base_parsed.scheme.lower()
    target_scheme = target_parsed.scheme.lower()
    if not base_scheme or base_scheme != target_scheme:
        return False

    # Hostname must match normalized base hostname
    base_host = (base_parsed.hostname or "").lower()
    target_host = (target_parsed.hostname or "").lower()
    if not base_host or base_host != target_host:
        return False

    # Effective port must match (including default vs nondefault port mismatches)
    if _effective_port(base_parsed) != _effective_port(target_parsed):
        return False

    # Path must match expected target_path (e.g. /login)
    if target_parsed.path != target_path:
        return False

    # returnTo parameter must decode to exact expected protected route
    query_params = urllib.parse.parse_qs(target_parsed.query, keep_blank_values=True)
    return_to_list = query_params.get("returnTo")
    if not return_to_list or len(return_to_list) != 1:
        return False

    decoded_return_to = urllib.parse.unquote(return_to_list[0])
    if decoded_return_to != protected_path:
        return False

    return True
```

This guarantees:
- Unauthenticated requests MUST redirect (302/303/307/308). If candidate returns 200 OK without auth, it fails closed (`False`).
- Relative `/login?returnTo=%2Foperator` and absolute `https://...` on the candidate host with matching scheme, host, and effective port are accepted.
- Scheme downgrade (e.g. HTTPS base to HTTP target) is rejected (`False`).
- Effective port mismatches (e.g. port 443 vs 8443) are rejected (`False`).
- Userinfo (credentials in target location) and URL fragments are rejected (`False`).
- Hostile external origin redirects (`https://attacker.com/login`) or protocol-relative URLs (`//attacker.com/login`) are rejected (`False`).
- Hostile `returnTo` values (`https://attacker.com`, `/evil-path`, `/operator/extra`) are rejected (`False`).
- Redirects to paths other than `/login` or missing/multiple `returnTo` parameters are rejected (`False`).

## 4. Test Verification Receipts

- `pytest tests/ops/test_cloud_run_live_deployment.py`: Passed (357 passed).
- `ruff check`: All checks passed clean on modified python files.
- `vitest apps/web/src/lib/auth/__tests__/middleware.test.ts`: Passed (2/2 passed).

