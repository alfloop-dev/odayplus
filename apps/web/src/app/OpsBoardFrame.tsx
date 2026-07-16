"use client";
/**
 * Client frame that wires Next.js routing into the framework-agnostic shell:
 * - active route highlight via usePathname
 * - next/link adapter for the Sidebar
 * - ShellProvider holds the role / theme / density state
 * - live header counts + offline detection
 *
 * Header counts (ODP-PGAP-SHELL-001): these used to be hardcoded
 * `taskCount={7} notificationCount={3}` — a POC fixture that told every
 * operator they had seven tasks regardless of the truth. They are now read from
 * the same aggregate the first screen renders. They load after mount rather
 * than in the server layout, so no route blocks its render on them, and when
 * they cannot be fetched they are omitted rather than guessed: a badge that is
 * always wrong is worse than no badge.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import {
  AppShell,
  GlobalHeader,
  type GlobalHeaderProps,
  Sidebar,
  ShellProvider,
  type LinkComponent,
} from "@oday-plus/ui";
import { OfflineBanner } from "../../features/shell/OfflineBanner.tsx";
import { shellClient } from "../../features/shell/shellClient.ts";

export type ShellEnvironment = GlobalHeaderProps["environment"];

const NavLink: LinkComponent = ({ href, children, ...rest }) => (
  <Link href={href} {...rest}>
    {children}
  </Link>
);

type Counts = { taskCount?: number; notificationCount?: number };

function useHeaderCounts(enabled: boolean): Counts {
  const [counts, setCounts] = useState<Counts>({});

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;

    void (async () => {
      try {
        const client = shellClient();
        if (!client) return;
        const home = await client.getShellHome();
        if (cancelled) return;
        setCounts({
          taskCount: home.status.openTasks,
          notificationCount: home.status.unacknowledgedNotifications,
        });
      } catch {
        // Leave the counts absent. The page below surfaces the real error
        // state; the header must not invent a number to fill the badge.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return counts;
}

export function OpsBoardFrame({
  children,
  environment,
}: {
  children: ReactNode;
  environment: ShellEnvironment;
}) {
  const pathname = usePathname() || "/";

  // The Operator Console (/operator) is a full-bleed surface that ships its own
  // top navigation ("Top Navigation" in the canonical package-6 design). It must
  // NOT be double-wrapped in the OpsBoard sidebar + global header, which the
  // design does not have and which collides at constrained widths.
  // Shared shell parity fix for every operator workspace (ODP-OC-R4-004 Growth,
  // ODP-OC-R4-005 Network, …). ShellProvider is kept so @oday-plus/ui components
  // still resolve role/theme/density context.
  const isOperator = pathname.startsWith("/operator");

  // The franchisee portal is not an operator surface (ODP-PGAP-SHELL-001).
  // Wrapping it in the OpsBoard chrome would (a) show a franchisee the operator
  // navigation — Task Center, 平台管理, 治理稽核 — which is exactly the
  // operator-only data this task must keep off their screen, and (b) hand them
  // a fixed desktop sidebar that leaves ~115px of content on a phone, when the
  // franchisee portal is a mobile-first product. It ships its own single-column
  // layout instead.
  const isFranchisee = pathname.startsWith("/franchisee");

  // Only the OpsBoard chrome renders the count badges, and a franchisee has no
  // authorization for the operator aggregate — asking would just log a 403.
  const counts = useHeaderCounts(!isOperator && !isFranchisee);

  if (isOperator) {
    return <ShellProvider initialRole="ops_manager">{children}</ShellProvider>;
  }

  if (isFranchisee) {
    return <ShellProvider initialRole="franchisee">{children}</ShellProvider>;
  }

  return (
    <ShellProvider initialRole="ops_manager">
      <AppShell
        header={
          <GlobalHeader
            environment={environment}
            taskCount={counts.taskCount}
            notificationCount={counts.notificationCount}
          />
        }
        sidebar={<Sidebar activeHref={pathname} linkComponent={NavLink} />}
      >
        <OfflineBanner />
        {children}
      </AppShell>
    </ShellProvider>
  );
}
