"use client";
/**
 * Sidebar — role-aware workspace navigation.
 *
 * Items the active role cannot access are NOT rendered (omitted, not disabled);
 * read-only items show a read-only marker (contracts §3.3, §6.2). Active item
 * uses aria-current="page".
 */
import type { ComponentType, ReactNode } from "react";
import type { NavItem } from "@oday-plus/domain-types";
import { NAV_ITEMS } from "../nav/routes.ts";
import { navForRole, isReadOnlyForRole } from "../nav/filterNav.ts";
import { useShell } from "./ShellContext.tsx";

/** Minimal link contract so the package stays framework-agnostic. */
export type LinkComponent = ComponentType<{
  href: string;
  className?: string;
  "aria-current"?: "page" | undefined;
  "data-testid"?: string;
  children: ReactNode;
}>;

const DefaultLink: LinkComponent = ({ href, children, ...rest }) => (
  <a href={href} {...rest}>
    {children}
  </a>
);

export type SidebarProps = {
  activeHref: string;
  items?: NavItem[];
  collapsed?: boolean;
  linkComponent?: LinkComponent;
};

function isActive(href: string, activeHref: string): boolean {
  if (href === "/") return activeHref === "/";
  return activeHref === href || activeHref.startsWith(href + "/");
}

export function Sidebar({
  activeHref,
  items = NAV_ITEMS,
  collapsed = false,
  linkComponent,
}: SidebarProps) {
  const { role } = useShell();
  const Link = linkComponent ?? DefaultLink;
  const visible = navForRole(items, role);

  return (
    <nav
      className="odp-sidebar"
      aria-label="工作區導覽"
      data-collapsed={collapsed}
      data-testid="sidebar"
    >
      <ul className="odp-sidebar__list">
        {visible.map((item) => {
          const active = isActive(item.href, activeHref);
          const readOnly = isReadOnlyForRole(item, role);
          return (
            <li key={item.key}>
              <Link
                href={item.href}
                className="odp-navlink"
                aria-current={active ? "page" : undefined}
                data-testid={`nav-${item.key}`}
              >
                <span className="odp-navlink__icon" aria-hidden="true">
                  {(item.icon ?? item.key).slice(0, 2)}
                </span>
                {!collapsed ? (
                  <>
                    <span className="odp-navlink__label">{item.label}</span>
                    {readOnly ? (
                      <span className="odp-navlink__ro" title="唯讀">
                        唯讀
                      </span>
                    ) : null}
                  </>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
