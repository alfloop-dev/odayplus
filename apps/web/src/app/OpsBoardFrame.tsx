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
