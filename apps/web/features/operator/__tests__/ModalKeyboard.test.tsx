import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Modal } from "@oday-plus/ui";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

function Example() {
  const [open, setOpen] = useState(false);
  return <><button onClick={() => setOpen(true)}>開啟既有 Modal</button><Modal open={open} title="Keyboard contract" onClose={() => setOpen(false)}><label>理由<textarea /></label></Modal></>;
}

describe("existing Modal keyboard containment and restoration", () => {
  it("uses real Tab/Shift+Tab keyboard events to wrap focus and Escape to restore the invoker", () => {
    render(<Example />);
    const trigger = screen.getByRole("button", { name: "開啟既有 Modal" });
    trigger.focus();
    fireEvent.click(trigger);
    const close = screen.getByRole("button", { name: "關閉" });
    const cancel = screen.getByRole("button", { name: "取消" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(cancel).toHaveFocus();
    fireEvent.keyDown(cancel, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("does not steal typed-input focus when the current close callback changes", () => {
    const first = vi.fn();
    const latest = vi.fn();
    const view = render(<Modal open title="Stable focus" onClose={first}><label>理由<textarea /></label></Modal>);
    const input = screen.getByRole("textbox", { name: "理由" });
    input.focus();
    fireEvent.change(input, { target: { value: "typed reason" } });
    view.rerender(<Modal open title="Stable focus" onClose={latest}><label>理由<textarea /></label></Modal>);
    expect(input).toHaveFocus();
    expect(input).toHaveValue("typed reason");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(first).not.toHaveBeenCalled();
    expect(latest).toHaveBeenCalledTimes(1);
  });

  it("excludes hidden and disabled controls from Tab boundaries and keeps an in-flight dialog open", () => {
    const onClose = vi.fn();
    render(<Modal open title="Busy dialog" onClose={onClose} closeDisabled><button disabled>Disabled</button><button hidden>Hidden</button><button style={{ display: "none" }}>CSS hidden</button><input aria-label="Only available control" /></Modal>);
    const input = screen.getByRole("textbox", { name: "Only available control" });
    expect(input).toHaveFocus();
    fireEvent.keyDown(input, { key: "Tab" });
    expect(input).toHaveFocus();
    fireEvent.keyDown(input, { key: "Tab", shiftKey: true });
    expect(input).toHaveFocus();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "關閉" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
  });

  it("contains keyboard focus on the dialog itself if no controls can accept focus", () => {
    render(<Modal open title="No available controls" onClose={() => undefined} closeDisabled><button disabled>Disabled</button></Modal>);
    expect(screen.getByRole("dialog")).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("dialog")).toHaveFocus();
  });
});
