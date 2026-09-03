# ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001 — merge queue 批次「不做」決定的權威性查證

- **任務識別碼**：`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`
- **文件路徑**：`docs/evidence/ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md`
- **日期**：2026-09-03
- **任務負責人**：Claude2
- **審查人**：Antigravity4
- **基準代碼**：`origin/dev` @ `4f9ca63e`
- **判定狀態**：`BLOCKED_BY_EVIDENCE`（查無權威裁決，阻擋至 Human/Ops；**未代簽任何 disposition**）
- **關聯依據**：
  - `docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md` §第 19 項（merge queue 批次）
  - `docs/plans/ODP_REMEDIATION_PLAN_2026-09-03.md` §「目前不進入實作的方向」
  - `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md` §2、§3（五階段 disposition 與法定欄位）
  - `docs/runbooks/dev-merge-queue.md`、`.github/branch-protection/policy.json`
  - 前置任務：`ODP-REQ-DISPOSITION-GOVERNANCE-001`（disposition gate）

---

## 1. 結論

第 19 項要補的是「**已裁決不做**」這句話的法定欄位：decision link、decider、date、scope、expiry／review date、reopen trigger。

查證結果是：**那個裁決不存在**。可稽核欄位補不上去，不是因為沒人去填，是因為沒有可指向的裁決來源。全樹只有兩類痕跡：一個可調的組態預設值，以及一份把該預設值轉述成「已裁決」的計畫文件。兩者都由 AI 代理人在實作 commit 內產生，沒有任何有權限的人類簽署。

依 `ODP_REQUIREMENT_DISPOSITIONS.md` §3.2「嚴禁 AI 自簽豁免」與 §1「缺席冒充完成」，本任務**不鑄造 `DECIDED` disposition**，改以 `BLOCKED_BY_EVIDENCE` 提報 Human/Ops，並附上一份未簽署的 disposition 封包（§5），待有權限者裁決後才可登記。

一併查出並修掉的是使這種轉述得以過閘的結構問題：disposition gate 只審查自願宣告 `DECIDED` 的成員（§6）。

---

## 2. 查證範圍與命令

以下為完整搜尋足跡，供覆核者重跑：

| # | 查證命令 | 結果 |
|---|---|---|
| 1 | `grep -rn -i "merge.?queue\|合併佇列" docs/ delivery_toolchain/ .github/` | 僅組態、runbook、CI workflow 與計畫文件；**無決策紀錄** |
| 2 | `git log --oneline -S "merge queue 批次" --all` | 單一 commit `6a456aaa`（見 §3.3） |
| 3 | `git log -- docs/runbooks/dev-merge-queue.md .github/branch-protection/policy.json` | 3 位作者，皆為 AI（見 §3.1） |
| 4 | `grep -rln "Architecture Board" docs/ delivery_toolchain/` | 僅出現在引用它的 3 個檔案本身；**無獨立會議或裁決紀錄** |
| 5 | `git log --format='%an' \| sort \| uniq -c` 交叉比對佇列相關 commit | 人類作者（`ajoe734`／`bjoe734`）最後一次觸及合併閘為 2026-07-15，**早於佇列上線 2026-08-19**，且內容為 merge gate 而非批次 |
| 6 | `docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md` 第 19 項與 `set_valued_requirements.json` 對照 | merge queue 批次**不是** `ODP-FR-*` 集合型需求成員，manifest 內無對應條目 |

---

## 3. 三個痕跡，以及為什麼都不是裁決

### 3.1 組態預設值 —— 是可調參數，不是裁決

`.github/branch-protection/policy.json`：

```json
"min_entries_to_merge": 1,
"min_entries_to_merge_wait_minutes": 5
```

`docs/runbooks/dev-merge-queue.md:41-42` 的理由欄：

> `min_entries_to_merge` = `1`：Do not hold a ready PR waiting for company; latency matters more than batching here.
>
> `min_entries_to_merge_wait_minutes` = `5`：Inert while `min_entries_to_merge` is 1; **kept explicit so raising the minimum later is a one-value change.**

第二列自己說明了第一列的性質：這是一個**預期會被調高**的預設值，並刻意保留了調高所需的參數。一個為「之後改」而設計的組態，不能同時是「不做」的終局裁決。

出處 commit `944ad12f`（2026-08-19，`ODP-ORCH-MERGE-QUEUE-ACTIVATION-001`），trailer 為 `LLM-Agent: Antigravity` / `Reviewer: Claude2`——作者與審查者皆為 AI 代理人。

### 3.2 未被量測的前提

runbook 對 `grouping_strategy` 的理由寫道：本 fleet 吞吐約 1.5 merges/hour，佇列深度**很少超過 2**。批次在佇列深度長期為 1–2 時本來就沒有作用面。這是一個合理的工程判斷，但它是**條件性的**：條件變了（fleet 併發提高、CI 時間拉長），結論就變。條件性判斷正是需要 `expiry` 與 `reopen_trigger` 的那一類，而非可以永久結案的那一類。

