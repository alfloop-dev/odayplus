"use client";

import { useEffect, useRef } from "react";
import { Button } from "./Button.tsx";
import type { ActionSpec } from "./contracts.ts";

export type CommandPaletteProps = {
  open: boolean;
  commands: readonly ActionSpec[];
  recent?: readonly ActionSpec[];
  onSelect?: (command: ActionSpec) => void;
  onClose: () => void;
  query?: string;
  onQueryChange?: (query: string) => void;
  className?: string;
};

export function CommandPalette({
  open,
  commands,
  recent = [],
  onSelect,
  onClose,
  query = "",
  onQueryChange,
  className,
}: CommandPaletteProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const allCommands = [...recent, ...commands].filter((command) => command.permitted !== false);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    inputRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);

  if (!open) {
    return null;
  }

  return (
    <div className="odp-overlay" data-layer="command-palette">
      <section
        className={["odp-command", className].filter(Boolean).join(" ")}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <header className="odp-command__header">
          <input
            ref={inputRef}
            className="odp-command__input"
            value={query}
            placeholder="搜尋頁面、實體或動作"
            onChange={(event) => onQueryChange?.(event.currentTarget.value)}
          />
          <Button variant="ghost" onClick={onClose}>
            關閉
          </Button>
        </header>
        <ul className="odp-command__list">
          {allCommands.map((command) => (
            <li key={command.id}>
              <button
                type="button"
                className="odp-command__item"
                disabled={Boolean(command.disabledReason)}
                title={command.disabledReason}
                onClick={() => {
                  command.onSelect?.();
                  onSelect?.(command);
                }}
              >
                <span>{command.icon}</span>
                <span>{command.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
