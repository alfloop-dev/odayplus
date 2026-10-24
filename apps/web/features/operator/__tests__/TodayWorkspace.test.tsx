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
  it("renders an unavailable metric as a dash even if the response carries a zero", () => {
    const envelope = buildEnvelope();
    envelope.today.kpis[0] = { label: "高風險未指派", value: "0", availability: "unavailable", meta: "資料尚未備妥" };
    // Normalize at the actual transport boundary before rendering.
    const normalized = normalizeShellEnvelope(envelope, { allowFixtureFallback: false });
    render(<TodayWorkspace envelope={normalized} onApprovalDecision={vi.fn()} onTargetSelect={vi.fn()} />);
    const card = screen.getByText("高風險未指派").closest("article")!;
    expect(card).toHaveAttribute("data-availability", "unavailable");
    expect(within(card).getByText("—")).toBeInTheDocument();
    expect(within(card).queryByText("0")).not.toBeInTheDocument();
  });

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

  it("keeps queue routing and opens the full approval owner without a Home mutation", () => {
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
    expect(onApprovalDecision).not.toHaveBeenCalled();
    expect(onTargetSelect).toHaveBeenLastCalledWith(
      { workspace: "govern", entityId: "APR-501", tab: "approvals" }, "APR-501",
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

  it("formats actual ISO queue values without choosing a business timezone or inventing an owner name", () => {
    const envelope = buildEnvelope();
    const owner = "00000000-0000-0000-0000-000000000301";
    const instant = "2026-10-16T04:10:38.424206+00:00";
    Object.assign(envelope.today.queue[0], { owner, time: instant, status: "new" });
    envelope.today.queue.push({ ...envelope.today.queue[0], id: "native-intervention", title: "CLEANING intervention",
      status: "PENDING_APPROVAL", time: "2026-10-16T13:10:38.424206+09:00" });
    render(<TodayWorkspace envelope={envelope} onTargetSelect={vi.fn()} />);
    const queue = within(screen.getByTestId("operator-today-queue"));
    expect(queue.getByText("新進")).toBeInTheDocument();
    expect(queue.getByText("待核准")).toBeInTheDocument();
    expect(queue.getByText("清潔干預")).toHaveAttribute("title", "CLEANING intervention");
    expect(queue.getByText("10/16 04:10 UTC")).toHaveAttribute("dateTime", instant);
    expect(queue.getByText("10/16 04:10 UTC")).toHaveAttribute("title", instant);
    expect(queue.getByText("10/16 13:10 UTC+09:00")).toBeInTheDocument();
    expect(queue.getAllByText("帳號 0000…0301")).toHaveLength(2);
    for (const label of queue.getAllByText("帳號 0000…0301")) expect(label).toHaveAttribute("title", owner);
    expect(envelope.today.queue[0].time).toBe(instant);
    expect(envelope.today.queue[0].owner).toBe(owner);
  });

  it("describes security authorization as permission while preserving its source values", () => {
    const envelope = buildEnvelope();
    const actor = "00000000-0000-0000-0000-000000000301";
    envelope.today.auditFeed = [
      { actor, category: "security.authorization", detail: "approve intervention: allow", time: "08:12" },
      { actor: "系統", category: "security.authentication", detail: "authenticate auth/local: success", time: "08:13" },
      { actor: "原始帳號", category: "custom.audit", detail: "unmapped event: deny", time: "08:14" },
    ];
    Object.assign(envelope.today.decisions[0], { title: "Intervention approval", meta: "CLEANING", status: "PENDING_APPROVAL" });
    render(<TodayWorkspace envelope={envelope} onTargetSelect={vi.fn()} />);
    const audit = within(screen.getByTestId("operator-audit-feed"));
    expect(audit.getByText("干預核准權限：允許")).toHaveAttribute("title", "approve intervention: allow");
    expect(audit.getByText("權限檢查")).toHaveAttribute("title", "security.authorization");
    expect(audit.getByText("帳號 0000…0301")).toHaveAttribute("title", actor);
    expect(audit.queryByText("已核准")).not.toBeInTheDocument();
    expect(audit.getByText("本機登入驗證成功")).toBeInTheDocument();
    expect(audit.getByText("unmapped event: deny")).toHaveAttribute("title", "unmapped event: deny");
    const decisions = within(screen.getByTestId("operator-decision-rail"));
    expect(decisions.getByText("待核准")).toBeInTheDocument();
    expect(decisions.getByText("清潔")).toHaveAttribute("title", "CLEANING");
    expect(decisions.getByText("干預核准")).toHaveAttribute("title", "Intervention approval");
  });

  it("preserves unknown statuses, human display names, and non-ISO time labels", () => {
    const envelope = buildEnvelope();
    Object.assign(envelope.today.queue[0], { status: "constructor", owner: "陳建宏", time: "1h 24m" });
    render(<TodayWorkspace envelope={envelope} onTargetSelect={vi.fn()} />);
    const queue = within(screen.getByTestId("operator-today-queue"));
    expect(queue.getByText("constructor")).toBeInTheDocument();
    expect(queue.getByText("陳建宏")).toHaveAttribute("title", "陳建宏");
    expect(queue.getByText("1h 24m")).not.toHaveAttribute("dateTime");
  });

});
