# ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001 — runtime evidence

Task: Fix candidate Web protected redirect smoke contract.
Owner: Antigravity2 · Reviewer: Claude · Phase: Live Runtime Deployment · 2026-07-29

Scope guard: This task updates `apps/web/src/middleware.ts` for base origin resolution behind Cloud Run TLS termination, updates `scripts/deployment/validate_cloud_run_live_deployment.py` to capture raw Location headers in check details and reports, and adds test coverage. No Package 10 visual components, page layouts, design archives, or API business responses are touched.

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

## 2. Captured Header & Root Cause Analysis

1. **Exact Candidate Location Capture**:
   - `status`: 307
   - `observed_location_header`: `http://0.0.0.0:8080/login?returnTo=%2Foperator`
   - Capture artifact: `docs/evidence/runtime/ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001/candidate-location-capture.json`

2. **Root Cause**:
   - `infra/docker/Dockerfile.web` runs Next 15.5.21 standalone with `ENV HOSTNAME=0.0.0.0 PORT=8080`.
   - `apps/web/src/middleware.ts` previously evaluated `new URL("/login", request.url)` when redirecting unauthenticated requests. In Next.js standalone mode behind Cloud Run TLS termination, `request.url` evaluates to the bound container host/port (`http://0.0.0.0:8080/operator`), causing middleware to emit `Location: http://0.0.0.0:8080/login?returnTo=%2Foperator`.
   - The deployment validator `_is_safe_protected_redirect` in `scripts/deployment/validate_cloud_run_live_deployment.py` requires same-origin matching against `web_url` (`https://candidate-...a.run.app`). It correctly rejected `http://0.0.0.0:8080/...` as a scheme downgrade (HTTPS -> HTTP) and host mismatch.

3. **Remediation**:
   - **`apps/web/src/middleware.ts`**: Updated `middleware.ts` with `resolveRequestOrigin()` to derive the base origin from `ODP_WEB_BASE_URL` (if configured) or reverse proxy headers `x-forwarded-proto` and `x-forwarded-host`/`host`. Under Cloud Run, this produces canonical absolute HTTPS redirects (`https://candidate-...a.run.app/login?returnTo=%2Foperator`).
   - **`scripts/deployment/validate_cloud_run_live_deployment.py`**: Updated `smoke_checks()` to record `location` in `report["web_operator_redirect"]` and include `location=...` in the `smoke:web:/operator` CheckResult detail for immediate diagnosability.
   - **Hardened Fail-Closed Validator**: Kept all strict security checks in `_is_safe_protected_redirect`: scheme/host/effective-port matching, userinfo/fragment rejection, single-value returnTo matching `/operator`, and ValueError exception handling.

## 3. Implementation Code

```python
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

    try:
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
        base_port = _effective_port(base_parsed)
        target_port = _effective_port(target_parsed)
        if base_port is None or target_port is None or base_port != target_port:
            return False

        # Path must match expected target_path (e.g. /login)
        if target_parsed.path != target_path:
            return False

        # returnTo parameter (parse_qs already URL-decodes values once; avoid double-decoding)
        query_params = urllib.parse.parse_qs(target_parsed.query, keep_blank_values=True)
        return_to_list = query_params.get("returnTo")
        if not return_to_list or len(return_to_list) != 1:
            return False

        if return_to_list[0] != protected_path:
            return False

        return True
    except ValueError:
        return False
```

## 4. Security & Fail-Closed Contract Guarantees

- Unauthenticated requests MUST redirect (302/303/307/308). 200 OK without auth fails closed (`False`).
- Absolute `https://...` on the candidate host with matching scheme, host, and effective port are accepted.
- Scheme downgrade (HTTPS base to HTTP target) is rejected (`False`).
- Effective port mismatches (e.g. port 443 vs 8443) are rejected (`False`).
- Malformed non-numeric ports (e.g. `:bad`) and out-of-range ports (e.g. `:99999`) catch `ValueError` and fail closed (`False`).
- Userinfo (credentials in target location) and URL fragments are rejected (`False`).
- Hostile external origin redirects (`https://attacker.com/login`) or protocol-relative URLs (`//attacker.com/login`) are rejected (`False`).
- Hostile `returnTo` values (`https://attacker.com`, `/evil-path`, `/operator/extra`) and double-encoded values (`%252Foperator`) are rejected (`False`).

## 5. Test Verification Receipts

- `pytest tests/ops/test_cloud_run_live_deployment.py`: Passed (356/357 passed, 1 pre-existing env skip due to missing `uv`).
- `apps/web/src/lib/auth/__tests__/middleware.test.ts`: Added unit test coverage for reverse proxy headers (`x-forwarded-proto`, `x-forwarded-host`) and `ODP_WEB_BASE_URL`.
