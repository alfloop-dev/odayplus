"use client";

/**
 * Keyboard command navigation for search results (acceptance §4).
 *
 * Arrow keys move a roving focus across `[data-nav-index]` targets and Enter
 * opens the focused one. Focus is moved rather than a custom "selected" style
 * painted, so the browser's own focus ring, screen-reader announcement and
 * Enter-activates-a-link behaviour all keep working — a bespoke selection model
 * would have to re-implement each of those and would drift from them.
 *
 * `/` focuses the search box, matching the command palette's convention.
 */
import { useEffect } from "react";

export function SearchKeyboardNav({ count }: { count: number }) {
  useEffect(() => {
    if (count === 0) return undefined;

    function targets(): HTMLElement[] {
      return Array.from(document.querySelectorAll<HTMLElement>("[data-nav-index]")).sort(
        (a, b) => Number(a.dataset.navIndex) - Number(b.dataset.navIndex),
      );
    }

    function onKeyDown(event: KeyboardEvent) {
      const isTyping =
        event.target instanceof HTMLElement &&
        ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName);

      if (event.key === "/" && !isTyping) {
        event.preventDefault();
        document.querySelector<HTMLInputElement>("[data-testid='search-input']")?.focus();
        return;
      }
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      // Arrow keys inside the query box move the caret; leave them alone.
      if (isTyping && event.target instanceof HTMLElement && event.target.tagName !== "INPUT") {
        return;
      }

      const items = targets();
      if (items.length === 0) return;
      event.preventDefault();

      const current = items.findIndex((item) => item === document.activeElement);
      const delta = event.key === "ArrowDown" ? 1 : -1;
      // From the query box (current === -1), ArrowDown enters at the first item
      // and ArrowUp wraps to the last.
      const next =
        current === -1
          ? delta === 1
            ? 0
            : items.length - 1
          : (current + delta + items.length) % items.length;
      items[next]?.focus();
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [count]);

  return null;
}
