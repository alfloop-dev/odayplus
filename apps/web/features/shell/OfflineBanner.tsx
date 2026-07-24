"use client";

/**
 * Offline surface (ODP-PGAP-SHELL-001, acceptance §7).
 *
 * `navigator.onLine` only proves the browser has *a* link, not that the backend
 * is reachable — but a false negative here is cheap (a banner) while a false
 * positive is not (an operator trusting a stale screen). So this reports the
 * browser's own state and says plainly that what is on screen may be stale,
 * rather than claiming the data is current.
 *
 * When the connection returns the route is refreshed, so the shell recovers
 * without the operator reloading by hand.
 */
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ShellState } from "./ShellStates.tsx";

export function OfflineBanner() {
  const router = useRouter();
  // Start online: the server render has no navigator, and assuming offline
  // would flash a false banner on every first paint.
  const [online, setOnline] = useState(true);

  useEffect(() => {
    setOnline(navigator.onLine);

    function goOnline() {
      setOnline(true);
      // Recovery: re-fetch the server components the outage may have starved.
      router.refresh();
    }
    function goOffline() {
      setOnline(false);
    }

    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, [router]);

  if (online) return null;

  return (
    <div data-testid="offline-banner">
      <ShellState kind="offline" testId="shell-state-offline" />
    </div>
  );
}
