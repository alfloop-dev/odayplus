/**
 * 404 surface (ODP-PGAP-SHELL-001, acceptance §7).
 *
 * Every dead link lands here — including a deep link to a task that no longer
 * exists — so it must route the operator onward rather than dead-end.
 */
import Link from "next/link";
import { ShellState } from "../../features/shell/ShellStates.tsx";

export default function NotFound() {
  return (
    <div className="odp-content" data-testid="route-not-found">
      <ShellState
        kind="not-found"
        testId="shell-state-not-found"
        actions={
          <>
            <Link href="/operator" data-testid="not-found-home">
              回到營運管理
            </Link>
          </>
        }
      />
    </div>
  );
}
