---
evidence_id: ODP-SPEC-SOURCE-PROVENANCE-001
title: "ODP-SA-06 與 ODP-FR-AVM-001 canonical source provenance"
date: 2026-09-03
status: BLOCKED_BY_EVIDENCE
owner: Codex2
reviewer: Antigravity5
repository: alfloop-dev/odayplus
observed_ref: 75d25f653aa12c21a3f9627f29af2ed4def73153
---

# ODP-SA-06 與 ODP-FR-AVM-001 canonical source provenance

## 結論

截至 2026-09-03，`ODP-SA-06` 與 `ODP-FR-AVM-001` 都是
`BLOCKED_BY_EVIDENCE`。本 repo 有查證報告與修正案的轉錄／衍生內容，
但沒有可驗證的原始 canonical 規格 artifact，因此本文件不填入推測的
canonical 版本、位置或 hash，也不把下列 repo 文件冒稱為 canonical source。

兩筆需求各自的 disposition 如下：

| Requirement | Canonical source disposition | Repo 內可追溯的 transcription／related artifact |
|---|---|---|
| `ODP-SA-06` | `BLOCKED_BY_EVIDENCE`; 原始 `ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md` 的版本、權威位置與內容 SHA-256 均不可確認 | `docs/evidence/ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md`（查證轉錄／摘要，SHA-256 見「被查證但不升格為 canonical 的文件」）；`docs/design/ODP-SA-06-AMD-001.md` 是 amendment，不是原始規格 |
| `ODP-FR-AVM-001` | `BLOCKED_BY_EVIDENCE`; 需求預期位於 ODP-SA-06 的 AVM 條目，但沒有可驗證的 canonical row／document version／location／content SHA-256 | 同一查證報告的 `AVM-001` 摘要（沒有完整原始條文）；相關 amendment 只處理其他 AVM 條目，不能補足本項來源 |

## Canonical source 與 repo transcription 的界線

本 evidence 使用以下判準：

- **Canonical source** 必須是可由來源持有人或 immutable repository reference
  取得的原始 artifact，並可記錄文件版本、精確位置與該 artifact bytes 的
  SHA-256。
- **Repo transcription** 是本 repo 對外部規格的轉錄、摘要、查證結果或修正案。
  即使內容聲稱來自某份規格，也不因此取得 canonical 身分。
- **Related artifact** 只能作為查證脈絡。它的版本與 hash 不得被挪用成
  缺失 canonical source 的版本與 hash。

因此，`docs/design/ODP-SA-06-AMD-001.md` front matter 的 `version: 0.3.0`
只代表 amendment 自己的版本；它的 `amends:
ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md` 反而證明原始規格是
另一個 artifact，不能把 `0.3.0` 當成 `ODP-SA-06` 的 canonical version。

## 查證範圍與結果

查證固定在 task brief 所給的 repository/ref，不以檔名猜測來源：

1. 在 `75d25f653aa12c21a3f9627f29af2ed4def73153`（`origin/dev`）查詢
   `ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md` 的 repo path，未找到。
   `git rev-list --objects --all` 也沒有找到該原始檔或獨立
   `ODP-FR-AVM-001` artifact。
2. `origin/main` 的 pinned ref 是
   `574dde52b56992b5088aedc74332e2e90fb40b44`；其 tree 同樣沒有原始
   `ODP-SA-06` 檔。這不是把 `main` 當 canonical，而是排除另一個可見 repo
   ref 中存在來源的可能性。
3. repo 目前只有 `docs/design/ODP-SA-06-AMD-001.md`。它聲明自己是
   `ODP-SA-06` 的 amendment（`version: 0.3.0`、`status: draft-for-review`），
   並沒有提供原始規格的 immutable location 或 content hash。
4. `ODP-FR-AVM-001` 的可見內容只有查證報告中的 `AVM-001` 摘要；報告把
   要求概括為 `GM_TTM／GM_FWD／折舊／資產／租約／正常化`，並記錄折舊
   尚未接入 AVM。這是查證轉錄，不是原始 FR 條文。

### 被查證但不升格為 canonical 的文件

| Artifact | Repo revision／版本 | Content SHA-256 | Classification |
|---|---|---|---|
| `docs/evidence/ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md` | task head `75d25f653aa12c21a3f9627f29af2ed4def73153`; 文件內查證基準為 `cc4b96d7` | `a7783faeb11617bb928144ae599d76877f4be6a06140f5f29820495232a9f242` | repo evidence transcription／查證摘要，不是 canonical source |
| `docs/design/ODP-SA-06-AMD-001.md` | `version: 0.3.0`; task head `75d25f653aa12c21a3f9627f29af2ed4def73153` | `dd3bc2f239c858f14e121d8b66bcf844f0e7a884c2b0d2a6e9ee316abea23432` | amendment，且 `status: draft-for-review`；不是原始規格 |

