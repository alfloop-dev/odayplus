"use client";

import { useEffect, useRef, type ReactNode } from "react";
import styles from "./intake.module.css";

// Shared modal shell for the four intake dialogs.
//
// The archived design markup has no aria/role/keyboard affordances at all
// (it renders clickable <div>s), but §9 of the requirements makes every
// dialog keyboard operable. Rather than repeat that per dialog, the shell
// owns: labelled dialog semantics, Escape-to-close, initial focus, focus
// restoration, and a focus trap.

export function IntakeDialogShell({
  ariaLabel,
  children,
  className,
  onClose,
  screenLabel,
  stacked,
  testId,
}: {
  ariaLabel: string;
  children: ReactNode;
  className?: string;
  onClose: () => void;
  screenLabel: string;
  stacked?: boolean;
  testId: string;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    restoreRef.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    focusables(panel)[0]?.focus();

    return () => {
      // Returning focus to the invoker keeps keyboard context after close.
      restoreRef.current?.focus?.();
    };
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const items = focusables(panelRef.current);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [onClose]);

  return (
    <div
      className={`${styles.overlay} ${stacked ? styles.overlayStacked : ""}`}
      data-screen-label={screenLabel}
      data-testid={testId}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        aria-label={ariaLabel}
        aria-modal="true"
        className={`${styles.panel} ${className ?? ""}`}
        ref={panelRef}
        role="dialog"
      >
        {children}
      </div>
    </div>
  );
}

function focusables(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => element.offsetParent !== null || element === document.activeElement);
}
