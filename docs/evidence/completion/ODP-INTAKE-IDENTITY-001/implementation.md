# ODP-INTAKE-IDENTITY-001 implementation evidence

## Delivered runtime behavior

- `IdentityGraph` stores immutable source-edge generations and selects exactly one current edge per tenant/source identity.
- Merge creates canonical property redirects, closes affected effective edges, and appends superseding edges.
- Split reassigns selected source identities without rewriting historical edges.
- Unmerge/reversal restores the recorded before-graph with new edge generations and closes merge redirects.
- All graph mutations execute against a private transaction copy; validation, injected failure, cycle detection, and concurrency conflicts discard the complete attempted mutation.
- Listing and candidate references retain `property_id_at_creation`; current reads follow redirects while source reads expose deterministic complete lineage and optional `as_of` selection.
- Every property, edge, redirect, reference, decision, version, and query is tenant-qualified.

## Owned boundary

The task owns the in-memory/runtime identity domain, command service, integration read adapter, focused tests, and this evidence. It does not change the canonical SQL/OpenAPI/event documents or implement their HTTP and PostgreSQL adapters; those compose through the approved contracts and sibling execution lanes.

## Verification

Run on 2026-07-18 UTC:

```text
uv run pytest tests/unit/listing/test_identity_graph.py tests/integration/test_assisted_listing_identity.py -q
7 passed

python3 -m ruff check modules/integration modules/listing tests/unit/listing/test_identity_graph.py tests/integration/test_assisted_listing_identity.py
All checks passed!
```

The package exports were changed to lazy loading because eager `modules.listing` initialization caused a pre-existing circular import through `modules.external_data.application.listing_feed_adapter`. Public names remain unchanged.
