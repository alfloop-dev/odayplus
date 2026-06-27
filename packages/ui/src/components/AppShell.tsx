/**
 * AppShell — fixed full-app frame: Global Header + Sidebar + Main.
 * Landmark roles + skip-to-content (contracts §3.1, visual system §4.1).
 * The shell is layout only; it injects no page business logic.
 */
import type { ReactNode } from "react";

export type AppShellProps = {
  header: ReactNode;
  sidebar: ReactNode;
  children: ReactNode;
  drawer?: ReactNode;
};

export function AppShell({ header, sidebar, children, drawer }: AppShellProps) {
  return (
    <div className="odp-shell" data-testid="app-shell">
      <a className="odp-skip-link" href="#odp-main-content">
        跳到主要內容
      </a>
      {header}
      {sidebar}
      <main className="odp-main" id="odp-main-content">
        {children}
      </main>
      {drawer}
    </div>
  );
}
