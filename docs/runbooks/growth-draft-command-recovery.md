# Growth draft command recovery

Date: 2026-10-12

## Defects corrected

The builder regenerated create and submit keys on each click. If creation was
acknowledged but submission failed, retrying repeated creation rather than using
the known action ID. It also described failed creation as a local audit success.
Create/submit API routes accepted body-supplied actor identity, and the service's
legacy key-only cache could return an unrelated actor/request/operation receipt.

## Current contract

The existing five-step payload and three draft kinds remain unchanged. Each open
builder retains independent create and submit keys. Unchanged create payloads reuse
the create key; editing an unconfirmed payload creates a new intent. Correlation
IDs remain per attempt. These two client calls use a 20-second request timeout;
abort or a lost response does not prove server rollback.

Creation must acknowledge a nonempty action ID, DRAFT status and requested kind.
A confirmed draft is retained in the builder. Failed/uncertain submission stays
visible, names that draft and offers retry of its submit endpoint or retention of
the draft. Retention closes the builder without another write. Prior-step editing
is disabled after creation so displayed changes cannot imply that the saved draft
was updated. Submit must acknowledge the requested action, PENDING_APPROVAL and a
pending Growth approval whose ref is that action. Wrong/malformed responses cannot
claim success. A synchronous busy guard suppresses duplicate clicks.

The API uses the authenticated principal's subject/tenant and the verified role;
body actor fields are compatibility inputs and do not provide authority. Create
and submit cache receipts in private application aggregate state, keyed by
actor/tenant/idempotency key with operation, complete command payload and verified
role bound into the request digest. Same key with changed command/record/role
returns IDEMPOTENCY_CONFLICT. Other actors do not inherit a receipt. Legacy raw-key
receipts are refused as UNBOUND_LEGACY_REPLAY. Current referenced segment/action
and tenant checks precede return of a cached receipt. Export/reload retains the
bindings. Authorization still runs first at the HTTP boundary.

This private receipt map is not a canonical schema, approved retention policy,
all-store authorization or a physical SQL transaction contract. No database
migration, source authority or production setting is introduced here.

## Evidence and limits

Component tests cover uncertain creation, confirmed-create/uncertain-submit,
phase-specific keys, retaining the draft and wrong approval acknowledgements.
API tests cover forged body actor, changed payload/operation/record, other actor,
authorization denial, legacy receipt refusal, tenant mismatch and export/reload.
The existing assembled live-domain suite also runs; its SQLite test assembly is
not production PostgreSQL acceptance or a canonical first-party draft write.

The browser create case now checks the actual API receipt and stored action/audit
binding after creation, rather than a console message. Supplemental tests at
1440/1024/390 check partial success, pending dismissal, retry keys and axe. They
remain outside the fixed business inventory. The initial cold-start tab case was
flaky; it now waits for the actual gtab URL navigation before checking content and
is reverified with zero retries. The original failure output remains evidence.

Keys and the known draft ID are local to the open builder; this does not provide
cross-reload recovery or multi-process exactly-once mutation. Changed unconfirmed
intent can represent another draft, and a previously uncertain request may already
have committed. Draft editing after creation, all fine-grained scopes, physical
business/audit/outbox atomicity, full source/model binding and independent R7/UAT
and release authority remain separate whole-system acceptance requirements.
