import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  normalizeShellEnvelope,
  TodayWorkspace,
  type OperatorShellEnvelope,
} from "../TodayWorkspace";

function buildEnvelope(): OperatorShellEnvelope {
  return normalizeShellEnvelope(
    {
      meta: {
        source: "operator-shell-production",
        role: {
          id: "ops-lead",
          label: "營運主管",
          subtitle: "全域營運",
          allowedWorkspaces: ["today", "store", "growth", "network", "govern"],
        },
        counts: {
          approvals: 1,
          critical: 1,
          notifications: 2,
          search: 4,
          taskCenter: 2,
        },
      },
      navigation: {
        roles: [],
        workspaces: [],
        allowedWorkspaces: ["today", "store", "growth", "network", "govern"],
      },
      today: {
        hero: {
          name: "林承翰",
          roleLabel: "營運主管",
          scope: "全品牌・12 門市・北北桃",
          dateLabel: "2026/07/20・週一",
        },
        kpis: [
          { label: "高風險未指派", value: "1", meta: "完成 Triage 與指派", tone: "danger" },
          { label: "已逾期 Issue", value: "1", meta: "優先處理 SLA 逾期", tone: "danger" },
          { label: "即將逾期", value: "4", meta: "今日期限內需完成", tone: "warning" },
          { label: "成效待判斷", value: "1", meta: "請完成 Outcome Review", tone: "info" },
          { label: "待我核准", value: "4", meta: "前往治理稽核", tone: "info" },
          { label: "需升級門市", value: "1", meta: "連續紅燈", tone: "neutral" },
        ],
        queue: [
          {
            id: "ISS-1021",
            title: "Kiosk 離線與遠端重啟失敗",
            description: "皇羽自助洗衣新莊店",
            meta: "設備異常",
            owner: "陳建宏",
            status: "已逾期",
            time: "1h 24m",
            tone: "danger",
            workspace: "store",
            target: { workspace: "store", entityId: "ISS-1021", tab: "assign" },
          },
        ],
        decisions: [
          {
            id: "APR-501",
            title: "SiteScore 審核：板橋府中候選點",
            meta: "7/8 前",
            status: "核准",
            cta: "進行核准",
            tone: "info",
            target: { workspace: "govern", entityId: "APR-501", tab: "approvals" },
          },
        ],
        riskRows: [
          {
            label: "Oday 信義松仁店",
            score: 72,
            signal: "支付異常處理中",
            tone: "warning",
          },
          {
            label: "洗多星 中壢中原店",
            score: 91,
            signal: "連續紅燈",
            tone: "danger",
          },
        ],
        auditFeed: [
          {
            actor: "系統",
            category: "ISS-1024",
            detail: "新增 Google 一星評價",
            time: "08:12",
          },
        ],
      },
      notifications: [],
      search: { items: [] },
    },
    { allowFixtureFallback: false },
  );
}

afterEach(() => {
  cleanup();
});

describe("Package 10 R7 Today workspace", () => {
  it("renders the canonical six-card, queue, and ordered decision rail structure", () => {
    render(
      <TodayWorkspace
        envelope={buildEnvelope()}
        onApprovalDecision={vi.fn()}
        onTargetSelect={vi.fn()}
      />,
    );

    const workspace = screen.getByTestId("operator-today-workspace");
    expect(workspace).toHaveAttribute("data-visual-layout", "package-10-r7");
    expect(screen.getByText("早安，林承翰 — 營運主管")).toBeInTheDocument();
    expect(screen.getByText("資料範圍：全品牌・12 門市・北北桃")).toBeInTheDocument();
    expect(screen.getByText("2026/07/20・週一")).toBeInTheDocument();

    const kpis = screen.getByTestId("operator-today-kpis");
    expect(kpis).toHaveAttribute("data-visual-layout", "six-column-kpi");
    expect(within(kpis).getAllByRole("article")).toHaveLength(6);

    expect(screen.getByRole("heading", { name: "今天最需要處理" })).toBeInTheDocument();
    expect(screen.getByText("依嚴重度與 SLA 排序")).toBeInTheDocument();

    const railOrder = Array.from(
      workspace.querySelectorAll<HTMLElement>("[data-today-rail-section]"),
    ).map((section) => section.dataset.todayRailSection);
    expect(railOrder).toEqual(["decisions", "risk", "audit"]);
    expect(screen.getByRole("heading", { name: "需要你決策" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "門市風險快照" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /最近動態/ })).toBeInTheDocument();
    expect(screen.getAllByRole("img", { name: /風險分數/ })).toHaveLength(2);
    expect(screen.queryByText("Operational signals")).not.toBeInTheDocument();
  });

  it("keeps queue routing and approval mutations connected to the existing callbacks", () => {
    const onApprovalDecision = vi.fn();
    const onTargetSelect = vi.fn();
    const envelope = buildEnvelope();

    render(
      <TodayWorkspace
        envelope={envelope}
        onApprovalDecision={onApprovalDecision}
        onTargetSelect={onTargetSelect}
      />,
    );

    fireEvent.click(
      within(screen.getByTestId("operator-today-queue")).getByRole("button", {
        name: /ISS-1021/,
      }),
    );
    expect(onTargetSelect).toHaveBeenCalledWith(
      { workspace: "store", entityId: "ISS-1021", tab: "assign" },
      "ISS-1021",
    );

    fireEvent.click(screen.getByRole("button", { name: "核准" }));
    expect(onApprovalDecision).toHaveBeenCalledWith(
      "APR-501",
      "approved",
      expect.objectContaining({
        actorName: "林承翰",
        actorRoleId: "ops-lead",
      }),
    );
  });

  it("renders explicit empty states without filling missing live data from fixtures", () => {
    const envelope = normalizeShellEnvelope(undefined, {
      allowFixtureFallback: false,
    });

    render(
      <TodayWorkspace
        envelope={envelope}
        onApprovalDecision={vi.fn()}
        onTargetSelect={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "今日工作" })).toBeInTheDocument();
    expect(screen.queryByText(/林承翰/)).not.toBeInTheDocument();
    expect(screen.getByTestId("operator-today-kpis-empty")).toBeInTheDocument();
    expect(screen.getByTestId("operator-today-queue-empty")).toBeInTheDocument();
    expect(screen.getByTestId("operator-decisions-empty")).toBeInTheDocument();
    expect(screen.getByTestId("operator-risk-empty")).toBeInTheDocument();
    expect(screen.getByTestId("operator-audit-empty")).toBeInTheDocument();
  });
});
