/**
 * Canonical OpsBoard route map.
 *
 * Covers the 14 top-level work areas required by ODP-R0-004 acceptance:
 * home / tasks / search / expansion / operations / interventions / pricing /
 * adlift / avm / netplan / learning / audit / admin / franchisee.
 *
 * Each item declares the roles that may see it (role-aware navigation). Items a
 * role cannot access are omitted by the Sidebar — never rendered disabled
 * (component contracts §3.3, §6.2). `readOnlyRoles` view a read-only screen.
 */
import type { NavItem, RouteKey } from "@oday-plus/domain-types";

export const NAV_ITEMS: NavItem[] = [
  {
    key: "home",
    label: "總覽",
    href: "/",
    icon: "home",
    description: "OpsBoard 第一屏：跨模組狀態、待辦與最近決策的彙整。",
  },
  {
    key: "tasks",
    label: "任務中心",
    href: "/tasks",
    icon: "tasks",
    description: "個人與團隊待辦：待核准、待補件、待觀察的決策任務。",
  },
  {
    key: "search",
    label: "全域搜尋",
    href: "/search",
    icon: "search",
    description: "跨實體搜尋門市、候選點、決策、模型版本與稽核紀錄。",
  },
  {
    key: "expansion",
    label: "展店選址",
    href: "/expansion",
    icon: "map",
    roles: ["expansion_reviewer", "ops_manager", "admin"],
    description: "HeatZone 評分、候選點審查與 SiteScore 報告。",
  },
  {
    key: "operations",
    label: "營運監控",
    href: "/operations",
    icon: "activity",
    roles: ["ops_manager", "admin", "franchisee"],
    readOnlyRoles: ["franchisee"],
    description: "門市四燈狀態、預測帶與根因證據。",
  },
  {
    key: "interventions",
    label: "干預決策",
    href: "/interventions",
    icon: "intervention",
    roles: ["ops_manager", "admin"],
    description: "干預建議、資格/衝突檢查、核准與觀察窗追蹤。",
  },
  {
    key: "pricing",
    label: "定價",
    href: "/pricing",
    icon: "price",
    roles: ["pricing_analyst", "ops_manager", "admin"],
    description: "調價方案比較、硬限制檢查與人工核准。",
  },
  {
    key: "adlift",
    label: "廣告增益",
    href: "/adlift",
    icon: "campaign",
    roles: ["pricing_analyst", "ops_manager", "admin"],
    description: "廣告活動因果增益（treatment/control）與 iROMI 評估。",
  },
  {
    key: "avm",
    label: "門市估值",
    href: "/avm",
    icon: "valuation",
    roles: ["finance_legal", "ops_manager", "admin"],
    readOnlyRoles: ["ops_manager"],
    description: "AVM 公允價值區間、底價/開價與資料室完整度。",
  },
  {
    key: "netplan",
    label: "網路規劃",
    href: "/netplan",
    icon: "network",
    roles: ["ops_manager", "finance_legal", "admin"],
    description: "NetPlan 情境：OPEN/KEEP/IMPROVE/MOVE/EXIT 與 solver 結果。",
  },
  {
    key: "learning",
    label: "模型與學習",
    href: "/learning",
    icon: "model",
    roles: ["ai_data", "auditor", "admin"],
    readOnlyRoles: ["auditor"],
    description: "模型版本、發布階段、drift 監控與 release/rollback。",
  },
  {
    key: "audit",
    label: "稽核軌跡",
    href: "/audit",
    icon: "audit",
    roles: ["auditor", "finance_legal", "admin"],
    readOnlyRoles: ["finance_legal"],
    description: "決策稽核時間軸與可匯出證據包。",
  },
  {
    key: "admin",
    label: "平台管理",
    href: "/admin",
    icon: "lock",
    roles: ["admin"],
    description: "工作區、權限、環境與平台設定。",
  },
  {
    key: "franchisee",
    label: "加盟主入口",
    href: "/franchisee",
    icon: "user",
    roles: ["franchisee", "ops_manager", "admin"],
    readOnlyRoles: ["ops_manager"],
    description: "加盟主檢視：門市健康度、通知與簡易回報。",
  },
];

/** Fast lookup by route key. */
export const NAV_BY_KEY: Record<RouteKey, NavItem> = NAV_ITEMS.reduce(
  (acc, item) => {
    acc[item.key] = item;
    return acc;
  },
  {} as Record<RouteKey, NavItem>,
);

/** All route keys, in canonical order. */
export const ROUTE_KEYS: RouteKey[] = NAV_ITEMS.map((i) => i.key);
