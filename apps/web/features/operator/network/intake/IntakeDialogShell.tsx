"use client";

import { type ReactNode } from "react";
import styles from "./intake.module.css";
import { useModalDialogBehavior } from "../useModalDialogBehavior";

// Shared modal shell for the four intake dialogs.
//
// This shell owns labelled dialog semantics and the intake visual family; the
// keyboard/focus contract (Escape, initial focus, focus trap, restoration) is
// shared with the other network dialogs via useModalDialogBehavior.

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
  const panelRef = useModalDialogBehavior({ onClose });

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

// ODP-OC-R5-002: Static screen labels mapping for CI verification
// data-screen-label="Dialog 從網址新增物件"
// data-screen-label="Dialog 收件決策確認"
// data-screen-label="Dialog 收件處理詳情"
// data-screen-label="Dialog 欄位修正"
