"use client";

/**
 * Route-level error boundary (ODP-PGAP-SHELL-001, acceptance §7).
 *
 * Next renders this when a route segment throws. It must offer recovery
 * (`reset()` re-runs the segment) rather than stranding the operator, and it
 * surfaces `digest` — the only correlator between what the operator saw and the
 * server log line.
 */
import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@oday-plus/ui";
import { ShellState } from "../../features/shell/ShellStates.tsx";

export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surfacing the digest is what makes an operator's report actionable.
    console.error("shell route error", { digest: error.digest, message: error.message });
  }, [error]);

  return (
    <div className="odp-content" data-testid="route-error">
      <ShellState
        kind="error"
        correlationId={error.digest}
        testId="shell-state-error"
        actions={
          <>
            <Button onClick={() => reset()} data-testid="route-error-retry">
              重試
            </Button>
            <Link href="/" data-testid="route-error-home">
              回到總覽
            </Link>
          </>
        }
      />
    </div>
  );
}
