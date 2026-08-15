"use client";

import { useEffect, useRef, type MutableRefObject } from "react";

// The keyboard/focus contract every Operator Console network dialog owes
// (design §9), extracted so it is implemented once rather than per dialog.
//
// The archived design markup has no aria/role/keyboard affordances at all (it
// renders clickable <div>s), but §9 makes every dialog keyboard operable. This
// hook owns: initial focus, Escape-to-close, a focus trap, and focus
// restoration. Dialog markup and styling stay with each dialog, because the
// intake dialogs and the network dialogs belong to different visual families.
//
// `dismissible` exists for high-impact writes: while a write is in flight the
// dialog must NOT be dismissable, or the operator can close it and leave a
// write they can no longer see the result of.

export function useModalDialogBehavior({
  dismissible = true,
  onClose,
}: {
  dismissible?: boolean;
  onClose: () => void;
}): MutableRefObject<HTMLDivElement | null> {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    restoreRef.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    // Prefer the dialog's declared entry control. Falling back to the first
    // focusable would land on the close button (it leads in DOM order), which
    // is a poor place to start a form.
    const preferred = panel?.querySelector<HTMLElement>("[data-autofocus]");
    (preferred ?? focusables(panel)[0])?.focus();

    return () => {
      // Returning focus to the invoker keeps keyboard context after close.
      restoreRef.current?.focus?.();
    };
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        // A write in flight is not cancellable from here; swallow the key
        // rather than hiding a request whose outcome is still unknown.
        if (dismissible) onClose();
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
  }, [dismissible, onClose]);

  return panelRef;
}

export function focusables(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(
    root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => element.offsetParent !== null || element === document.activeElement);
}
