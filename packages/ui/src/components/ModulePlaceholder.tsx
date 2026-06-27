/**
 * ModulePlaceholder — the R0 first screen for each work area. Renders a proper
 * PageHeader + an EmptyState-style card that always offers a next step
 * (contracts §4.12: never just "no data"). Module UI plugs in here later.
 */
import { PageHeader } from "./PageHeader.tsx";
import { NAV_BY_KEY } from "../nav/routes.ts";
import type { RouteKey } from "@oday-plus/domain-types";

export type ModulePlaceholderProps = {
  routeKey: RouteKey;
  /** override the auto title (defaults to the nav label). */
  title?: string;
  /** extra zh-TW lines describing what this screen will host. */
  scope?: string[];
};

export function ModulePlaceholder({
  routeKey,
  title,
  scope,
}: ModulePlaceholderProps) {
  const item = NAV_BY_KEY[routeKey];
  const heading = title ?? item.label;
  const isHome = routeKey === "home";

  return (
    <>
      <PageHeader
        title={heading}
        summary={item.description}
        status={{ label: "R0 SHELL", tone: "blue", marker: "•" }}
        breadcrumb={
          isHome ? [{ label: "總覽" }] : [{ label: "總覽", href: "/" }, { label: heading }]
        }
        lastUpdated="—（尚無資料來源）"
      />
      <div className="odp-content">
        <div className="odp-placeholder" data-testid={`module-${routeKey}`}>
          <div className="odp-card">
            <h2 className="odp-card__title">骨架就緒，等待模組 UI 接入</h2>
            <p className="odp-muted">
              這是 OpsBoard R0 導覽骨架的「{heading}」工作區占位畫面。Shell、路由與
              design token 已就緒；後續模組 UI 依
              <code> docs/design </code>
              元件契約插入此處，不需重建框架。
            </p>
          </div>

          {scope && scope.length > 0 ? (
            <div className="odp-card">
              <h2 className="odp-card__title">本工作區後續承載</h2>
              <ul className="odp-muted">
                {scope.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </div>
    </>
  );
}
