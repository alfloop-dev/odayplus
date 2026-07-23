"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@oday-plus/ui";
import { ShellState } from "../../../../../../../features/shell/ShellStates.tsx";

export default function IntakeDetailRouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("intake detail route error", {
      digest: error.digest,
      message: error.message,
    });
  }, [error]);

  return (
    <div className="odp-content" data-testid="intake-route-error">
      <ShellState
        actions={
          <>
            <Button data-testid="intake-route-error-retry" onClick={() => reset()}>
              重新載入
            </Button>
            <Link data-testid="intake-route-error-inbox" href="/w/expansion/listings">
              返回 Listing 收件匣
            </Link>
          </>
        }
        correlationId={error.digest}
        kind="error"
        testId="intake-route-state-error"
      />
    </div>
  );
}
