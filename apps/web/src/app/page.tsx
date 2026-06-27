import Link from "next/link";
import { PageHeader, NAV_ITEMS } from "@oday-plus/ui";

/** OpsBoard first screen — cross-module overview + entry points to every area. */
export default function HomePage() {
  const modules = NAV_ITEMS.filter((i) => i.key !== "home");
  return (
    <>
      <PageHeader
        title="OpsBoard 總覽"
        summary="ODay Plus 營運決策平台第一屏：跨模組狀態、待辦與最近決策的彙整。"
        status={{ label: "R0 SHELL", tone: "blue", marker: "•" }}
        breadcrumb={[{ label: "總覽" }]}
        lastUpdated="—（尚無資料來源）"
      />
      <div className="odp-content">
        <p className="odp-muted" style={{ marginTop: 0 }}>
          導覽骨架與 design token 已就緒。下列工作區為占位畫面，後續模組 UI 依
          <code> docs/design </code> 元件契約接入。
        </p>
        <ul
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: "var(--odp-space-4)",
            listStyle: "none",
            padding: 0,
          }}
          data-testid="home-module-grid"
        >
          {modules.map((m) => (
            <li key={m.key}>
              <Link
                href={m.href}
                className="odp-card"
                data-testid={`home-card-${m.key}`}
                style={{ display: "block", textDecoration: "none", color: "inherit" }}
              >
                <h2 className="odp-card__title">{m.label}</h2>
                <p className="odp-muted" style={{ margin: 0 }}>
                  {m.description}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}
