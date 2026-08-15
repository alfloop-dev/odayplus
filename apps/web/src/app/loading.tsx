/**
 * Route-level loading surface (ODP-PGAP-SHELL-001, acceptance §7).
 *
 * Shown while a server component streams. `role="status"` + `aria-live` come
 * from ShellState, so the wait is announced rather than silent.
 */
import { ShellState } from "../../features/shell/ShellStates.tsx";

export default function Loading() {
  return (
    <div className="odp-content" data-testid="route-loading">
      <ShellState kind="loading" testId="shell-state-loading" />
    </div>
  );
}
