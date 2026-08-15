# Operator Console Design Package 7 Diff

Date: 2026-07-15
Status: verified material R5 change

## Source Receipt

- Received: `/home/lupin/oday-plus/Oday Plus 營運管理後台 (7).zip`
- Canonical: `docs_archive/00_source_zips/operator_console/r5-20260715-package-7/`
- ZIP SHA-256: `fa1a980d1d0c3fe2102e11ac009a57a1fe25bdb5539f9bd03378c2a628a9b552`
- Unzip integrity: passed
- Design identity: R5
- Demo state: `oday-plus-r5-20260714`
- Screen labels: 37

## Package 6 Comparison

| Item | Package 6 / R4 | Package 7 / R5 | Result |
|---|---:|---:|---|
| ZIP bytes | 153,747 | 340,326 | Changed |
| Interactive HTML | 443,502 | 507,580 | Changed |
| Screen labels | 32 | 37 | Five added |
| `oday-map.js` | SHA `95d92b...` | SHA `95d92b...` | Identical |
| `support.js` | SHA `e0650b...` | SHA `e0650b...` | Identical |

No R4 screen label was removed. Added labels:

1. `Network URL 收件佇列`
2. `Dialog 從網址新增物件`
3. `Dialog 收件處理詳情`
4. `Dialog 欄位修正`
5. `Dialog 收件決策確認`

## Functional Delta

R5 adds the ODP-UXD-003-ADD-001 assisted listing intake workflow:

- URL submission from Listing Radar and Find Areas with HeatZone context.
- Exact URL identity check before retrieval.
- Source policy states and fail-closed assisted-entry fallback.
- Real processing stages instead of fake percentage progress.
- Parsed, normalized, corrected, and low-confidence field review.
- NEW, EXACT_DUPLICATE, REVISION, POSSIBLE_MATCH, and QUARANTINED outcomes.
- Side-by-side match evidence and explicit human decision confirmation.
- Snapshot, parser, freshness, correlation, before/after, reason, and audit data.
- No scheduled discovery, result-page crawling, private API use, or automatic
  merge of ambiguous matches.

## Task Impact Audit

| Existing execution scope | R5 impact | Required action |
|---|---|---|
| R4 Shell, Today, Store Ops, Growth, Candidate, SiteScore, Review, Rebalance, Govern, security | No removed labels; historical completion remains valid | Do not reopen; R5 validation must regression-test them against package 7 |
| `ODP-OC-R4-005` Network intake | Material delta after task completion | New R5 implementation task required |
| `ODP-EXT-002` assisted listing ingestion | Directly represented by R5 design | Add package 7 as canonical design source and implement contract end to end |
| `ODP-OC-R4-011` validation | Package 6 / 32-label gate is no longer release-complete | Keep existing defect fixes, but R5 validation task owns 37-label release gate |
| `ODP-OC-R4-012` release | R4 release target is stale | Supersede with R5 release task |

## Known Source Issue

The R5 summary header and interactive HTML consistently identify
`oday-plus-r5-20260714`. One legacy paragraph in the summary still mentions the
R4 demo state. The main interactive HTML and R5 header are authoritative; the
archive preserves the received bytes unchanged.

## Decision

Package 7 is the canonical Operator Console source for new work. Package 6
remains historical evidence only. No task may claim current design parity using
the package 6 hash or a 32-label gate after this receipt.
