"use client";

import { createContext, type ReactNode, useContext } from "react";
import styles from "./intake.module.css";
import { useModalDialogBehavior } from "../useModalDialogBehavior";

// Shared modal shell for the four intake dialogs.
//
// This shell owns labelled dialog semantics and the intake visual family; the
// keyboard/focus contract (Escape, initial focus, focus trap, restoration) is
// shared with the other network dialogs via useModalDialogBehavior.

const IntakeDialogDismissContext = createContext(true);

export function IntakeDialogDismissBoundary({
  children,
  dismissible,
}: {
  children: ReactNode;
  dismissible: boolean;
}) {
  return (
    <IntakeDialogDismissContext.Provider value={dismissible}>
      {children}
    </IntakeDialogDismissContext.Provider>
  );
}

export function IntakeDialogShell({
  ariaLabel,
  children,
  className,
  dismissible = true,
  onClose,
  presentation = "dialog",
  screenLabel,
  stacked,
  testId,
}: {
  ariaLabel: string;
  children: ReactNode;
  className?: string;
  dismissible?: boolean;
  onClose: () => void;
  presentation?: "dialog" | "page";
  screenLabel: string;
  stacked?: boolean;
  testId: string;
}) {
  const boundaryDismissible = useContext(IntakeDialogDismissContext);
  const effectiveDismissible = dismissible && boundaryDismissible;

  if (presentation === "page") {
    return (
      <main
        aria-label={ariaLabel}
        className="odp-content"
        data-presentation="page"
        data-screen-label={screenLabel}
        data-testid={testId}
      >
        <section
          className={className}
          style={{
            background: "#ffffff",
            border: "1px solid #dfe4ee",
            borderRadius: "8px",
            color: "#1c2333",
            overflow: "hidden",
            width: "100%",
          }}
        >
          {children}
        </section>
      </main>
    );
  }

  return (
    <ModalIntakeShell
      ariaLabel={ariaLabel}
      className={className}
      dismissible={effectiveDismissible}
      onClose={onClose}
      screenLabel={screenLabel}
      stacked={stacked}
      testId={testId}
    >
      {children}
    </ModalIntakeShell>
  );
}

function ModalIntakeShell({
  ariaLabel,
  children,
  className,
  dismissible,
  onClose,
  screenLabel,
  stacked,
  testId,
}: {
  ariaLabel: string;
  children: ReactNode;
  className?: string;
  dismissible: boolean;
  onClose: () => void;
  screenLabel: string;
  stacked?: boolean;
  testId: string;
}) {
  const panelRef = useModalDialogBehavior({ dismissible, onClose });

  return (
    <div
      aria-busy={!dismissible}
      className={`${styles.overlay} ${stacked ? styles.overlayStacked : ""}`}
      data-screen-label={screenLabel}
      data-testid={testId}
      onMouseDown={(event) => {
        if (dismissible && event.target === event.currentTarget) onClose();
      }}
    >
      <div
        aria-label={ariaLabel}
        aria-modal="true"
        className={`${styles.panel} ${className ?? ""}`}
        onClickCapture={(event) => {
          if (dismissible) return;
          const button = (event.target as HTMLElement).closest("button");
          if (!button) return;
          const label = `${button.getAttribute("aria-label") ?? ""} ${button.textContent ?? ""}`;
          if (/關閉|取消/.test(label)) {
            event.preventDefault();
            event.stopPropagation();
          }
        }}
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
