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
  closeDisabled?: boolean;
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
  closeDisabled = false,
  className,
}: ModalProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const behavior = useRef({ onClose, destructive, closeDisabled });
  behavior.current = { onClose, destructive, closeDisabled };
  const [confirmText, setConfirmText] = useState("");
  const confirmSatisfied = !requireConfirmText || confirmText === requireConfirmText;

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const restoreTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null;
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
    (closeRef.current?.disabled ? focusables()[0] ?? dialogRef.current : closeRef.current)?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        if (!behavior.current.destructive && !behavior.current.closeDisabled) behavior.current.onClose();
      } else if (event.key === "Tab") {
        const items = focusables();
        const first = items[0];
        const last = items[items.length - 1];
        if (!first || !last) {
          event.preventDefault();
          dialogRef.current?.focus();
        } else if (!dialogRef.current?.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      if (restoreTarget?.isConnected) restoreTarget.focus();
    };
  }, [open]);

  if (!open) {
    return null;
  }

  return (
    <div className="odp-overlay" data-layer="modal">
      <section
        ref={dialogRef}
        tabIndex={-1}
        className={["odp-modal", className].filter(Boolean).join(" ")}
        role="dialog"
        aria-modal="true"
        aria-labelledby="odp-modal-title"
      >
        <header className="odp-modal__header">
          <h2 id="odp-modal-title">{title}</h2>
          <Button ref={closeRef} variant="ghost" disabled={destructive || closeDisabled} onClick={onClose}>
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
          <Button variant="secondary" disabled={closeDisabled} onClick={onClose}>
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
