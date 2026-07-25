/**
 * Operator Console (React). STATUS as of 2026-07-25:
 * This IS the CI-verified R5 / Package-7 implementation — `scripts/e2e/check_product_grade_ci_gates.py`
 * confirms all 37 R5 screen labels are implemented here. It is NOT "divergent" and must NOT be
 * deleted or rewritten from scratch.
 *
 * Remaining work is a DELTA, tracked in
 * docs/design/OPERATOR_CONSOLE_R7_UPLIFT_EXECUTION_TASKS_2026-07-25.md:
 *   - fix web->API identity federation (deployed /operator shows the no-data 401 fail-closed gate,
 *     NOT a wrong design) — ODP-OC-R7-AUTH-001
 *   - uplift R5(37 labels) -> R7/Package 10 (40 labels) + VDC-001..005 — ODP-OC-R7-FE-DELTA-001
 *
 * The deployed empty "OPERATOR_DATA_LOADING" gate is a data/auth problem, not a design problem.
 * Do not re-diagnose this as "wrong UI" and rebuild it.
 */
export * from "./types";
export * from "./fixtures";
export * from "./state";
export * from "./adapters";
export * from "./policy";
export * from "./OperatorConsole";
export * from "./DesignAlignedWorkspaces";
export * from "./GovernanceWorkspace";
export * from "./NetworkFindAreasWorkspace";

