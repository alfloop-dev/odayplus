/**
 * Role vocabulary — the personas the OpsBoard serves (visual system §2).
 * Roles drive role-aware navigation: items the role cannot access are NOT
 * rendered (component contracts §3.3, §6.2 — not disabled, omitted).
 */

export type Role =
  | "expansion_reviewer" // 展店審查
  | "ops_manager" // 營運主管
  | "pricing_analyst" // 定價團隊
  | "finance_legal" // 財務法務
  | "ai_data" // AI / 資料團隊
  | "franchisee" // 加盟主
  | "auditor" // 稽核
  | "admin"; // 平台管理

export const ROLES: readonly Role[] = [
  "expansion_reviewer",
  "ops_manager",
  "pricing_analyst",
  "finance_legal",
  "ai_data",
  "franchisee",
  "auditor",
  "admin",
];

/** Human-readable zh-TW label for each role (UI default language §8.3). */
export const roleLabel: Record<Role, string> = {
  expansion_reviewer: "展店審查",
  ops_manager: "營運主管",
  pricing_analyst: "定價團隊",
  finance_legal: "財務法務",
  ai_data: "AI / 資料團隊",
  franchisee: "加盟主",
  auditor: "稽核",
  admin: "平台管理",
};
