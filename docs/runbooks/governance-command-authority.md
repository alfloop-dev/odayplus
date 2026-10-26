# Governance command identity, retry and export admission

Date: 2026-10-12

Governance decisions and fixture export metadata use the guard-verified principal
subject/tenant and accepted operator role. Body actor/role fields remain compatible
inputs, but cannot supply authority. Missing decision/export guard wiring denies
the command. Existing APPROVE/CREATE permission assignments are retained.

The private aggregate command receipt map binds actor/tenant/key to operation,
record/ref or export scope/range/format/retention, reason and verified role. A
changed command conflicts, another actor cannot inherit its receipt, and legacy
raw-key receipts are refused without rewriting historical evidence. Current
records and tenant are checked before a decision replay. Export/reload preserves
receipts; this private map is not a canonical schema or retention policy.

Growth delegation forwards verified identity, tenant and the same key, preserves
the linked Growth ref in the parent's decision/audit, and translates child domain
errors. A parent receipt is not sufficient evidence that a child save succeeded:
a matching retry replays/reconciles the bound Growth command before returning the
parent receipt. This prevents duplicate local decisions in the tested partial-save
case. It does not make the two repository writes a single physical transaction;
concurrency, canonical business/audit/outbox and fine-grained scopes remain open.

Blank approve reasons remain blank under the existing legacy optional-reason rule;
the service/UI no longer invent a risk/budget justification. Return/reject still
require 10 trimmed characters. Required human reason and segregation policy for
canonical decisions still need their authoritative binding and admission.

The old Evidence Package method generated only a fixed filename/size and claimed
retention text. It never produced a formal downloadable artifact. Non-fixture or
canonical mode now returns CANONICAL_EVIDENCE_EXPORT_REQUIRED before creating any
history/audit/receipt or claiming generation. Explicit fixtures retain metadata-only
demo behavior; they are not proof of a real file, signed URL, masking or retention.
The assembled canonical API test asserts 503 and unchanged history across restart.

Formal export remains required for whole-system completion. The existing canonical
AuditEvidenceExportService needs admitted program/decision cards, correlation/time
scope, identity boundary, purpose, classification/masking and retention/authorization
bindings; its request is not interchangeable with this legacy date/module/format
form. No values or approval receipts are fabricated to bridge that gap.

The web workbench retains per-intent decision/export keys, validates matching
record/action/status or export scope, and waits at most 20 seconds. Any failed or
mismatched API acknowledgement returns without local success, including in fixture
mode. Pending export settings are disabled and named. A failed new export clears
its result, and confirmed export resets intent for a later deliberate generation.
Export history contrast is fixed inside its existing panel. Local keys do not
provide durable cross-reload retry or multi-process exactly-once mutation.

Evidence contains the final candidate, raw failures and final checks. The first
new actor tests confused approval ID with entityRef; the assertions now read the
actual related record from the snapshot. A canonical constructor without its owned
repositories is separately tested as invalid configuration; formal export refusal
uses actual assembled repositories. Initial browser axe scans found 2.56:1 history
text contrast and were corrected. No failed attempt is called complete acceptance.
