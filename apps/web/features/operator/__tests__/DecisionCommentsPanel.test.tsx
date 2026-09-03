import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DecisionCommentsPanel } from "../governance/DecisionCommentsPanel";

const comment = {
  id: "cmt-1",
  tenantId: "tenant-a",
  targetType: "approval" as const,
  targetId: "APR-501",
  content: "Review evidence",
  createdBy: "operator-ops-lead",
  createdAt: "2026-09-03T10:00:00Z",
  updatedBy: null,
  updatedAt: null,
  edited: false,
  editCount: 0,
  history: [{ action: "created", actorId: "operator-ops-lead", occurredAt: "2026-09-03T10:00:00Z" }],
};

describe("DecisionCommentsPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.sessionStorage.clear();
  });

  it("renders the empty state and reads back created and edited comments", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/comments?") && (!init?.method || init.method === "GET")) {
        return new Response(JSON.stringify({ items: [] }), { status: 200 });
      }
      if (url.endsWith("/comments") && init?.method === "POST") {
        return new Response(JSON.stringify({ comment }), { status: 200 });
      }
      return new Response(
        JSON.stringify({ comment: { ...comment, content: "Updated evidence context", edited: true, editCount: 1 } }),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<DecisionCommentsPanel roleId="ops-lead" targetId="APR-501" targetType="approval" />);
    expect(await screen.findByTestId("decision-comments-empty")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "新增留言" }), {
      target: { value: "Review evidence" },
    });
    fireEvent.click(screen.getByTestId("decision-comments-create"));
    expect(await screen.findByText("Review evidence")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "編輯" }));
    fireEvent.change(screen.getByRole("textbox", { name: "編輯留言 cmt-1" }), {
      target: { value: "Updated evidence context" },
    });
    fireEvent.click(screen.getByRole("button", { name: "儲存編輯" }));
    expect(await screen.findByRole("button", { name: "編輯" })).toBeInTheDocument();
    expect(screen.getByText("Updated evidence context")).toBeInTheDocument();

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([, options]) => options?.method === "POST")).toBe(true);
      expect(fetchMock.mock.calls.some(([, options]) => options?.method === "PATCH")).toBe(true);
    });
  });

  it("does not render write controls for a read-only role", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200 })),
    );

    render(
      <DecisionCommentsPanel
        canComment={false}
        roleId="pm-audit"
        targetId="APR-501"
        targetType="approval"
      />,
    );

    expect(await screen.findByTestId("decision-comments-empty")).toBeInTheDocument();
    expect(screen.getByText("目前角色僅可查看留言。")).toBeInTheDocument();
    expect(screen.queryByTestId("decision-comments-create")).not.toBeInTheDocument();
  });
});
