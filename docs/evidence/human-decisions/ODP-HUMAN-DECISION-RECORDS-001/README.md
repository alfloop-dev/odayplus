# ODP-HUMAN-DECISION-RECORDS-001：操作者裁決的謄錄紀錄

- **Task ID**: `ODP-HUMAN-DECISION-RECORDS-001`
- **建立於**: 2026-09-22
- **基準**: `cb910b0f45fa340c45c3e965e78ba17f379ffff6`

## 這是什麼，以及**不是**什麼

本目錄收錄三份在互動工作階段中由本機操作者提供、並由 AI 代為謄錄的裁決內容。
建立本目錄的唯一理由是：在此之前這三份檔案只存在於單一機器的未追蹤目錄
（`support/handoffs/remaining-inputs-20260913/`）。裁決內容有可能隨那台機器一起消失。

**這些檔案不是已簽署的權威收據，不滿足 H01，也不解除任何閘門。**

`ODP-OSS-DECISION-PACK-001/missing-human-inputs.json` 對 H01 的要求寫得很明確：

> `source_system`：External authoritative system where receipts are stored and can be
> read back。**Repo-internal JSON with self-calculated hash is not a verifiable
> authoritative source.**

而「必須有可回讀的外部權威系統」這條要求，本身就是操作者自己的裁決：
`case-matrix.json` 的 D14 記錄為 *External authoritative system with readback*，
D12 記錄為 *Fail closed on missing, expired, unreadable, scope/hash mismatch*。
所以這道閘不是 AI 生出來擋路的，是本專案自己選的。

本目錄裡的檔案正是「repo-internal，自算雜湊」。它們沒有 principal ID、沒有簽章、
沒有可回讀的外部憑據。每份檔案末段都自行載明「由本機操作者於互動工作階段中提供
決定內容與具名，Claude 代為謄錄，非簽核人本人在此檔案上的數位簽署」。

**任何後續工作者不得**把本目錄當成 H01 已滿足、不得據此把
`docs/security/license_policy.json` 的 `status` 從 `proposed` 改掉、不得據此在
`docs/security/license_exemptions.json` 新增條目、也不得據此宣稱
`HUMAN-OSS-LEGAL-APPROVAL-001` 已完成。

## 這些紀錄可以用在哪裡

可以作為 release gate registry 中 `passed-with-deviation` 之 `deviation` 物件的
佐證來源——偏差機制要求的是具名裁決人與複審日期，不是外部權威收據。
兩者的證據強度不同，登記為偏差就是誠實地承認這個差別。

## 檔案

| 檔案 | 裁決日期 | 內容 | 量測基準 |
|---|---|---|---|
| [`2026-09-18-oss-license-four-lgpl-cases.md`](2026-09-18-oss-license-four-lgpl-cases.md) | 2026-09-18 | 四案 LGPL（sharp-libvips、psycopg2、psycopg3、moocore）全部「附條件允許」，四案共通條件四項 | `docs/security/license_policy.json` 的 `review_required.cases` |
| [`2026-09-18-dev-toolchain-vulnerability-risk-acceptance.md`](2026-09-18-dev-toolchain-vulnerability-risk-acceptance.md) | 2026-09-18 | 開發工具鏈 3 個漏洞（js-yaml high、vitest／@vitest/mocker moderate）範圍限定風險接受 | `dc0eb370b29e50f2fc916e008bdab3d08e0a3ddc` 的 `npm audit --json` |
| [`2026-09-17-h07-cdc-and-retention-decisions.md`](2026-09-17-h07-cdc-and-retention-decisions.md) | 2026-09-17 | H07 的五項 CDC／保存期裁決 | — |

## 與既有紀錄的關係

`ODP-OSS-DECISION-PACK-001/case-matrix.json` 已於 2026-09-08 記錄同一批 OSS 個案的
使用者選擇（D01–D14）。本目錄的 2026-09-18 版本是同一批決定在較晚時點、以較完整的
條件敘述重新表述，兩者方向一致：case matrix 記 D01 `A: Allow use`、D03／D04 `A: Allow with conditions`、
D06 `TEMPORARY_ACCEPT, limited to internal dev scope`，與本目錄的「附條件允許」與
「範圍限定風險接受」相符；D01 的條件內容即 case matrix 所列的義務。若兩者出現實質衝突，以日期較晚者為準，
並應由操作者本人再確認一次。

`2026-09-18-dev-toolchain-vulnerability-risk-acceptance.md` 的數字與
`missing-human-inputs.json` 中 H02 記錄的「2026-09-08 實測 0 個漏洞」不同。
兩者都是實測值，只是時點不同：漏洞數會隨 advisory 發布與 lockfile 變動。
引用時必須連同量測基準 SHA 一起引用，不可只引數字。
