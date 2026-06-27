/**
 * Role-aware navigation filtering (component contracts §3.3, §6.2).
 * Items with no `roles` are visible to everyone. Items the role cannot access
 * are omitted entirely (not disabled).
 */
import type { NavItem, Role } from "@oday-plus/domain-types";

export function isVisibleForRole(item: NavItem, role: Role): boolean {
  if (!item.roles || item.roles.length === 0) return true;
  return item.roles.includes(role);
}

export function isReadOnlyForRole(item: NavItem, role: Role): boolean {
  return Boolean(item.readOnlyRoles?.includes(role));
}

export function navForRole(items: NavItem[], role: Role): NavItem[] {
  return items
    .filter((item) => isVisibleForRole(item, role))
    .map((item) =>
      item.children
        ? { ...item, children: navForRole(item.children, role) }
        : item,
    );
}
