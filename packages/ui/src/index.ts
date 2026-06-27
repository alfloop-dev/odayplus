/**
 * @oday-plus/ui — OpsBoard shell + design-system React components.
 * Token-only (visual system §10.1); consumes @oday-plus/design-tokens and
 * @oday-plus/domain-types. Import the stylesheet once at the app root:
 *   import "@oday-plus/ui/styles/shell.css";
 */
export const designSystemName = "ODay Plus UI";

export { AppShell, type AppShellProps } from "./components/AppShell.tsx";
export {
  GlobalHeader,
  type GlobalHeaderProps,
} from "./components/GlobalHeader.tsx";
export {
  Sidebar,
  type SidebarProps,
  type LinkComponent,
} from "./components/Sidebar.tsx";
export {
  PageHeader,
  type PageHeaderProps,
  type BreadcrumbItem,
} from "./components/PageHeader.tsx";
export { Badge, type BadgeProps } from "./components/Badge.tsx";
export {
  ModulePlaceholder,
  type ModulePlaceholderProps,
} from "./components/ModulePlaceholder.tsx";
export {
  ShellProvider,
  useShell,
  type ShellState,
  type ShellProviderProps,
} from "./components/ShellContext.tsx";

export { NAV_ITEMS, NAV_BY_KEY, ROUTE_KEYS } from "./nav/routes.ts";
export {
  navForRole,
  isVisibleForRole,
  isReadOnlyForRole,
} from "./nav/filterNav.ts";
