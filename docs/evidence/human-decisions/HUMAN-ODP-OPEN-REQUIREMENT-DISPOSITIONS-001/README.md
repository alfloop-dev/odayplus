# HUMAN-ODP-OPEN-REQUIREMENT-DISPOSITIONS-001 — ADJUST 與 AVM Finance 核准紀錄

- **核准者**：蔡尚志（負責人），GitHub `ajoe734`（id 169176954）
- **權威紀錄**：[alfloop-dev/odayplus#1446](https://github.com/alfloop-dev/odayplus/issues/1446)，由核准者本人發布
- **收據**：`requirement-disposition-approval-receipt.json`，sha256 `cecb881659ba0f3542a99af63fcce48317cdbabe4756cc9109b6349b2c4e5be0`，與 issue 內 JSON 逐字相同（見 `readback.json`）
- **複審日**：2027-10-09

## 決定

| 義務 | 決定 |
|---|---|
| `ODP-ADJUST-OPERATIONAL-CONFIRMATION` | 納入本 task；Operations Lead 與 Product Lead 為核准者本人。門市調整進行中的介入，現行實務為 stop-plus-recreate（停止原介入、另開新介入並記錄關聯），適用全部門市與介入類型，決策日 2026-10-09 |
| `ODP-AVM-FINANCE-CUTOVER` | Finance Owner、Product Owner、ML Risk Owner 為核准者本人。R-4 回滾門檻：數值（變動 >15% 且無資料面解釋者 >5%）、結構（任 1 張 evidence 不全的卡）、校準（P10–P90 coverage 較 v0 低 >5 個百分點）。Production 估值 cutover 附條件核准 |

## 限制

- 本紀錄不改需求狀態、registry 或任何 gate。
- AVM cutover 核准不取代技術閘：折舊契約測試通過、`DEPRECIATION` 轉為 satisfied、模型完成 registry／promotion／rollback target 讀回，且 production 准入閘全數通過後才可實際執行。門檻或折舊參數變更需重新核准。
