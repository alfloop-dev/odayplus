/**
 * Shared structural types used by data-bearing components.
 * Source of truth: docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md §2.
 */
import type { DataStatus } from "./status.ts";

/** Uncertainty interval — predictions/valuations must never show only p50. */
export type Interval = { p10: number; p50: number; p90: number; unit?: string };

export type Confidence = {
  level: "high" | "medium" | "low";
  reasons: string[];
};

export type ApiError = {
  code: string;
  message: string;
  correlation_id: string;
  retryable: boolean;
  details?: unknown;
  field_errors?: { field: string; message: string }[];
};

export type AuditMeta = {
  actor: string;
  timestamp: string;
  reason?: string;
  modelVersion?: string;
  policyVersion?: string;
  featureSnapshotTime?: string;
  before?: unknown;
  after?: unknown;
};

/** Data-quality envelope accepted by every data-bearing component (contracts §1). */
export type DataQuality = {
  status: DataStatus;
  snapshotTime: string;
  sources: string[];
  warnings: string[];
};

/** Field-level permission visibility for sensitive columns (visual system §10.4). */
export type FieldVisibility = "visible" | "masked" | "aggregated" | "hidden";

export type UserRef = {
  id: string;
  name: string;
  /** roles the user currently holds — drives role-aware navigation. */
  roles: import("./roles.ts").Role[];
};

export type WorkspaceRef = {
  id: string;
  label: string;
};
