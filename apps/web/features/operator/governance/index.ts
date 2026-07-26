/**
 * Govern workspace feature sub-module barrel.
 *
 * Re-exports the public surface of the Govern workspace component and its API
 * loader so consumers import from this directory rather than individual files.
 *
 * Owned by: ODP-OC-R4-009
 * Composes with: apps/web/features/operator/GovernanceWorkspace.tsx,
 *                apps/web/features/operator/governance/governanceLoader.ts
 */

export { GovernanceWorkspace } from "../GovernanceWorkspace";
export type { GovernanceWorkspaceProps } from "../GovernanceWorkspace";

export {
  fetchGovernanceSnapshot,
  submitGovernanceDecision,
  exportEvidencePackage,
} from "./governanceLoader";

export type {
  GovernanceSnapshot,
  GovernanceStatusBoard,
  GovernanceStatusRow,
  GovernanceEvidencePackage,
  GovernanceDecisionResult,
  EvidencePackageRecord,
} from "./governanceLoader";
