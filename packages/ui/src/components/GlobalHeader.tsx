"use client";
/**
 * GlobalHeader — logo, global search, task/notification counters, environment
 * badge, and (R0 placeholders) role / theme / density switchers.
 * Contracts §3.2. Env badge carries text, never colour-only.
 */
import { ROLES, roleLabel } from "@oday-plus/domain-types";
import type { ThemeName, DensityName } from "@oday-plus/design-tokens";
import { useShell } from "./ShellContext.tsx";

export type GlobalHeaderProps = {
  environment: "dev" | "staging" | "production";
  taskCount?: number;
  notificationCount?: number;
  productName?: string;
};

const THEMES: ThemeName[] = ["light", "dark", "high-contrast", "presentation"];
const DENSITIES: DensityName[] = ["comfortable", "compact", "presentation"];

export function GlobalHeader({
  environment,
  taskCount = 0,
  notificationCount = 0,
  productName = "ODay Plus OpsBoard",
}: GlobalHeaderProps) {
  const { role, setRole, theme, setTheme, density, setDensity } = useShell();

  return (
    <header className="odp-header" role="banner" data-testid="global-header">
      <div className="odp-header__brand">
        <span className="odp-navlink__icon" aria-hidden="true">
          OP
        </span>
        <span>{productName}</span>
      </div>

      <input
        className="odp-header__search"
        type="search"
        placeholder="搜尋門市、決策、模型版本…（Cmd/Ctrl+K）"
        aria-label="全域搜尋"
        data-testid="global-search"
      />

      <div className="odp-header__spacer" />

      {/* Role switcher — drives role-aware navigation in R0 (no auth backend) */}
      <label className="odp-muted" htmlFor="odp-role">
        角色
      </label>
      <select
        id="odp-role"
        className="odp-select"
        aria-label="切換角色"
        data-testid="role-switcher"
        value={role}
        onChange={(e) => setRole(e.target.value as typeof role)}
      >
        {ROLES.map((r) => (
          <option key={r} value={r}>
            {roleLabel[r]}
          </option>
        ))}
      </select>

      <select
        className="odp-select"
        aria-label="切換主題"
        data-testid="theme-switcher"
        value={theme}
        onChange={(e) => setTheme(e.target.value as ThemeName)}
      >
        {THEMES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>

      <select
        className="odp-select"
        aria-label="切換密度"
        data-testid="density-switcher"
        value={density}
        onChange={(e) => setDensity(e.target.value as DensityName)}
      >
        {DENSITIES.map((d) => (
          <option key={d} value={d}>
            {d}
          </option>
        ))}
      </select>

      <button
        type="button"
        className="odp-iconbtn"
        aria-label={`任務中心，${taskCount} 項待辦`}
        data-testid="task-center"
      >
        任務
        {taskCount > 0 ? (
          <span className="odp-iconbtn__count">{taskCount}</span>
        ) : null}
      </button>

      <button
        type="button"
        className="odp-iconbtn"
        aria-label={`通知，${notificationCount} 則新訊息`}
        data-testid="notifications"
      >
        通知
        {notificationCount > 0 ? (
          <span className="odp-iconbtn__count">{notificationCount}</span>
        ) : null}
      </button>

      <span
        className="odp-env-badge"
        data-env={environment}
        data-testid="env-badge"
      >
        {environment}
      </span>

      <button
        type="button"
        className="odp-iconbtn"
        aria-label="使用者選單"
        data-testid="user-menu"
      >
        使用者
      </button>
    </header>
  );
}
