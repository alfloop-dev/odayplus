import Link from "next/link";
import { PageHeader } from "@oday-plus/ui";
import { GrowthWorkspace } from "./GrowthWorkspace.tsx";
import styles from "./operator.module.css";

type SearchParams = Record<string, string | string[] | undefined>;

/**
 * A workspace slot in the Operator Console. `growth` is delivered by this task
 * (ODP-OC-FE-03); the remaining slots are placeholders that the sibling
 * Operator Console FE tasks replace in place — a worker owning one of them swaps
 * its `case` in {@link OperatorConsole} for the real workspace, exactly the way
 * this task replaced the Growth placeholder.
 */
type WorkspaceSlot = {
  key: string;
  label: string;
  /** owning task id, shown on the placeholder so scope is unambiguous. */
  owner: string;
  /** true once a real workspace is wired for this slot. */
  ready: boolean;
};

const WORKSPACES: WorkspaceSlot[] = [
  { key: "overview", label: "總覽", owner: "ODP-OC-FE-00", ready: false },
  { key: "growth", label: "營收成長", owner: "ODP-OC-FE-03", ready: true },
  { key: "expansion", label: "展店擴張", owner: "ODP-OC-FE-01", ready: false },
  { key: "operations", label: "營運預警", owner: "ODP-OC-FE-02", ready: false },
  { key: "intervention", label: "干預調價", owner: "ODP-OC-FE-04", ready: false },
  { key: "network", label: "店網規劃", owner: "ODP-OC-FE-05", ready: false },
];

const DEFAULT_WORKSPACE = "growth";
const BASE_PATH = "/operator";

/**
 * Operator Console — a single console that hosts the operator workspaces behind
 * a URL-synced tab strip (`?ws=<key>`). This task delivers the Growth
 * workspace; other tabs render a placeholder until their owning task lands.
 */
export function OperatorConsole({ searchParams = {} }: { searchParams?: SearchParams }) {
  const requested = readParam(searchParams.ws);
  const active =
    WORKSPACES.find((slot) => slot.key === requested)?.key ?? DEFAULT_WORKSPACE;

  return (
    <>
      <nav className={styles.consoleTabs} aria-label="Operator Console 工作區" data-testid="operator-console-tabs">
        {WORKSPACES.map((slot) => (
          <Link
            key={slot.key}
            href={`${BASE_PATH}?ws=${slot.key}`}
            aria-current={slot.key === active ? "page" : undefined}
            data-testid={`operator-tab-${slot.key}`}
          >
            {slot.label}
          </Link>
        ))}
      </nav>
      <ActiveWorkspace activeKey={active} searchParams={searchParams} />
    </>
  );
}

function ActiveWorkspace({
  activeKey,
  searchParams,
}: {
  activeKey: string;
  searchParams: SearchParams;
}) {
  switch (activeKey) {
    case "growth":
      return <GrowthWorkspace searchParams={searchParams} basePath={BASE_PATH} />;
    default: {
      const slot = WORKSPACES.find((entry) => entry.key === activeKey);
      return <ConsolePlaceholder slot={slot} />;
    }
  }
}

function ConsolePlaceholder({ slot }: { slot?: WorkspaceSlot }) {
  const label = slot?.label ?? "工作區";
  const owner = slot?.owner ?? "Operator Console FE";
  return (
    <>
      <PageHeader
        title={label}
        summary="Operator Console 工作區占位畫面 — 由對應的 FE 任務接入實際內容。"
        breadcrumb={[{ label: "Operator Console", href: BASE_PATH }, { label }]}
        status={{ label: "PLACEHOLDER", tone: "blue", marker: "•" }}
        lastUpdated="—（尚無資料來源）"
      />
      <div className="odp-content">
        <div className="odp-placeholder" data-testid={`operator-placeholder-${slot?.key ?? "unknown"}`}>
          <div className="odp-card">
            <h2 className="odp-card__title">{label}工作區尚未接入</h2>
            <p className="odp-muted">
              此工作區由任務 <code>{owner}</code> 負責。接入方式與「營收成長」相同：於
              <code> OperatorConsole </code> 對應 <code>case</code> 換上實際 workspace 元件即可，
              不需重建 console 框架。
            </p>
          </div>
        </div>
      </div>
    </>
  );
}

function readParam(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}
