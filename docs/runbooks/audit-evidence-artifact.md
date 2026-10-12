# Retained evidence JSON/CSV archive

Task: `ODP-SYS-ACCEPT-261011`. This implements real PDF and archive renderers and an
authenticated local/test download; full production Evidence Package acceptance
remains incomplete. It does not replace the R7 PDF/CSV form or authorize launch.

## Files and integrity

`render_evidence_archive` consumes a persisted `RetainedEvidence`, verifies its
existing record/governance integrity, and refuses purge tombstones or unmasked
bundles. The ZIP contains the full original `bundle.json`,
`retained-evidence.json`, decision cards, audit events, subsidy matrix CSVs, and a
manifest with the exact byte count and SHA-256 of each payload file. Existing
bundle/record hashes retain their original meanings; the archive SHA-256 is a
separate digest. Hash-chain metadata is not presented as a cryptographic signer.

CSV includes every row field, serializing nested values as JSON. Cells that
could execute spreadsheet formulas receive a leading apostrophe; original
values remain unchanged in `bundle.json`. JSON has no fabricated evidence,
readiness, missing-requirement, policy, actor, or correlation values. Fixed ZIP
metadata and uncompressed entries reproduce identical bytes for the same
retained record after a SQLite restart. A legal hold or purge legitimately
changes the retained record; this guarantee does not claim unchanged governance.

## HTTP boundary

`POST /audit/evidence/export` now takes `requested_by` and `identity_boundary`
from the permission guard's principal. Legacy body fields remain accepted for
API compatibility but cannot set the actor or boundary. The existing boundary
field stores canonical JSON of the verified subject and tenant, without adding
canonical data columns or a migration.

The list, full-record and expired-record read endpoints expose only a record
whose exporter and exact subject/tenant boundary match the caller. Other
principals and tenants see an empty list or 404. Legacy internal/service exports
without a verifiable HTTP boundary are not automatically reassigned. Retention
administration retains its existing explicit platform-role mapping and
separation-of-duties checks; this change does not grant cross-principal viewing.

`GET /audit/evidence/exports/{export_id}/download` requires `audit:EXPORT` as well
as record ownership. It returns actual ZIP bytes, `X-Artifact-SHA256`, attachment
disposition, `private, no-store`, and `nosniff`. An expired download authorization
or a purge returns 410. A verified download creates `audit.evidence_download.v1`
with the actor, actual archive hash/size, original bundle checksum and correlation.
The optional `format=pdf` parameter returns the PDF of the same retained record;
omitting it retains the ZIP default. Unsupported formats return 422. Both formats
share the ownership, authorization-expiry, sensitive-data and production gates,
and successful audits record the actual format and response digest.
This is an authenticated response, not a seven-day signed URL or proof that the
browser saved the response.

Sensitive/restricted/secret exports cannot be downloaded using caller-supplied
`authorized_by`/`authorization_id` strings. They return 403 until an independently
verified authorization mechanism is connected. In `ODP_PRODUCT_MODE=production`,
caller-provided export cards/correlation/scope return 503
`CANONICAL_EVIDENCE_INPUT_REQUIRED`, and downloads return 503
`CANONICAL_EVIDENCE_DOWNLOAD_ADMISSION_REQUIRED`. These gates prevent this local
artifact path from being mistaken for admitted production evidence. Existing
production authentication remains required before either handler runs.

## Required before production admission

An approved program, tenant/module/time scope and authoritative immutable cards
and AuditEvents must supply the exporter through a canonical resolver. Free-text
scope and body classification are not proof of authority. Complete record,
brand, region and store visibility, sensitive authorization receipts, human
reason/segregation rules, privacy checks, artifact retention and download audit
must be reviewed against the admitted source and actor manifest.

The R7 Govern form still needs its canonical data resolver,
reliable export command/persistence, real download integration, artifact-backed
history and exact source review. Its non-fixture export remains unavailable; a
Neither the PDF renderer nor JSON/CSV ZIP completes the canonical form or system acceptance.

## PDF portability and attribution

The PDF contains export metadata, every decision/event/matrix row, and the full
retained-record appendix. It draws JSON as plain text, paginates long values
including unbroken CJK/IDs, and refuses missing glyphs instead of replacing
evidence with empty boxes. Fonts are embedded and no web links or JavaScript
actions are created from evidence strings. The same retained record, locked
renderer and font generate identical bytes after restart. It is not claimed as
PDF/A, a digital signature, or an independently admitted evidence source.

Runtime uses [ReportLab](https://docs.reportlab.com/reportlab/userguide/ch3_fonts/)
5.0.1 from the frozen lock and unmodified IBM Plex Sans TC, pinned in
`modules/opsboard/audit/assets/ibm-plex-sans-tc/SOURCE.json` with upstream commit,
Git blob and SHA-256. The adjacent original OFL-1.1 license covers the font;
its attribution must accompany redistribution. The Python dependency notice is
generated from installed distributions. The dependency and font source records
do not invent any release approval or alter human policy receipts.

## Verification

Run the evidence artifact/export/persistence/assisted-listing integration tests
and domain API RBAC suite. They cover archive hashes/CSV/Unicode, spreadsheet
formula protection, tampering, masking, purge, restart, verified ownership,
cross-tenant denial, view-versus-export permission, sensitive/expired download
and production admission refusal. The production input gate test uses an
explicit synthetic principal; it does not validate a production JWT or UAT.

The existing FE-05 browser test now waits for a server-verified snapshot and
checks the API receipt actor against Decision Log and Audit Trail. Its explicit
fixture export verifies metadata propagation only. Original CI/local failures
must remain in the acceptance evidence; do not change the fixed inventory count,
mark manual checks complete, or substitute a demo receipt for a real artifact.