### 3.3 計畫文件的轉述 —— 循環自證

`docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md` 第 19 項首次寫入時的原文（commit `6a456aaa`，2026-09-03，作者 `Claude`）：

```
| 19 | merge queue 批次 | 已裁決不做 |
```

該行**未引用任何來源**。它把 §3.1 的組態理由升格成「已裁決」，而後續版本又以「`DECIDED`：方向是不做」為前提，要求補上 decider 與日期。若據此登記 disposition，`formal_decision_ref` 只能指回這份轉述文件本身——`set_valued_requirements.json` 的 `_source_provenance.policy` 已明文拒絕這種形狀：

> Repo transcriptions and amendments are not canonical sources.

### 3.4 「Architecture Board」在本庫沒有獨立存在

現行 manifest 中 `ODP-FR-NET-002` 兩筆 `DECIDED` 的 `decider` 均為 `Human/Ops (Architecture Board)`。全樹搜尋顯示，這個機構名稱只出現在**引用它的那三個檔案**（政策文件、manifest、測試）之中，沒有任何會議紀錄、裁決編號或獨立紀錄可供交叉查核。

本文件**不主張**那兩筆裁決不實——它們的 `note` 帶有早於治理任務的 2026-09-02 工程裁決紀錄，屬既有前例。此處要指出的是：**光靠 `decider` 字串無法區分「有人裁決過」與「有人寫過有人裁決過」**。這正是本任務拒絕比照辦理的理由：再增一筆同樣無法交叉查核的簽署，會把稽核線索稀釋成慣例。

---

## 4. 判定

| 項目 | 判定 |
|---|---|
| 是否存在權威的不實作裁決？ | **否** |
| 是否已補齊 decider／date／scope／expiry／reopen trigger？ | **否，且不得由 AI 補** |
| 本任務是否登記 `DECIDED` disposition？ | **否**（§5 為未簽署封包） |
| `set_valued_requirements.json` 是否新增條目？ | **否**（§7） |
| 任務狀態 | `BLOCKED_BY_EVIDENCE` → 阻擋至 **Human/Ops** |

---

## 5. 未簽署的 disposition 封包（handback 給 Human/Ops）

以下為裁決者若決定「維持不批次」時，登記所需的完整欄位。**空白欄位必須由有權限的人類填寫**；AI 代理人不得代填 `decider`、`decision_date`、`risk_owner`。

| 法定欄位 | 內容 |
|---|---|
| `formal_decision_ref` | 待填：裁決紀錄的固定位置（governance 文件章節、PR 或 RFC 編號） |
| `decider` | **待填**：具名之有權限人類角色（不接受泛稱機構名稱作為唯一識別） |
| `decision_date` | **待填**：實際裁決日期 |
| `scope` | 建議：`dev` 分支 merge queue 的批次合併（`min_entries_to_merge` > 1），不含 `main` 與其他 repo |
| `risk_owner` | **待填**：承擔「佇列變深時延遲與 CI 成本上升」風險的人類 owner |
| `expiry` | 建議 ≤ 12 個月；此判斷綁定當前 fleet 吞吐（約 1.5 merges/hour、佇列深度 ≤ 2），該前提會隨時間變動 |
| `reopen_trigger` | 建議：任一條成立即重啟——(a) `dev` 佇列平均深度連續 7 日 ≥ 3；(b) 每 PR 平均 CI 佔用時間較 2026-08-19 基準上升 50%；(c) `min_entries_to_merge` 被調離 `1` |

**若裁決者的實際意思是「現在不需要、之後再看」**，正確狀態不是 `DECIDED`，而是 `OPEN` 或 `IMPLEMENTATION_READY`（含 `assigned_to` 與 `target_phase`）——見 `ODP_REQUIREMENT_DISPOSITIONS.md` §2。以 `DECIDED` 承載「暫時不做」會讓一個可逆的調參決定取得永久豁免的外觀。

**替代路徑**：若裁決者認為 merge queue 批次從來不是需求層的承諾，而只是一個組態預設值，則正確處置是把第 19 項從「已知的缺席」清單移除，並在 runbook 註明其為可調參數——而非為它補一份豁免。此路徑同樣需要人類裁決，本任務不得代為選擇。

---

## 6. 一併修復：讓「note 當修訂」無法過閘

查證過程中發現 disposition gate 有一個與本項同形的缺口。`ODP_REQUIREMENT_DISPOSITIONS.md` §3.1 早已寫明「在 `note` 中自行填寫 `DECIDED ...` 而未提供合規結構化 `disposition` 物件者，CI 檢查視為違規並直接中斷」——但那句話從來只是政策文字：`check_requirement_members.py` 只對自願宣告 `state: DECIDED` 的成員檢查法定欄位，沒有任何程式碼讀過 note 的裁決宣稱。規範與執行之間的這道落差，正是本輪 remediation 的成因四（閘存在、內容正確、但結構上不會被執行）。因此有兩條路可以繞過它：

