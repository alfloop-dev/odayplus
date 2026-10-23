# Source admission readback — 2026-10-12

This technical readback reconciles the human source approval with the current
producer implementation. It changes no source approval, classification digest,
production flag or release-authority status.

## Authoritative versions

Producer repository: `alfloop-dev/oday-data-platform`, dev head
`606c35f26a47df9eae7667eaeb82f5eebf75fa22`.

- PR #81 merged 2026-10-08: restricted/prohibited raw persistence refusal.
- PR #82 merged 2026-10-08: candidate-only Google Places, no raw persistence.
- Approval receipt: `HUMAN-OSS-LEGAL-APPROVAL-001`, SHA256
  `2119dc6f74335d3a1884e0a0dfda6db61171df0542c46371c1719c26204d83c7`.

The earlier approval-time note that classification had no downstream effect is
historical. At the producer head above, `require_persistable_classification`
refuses declared RESTRICTED/PROHIBITED fields before provider access or local
publication. Buffered evidence is committed only after observed-field and
contract validation; failures discard only owned staging output and omit raw
values from public errors. Resume/import paths recheck classification.

The legacy Google bulk/raw asset refuses execution before cache, client,
kernel or evidence-store construction. The candidate verifier keeps minimal
place ID/conclusion records; this does not admit billing, quota, egress or
live runtime evidence.

## Classification is not capture admission

All thirteen approved classification artifacts remain BOUND against the
producer validator. Three declare fields that the current persistence policy
refuses: `mof` (5 declarations), `overture` (3) and `site_context` (3).
Declarations count classified fields, not people or stored rows. Their raw
lanes remain PERSISTENCE_PROHIBITED; this is not a reviewed redaction path or
an admitted full masked release. The other ten still need runtime inputs,
source switches/receipts, egress and source-specific operational evidence.

The approved classification JSON and approval receipt are unchanged. In
particular, restrictions are not downgraded to make capture succeed.

## Consumer contract refresh

The foundation v0.4.1 release artifacts remain byte-identical to the current
producer and retain their existing artifact pin. Product v0.4.1 changed only
its released catalog's Site Market Context model signature and release
content digest; public contract schemas, compatibility and dependency closure
are byte-identical. Oday Plus now pins the exact producer head above and
published product manifest:

- Product content digest:
  `388e5145e9bf106683168cefc0663caad4419acbcab376554e1d2d137d383af9`.
- Product release.json SHA256:
  `acdc828ecca8e5a476c3b02f60d6e53d992f8c05a40877e9babb94e629ee4bdc`.

Client models are regenerated from the public release. Only producer-source
annotations change; model fields and schema digests do not change. No producer
implementation, SQL storage schema or relation-ownership catalog is copied.
A contract artifact pin is not a deployed producer image/version readback.

## Validation and remaining release work

The isolated producer check at the exact head above passed 158 tests and
69 subtests (41 deprecation warnings), covering raw classification enforcement,
POI sources and transport acquisition with offline fixtures. No live source
request or production storage write was performed. Consumer contract pin,
compatibility, generated-client and market-read tests plus the EMGI consumer
boundary validate this refresh separately.

Live producer version/image, first-party captures, immutable retention,
source policy/egress and model/product admission remain unproven. Google billing
and candidate-only operational admission remain required. Independent review,
real-account manifests, release rehearsals and the nine human release
approvals still govern production cutover.
