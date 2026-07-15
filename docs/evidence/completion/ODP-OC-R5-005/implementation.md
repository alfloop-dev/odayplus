# ODP-OC-R5-005 Implementation Evidence

## Scope

- Added `modules/external_data/security/` as the assisted-listing retrieval security boundary.
- Wired assisted listing URL intake to reject embedded credentials, token/cookie submission fields, private endpoint fields, sensitive query material, and local/private IP-literal targets before processing.
- Kept fixture replay as the product default while defining a governed live-retrieval gate with injected resolver/fetcher doubles for DNS, redirect, timeout, response-size, and content-type enforcement.
- Updated assisted intake retry so non-retrievable policies (`POLICY_UNKNOWN`, `SOURCE_BLOCKED`, `ASSISTED_ENTRY_ONLY`, `AUTH_REQUIRED`) never call retrieval.
- Enforced idempotency key and correlation id on identity-affecting correction, intake decision, promotion, and listing merge writes; idempotent replay returns the cached result without appending audit evidence.
- Redacted contact, personal, credential, and token material before raw snapshots are stored in intake state.

## Acceptance Mapping

- Unsupported retrieval methods and non-http(s) schemes fail closed in `RetrievalSecurityGate`.
- Loopback, private, link-local, multicast, reserved, IPv6-local, and cloud metadata targets are rejected before fetcher invocation.
- DNS resolution and every redirect hop are revalidated with injected resolver tests, including DNS-rebinding.
- Non-retrievable source policies short-circuit before retrieval on submit and retry.
- UI/API payloads reject raw credentials, cookies, bearer tokens, token query material, and private API endpoint fields with sanitized errors.
- Retrieval limits produce retryable timeout/connection failures and terminal method, scheme, network, redirect, size, and content-type failures.
- Stored raw snapshots redact sensitive contact/personal/token fields recursively.
- Site reviewer/read-only role can read listing state but receives 403 for submit, correct, merge, quarantine decision, and promote.
- High-impact writes require reason/risk disclosure where applicable, idempotency, correlation, and append-only audit evidence.

## Notes

- No live provider credentials, cookies, bearer tokens, private endpoints, or production session material were added.
- Security tests use controlled local doubles only; they do not make outbound network requests.
