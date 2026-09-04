---
evidence_id: ODP-AVM-DEPRECIATION-CONTRACT-001
title: "ODP-FR-AVM-001 DEPRECIATION 成員處置證據"
date: 2026-09-04
status: IMPLEMENTATION_READY
owner: Claude2
reviewer: Antigravity6
repository: alfloop-dev/odayplus
observed_ref: a62298f3f586d1e531dfcb0af7ba0bb14356b8ca
---

# `ODP-FR-AVM-001` / `DEPRECIATION`：處置證據

本文是 [`set_valued_requirements.json`](../../delivery_toolchain/governance/set_valued_requirements.json) 中
`ODP-FR-AVM-001` 的 `DEPRECIATION` 成員為何登記為 `IMPLEMENTATION_READY` 的證據，
以及該處置底下每一條引用在 `a62298f3f586d1e531dfcb0af7ba0bb14356b8ca` 這個基準上**重新驗證過**的紀錄。

契約本身在 [AVM 資產折舊契約](../design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md)；
政策登錄在 [Requirement Dispositions §4.9](../governance/ODP_REQUIREMENT_DISPOSITIONS.md)。
本文不重述契約條文，只回答三個稽核問題：**狀態選得對不對、引用還在不在、什麼時候才能變 `VERIFIED`**。

---

## 1. 為什麼是 `IMPLEMENTATION_READY`，不是另外兩種

三個候選狀態各自對應一種不同的現實，選錯就會把缺口藏起來：

| 狀態 | 若選它，等於宣稱 | 為什麼不成立 |
|---|---|---|
| `VERIFIED` | 折舊已進入估值路徑 | `modules/avm` 內沒有任何折舊計算；且 `status: absent` 依政策 §3.1 本來就不得為 `VERIFIED` |
| `DECIDED` | 有權限的人類已裁決不納入折舊 | 沒有這場裁決。要編出 `decider` 才能通過閘，正是政策 §3.2 禁止的 AI 自簽 |
| `BLOCKED_BY_EVIDENCE` | 缺少證據以致無法判定或無法動工 | 不成立：判定已做出（AVM-specific，四項證據見 §2），驗收標準已可執行（§3）。實作**現在就能開始**，欠的是工，不是證據 |

因此是 `IMPLEMENTATION_READY`：**規格與驗收標準已鎖定、已指名 owner 與交付批次，缺的是實作。**

成員維持 `status: absent`。契約寫完不等於缺口關閉——這兩件事在同一筆紀錄裡分開表示，
是這個 manifest 存在的理由。

## 2. 判定的四項證據（於 `a62298f3f586d1e531dfcb0af7ba0bb14356b8ca` 重新驗證）

判定是「AVM-specific，且連共用純函式都不抽」。四項證據在 base advance 之後逐條複查，
行號為本基準上的實際位置：

| # | 主張 | 位置（本基準） | 複查結果 |
|---|---|---|---|
| 1 | 被量測的對象不同：模擬器算的是 format catalog 的**全新**機型組合，沒有「已使用多久」這個維度 | `modules/site_economics/domain/simulator.py:331-357` | 成立；`item_capex` / `item_residual` / `item_life` 逐項來自 `format_spec.machine_mix.items` |
| 2 | 輸出去向不同：折舊只走稅盾，現金流繞過它 | `simulator.py:470`（`ebit`）、`:487`（`taxable_income`）、`:519-520`（`unlevered_op_cf` / `levered_op_cf`） | 成立；折舊不出現在任一條現金流算式 |
| 3 | 時鐘原點相反：模擬器由開店月往後推，估值由設備投入使用日往回推 | `simulator.py` 模擬迴圈以 `m` 為月序；`modules/avm/domain/valuation.py:365-374` 無任何時間維度 | 成立 |
| 4 | 殘值不是同一種東西：一邊是期末退場現金流入，一邊是折舊下限 | `simulator.py:528`（`terminal_salvage`） | 成立 |
| — | `modules/avm` 不 import `site_economics` | `modules/avm/tests/test_avm_depreciation_contract.py::TestTheVerdictIsAVMSpecific::test_avm_does_not_import_site_economics` | 常綠測試，本基準上 PASSED |

