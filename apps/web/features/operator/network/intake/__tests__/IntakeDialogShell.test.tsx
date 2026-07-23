import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  IntakeDialogDismissBoundary,
  IntakeDialogShell,
} from "../IntakeDialogShell";

describe("IntakeDialogShell", () => {
  it("blocks Escape, overlay, close, and cancel while a high-risk write is busy", () => {
    const onClose = vi.fn();
    render(
      <IntakeDialogDismissBoundary dismissible={false}>
        <IntakeDialogShell
          ariaLabel="高風險決策"
          onClose={onClose}
          screenLabel="Dialog 高風險決策"
          testId="busy-dialog"
        >
          <button aria-label="關閉" onClick={onClose} type="button">
            ×
          </button>
          <button onClick={onClose} type="button">
            取消
          </button>
          <button type="button">送出中</button>
        </IntakeDialogShell>
      </IntakeDialogDismissBoundary>,
    );

    const overlay = screen.getByTestId("busy-dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    fireEvent.mouseDown(overlay);
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(overlay.getAttribute("aria-busy")).toBe("true");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("renders page presentation without modal semantics or an overlay", () => {
    render(
      <IntakeDialogShell
        ariaLabel="收件處理詳情"
        onClose={() => undefined}
        presentation="page"
        screenLabel="Page 收件處理詳情"
        testId="detail-page"
      >
        <h1>INTAKE-1</h1>
      </IntakeDialogShell>,
    );

    const page = screen.getByTestId("detail-page");
    expect(page.tagName).toBe("MAIN");
    expect(page.getAttribute("data-presentation")).toBe("page");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
