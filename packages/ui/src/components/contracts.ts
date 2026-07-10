import type { ReactNode } from "react";
import type {
  ApiError,
  AuditMeta,
  Confidence,
  DataQuality,
  EntityRef,
  Factor,
} from "@oday-plus/domain-types";
import type {
  DataStatus,
  DecisionStatus,
  ModelStatus,
  RiskLevel,
  StatusTone,
} from "@oday-plus/domain-types";

export type Density = "comfortable" | "compact" | "presentation";

export type PermissionAware = {
  permission?: string;
  permitted?: boolean;
  disabledReason?: string;
};

export type AsyncContract = {
  loading?: boolean;
  error?: ApiError;
  emptyState?: EmptyStateContract;
};

export type DataQualityAware = {
  dataQuality?: DataQuality;
};

export type ActionSpec = PermissionAware & {
  id: string;
  label: string;
  tone?: StatusTone | "danger" | "warning" | "success";
  icon?: ReactNode;
  href?: string;
  onSelect?: () => void;
  loading?: boolean;
  requiresReason?: boolean;
  requiresAudit?: boolean;
};

export type FilterOption = {
  label: string;
  value: string;
};

export type FilterSpec = {
  id: string;
  label: string;
  value?: string;
  placeholder?: string;
  options?: FilterOption[];
  active?: boolean;
  disabled?: boolean;
  onChange?: (value: string) => void;
};

export type SavedViewSpec = {
  id: string;
  label: string;
  active?: boolean;
  onSelect?: () => void;
};

export type EmptyStateContract = {
  title: string;
  description: string;
  nextActions: ActionSpec[];
  docLink?: { label: string; href: string };
};

export type BadgeSpec = {
  label: string;
  tone?: StatusTone;
  marker?: string;
};

export type TableSort = {
  columnId: string;
  direction: "asc" | "desc";
};

export type TableColumnSpec<TData> = {
  id: string;
  header: ReactNode;
  accessor?: keyof TData;
  render?: (row: TData) => ReactNode;
  sortable?: boolean;
  masked?: boolean;
  align?: "start" | "center" | "end";
};

export type TablePagination = {
  server: true;
  page: number;
  pageSize: number;
  total: number;
  onPageChange?: (page: number) => void;
};

export type TableSelection<TId extends string | number = string> = {
  selectedIds: readonly TId[];
  onChange: (selectedIds: readonly TId[]) => void;
  getRowId: (rowIndex: number) => TId;
};

export type FieldError = {
  field: string;
  message: string;
};

export type FormSchema<TValues> = {
  safeParse?: (values: TValues) => { success: boolean; error?: unknown };
  parse?: (values: TValues) => TValues;
};

export type TabSpec = PermissionAware & {
  id: string;
  label: string;
  badge?: string | number;
  panel: ReactNode;
};

export type TimelineNodeSpec = {
  id: string;
  timestamp: string;
  actor: string;
  eventType: string;
  status: string;
  description: string;
  relatedArtifact?: EntityRef;
  href?: string;
};

export type ApprovalDecision = "APPROVE" | "REJECT" | "REQUEST_REVISION";

export type ApprovalRecommendation = {
  text: string;
  modelVersion?: string;
  policyVersion?: string;
  generatedAt?: string;
  requiresApproval: boolean;
};

export type ApprovalSubmitPayload = {
  decision: ApprovalDecision;
  reason: string;
  riskAcknowledged: boolean;
  attachments?: File[];
};

export type ApprovalSubmitResult = {
  decisionId: string;
  auditEventId?: string;
  status?: DecisionStatus;
};

export type EvidenceComparable = {
  id: string;
  label: string;
  summary?: string;
  score?: string | number;
  href?: string;
};

export type EvidenceTrend = {
  label: string;
  value: string;
  direction?: "up" | "down" | "flat";
};

export type CoreUiComponentKey =
  | "Toolbar"
  | "FilterBar"
  | "Drawer"
  | "Button"
  | "Card"
  | "Table"
  | "Form"
  | "Modal"
  | "Tabs"
  | "Timeline"
  | "Toast"
  | "Tooltip"
  | "CommandPalette"
  | "EmptyState"
  | "DataStatusBadge"
  | "ModelVersionBadge"
  | "ApprovalPanel"
  | "AuditMetadata"
  | "AlertChip"
  | "EvidencePanel";

export const CORE_UI_COMPONENT_KEYS: readonly CoreUiComponentKey[] = [
  "Toolbar",
  "FilterBar",
  "Drawer",
  "Button",
  "Card",
  "Table",
  "Form",
  "Modal",
  "Tabs",
  "Timeline",
  "Toast",
  "Tooltip",
  "CommandPalette",
  "EmptyState",
  "DataStatusBadge",
  "ModelVersionBadge",
  "ApprovalPanel",
  "AuditMetadata",
  "AlertChip",
  "EvidencePanel",
] as const;

export type {
  ApiError,
  AuditMeta,
  Confidence,
  DataQuality,
  DataStatus,
  DecisionStatus,
  EntityRef,
  Factor,
  ModelStatus,
  RiskLevel,
  StatusTone,
};
