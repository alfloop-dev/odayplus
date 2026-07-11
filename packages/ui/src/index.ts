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
export { Button, type ButtonProps, type ButtonVariant } from "./components/Button.tsx";
export { Card, type CardProps } from "./components/Card.tsx";
export {
  Toolbar,
  FilterBar,
  type ToolbarProps,
} from "./components/Toolbar.tsx";
export { Drawer, type DrawerProps } from "./components/Drawer.tsx";
export { Table, type TableProps } from "./components/Table.tsx";
export { Form, type FormProps } from "./components/Form.tsx";
export { Modal, type ModalProps } from "./components/Modal.tsx";
export { Tabs, type TabsProps } from "./components/Tabs.tsx";
export { Timeline, type TimelineProps } from "./components/Timeline.tsx";
export { Toast, type ToastProps } from "./components/Toast.tsx";
export { Tooltip, type TooltipProps } from "./components/Tooltip.tsx";
export {
  CommandPalette,
  type CommandPaletteProps,
} from "./components/CommandPalette.tsx";
export { EmptyState, type EmptyStateProps } from "./components/EmptyState.tsx";
export {
  DataStatusBadge,
  ModelVersionBadge,
  AlertChip,
  type DataStatusBadgeProps,
  type ModelVersionBadgeProps,
  type AlertChipProps,
} from "./components/StatusBadges.tsx";
export {
  ApprovalPanel,
  type ApprovalPanelProps,
} from "./components/ApprovalPanel.tsx";
export {
  AuditMetadata,
  type AuditMetadataProps,
} from "./components/AuditMetadata.tsx";
export { EvidencePanel, type EvidencePanelProps } from "./components/EvidencePanel.tsx";
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
export {
  CORE_UI_COMPONENT_KEYS,
  type ActionSpec,
  type ApprovalDecision,
  type ApprovalRecommendation,
  type ApprovalSubmitPayload,
  type ApprovalSubmitResult,
  type AsyncContract,
  type BadgeSpec,
  type CoreUiComponentKey,
  type DataQualityAware,
  type Density,
  type EmptyStateContract,
  type EvidenceComparable,
  type EvidenceTrend,
  type FieldError,
  type FilterOption,
  type FilterSpec,
  type FormSchema,
  type PermissionAware,
  type SavedViewSpec,
  type TabSpec,
  type TableColumnSpec,
  type TablePagination,
  type TableSelection,
  type TableSort,
  type TimelineNodeSpec,
} from "./components/contracts.ts";

export { NAV_ITEMS, NAV_BY_KEY, ROUTE_KEYS } from "./nav/routes.ts";
export {
  navForRole,
  isVisibleForRole,
  isReadOnlyForRole,
} from "./nav/filterNav.ts";
