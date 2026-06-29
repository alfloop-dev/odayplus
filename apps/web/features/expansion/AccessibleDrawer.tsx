"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import styles from "./expansion.module.css";

type AccessibleDrawerProps = {
  title: string;
  children: ReactNode;
  testId: string;
  returnFocusTestId?: string;
};

export function AccessibleDrawer({
  title,
  children,
  testId,
  returnFocusTestId,
}: AccessibleDrawerProps) {
  const [open, setOpen] = useState(true);

  const closeDrawer = () => {
    setOpen(false);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("drawer");
      window.history.replaceState(window.history.state, "", url);
    }
    window.requestAnimationFrame(() => {
      const selector = returnFocusTestId
        ? `[data-testid="${returnFocusTestId}"]`
        : "[aria-current='true']";
      const target = document.querySelector<HTMLElement>(selector);
      target?.focus();
    });
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeDrawer();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });

  if (!open) return null;

  return (
    <aside className={styles.drawer} aria-label={title} data-testid={testId}>
      <div className={styles.drawerHeader}>
        <h2>{title}</h2>
        <button
          aria-label={`Close ${title}`}
          className={styles.drawerCloseButton}
          data-testid={`${testId}-close`}
          onClick={closeDrawer}
          type="button"
        >
          Esc
        </button>
      </div>
      {children}
      <div className={styles.drawerFooter}>
        <a href="#prev">上一筆</a>
        <a href="#next">下一筆</a>
        <a href="#deep-link">Deep link</a>
      </div>
    </aside>
  );
}
