# F1 explicit descriptor contract: finite clarification required

**SOURCE ONLY / NOT review-ready / F1 and F3 remain OPEN.**

Actual Manager run `pi-20261015T153046Z-c8e7e4c0` inspected clean parent
`2b9148c0fb49b4641fe3821263634a3503517b2a` / tree
`5c9100d8bdf6e6771d4b7eebf923ee1c2c52399f`, with fetched
admitted `origin/dev` `19562ad2a8773099db26366446b3488b446e630c`.
The accompanying [static inspection](pi-batch007-f1-contract-inspection.json)
records the full tree, source-body SHA256s, actual running Manager allocation,
361-entry scope, 17 source documents and canonical/materialized batch007 hashes.
This is author static inspection, NOT a test receipt, independent assessment,
new contract grant, authorization waiver or proof of HTTP exploit reach.

## Concrete conflict before any F1 implementation

`shared/auth/abac.py:ResourceDescriptor` has no explicit authoritative
bound/global/type-level discriminator. Its `tenant_id` and `resource_id` both
default to `None`. The descriptor, default engine, identity and tenant guard are
byte-equal to admitted origin/dev. No production or test source has been edited
by this run, and no source imports or tests were executed.

The complete original `tests/security/test_rbac_abac.py` is also base-byte-equal.
Its ten descriptor-using test functions contain **15 descriptor constructions,
ZERO with a tenant keyword and ZERO with a resource-id keyword**. In particular:

- `test_cross_brand_principal_denied`: own-brand VIEW must allow; foreign and
  missing brand must deny specifically with `scope.brand` and its exact reason.
- `test_module_outside_scope_denied`: own-module VIEW must allow; foreign and
  missing module must deny specifically with `scope.module` and its exact reason.
- `test_franchisee_limited_to_own_store`: own-store VIEW must allow.
- `test_high_risk_allowed_when_flag_enabled_with_dual_approval`: tenantless
  `ResourceDescriptor(type="priceops")` EXECUTE must allow with audit obligation
  and an actual allow audit event after enabling the existing dual-approval flag.
- Other original negatives require classification, disabled-flag and SoD policy
  identities, not an earlier missing-tenant refusal masking those checks.

The static report preserves exact descriptor expressions, function names/lines
and assertion counts. It does not assign any of these fixtures a new global
business meaning. Inserting strict tenant isolation before these checks would
change their observed policy/outcome; moving it after them still denies the
original positives. Neither is a verified acceptable composition.

Batch007 requires BOTH explicit authoritative global/abstract intent (never
inferred from missing tenant/id/type/role/client attributes) AND preservation of
all original assertions/no retagging to hide baseline behavior. Leaving old
constructors implicitly abstract would violate the former. Quietly changing
fixtures to global so the assertions stay green could violate the latter.
A new descriptor field alone does not resolve the original inputs' intent.
Therefore this run follows the disposition's STOP rule rather than choosing a
compatibility exemption or concealing the ambiguity with passing new tests.

## Read-only caller inspection: distinct contracts, not a blanket migration

- `apps/api/oday_api/security/dependencies.py:require_permission` explicitly
  documents a TYPE-LEVEL RBAC check. It builds an audit descriptor and records
  its independently computed decision; that is not evidence that arbitrary
  default-engine descriptors are globally scoped. The helper's principal tenant
  must not be reused as fake record ownership to repair F1.
- `tests/integration/test_auth_boundary_authz.py:_view_forecast` supplies a fixed
  resource tenant `tenant-a`; its engine-composition and smoke descriptors show
  that missing resource ID does NOT mean a global/abstract target. This file is
  not leased for edits; it was inspected read-only and remains unchanged.
- `modules/external_data/application/market_data_facade.py` distinguishes its
  owning foundation-resource contract and separately enforces tenant evidence
  on non-foundation envelopes. Its decisions are not a generic default-engine
  resource-type exception. No scope or permission to migrate that facade is
  requested or assumed.
- `shared/auth/tenant.py:check_tenant_isolation` is the existing guard to reuse,
  not edit. Accepted registered/current/object-bound waiver decisions contain
  `audit` and `cross_tenant_waiver`; these must survive ABAC and engine merging.
  The current `evaluate_abac` discards allowed policy decisions, the normal
  engine allow discards ABAC obligations, and audit selection does not consult
  obligations. These owning seams need repair after contract clarification,
  not a new waiver, silent role bypass or separate audit-model rewrite.

This is a bounded inspection, not exhaustive dynamic consumer classification,
whole-composition review, proven API exposure or a claim that another production
path already needs modification.

## Exact disposition requested from Manager / Human-Ops

Please clarify the permitted source migration within the ALREADY leased
`shared/auth/abac.py`, `shared/auth/engine.py` and
`tests/security/test_rbac_abac.py` before renewing implementation:

1. Confirm whether original type-level fixture INPUTS may be explicitly declared
   abstract using a server-owned descriptor constructor/field, while preserving
   EVERY original function, assertion, expected policy/reason, high-risk hook and
   audit requirement. Alternatively specify genuine owning tenant fixtures where
   those are record-bound checks. Do not authorize guessing all missing tenants
   global, assigning principal fallback ownership, or bypassing test outcomes.
2. Confirm the authoritative descriptor contract: default actual targets remain
   tenant-bound; a global/type-level declaration is explicit, server-owned,
   constrained and cannot override explicit ownership/mismatch. An undecorated
   missing-tenant actual record must deny. A global flag in generic attributes,
   a role name or missing resource ID is NOT an authority root.
3. If those exact original inputs must remain unchanged, adjudicate the remaining
   conflict rather than claiming both fail-closed missing ownership and unchanged
   positive outcomes. Any new business classification or further owning path
   needs its own finite disposition/lease first. This request grants none.

After clarification, retain registered-waiver authority and strict operator-viewer
policy, scope/classification, flags/SoD/two-person controls; merge audit/waiver
obligations and audit all required tenant paths. Add separate direct owning
own/foreign/missing/invalid/spoofed-global/waiver controls without changing the
original2178/178 selection membership. No tests are requested to be removed,
skipped, weakened or reclassified as passes.

F2 remains only the preceding partial source repair with its own historical
measurements. F3 distinct issued-cookie/all-exit once-only bounded cleanup is
still OPEN and untouched in this run. Do not run final proof against this known
incomplete composition, or review-submit simply to show activity. After ALL
repairs and docs freeze, ALL fresh14 canonical/unchanged17 supplemental/owning/
normal terminal CI/configured full sequential assessment are still required.
Rejected13c packet1 and uninspected16 remain preserved; no proof transfer or
underlying account-independence claim. Human/Ops final PENDING, original complete
blocked6/failed-Pi/PR1459@a129 unchanged, quarantine/bindingNULL/cleanupUNKNOWN/
HOLD/goalfalse. No finalization, approval, merge arming, merge or live effects.
