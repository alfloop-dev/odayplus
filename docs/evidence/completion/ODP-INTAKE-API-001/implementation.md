# ODP-INTAKE-API-001 implementation evidence

The task implements the approved Assisted Listing Intake v1 HTTP surface at
`/api/v1`. The existing Operator Console compatibility surface remains at
`/api/v1/operator/network-listings`.

## Delivered

- Effective OpenAPI 1.1.3 is built from the base plus all five overlays in the
  review-manifest order.
- A committed effective JSON artifact and generated TypeScript schema/path
  namespace are produced by `scripts/generate_assisted_listing_intake_client.py`.
- Runtime routes cover all 27 approved operations: query, URL and batch intake,
  correction, match/identity decisions, merge/split/unmerge, assignment, retry,
  saved views, promotion, lifecycle actions, SLA actions, and review/reversal.
- The HTTP boundary fails closed without tenant scope, enforces opaque cursor
  rejection, idempotent replay/payload conflict, partial batch success, and
  `If-Match` preconditions on high-impact writes.
- Runtime-to-effective-operation coverage and generated-client drift are pinned
  by contract tests.

## Composition boundary

This lane owns the API, artifact/client generation, and contract tests. Durable
PostgreSQL schema, state-machine internals, authorization policy expansion, and
event publication remain owned by their sibling Assisted Intake tasks. The
`AssistedIntakeStore` constructor is injectable so a durable adapter can compose
without changing the approved HTTP surface.
