/**
 * Global search (ODP-PGAP-SHELL-001, acceptance §4).
 *
 * Authorized cross-domain results plus keyboard command navigation. The query
 * lives in the URL so a result set is shareable and the back button works.
 *
 * Authorization is the server's: it returns only what the role may reach, so an
 * unauthorized entity is absent from the payload rather than filtered out of
 * the render — a client-side filter would still ship the title to the browser.
 */
import Link from "next/link";
import { Badge, PageHeader } from "@oday-plus/ui";
import type { ShellSearchResponse } from "@oday-plus/openapi-client";
import type { ApiResource } from "./resource.ts";
import { ShellDataSource, ShellResourceState, ShellState } from "./ShellStates.tsx";
import { SearchKeyboardNav } from "./SearchKeyboardNav.tsx";
import { formatStamp } from "./HomeWorkspace.tsx";
import styles from "./shell.module.css";

export function SearchWorkspace({
  results,
  query,
}: {
  results: ApiResource<ShellSearchResponse>;
  query: string;
}) {
  const data = results.data;

  return (
    <>
      <PageHeader
        title="全域搜尋"
        summary="跨模組搜尋門市、候選點、決策與工作區；僅顯示你有權限的結果。"
        status={
          data
            ? { label: `${data.total} 筆結果`, tone: "blue", marker: "•" }
            : { label: "無法取得", tone: "red", marker: "•" }
        }
        breadcrumb={[{ label: "總覽", href: "/" }, { label: "全域搜尋" }]}
        lastUpdated={data ? formatStamp(data.meta.generatedAt) : "—"}
      />
      <div className="odp-content" data-testid="shell-search">
        {/* GET so the query lands in the URL — a result set must be shareable. */}
        <form className={styles.searchForm} method="get" action="/search" role="search">
          <label htmlFor="search-q" className="odp-visually-hidden">
            搜尋
          </label>
          <input
            id="search-q"
            name="q"
            type="search"
            defaultValue={query}
            placeholder="搜尋門市、候選點、決策、工作區…"
            className={styles.searchInput}
            data-testid="search-input"
            autoComplete="off"
          />
          <button type="submit" className={styles.filterLink} data-testid="search-submit">
            搜尋
          </button>
        </form>

        {data ? (
          <SearchBody data={data} results={results} />
        ) : (
          <ShellResourceState resource={results} testId="search-state" />
        )}
      </div>
    </>
  );
}

function SearchBody({
  data,
  results,
}: {
  data: ShellSearchResponse;
  results: ApiResource<ShellSearchResponse>;
}) {
  return (
    <>
      <div className={styles.statusRow}>
        <ShellDataSource
          resource={results}
          endpoint="/operator/shell/search"
          testId="search-data-source"
        />
        <span className={styles.rowMeta} data-testid="search-scope">
          搜尋範圍：{(data.meta.allowedWorkspaces ?? []).join("、")}
        </span>
      </div>

      {/* ↑/↓ to move, Enter to open — the palette and this page share targets. */}
      <SearchKeyboardNav count={data.items.length + data.commands.length} />

      {data.commands.length > 0 ? (
        <section className={styles.section} data-testid="search-commands">
          <div className={styles.sectionHead}>
            <h2 className={styles.sectionTitle}>快速前往</h2>
            <span className={styles.rowMeta}>按 ↑ ↓ 選擇，Enter 開啟</span>
          </div>
          <ul className={styles.list}>
            {data.commands.map((command, index) => (
              <li key={command.id}>
                <Link
                  href={command.href}
                  className={styles.resultLink}
                  data-testid={`search-command-${command.id}`}
                  data-nav-index={index}
                >
                  <p className={styles.rowTitle}>{command.label}</p>
                  <p className={styles.rowMeta}>{command.description}</p>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className={styles.section} data-testid="search-results">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle}>搜尋結果</h2>
        </div>
        {data.items.length === 0 ? (
          <ShellState
            kind="empty"
            testId="search-empty"
            detail="沒有符合的結果。可能是關鍵字不同，或該項目不在你的權限範圍內。"
            actions={<Link href="/search">清除搜尋</Link>}
          />
        ) : (
          <ul className={styles.list}>
            {data.items.map((item, index) => (
              <li key={item.id}>
                <Link
                  href={item.href}
                  className={styles.resultLink}
                  data-testid={`search-result-${item.entityId}`}
                  data-workspace={item.workspace}
                  data-nav-index={data.commands.length + index}
                >
                  <p className={styles.rowTitle}>
                    {item.label} <Badge label={item.workspace} tone="gray" marker="◫" />
                  </p>
                  <p className={styles.rowMeta}>{item.description}</p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
