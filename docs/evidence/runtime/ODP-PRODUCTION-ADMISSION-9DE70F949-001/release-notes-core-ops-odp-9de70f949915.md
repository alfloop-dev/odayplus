# ODay Plus 核心營運版上線說明（`core-ops` · `odp-9de70f949915`）

| 欄位 | 值 |
|---|---|
| 版本／release ID | `odp-9de70f949915` |
| 核准日期 | `2026-11-28`（`HUMANOPS-PRODUCTION-GO-20261128A` / HD-23） |
| release SHA | `9de70f949915706d1bad1bc25c24b681eeb902e9` |
| manifest digest | `sha256:964d00090d8516b30d6329dcc5e0550cbab0342f9d32567b651003aa12ef0ccd` |
| 發布範圍 | `core-ops`：核心營運功能；不含預測與求解器模型功能 |
| 正式網址 | `https://console.oday-plus.com.tw` |

---

## 本次上線內容

ODay Plus 首波正式上線開放**核心營運功能（`core-ops`）**。需要機器學習預測或最佳化求解器的功能尚未開放，系統會如實標示「尚未就緒」，**不會**提供替代或估計預測值。

### 已開放功能
- **帳號與認證**：採邀請制本機帳密登入（未啟用 Google OAuth，D15）；首次登入強制更換密碼，支援登出、閒置逾時與連續登入失敗節流保護。
- **角色權限（RBAC）**：依角色顯示功能並阻擋越權操作；系統管理員（`platform_admin`）僅管理帳號、角色與功能開關，無法檢視營運業務資料。
- **多租戶資料隔離**：跨租戶存取一律拒絕並寫入安全稽核紀錄。
- **不可竄改稽核保存**：重要操作、權限拒絕與證據包匯出均留存稽核紀錄，並寫入鎖定 7 年保存期之 WORM 儲存（`oday-prod-audit-worm-odayplus-prod-20260826`）。
- **核心營運工作台（非模型相依範圍）**：Today 總覽、門市營運議題處理（Store Ops）、治理核可流程（Governance）、物件代填收件權限控管。

### 尚未開放功能（顯示不可用）
- **AVM**（估價）、**ForecastOps**（營運預測與四燈總覽）、**HeatZone**（熱區）、**SiteScore**（選址評分）、**Learning Hub** 模型登錄與 **NetPlan** 求解器相關功能；以及首波排除之 PriceOps、AdLift、Growth、Franchisee 模組。
- 第三方外部資料來源維持**全部關閉**（`ODP_EXTERNAL_PROVIDER_MODE=disabled`）。

## 聯絡與值班窗口
- **On-call／Watch／事故指揮負責人**：蔡尚志（Human/Ops，Cloud Monitoring 告警路由與上線後 30 分鐘 watch window 及 24 小時健康讀回）。