查證報告本身的 `線上版` artifact link 也沒有被本 task 當作 canonical
source：它沒有提供 `ODP-SA-06`／`ODP-FR-AVM-001` 原始 bytes 的 immutable
來源證明、版本綁定與 hash chain。

## 兩筆 blocked evidence record

### `ODP-SA-06`

- **Status:** `BLOCKED_BY_EVIDENCE`
- **Expected canonical artifact:** `ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md`
- **Canonical version:** unknown；不能以 amendment `0.3.0` 代替
- **Canonical location:** unknown；在 pinned `origin/dev` 與可見 Git history
  未找到該 path
- **Canonical content SHA-256:** unavailable
- **Observed transcription:** `docs/evidence/ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md`，
  §「成因二」及附錄 A；SHA-256 為
  `a7783faeb11617bb928144ae599d76877f4be6a06140f5f29820495232a9f242`
- **Why blocked:** 原始規格 artifact、版本與取得位置未提供；repo amendment
  只宣告它 amends 該 artifact，沒有使 artifact 可取得。

### `ODP-FR-AVM-001`

- **Status:** `BLOCKED_BY_EVIDENCE`
- **Expected canonical artifact/location:** 預期是
  `ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md` 的 AVM `AVM-001`
  row；此 location 是依 repo amendment 的 `amends` 關係推得的查找目標，
  不是已驗證來源位置
- **Canonical version:** unknown
- **Canonical content SHA-256:** unavailable
- **Observed transcription:** 同一查證報告附錄 A 的 `AVM-001` row（文件第
  246 行，task head 內容）；該 row 是摘要，沒有完整 FR 條文或 source bytes
- **Why blocked:** 全 repo 沒有獨立 `ODP-FR-AVM-001` 文件，也沒有可驗證的
  ODP-SA-06 原始 artifact 來確認該 row 的版本、位置與完整內容。

## Owner、重查方式與期限

- **Evidence owner:** `Product Lead`（repo amendment 的 declared owner；須由
  Product／source custodian 提供或確認原始規格 artifact）。`Codex2` 只負責
  本次 evidence capture，不是 canonical source owner。
- **Next check date:** `2026-09-10`
- **Recheck procedure:**
  1. 向 Product Lead／source custodian 索取原始 `ODP-SA-06` artifact，或
     一個可讀取的 immutable repository ref／export；同時確認
     `ODP-FR-AVM-001` 是該文件中的 row，或另有獨立 canonical AVM 文件。
  2. 對取得的原始 bytes 記錄 document id、版本、精確 path／URL／ref、取得
     日期與 `sha256sum` 結果；不得只記聊天訊息、搜尋結果或摘要連結。
  3. 將兩筆 record 從 `BLOCKED_BY_EVIDENCE` 更新為可追溯狀態前，逐字確認
     `AVM-001` 條文與查證報告摘要的差異，並把新 hash 綁到本 evidence 與
     `set_valued_requirements.json` 的 provenance entry。
- **Reopen trigger:** 任一 PR、amendment 或 release evidence 宣稱
  `ODP-SA-06`／`ODP-FR-AVM-001` 已有 canonical 追溯，但沒有同時提供上述
  version、location 與 content SHA-256。

在來源 artifact 交付前，後續實作只能明確標示「依目前 repo transcription
處理」，不能宣稱已完成對 canonical 規格的 provenance。

## Reproduction receipt

以下命令在 `75d25f653aa12c21a3f9627f29af2ed4def73153` task baseline 上重現本
record 的核心查證；預期原始 canonical path 的 `git cat-file` 為 non-zero，
而 transcription hash 應與「被查證但不升格為 canonical 的文件」一節相同：

```bash
git cat-file -e 75d25f653aa12c21a3f9627f29af2ed4def73153:ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md
git cat-file -e 75d25f653aa12c21a3f9627f29af2ed4def73153:docs/design/ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md
git rev-list --objects --all | rg 'ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION|ODP-FR-AVM-001'
sha256sum docs/evidence/ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md
sha256sum docs/design/ODP-SA-06-AMD-001.md
```

這些命令只證明目前 repo／ref 的可見性與文件完整性；它們不能證明「不存在
任何外部 canonical source」，所以兩筆狀態保持 `BLOCKED_BY_EVIDENCE`，並以
owner 與 next-check date 保留重查責任。
