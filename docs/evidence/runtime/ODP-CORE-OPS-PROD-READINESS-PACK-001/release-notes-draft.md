# ODay Plus 核心營運版上線說明（core-ops）— 草稿

> **DRAFT · 未核准 · 不可對外發布。**
>
> - 版本、日期與 build 都留白，要等 production GO 決策後才能填入。
> - 內容依 `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md` 與現行程式整理。
> - 每一項「可用功能」都必須通過 `uat-plan.md` 的對應案例，才能保留在正式版說明中。

| 欄位 | 值 |
|---|---|
| 版本／release ID | （留白） |
| 上線日期 | （留白） |
| release SHA | （留白：production admission 的 candidate SHA） |
| manifest digest | （留白） |
| 發布範圍 | `core-ops`：核心營運；不含模型功能 |

---

## 這次上線的內容

ODay Plus 先開放**核心營運功能**。需要預測模型的功能還沒開放，系統會清楚標示「尚未就緒」，**不會**提供替代或估計的結果。

### 可以使用

依 `docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:8`、`:24`、`:207`：

- **登入與帳號**
  - 以受邀帳號登入。首次登入必須更換密碼。
  - 支援登出、閒置自動登出，以及連續登入失敗保護。
  - 登入方式依部署設定：預設是帳號密碼，Google 登入是選用（`.github/workflows/deploy-dev.yml:1245-1252`）。**production 實際採用哪一種待確認。**
- **權限**
  - 依角色顯示可用的功能。沒有權限的操作會明確被拒絕，不會顯示成空白。
  - 系統管理員負責管理使用者、角色與功能開關。管理員帳號**不能**查看營運資料，營運工作要用營運角色帳號。
- **資料隔離**：不同租戶的資料彼此隔離，跨租戶存取會被拒絕並記錄。
- **稽核**：重要操作與被拒絕的操作都會留下稽核紀錄，可匯出證據包。
- **營運主控台（不依賴模型的部分，待 UAT 確認）**：Today 總覽、門市營運議題處理、治理核可流程、物件代填流程的權限控管。

### 尚未開放

這些功能會顯示「不可用」：

- **AVM**（估價）
- **ForecastOps**（營運預測，包含四燈總覽）
- **HeatZone**（熱區）
- **SiteScore**（選址評分）
- **Learning Hub** 模型登錄
- **求解器相關功能**（例如 NetPlan 情境）

依據：`apps/web/src/lib/auth/operatorReleaseStatus.ts:8-13`；design `:160-170`。

模型就緒、而且通過模型風險審查之後，會另行發布完整版本（design `:8`、`:185-188`）。

### 外部資料

第三方資料來源目前**全部關閉**（`.github/workflows/deploy-dev.yml:1268-1270`），系統不會向外部抓取資料。

---

## 已知限制

1. 模型相依的頁面與操作會顯示不可用，請不要把畫面上的空白或拒絕當成資料錯誤。
2. 只有系統管理員的管理頁面會顯示「發布範圍」說明（`apps/web/src/app/operator/page.tsx:31-35`）。一般營運頁面沒有這段說明，請以本文件為準。
3. 帳號只能由系統管理員邀請建立，不開放自行註冊（`shared/identity/invitation_service.py:15-18`）。

## 問題回報

- 回報窗口：（留白，由 Support Lead 填寫）
- 緊急聯絡：（留白，見 `monitoring-oncall-plan.md` §4）

---

## 內部欄位（發布前移除）

| 項目 | 簽署人 | 角色 | 日期 | 決定 |
|---|---|---|---|---|
| release notes 內容核准 | | Product Owner | | □ 核准 □ 退回 |
| 對外公告時間 | | Communications Lead | | |
