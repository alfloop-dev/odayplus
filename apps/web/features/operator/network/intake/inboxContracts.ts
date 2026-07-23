import type {
  AssistedIntake,
  AssignmentReceipt,
  MatchOutcome,
} from "@oday-plus/openapi-client";

export type IntakeInboxLocation = {
  latitude: number;
  longitude: number;
  confidence?: number | string | null;
  source: string;
};

export type InboxIntakeRecord = AssistedIntake & {
  assignedAreaId?: string | null;
  lastObservedAt?: string | null;
  lastUpdatedAt?: string | null;
  location?: IntakeInboxLocation | null;
  maskedFields?: string[];
  needsReview?: boolean;
  restrictedData?: boolean;
  retryable?: boolean;
  issue?: string | null;
  matchOutcome?: MatchOutcome | null;
};

export type IntakeInboxSavedView = {
  id: string;
  label: string;
  count?: number | null;
};

export type IntakeInboxHeatZone = {
  id: string;
  label: string;
};

export type IntakeInboxBootstrapContext = {
  tenantId: string;
  scopeLabel: string;
  ownerLabel: string;
  submitterLabel: string;
  heatZones: IntakeInboxHeatZone[];
};

export type IntakeInboxQueryContract = {
  areaId?: string;
  assignmentStatus?: string;
  cursor?: string;
  failed?: string;
  heatZoneId?: string;
  intakeMethod?: string;
  intakeStage?: string;
  matchOutcome?: string;
  needsReview?: string;
  observedFrom?: string;
  observedTo?: string;
  owner?: string;
  page: number;
  pageSize: number;
  quarantined?: string;
  restrictedData?: string;
  retryable?: string;
  savedView?: string;
  search?: string;
  selectedHeatZoneId?: string;
  slaState?: string;
  sortBy: string;
  sortOrder: "asc" | "desc";
  sourceId?: string;
  submittedBy?: string;
  updatedFrom?: string;
  updatedTo?: string;
};

export type IntakeInboxPageContract = {
  items: InboxIntakeRecord[];
  total: number;
  page: number;
  pageSize: number;
  evidenceState: "complete" | "partial" | "degraded";
  nextCursor?: string | null;
  previousCursor?: string | null;
};

export type AuthoritativeInboxError = {
  status?: number;
  code: string;
  summary: string;
  nextAction: string;
  correlationId: string | null;
  occurredAt: string | null;
  retryable: boolean;
  currentVersion?: string | number | null;
  currentState?: string | null;
};

export type InboxCommandResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: AuthoritativeInboxError };

export type ClaimIntakeCommand = (
  intakeId: string,
) => Promise<InboxCommandResult<AssignmentReceipt>>;
