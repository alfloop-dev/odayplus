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

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus();
    };
  }, [onClose, open]);

  if (!open) {
    return null;
  }

  return (
    <div className="odp-overlay" data-layer="drawer">
      <aside
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