要對齊的參數來源（`useful_life_months=84`、`residual_value_ratio` 0.05–0.12）在
`modules/site_economics/domain/models.py:61-62` 與 `formats.py:39-40, 147-148`；
名字相近但語意不同的 `ResidualValueSpec.equipment_salvage_ratio`（0.12 / 0.15）在
`formats.py:281, 393`。兩者複查後仍在原位。

**由資料對齊，不由 import 對齊**——這是判定的實質內容，不只是措辭。

## 3. 驗收標準是可執行的，不是散文

`acceptance_criteria` 指向 `modules/avm/tests/test_avm_depreciation_contract.py` 的八條
`xfail(strict=True)` 規格：

| 測試 | 釘住的契約條款 |
|---|---|
| `test_two_inputs_differing_only_in_depreciation_produce_different_valuation` | 驗收關鍵：折舊真的進了計算 |
| `test_valuation_input_carries_the_depreciation_contract_fields` | C-2 輸入欄位 |
| `test_the_asset_lens_publishes_its_depreciation_evidence` | C-4 evidence 區塊 |
| `test_missing_depreciation_inputs_do_not_yield_a_complete_card` | C-5 fail closed |
| `test_an_appraised_basis_is_not_depreciated_twice` | C-1 基數歧義 |
| `test_a_legacy_card_keeps_its_legacy_version_and_is_not_recomputed` | L-1／L-2 舊卡處置 |
| `test_a_v0_pin_reproduces_the_pre_cutover_numbers` | R-1 逐位元回滾 |
| `test_calibration_does_not_silently_mix_depreciation_versions` | L-4 校準分版本 |

`strict=True` 使這八條在實作落地後會以 **XPASS 判紅**，強迫實作者回來拿掉標記。
換句話說：這個 `IMPLEMENTATION_READY` 有一個機械式的到期機制，不是一句「之後會做」。

本基準上的觀測結果：

```
$ uv run --frozen pytest modules/avm/tests/test_avm_depreciation_contract.py -q
.xxxxxxxx
1 passed, 8 xfailed
```

一條常綠（判定守衛），八條 xfail（契約未實作）。**這正是本處置所宣稱的狀態的可觀測形式。**

## 4. 什麼時候才能轉成 `VERIFIED`

同時滿足三項，缺一不可：

1. 八條 xfail 標記被拿掉且全部 PASSED——不是改標記，是實作讓它們通過；
2. `DEPRECIATION` 的 `status` 由 `absent` 改為 `satisfied`，`evidence` 指向真實存在的符號
   （`check_requirement_members.py` 會解析它，改名或刪除即失敗）；
3. 契約 R-4 的三個回滾門檻已由財務 owner 填入——**門檻未填不得 cutover**。

## 5. 仍屬於人類、本次不代簽的兩件事

- **R-4 回滾門檻**（數值／結構／校準三類）由財務 owner 訂定。本文不填推測值。
- **canonical 需求 bytes**：`ODP-FR-AVM-001` 的原始條文在
  [`ODP_SPEC_SOURCE_PROVENANCE_2026-09-03.md`](ODP_SPEC_SOURCE_PROVENANCE_2026-09-03.md)
  仍為 `BLOCKED_BY_EVIDENCE`，manifest 的 `_source_provenance` 已記錄。
  本處置因此只宣稱「依目前轉錄內容」成立，不宣稱對 canonical 規格完成追溯。

這兩件事都沒有被本次登記吸收成既成事實，也沒有被拿來當作降低狀態要求的理由。

## 6. 回歸測試

`tests/governance/test_avm001_disposition.py` 釘住本文的每一項主張：成員清單與計數、
`DEPRECIATION` 的處置狀態與必填欄位、處置**不得**攜帶任何法定裁決欄位（即不得是變相豁免）、
引用文件存在且互相指得到、驗收標準所指的八條規格確實存在且仍為 `strict=True`，
以及整份 manifest 通過 `check_requirement_members.check()`。

其中三條是負向測試：把 `DEPRECIATION` 改成 `VERIFIED`、改成由 AI 簽署的 `DECIDED`、
或拿掉整個 `disposition` 區塊，checker 都必須拒絕。它們證明這道閘對本成員**真的會紅**，
而不是恰好沒被檢查到。
