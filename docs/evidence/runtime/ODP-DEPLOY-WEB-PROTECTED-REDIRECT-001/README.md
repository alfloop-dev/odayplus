# ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001 — runtime evidence

Task: Fix candidate Web protected redirect smoke contract.
Owner: Antigravity2 · Reviewer: Claude · Phase: Live Runtime Deployment · 2026-07-29

Scope guard: This task updates `scripts/deployment/validate_cloud_run_live_deployment.py` to correctly resolve relative Location headers via urljoin before same-origin validation, and captures raw Location in check details and reports. `apps/web/src/middleware.ts` is **not changed** — the middleware emits a correct same-origin relative redirect. No Package 10 visual components, page layouts, design archives, or API business responses are touched.

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

1. **Coordinator Live Probe of Preserved Candidate Revision**:
   - `status`: 307
   - `observed_location_header`: `/login?returnTo=%2Foperator` (relative, same-origin)
   - Source: Coordinator live no-follow probe of the same preserved candidate revision as run 30436771086. The pre-change validator did not capture Location in its detail; the coordinator independently confirmed the real value.
   - Artifact: `docs/evidence/runtime/ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001/candidate-location-capture.json`

2. **Root Cause — Validator Prefix Match Rejected a Valid Same-Origin Relative Location**:
   - `apps/web/src/middleware.ts` correctly calls `new URL("/login", request.url)`, which produces a same-origin relative Location header: `/login?returnTo=%2Foperator`.
   - The pre-fix `_is_safe_protected_redirect` in `scripts/deployment/validate_cloud_run_live_deployment.py` validated Location using a prefix test: `location.startswith(f"{web_url}/login?")`. A valid relative Location `/login?returnTo=%2Foperator` does not start with the absolute candidate origin, so it was incorrectly rejected, producing `protected_redirect=false`.
   - **Middleware is not defective.** No middleware change is introduced or required.

3. **Remediation — Validator-Only Fix**:
   - **`scripts/deployment/validate_cloud_run_live_deployment.py`**: `_is_safe_protected_redirect` now resolves the raw Location via `urllib.parse.urljoin` against the candidate request URL before validation. Both absolute and relative same-origin redirects to `/login?returnTo=/operator` are now correctly accepted. All hostile patterns (scheme downgrade, host mismatch, port mismatch, external origin, protocol-relative, userinfo, fragment, hostile returnTo, double-encoded returnTo) are still rejected with fail-closed semantics.
   - **`scripts/deployment/validate_cloud_run_live_deployment.py`**: `smoke_checks()` now captures the raw Location header in `report["web_operator_redirect"]` and in the `smoke:web:/operator` CheckResult detail for immediate diagnosability.

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
- Relative same-origin `/login?returnTo=%2Foperator` and absolute `https://...` on the candidate host with matching scheme, host, and effective port are both accepted.
- Scheme downgrade (HTTPS base to HTTP target) is rejected (`False`).
- Effective port mismatches (e.g. port 443 vs 8443) are rejected (`False`).
- Malformed non-numeric ports (e.g. `:bad`) and out-of-range ports (e.g. `:99999`) catch `ValueError` and fail closed (`False`).
- Userinfo (credentials in target location) and URL fragments are rejected (`False`).
- Hostile external origin redirects (`https://attacker.com/login`) or protocol-relative URLs (`//attacker.com/login`) are rejected (`False`).
- Hostile `returnTo` values (`https://attacker.com`, `/evil-path`, `/operator/extra`) and double-encoded values (`%252Foperator`) are rejected (`False`).

## 5. Test Verification Receipts

- `pytest tests/ops/test_cloud_run_live_deployment.py::test_is_safe_protected_redirect_contract`: Passed (verified by reviewer: accepted relative and absolute safe redirects; rejected all hostile patterns).
- `pytest tests/ops/test_cloud_run_live_deployment.py`: 1 failure — `test_deploy_preflight_imports_runtime_dependencies_via_locked_python`, pre-existing environmental issue (`uv` not installed in worker sandbox), unrelated to this task.
- `ruff check --diff scripts/deployment/validate_cloud_run_live_deployment.py tests/ops/test_cloud_run_live_deployment.py`: clean.
- `apps/web/src/lib/auth/__tests__/middleware.test.ts`: No new tests added; original 2 tests from origin/dev baseline retained unchanged.
