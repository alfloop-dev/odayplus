"use client";

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Button } from "./Button.tsx";

export type DrawerProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: "default" | "wide";
  onPrev?: () => void;
  onNext?: () => void;
  deepLinkHref?: string;
  className?: string;
};

export function Drawer({
  open,
  onClose,
  title,
  children,
  width = "default",
  onPrev,
  onNext,
  deepLinkHref,
  className,
}: DrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const behavior = useRef(onClose); behavior.current = onClose;

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const focusables = () => Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
    ) ?? []).filter((element) => {
      if (element.matches(":disabled") || element.closest('[hidden], [inert], [aria-hidden="true"]') || element.getAttribute("type") === "hidden" || element.tabIndex < 0) return false;
      for (let parent: HTMLElement | null = element; parent && parent !== dialogRef.current; parent = parent.parentElement) {
        const style = window.getComputedStyle(parent);
        if (style.display === "none" || style.visibility === "hidden") return false;
      }
      return true;
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); behavior.current();
      } else if (event.key === "Tab") {
        const items = focusables(); const first = items[0]; const last = items[items.length - 1];
        if (!first || !last) { event.preventDefault(); dialogRef.current?.focus(); }
        else if (!dialogRef.current?.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [open]);

  if (!open) {
    return null;
  }

  return (
    <div className="odp-overlay" data-layer="drawer">
      <aside
        ref={dialogRef}
        tabIndex={-1}
        className={["odp-drawer", className].filter(Boolean).join(" ")}
        data-width={width}
        role="dialog"
        aria-modal="true"
        aria-labelledby="odp-drawer-title"
      >
        <header className="odp-drawer__header">
          <h2 id="odp-drawer-title">{title}</h2>
          <div className="odp-actions">
            {onPrev ? <Button onClick={onPrev}>上一筆</Button> : null}
            {onNext ? <Button onClick={onNext}>下一筆</Button> : null}
            {deepLinkHref ? (
              <a className="odp-text-link" href={deepLinkHref}>
                開啟完整頁
              </a>
            ) : null}
            <Button ref={closeRef} variant="ghost" onClick={onClose}>
              關閉
            </Button>
          </div>
        </header>
        <div className="odp-drawer__body">{children}</div>
      </aside>
    </div>
  );
}
