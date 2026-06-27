/**
 * Navigation contracts — the shape the Sidebar consumes (component contracts
 * §3.3). Permission/role gating and read-only marking are first-class so the
 * shell can omit items a role cannot access and badge read-only ones.
 */
import type { Role } from "./roles.ts";

/** Stable identifier for each top-level OpsBoard work area / route. */
export type RouteKey =
  | "home"
  | "tasks"
  | "search"
  | "expansion"
  | "operations"
  | "interventions"
  | "pricing"
  | "adlift"
  | "avm"
  | "netplan"
  | "learning"
  | "audit"
  | "admin"
  | "franchisee";

export type NavItem = {
  key: RouteKey;
  label: string;
  href: string;
  /** lucide-style icon name; resolved by the shell, kept data-only here. */
  icon?: string;
  /** roles allowed to see this item; empty/undefined = visible to all roles. */
  roles?: Role[];
  /** roles that may view but not act — rendered with a read-only marker. */
  readOnlyRoles?: Role[];
  /** one-line zh-TW description for placeholder screens. */
  description?: string;
  children?: NavItem[];
};