1. **把裁決寫進 `note`，狀態填別的。** 成員 note 寫「已裁決不做」而 disposition 是 `OPEN`，稽核輸出只會顯示一個「有理由、有 owner」的正常缺口。
2. **把豁免掛在非 `DECIDED` 狀態上。** 現行 manifest 的 `ODP-FR-NET-002 / DILUTION` 就是這個形狀：`status: satisfied`、`disposition.state: VERIFIED`，note 裁定完整配對形式不做，並帶有 `decider`、`expiry: 2027-09-01`、`reopen_trigger`——但**這些欄位過去沒有任何一項被驗證過**。它的 expiry 會在 2027-09-01 靜默失效，CI 仍然全綠。

本任務的 gate 變更（commit `d736aed0` 及其後續修補）：

- **法定欄位在哪裡出現就在哪裡受審**：帶其中任一欄位就必須帶齊全部，並通過 reference 可解析、decider 非 AI、expiry 未過期的檢查，不論 `state` 宣告為何。
- **note 不是修訂**：成員 `note` 或 disposition `rationale` 宣稱不實作裁決（`DECIDED 2026-09-02: not pursued`、`已裁決不做`、`decided not to implement`…）而狀態非 `DECIDED` 者，一律拒絕。偵測樣式刻意收窄——`It is not a release mode` 這類**描述缺席**的句子必須繼續通過，否則每個誠實的缺口都會被逼去申請它沒有的豁免（測試 `test_a_note_that_only_describes_an_absence_still_passes` 以現行 manifest 的 `BACKTEST`／`ADJUST` 原文守住這條界線）。
- **`decision_date` 納入法定欄位**：沒有日期的裁決無法計齡、無法追溯到做成它的那場會議。三個既有測試 fixture 早已按慣例帶了這個欄位，但沒有任何規則要求它。
- **刪掉 disposition 區塊不能藏起宣稱**：`satisfied` 成員本來就可以不帶 disposition，因此「把裁決留在 note、把區塊刪掉」是繞過上述規則最便宜的一條路。現在成員的 note 一律受審，不論它有沒有 disposition 區塊。

現行 manifest 在變更後**未經修改即通過**：這道閘拒絕的是本來就不該合法的形狀，不是 `NET-002` 那兩筆帶有工程裁決紀錄的處置。

### 刻意未納入的一項

`BLOCKED_BY_EVIDENCE` 與 `OPEN` 的 `next_review_date` 逾期**仍不會使 CI 失敗**（僅檢查格式）。現行三筆 `BLOCKED_BY_EVIDENCE` 的 `next_review_date` 均為 `2026-10-01`；把逾期改為失敗會在該日讓所有 product PR 同時轉紅，屬於獨立的排程決定，不在本任務授權範圍內。本段記錄此缺口，供後續任務評估。

---

## 7. 為什麼沒有動 `set_valued_requirements.json`

任務派工單將該檔列為可能相關檔案。查證後未修改，理由：

merge queue 批次**不是** `ODP-SA-06` 的集合型需求成員。manifest 的收錄範圍是「列舉 N 個成員的 `ODP-FR-*` 需求」，第 11–18 項各自對應一條 `ODP-FR` 需求，只有第 19 項沒有需求識別碼——它是交付基礎設施的組態，不是產品需求。為了讓 disposition 有地方登記而在 manifest 內鑄造一條需求，等於用治理檔案憑空產生一條規格；那與本輪 remediation 要修的「缺席冒充完成」是同一個形狀的反面。

本項的正式登記位置應由 §5 的裁決結果決定：若裁決成立，記入 `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md` 的交付治理章節；若判定非需求層承諾，則從第 19 項清單移除。

---

## 8. 可重現的驗證收據

```bash
# 全部在 python 3.12 下執行（cp314 無 pgserver wheel）
uv run --frozen --python 3.12 pytest -m "not requires_live_env" delivery_toolchain -q
# 全綠；其中 test_check_requirement_members.py 55 passed

uv run --frozen --python 3.12 ruff check delivery_toolchain scripts
# All checks passed!

uv run --frozen --python 3.12 python delivery_toolchain/governance/check_requirement_members.py --show-gaps
# Requirement member checks passed: 6 set-valued requirements, 32 members
# (24 satisfied, 8 absent and noted; dispositions: BLOCKED_BY_EVIDENCE=3,
#  DECIDED=1, IMPLEMENTATION_READY=1, OPEN=3, VERIFIED=24)
```

---

## 9. 後續動作

1. **Human/Ops**：對 §5 封包做出裁決，或選擇 §5 的替代路徑（判定非需求層承諾）。
2. 裁決做成後，由後續任務登記至 `ODP_REQUIREMENT_DISPOSITIONS.md`，並更新 `docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md` 第 19 項。
3. 在裁決做成之前，第 19 項的正確狀態是 `BLOCKED_BY_EVIDENCE`，**不是** `DECIDED`；計畫文件已依本查證更正（見 §3.3 對應的兩處修訂）。
