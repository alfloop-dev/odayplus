/**
 * Navigation contracts shared by role-aware ODay Plus surfaces.
 * Permission and read-only metadata remain data-only domain concerns.
 */
import type { Role } from "./roles.ts";

/** Stable identifier for each top-level ODay Plus work area. */
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
  /** Lucide-style icon name, kept data-only here. */
  icon?: string;
  /** roles allowed to see this item; empty/undefined = visible to all roles. */
  roles?: Role[];
  /** roles that may view but not act — rendered with a read-only marker. */
  readOnlyRoles?: Role[];
  /** one-line zh-TW description for placeholder screens. */
  description?: string;
  children?: NavItem[];
};
