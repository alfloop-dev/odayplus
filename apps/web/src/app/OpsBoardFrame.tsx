"use client";
/**
 * Client frame that wires Next.js routing into the framework-agnostic shell:
 * - active route highlight via usePathname
 * - next/link adapter for the Sidebar
 * - ShellProvider holds the placeholder role / theme / density state
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import {
  AppShell,
  GlobalHeader,
  Sidebar,
  ShellProvider,
  type LinkComponent,
} from "@oday-plus/ui";

const NavLink: LinkComponent = ({ href, children, ...rest }) => (
  <Link href={href} {...rest}>
    {children}
  </Link>
);

export function OpsBoardFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";

  // The Operator Console (/operator) is a full-bleed surface that ships its own
  // top navigation ("Top Navigation" in the canonical package-6 design). It must
  // NOT be double-wrapped in the OpsBoard sidebar + global header, which the
  // design does not have and which collides at constrained widths.
  // Shared shell parity fix for every operator workspace (ODP-OC-R4-004 Growth,
  // ODP-OC-R4-005 Network, …). ShellProvider is kept so @oday-plus/ui components
  // still resolve role/theme/density context.
  if (pathname.startsWith("/operator")) {
    return <ShellProvider initialRole="ops_manager">{children}</ShellProvider>;
  }

  return (
    <ShellProvider initialRole="ops_manager">
      <AppShell
        header={<GlobalHeader environment="dev" taskCount={7} notificationCount={3} />}
        sidebar={<Sidebar activeHref={pathname} linkComponent={NavLink} />}
      >
        {children}
      </AppShell>
    </ShellProvider>
  );
}
