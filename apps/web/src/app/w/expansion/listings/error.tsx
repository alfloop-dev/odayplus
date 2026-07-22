"use client";

import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@oday-plus/ui";
import { ShellState } from "../../../../../features/shell/ShellStates.tsx";

export default function ListingsRouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("listings route error", { digest: error.digest, message: error.message });
  }, [error]);

  return (
    <div className="odp-content" data-testid="listings-route-error">
      <ShellState
        kind="error"
        correlationId={error.digest}
        testId="listings-state-error"
        actions={
          <>
            <Button onClick={() => reset()} data-testid="listings-error-retry">
              重試
            </Button>
            <Link href="/w/expansion" data-testid="listings-error-home">
              回到展店選址
            </Link>
          </>
        }
      />
    </div>
  );
}
