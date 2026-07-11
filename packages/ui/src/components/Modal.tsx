"use client";

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Button } from "./Button.tsx";
import type { ActionSpec } from "./contracts.ts";

export type ModalProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  primaryAction?: ActionSpec;
  destructive?: boolean;
  requireConfirmText?: string;
  className?: string;
};

export function Modal({
  open,
  onClose,
  title,
  children,
  primaryAction,
  destructive = false,
  requireConfirmText,
  className,
}: ModalProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const [confirmText, setConfirmText] = useState("");
  const confirmSatisfied = !requireConfirmText || confirmText === requireConfirmText;

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !destructive) {
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [destructive, onClose, open]);

  if (!open) {
    return null;
  }

  return (
    <div className="odp-overlay" data-layer="modal">
      <section
        className={["odp-modal", className].filter(Boolean).join(" ")}
        role="dialog"
        aria-modal="true"
        aria-labelledby="odp-modal-title"
      >
        <header className="odp-modal__header">
          <h2 id="odp-modal-title">{title}</h2>
          <Button ref={closeRef} variant="ghost" disabled={destructive} onClick={onClose}>
            關閉
          </Button>
        </header>
        <div className="odp-modal__body">{children}</div>
        {requireConfirmText ? (
          <label className="odp-field">
            <span>輸入「{requireConfirmText}」以確認</span>
            <input className="odp-input" value={confirmText} onChange={(event) => setConfirmText(event.currentTarget.value)} />
          </label>
        ) : null}
        <footer className="odp-actions">
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          {primaryAction ? (
            <Button
              variant={destructive ? "danger" : "primary"}
              loading={primaryAction.loading}
              disabled={!confirmSatisfied || primaryAction.permitted === false}
              disabledReason={primaryAction.disabledReason}
              onClick={primaryAction.onSelect}
            >
              {primaryAction.label}
            </Button>
          ) : null}
        </footer>
      </section>
    </div>
  );
}
