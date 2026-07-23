import {
  MATCH_OUTCOME_LABEL,
  SOURCE_POLICY_LABEL,
  type AssistedIntake,
  type CanonicalAuditReference,
  type CanonicalIntakeInboxBootstrap,
  type CanonicalIntakeRuntimeDetail,
  type CanonicalIntakeSummary,
  type CanonicalIdentityDecisionReceipt,
  type CanonicalLifecycleReceipt,
  type CanonicalMatchCaseDetail,
  type CanonicalMatchGraphPlan,
  type CanonicalSavedView,
  type IntakeFieldCell,
  type IntakeFieldValue,
  type IntakeInboxPage,
  type IntakeInboxQuery,
  type IntakeStage,
  type MatchOutcome,
  type SourcePolicyState,
} from "@oday-plus/openapi-client";
import type {
  InboxIntakeRecord,
  IntakeInboxBootstrapContext,
  IntakeInboxPageContract,
  IntakeInboxQueryContract,
  IntakeInboxSavedView,
} from "./inboxContracts";
import type {
  IdentityActor,
  IdentityComparisonContract,
  IdentityComparisonField,
  IdentityComparisonFieldKey,
  IdentityDecisionReceipt,
  IdentityGraphNode,
  IdentityGraphPlan,
  IdentityGraphSnapshot,
  IdentityReviewWorkflow,
} from "./identityTypes";
import type { StructuredAuditEvent } from "./evidenceContracts";
import type {
  IntakeLifecycleAction,
  IntakeLifecycleSnapshot,
  PersistedLifecycleTransition,
} from "./useIntakeLifecycle";

const INTAKE_STATES = new Set([
  "SUBMITTED",
  "CHECKING_IDENTITY",
  "CHECKING_SOURCE_POLICY",
  "AWAITING_ASSISTED_ENTRY",
  "RETRIEVING",
  "PARSING",
  "MATCHING",
  "NEEDS_REVIEW",
  "READY",
  "QUARANTINED",
  "FAILED",
  "CANCELLED",
]);

const SOURCE_POLICY_STATES = new Set([
  "APPROVED_RETRIEVAL",
  "ASSISTED_ENTRY_ONLY",
  "AUTH_REQUIRED",
  "SOURCE_BLOCKED",
  "POLICY_UNKNOWN",
]);

const MATCH_OUTCOMES = new Set([
  "NEW",
  "EXACT_DUPLICATE",
  "REVISION",
  "POSSIBLE_MATCH",
  "QUARANTINED",
]);

function intakeStage(value: string): IntakeStage {
  return (INTAKE_STATES.has(value) ? value : "FAILED") as IntakeStage;
}

function policyState(value: string | null): SourcePolicyState {
  return (
    value && SOURCE_POLICY_STATES.has(value) ? value : "POLICY_UNKNOWN"
  ) as SourcePolicyState;
}

function matchOutcome(value: string | null): MatchOutcome | null {
  return value && MATCH_OUTCOMES.has(value) ? (value as MatchOutcome) : null;
}

function scalar(value: unknown): IntakeFieldValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  return value === undefined ? null : JSON.stringify(value);
}

function fieldLabel(path: string): string {
  const leaf = path.split(".").at(-1) ?? path;
  return leaf
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function canonicalFields(detail: CanonicalIntakeRuntimeDetail): Record<string, IntakeFieldCell> {
  return Object.fromEntries(
    detail.fields.map((field) => [
      field.field_path,
      {
        key: field.field_path,
        label: fieldLabel(field.field_path),
        sourceValue: scalar(field.parsed),
        normalizedValue: scalar(field.normalized),
        correctedValue: scalar(field.corrected),
        correctionReason: field.correction_reason,
        identity: /provider|source|address|rent|price|area/i.test(field.field_path),
        lowConfidence: field.confidence !== null && field.confidence < 0.7,
        masked: field.masked,
        mask_reason_code: field.mask_reason_code ?? undefined,
      },
    ]),
  );
}

function canonicalMatch(detail: CanonicalIntakeRuntimeDetail) {
  const match = detail.match_case;
  const outcome = matchOutcome(detail.match_outcome ?? match?.outcome ?? null);
  if (!match || !outcome) return null;
  const agreeingSignals = match.signals
    .filter((signal) => signal.agrees)
    .map((signal) => ({
      key: signal.key,
      label: signal.label,
      agrees: true,
      detail: signal.detail,
    }));
  const contradictingSignals = match.signals
    .filter((signal) => !signal.agrees)
    .map((signal) => ({
      key: signal.key,
      label: signal.label,
      agrees: false,
      detail: signal.detail,
    }));
  return {
    outcome,
    outcomeLabel: MATCH_OUTCOME_LABEL[outcome],
    confidence: match.confidence,
    targetListingId: match.target_listing_id,
    agreeingSignals,
    contradictingSignals,
    summary: match.summary,
  };
}

function canonicalAudit(detail: CanonicalIntakeRuntimeDetail) {
  return detail.audit.map((event) => ({
    id: event.audit_event_id,
    occurredAt: event.occurred_at,
    actorRoleId: event.actor_role ?? "",
    actorName: event.actor ?? "",
    action: event.action,
    targetId: detail.intake_id,
    message: event.reason_code ?? event.action,
    correlationId: event.correlation_id,
    metadata: {
      beforeAfter: beforeAfter(event),
      sourceSnapshotId: event.source_snapshot_id,
      parserVersion: event.parser_version,
      relatedIds: event.related_ids,
      evidenceState: event.evidence_state,
      result: event.result,
      resourceVersion: event.resource_version,
    },
  }));
}

export function canonicalDetailToRecord(
  detail: CanonicalIntakeRuntimeDetail,
): AssistedIntake {
  const policy = policyState(detail.policy_state);
  const scope = detail.scope as Record<string, unknown>;
  const assignment = detail.lifecycle.assignment;
  const sla = detail.lifecycle.sla;
  const job = detail.lifecycle.job;
  return {
    id: detail.intake_id,
    originalUrl: detail.original_url ?? "",
    canonicalUrl: detail.canonical_url ?? detail.original_url ?? "",
    submitter: detail.submitted_by,
    owner:
      detail.lifecycle.assignment?.owner_subject_id ??
      detail.assigned_to ??
      detail.submitted_by,
    heatZoneId:
      typeof scope.heat_zone_id === "string" ? scope.heat_zone_id : null,
    intakeMethod: detail.intake_method as AssistedIntake["intakeMethod"],
    stage: intakeStage(detail.state),
    sourceId: detail.source_id ?? "",
    policy,
    policyLabel: SOURCE_POLICY_LABEL[policy],
    policyReason: "",
    rawSnapshot: null,
    snapshotId: detail.source_snapshot_id,
    capturedAt: detail.evidence.captured_at,
    parserVersion: detail.evidence.parser_version ?? "",
    correlationId: detail.evidence.correlation_id,
    matchCaseId: detail.match_case_id,
    jobId: job?.job_id ?? null,
    parsedFields: canonicalFields(detail),
    matchResult: canonicalMatch(detail),
    auditEvents: canonicalAudit(detail),
    version: detail.version,
    assignmentId: assignment?.assignment_id ?? null,
    assignmentStatus: assignment?.status ?? null,
    assignmentVersion: assignment?.version ?? null,
    slaInstanceId: sla?.sla_instance_id ?? null,
    slaState: sla?.state ?? null,
    slaVersion: sla?.version ?? null,
    dueAt: detail.due_at ?? sla?.due_at ?? assignment?.due_at ?? null,
  };
}

export function canonicalSummaryToRecord(
  summary: CanonicalIntakeSummary,
): InboxIntakeRecord {
  const policy = policyState(summary.policy_state);
  return {
    id: summary.intake_id,
    originalUrl: summary.original_url ?? "",
    canonicalUrl: summary.canonical_url ?? summary.original_url ?? "",
    submitter: summary.submitted_by,
    owner: summary.owner_subject_id ?? summary.assigned_to ?? "",
    heatZoneId: summary.scope.heat_zone_id ?? summary.location.heat_zone_id,
    intakeMethod: summary.intake_method as AssistedIntake["intakeMethod"],
    stage: intakeStage(summary.state),
    sourceId: summary.source_id ?? "",
    policy,
    policyLabel: SOURCE_POLICY_LABEL[policy],
    policyReason: "",
    rawSnapshot: null,
    snapshotId: null,
    capturedAt: summary.last_observed_at,
    parserVersion: "",
    correlationId: null,
    parsedFields: {},
    matchResult: null,
    matchOutcome: matchOutcome(summary.match_outcome),
    auditEvents: [],
    version: summary.version,
    assignmentId: summary.assignment_id,
    assignmentStatus: summary.assignment_status,
    assignmentVersion: summary.assignment_version,
    slaInstanceId: summary.sla_instance_id,
    slaState: summary.sla_state,
    slaVersion: summary.sla_version,
    dueAt: summary.due_at,
    assignedAreaId:
      summary.scope.assigned_area_id ?? summary.location.assigned_area_id,
    lastObservedAt: summary.last_observed_at,
    lastUpdatedAt: summary.updated_at,
    maskedFields: summary.masked_fields,
    needsReview: summary.state === "NEEDS_REVIEW",
    restrictedData: summary.masking.restricted_data,
    retryable: summary.retryable,
    issue: summary.issue ?? summary.next_action,
  };
}

export function canonicalPageToInbox(
  page: IntakeInboxPage,
  currentPage: number,
): IntakeInboxPageContract {
  const items = page.items.map(canonicalSummaryToRecord);
  return {
    items,
    total: page.total_count,
    page: currentPage,
    pageSize: page.page_size,
    evidenceState: items.every(
      (item) =>
        item.policy === "ASSISTED_ENTRY_ONLY" ||
        item.policy === "AUTH_REQUIRED" ||
        item.capturedAt !== null,
    )
      ? "complete"
      : "partial",
    nextCursor: page.next_cursor,
  };
}

function booleanFilter(value?: string): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

export function inboxContractToCanonicalQuery(
  query: IntakeInboxQueryContract,
): IntakeInboxQuery {
  const sort =
    query.sortBy === "submittedAt"
      ? "submitted_at_desc"
      : query.sortBy === "dueAt"
        ? "due_at_asc"
        : query.sortBy === "stage"
          ? "status_asc"
          : "updated_at_desc";
  return {
    cursor: query.cursor || undefined,
    page_size: query.pageSize,
    sort,
    status: query.intakeStage ? [query.intakeStage] : undefined,
    intake_method: query.intakeMethod
      ? [query.intakeMethod as NonNullable<IntakeInboxQuery["intake_method"]>[number]]
      : undefined,
    source_id: query.sourceId ? [query.sourceId] : undefined,
    match_outcome: query.matchOutcome ? [query.matchOutcome] : undefined,
    submitted_by: query.submittedBy || undefined,
    needs_review: booleanFilter(query.needsReview),
    owner_subject_id: query.owner ? [query.owner] : undefined,
    assignment_status: query.assignmentStatus
      ? [
          query.assignmentStatus as NonNullable<
            IntakeInboxQuery["assignment_status"]
          >[number],
        ]
      : undefined,
    sla_state: query.slaState
      ? [query.slaState as NonNullable<IntakeInboxQuery["sla_state"]>[number]]
      : undefined,
    assigned_area_id: query.areaId || undefined,
    heat_zone_id: query.heatZoneId || query.selectedHeatZoneId || undefined,
    observed_from: query.observedFrom || undefined,
    observed_to: query.observedTo || undefined,
    updated_from: query.updatedFrom || undefined,
    updated_to: query.updatedTo || undefined,
    restricted_data: booleanFilter(query.restrictedData),
    quarantined: booleanFilter(query.quarantined),
    failed: booleanFilter(query.failed),
    retryable: booleanFilter(query.retryable),
    saved_view_id: query.savedView || undefined,
    q: query.search || undefined,
  };
}

export function canonicalBootstrapToInbox(
  bootstrap: CanonicalIntakeInboxBootstrap,
): IntakeInboxBootstrapContext {
  return {
    tenantId: bootstrap.tenant_id,
    scopeLabel: [
      ...bootstrap.scope.region_ids,
      ...bootstrap.scope.assigned_area_ids,
    ].join(" / ") || bootstrap.tenant_id,
    ownerLabel: bootstrap.subject_id,
    submitterLabel: bootstrap.subject_id,
    heatZones: bootstrap.heat_zones.map((zone) => ({
      id: zone.heat_zone_id,
      label: zone.label,
    })),
  };
}

export function canonicalSavedViewsToInbox(
  views: CanonicalSavedView[],
): IntakeInboxSavedView[] {
  return views.map((view) => ({
    id: view.saved_view_id,
    label: view.name,
  }));
}

function beforeAfter(event: CanonicalAuditReference) {
  const before =
    event.before && typeof event.before === "object"
      ? (event.before as Record<string, unknown>)
      : {};
  const after =
    event.after && typeof event.after === "object"
      ? (event.after as Record<string, unknown>)
      : {};
  return Object.fromEntries(
    [...new Set([...Object.keys(before), ...Object.keys(after)])].map((key) => [
      key,
      { before: scalar(before[key]), after: scalar(after[key]) },
    ]),
  );
}

export function canonicalAuditToStructured(
  detail: CanonicalIntakeRuntimeDetail,
): StructuredAuditEvent[] {
  return detail.audit.map((event) => ({
    id: event.audit_event_id,
    audit_event_id: event.audit_event_id,
    occurred_at: event.occurred_at,
    actor_name: event.actor,
    actor_role_id: event.actor_role,
    action: event.action,
    result: event.result,
    reason: event.reason_code,
    reason_code: event.reason_code,
    before_after: beforeAfter(event),
    source_snapshot_id: event.source_snapshot_id,
    parser_version: event.parser_version,
    related_ids: Object.fromEntries(
      Object.entries(event.related_ids).map(([key, value]) => [
        key,
        value === null || value === undefined ? null : String(value),
      ]),
    ),
    correlation_id: event.correlation_id,
    version: event.resource_version,
    evidence_state: event.evidence_state,
    message: event.action,
  }));
}

function lifecycleTransition(
  entry: CanonicalLifecycleReceipt,
  stream: PersistedLifecycleTransition["stream"],
): PersistedLifecycleTransition | null {
  const transitionId =
    entry.receipt.transition_id ?? entry.receipt_id ?? null;
  const toState = entry.receipt.to_state ?? entry.status ?? null;
  const occurredAt =
    entry.occurred_at ??
    entry.receipt.occurred_at ??
    entry.receipt.updated_at ??
    entry.receipt.created_at ??
    null;
  const versionAfter =
    entry.receipt.version_after ??
    entry.receipt.version ??
    entry.resource_version ??
    null;
  if (!transitionId || !toState || !occurredAt || versionAfter === null) {
    return null;
  }
  return {
    transition_id: transitionId,
    from_state: entry.receipt.from_state ?? null,
    to_state: toState,
    occurred_at: occurredAt,
    actor: entry.actor ?? entry.receipt.actor ?? "",
    reason_code: entry.receipt.reason ?? null,
    version_after: versionAfter,
    stream,
    attempt: entry.receipt.attempt,
    checkpoint: entry.receipt.checkpoint,
    owner_subject_id: entry.receipt.actor,
    correlation_id: entry.correlation_id ?? entry.receipt.correlation_id,
  };
}

function latestMutationReceipt(
  entries: CanonicalLifecycleReceipt[],
  predicate: (entry: CanonicalLifecycleReceipt) => boolean,
): CanonicalLifecycleReceipt | null {
  return [...entries].reverse().find(predicate) ?? null;
}

function lifecycleTransitions(
  entries: CanonicalLifecycleReceipt[],
  stream: PersistedLifecycleTransition["stream"],
): PersistedLifecycleTransition[] {
  return entries.flatMap((entry) => {
    const transition = lifecycleTransition(entry, stream);
    return transition ? [transition] : [];
  });
}

const LIFECYCLE_ACTION_MAP: Record<string, IntakeLifecycleAction> = {
  CANCEL: "CANCEL_INTAKE",
  RETRY: "RETRY_INTAKE",
  REOPEN: "REOPEN_INTAKE",
  CLAIM: "CLAIM_ASSIGNMENT",
  TRANSFER: "TRANSFER_ASSIGNMENT",
  PAUSE_SLA: "PAUSE_SLA",
  RESUME_SLA: "RESUME_SLA",
  ESCALATE: "ESCALATE_ASSIGNMENT",
  COMPLETE: "COMPLETE_ASSIGNMENT",
  CANCEL_JOB: "CANCEL_JOB",
  REPLAY_JOB: "REPLAY_JOB",
};

export function canonicalDetailToLifecycle(
  detail: CanonicalIntakeRuntimeDetail,
): IntakeLifecycleSnapshot {
  const lifecycle = detail.lifecycle;
  const assignmentHistory = lifecycleTransitions(
    lifecycle.assignment_history,
    "ASSIGNMENT",
  );
  const slaHistory = lifecycleTransitions(lifecycle.sla_history, "SLA");
  const decisionHistory = lifecycleTransitions(
    lifecycle.decision_history,
    "DECISION",
  );
  const promotionHistory = lifecycleTransitions(
    lifecycle.promotion_history,
    "PROMOTION",
  );
  const jobHistory = lifecycleTransitions(lifecycle.job_history, "JOB");
  const assignmentMutation = lifecycle.assignment
    ? latestMutationReceipt(
        lifecycle.mutation_receipts,
        (entry) =>
          entry.receipt.assignment_id ===
          lifecycle.assignment?.assignment_id,
      )
    : null;
  const slaMutation = lifecycle.sla
    ? latestMutationReceipt(
        lifecycle.mutation_receipts,
        (entry) =>
          entry.receipt.sla_instance_id === lifecycle.sla?.sla_instance_id,
      )
    : null;
  const jobMutation = lifecycle.job
    ? latestMutationReceipt(
        lifecycle.mutation_receipts,
        (entry) => entry.receipt.job_id === lifecycle.job?.job_id,
      )
    : null;
  return {
    record: canonicalDetailToRecord(detail),
    intake_history: detail.processing_history.map((entry) => ({
      ...entry,
      stream: "INTAKE",
    })),
    assignment: lifecycle.assignment
      ? {
          assignment_id: lifecycle.assignment.assignment_id,
          status: lifecycle.assignment.status as NonNullable<
            IntakeLifecycleSnapshot["assignment"]
          >["status"],
          owner_subject_id: lifecycle.assignment.owner_subject_id,
          queue_name: lifecycle.assignment.queue_id,
          due_at: lifecycle.assignment.due_at,
          version: lifecycle.assignment.version,
          audit_event_id:
            assignmentMutation?.receipt.audit_event_id ?? null,
        }
      : null,
    assignment_history: assignmentHistory,
    sla: lifecycle.sla
      ? {
          sla_instance_id: lifecycle.sla.sla_instance_id,
          state: lifecycle.sla.state as NonNullable<IntakeLifecycleSnapshot["sla"]>["state"],
          due_at: lifecycle.sla.due_at,
          paused_duration_seconds: lifecycle.sla.paused_duration_seconds,
          version: lifecycle.sla.version,
          audit_event_id: slaMutation?.receipt.audit_event_id ?? null,
          correlation_id:
            slaMutation?.correlation_id ??
            slaMutation?.receipt.correlation_id ??
            null,
        }
      : null,
    sla_history: slaHistory,
    decisions: lifecycle.decisions.flatMap((decision) => {
      const decisionId = decision.decision_id ?? decision.receipt_id;
      const occurredAt = decision.updated_at ?? decision.created_at;
      if (!decisionId || !occurredAt) return [];
      const mutation = latestMutationReceipt(
        lifecycle.mutation_receipts,
        (entry) => entry.receipt.decision_id === decisionId,
      );
      return [{
        decision_id: decisionId,
        decision_type: decision.action ?? "",
        status: decision.status as IntakeLifecycleSnapshot["decisions"][number]["status"],
        proposer_subject_id: decision.proposer,
        reviewer_subject_id: decision.reviewer,
        version: decision.version,
        occurred_at: occurredAt,
        audit_event_id: mutation?.receipt.audit_event_id ?? null,
        correlation_id:
          decision.correlation_id ??
          mutation?.correlation_id ??
          mutation?.receipt.correlation_id ??
          null,
      }];
    }),
    decision_history: decisionHistory,
    promotion: null,
    promotion_history: promotionHistory,
    jobs: lifecycle.job
      ? [
          {
            job_id: lifecycle.job.job_id,
            status: lifecycle.job.status as never,
            attempt: lifecycle.job.attempt ?? 0,
            checkpoint: lifecycle.job.checkpoint,
            next_retry_at: lifecycle.job.next_retry_at,
            version: lifecycle.job.version ?? 0,
            correlation_id:
              jobMutation?.correlation_id ??
              jobMutation?.receipt.correlation_id ??
              null,
          },
        ]
      : [],
    job_history: jobHistory,
    allowed_actions: lifecycle.actor_facts.allowed_actions.flatMap((action) => {
      const mapped = LIFECYCLE_ACTION_MAP[action];
      return mapped ? [mapped] : [];
    }),
    refreshed_at: detail.updated_at,
    updated_at: detail.updated_at,
    sequence: detail.version,
    version: detail.version,
  };
}

const COMPARISON_KEYS: Array<[RegExp, IdentityComparisonFieldKey]> = [
  [/source|provider.*id/i, "sourceId"],
  [/canonical.*url/i, "canonicalUrl"],
  [/address/i, "address"],
  [/area|ping/i, "area"],
  [/floor/i, "floor"],
  [/listing.*type|property.*type/i, "listingType"],
  [/rent|price/i, "rentOrPrice"],
  [/status/i, "status"],
];

function emptyComparisonField(): IdentityComparisonField {
  return {
    current: null,
    submitted: null,
    state: "MISSING",
    detail: "API 未提供此比較欄位。",
  };
}

function comparisonField(
  value: CanonicalMatchCaseDetail["comparison_fields"][number],
): IdentityComparisonField {
  return {
    current: {
      value: scalar(value.existing_value),
      displayValue: String(scalar(value.existing_value) ?? "未提供"),
    },
    submitted: {
      value: scalar(value.submitted_value),
      displayValue: String(scalar(value.submitted_value) ?? "未提供"),
    },
    state: value.agrees ? "MATCH" : "CHANGED",
    detail: value.detail ?? value.label,
  };
}

export function canonicalMatchToComparison(
  detail: CanonicalIntakeRuntimeDetail,
): IdentityComparisonContract | null {
  const match = detail.match_case;
  const outcome = matchOutcome(match?.outcome ?? detail.match_outcome);
  if (!match || !outcome) return null;
  const fields = Object.fromEntries(
    [
      "sourceId",
      "canonicalUrl",
      "address",
      "area",
      "floor",
      "listingType",
      "rentOrPrice",
      "status",
    ].map((key) => [key, emptyComparisonField()]),
  ) as IdentityComparisonContract["fields"];
  for (const field of match.comparison_fields) {
    const mapped = COMPARISON_KEYS.find(([pattern]) => pattern.test(field.field_path));
    if (mapped) fields[mapped[1]] = comparisonField(field);
  }
  return {
    matchCaseId: match.match_case_id,
    matchCaseVersion: match.version,
    outcome,
    confidence: match.confidence,
    summary: match.summary,
    currentListingId: match.target_listing_id,
    currentPropertyId: null,
    submittedIntakeId: detail.intake_id,
    submittedSnapshotId: detail.source_snapshot_id,
    submittedParserRunId: detail.parser_run_id,
    fields,
    agreeingSignals: match.signals
      .filter((signal) => signal.agrees)
      .map(({ key, label, detail: signalDetail }) => ({
        key,
        label,
        detail: signalDetail,
      })),
    contradictingSignals: match.signals
      .filter((signal) => !signal.agrees)
      .map(({ key, label, detail: signalDetail }) => ({
        key,
        label,
        detail: signalDetail,
      })),
  };
}

function identityActor(
  subjectId: string,
  role: string,
  displayName = subjectId,
): IdentityActor {
  return { subjectId, role, displayName };
}

function graphNode(node: CanonicalMatchGraphPlan["before_graph"]["nodes"][number]): IdentityGraphNode {
  return {
    nodeId: node.node_id,
    nodeType: node.node_type as IdentityGraphNode["nodeType"],
    label: node.node_id,
    effective: node.status === "EFFECTIVE" || node.status === "ACTIVE",
    version: null,
  };
}

function graphSnapshot(
  graph: CanonicalMatchGraphPlan["before_graph"],
): IdentityGraphSnapshot {
  return {
    nodes: graph.nodes.map(graphNode),
    edges: graph.edges.map((edge) => ({
      edgeId: edge.edge_id,
      fromNodeId:
        edge.source_property_id ?? edge.property_id ?? edge.listing_id ?? "",
      toNodeId:
        edge.target_property_id ?? edge.intake_id ?? edge.listing_id ?? "",
      relation: edge.relation,
      effectiveFrom: "",
      effectiveTo: edge.status === "SUPERSEDED" ? "" : null,
      supersedesEdgeId: edge.supersedes_edge_ids[0] ?? null,
    })),
  };
}

export function canonicalGraphPlan(
  plan: CanonicalMatchGraphPlan,
  fallbackProposer: IdentityActor,
): IdentityGraphPlan | null {
  const operation = plan.plan_type.toUpperCase();
  if (!["MERGE", "SPLIT", "UNMERGE", "REVERSAL"].includes(operation)) return null;
  const proposer = plan.proposer
    ? identityActor(plan.proposer.subject_id, plan.proposer.role_id)
    : fallbackProposer;
  return {
    planId: plan.plan_id,
    operation: operation as IdentityGraphPlan["operation"],
    state: plan.status as IdentityGraphPlan["state"],
    expectedGraphVersion: plan.expected_graph_version,
    originalDecisionId: plan.original_decision?.decision_id ?? null,
    proposer,
    requestedReviewer: plan.reviewer
      ? identityActor(plan.reviewer.subject_id, plan.reviewer.role_id)
      : null,
    before: graphSnapshot(plan.before_graph),
    after: graphSnapshot(plan.after_graph),
    redirects: plan.redirects.map((redirect) => ({
      fromPropertyId: redirect.from_property_id,
      toPropertyId: redirect.to_property_id,
      disposition:
        redirect.status === "CLOSED"
          ? "CLOSE"
          : redirect.status === "REVERSED"
            ? "REVERSE"
            : "CREATE",
    })),
    candidateImpacts: plan.candidate_impacts
      .filter((impact) => impact.candidate_site_id)
      .map((impact) => ({
        candidateSiteId: impact.candidate_site_id!,
        disposition:
          impact.disposition === "REASSIGN"
            ? "REASSIGN"
            : impact.disposition === "REQUIRE_REVIEW"
              ? "REQUIRE_REVIEW"
              : "KEEP_HISTORICAL",
        targetPropertyId: impact.target_property_id,
      })),
    lineageImpact: [
      plan.lineage_impact.summary,
      ...plan.lineage_impact.superseded_edge_ids.map(
        (edgeId) => `Superseded edge ${edgeId}`,
      ),
    ].filter(Boolean),
    riskSummary: plan.lineage_impact.summary,
  };
}

export function canonicalIdentityWorkflow(
  detail: CanonicalIntakeRuntimeDetail,
  currentActor: IdentityActor,
): IdentityReviewWorkflow {
  const latest = detail.lifecycle.latest_decision_receipt;
  const proposer = identityActor(
    latest?.proposer ?? detail.submitted_by,
    latest?.proposer ? "proposer" : "submitter",
  );
  const reviewer = latest?.reviewer
    ? identityActor(latest.reviewer, "reviewer")
    : null;
  const actions = new Set(detail.lifecycle.actor_facts.allowed_actions);
  return {
    status: (latest?.status ?? "DRAFT") as IdentityReviewWorkflow["status"],
    currentActor,
    proposer,
    reviewer,
    decisionId: latest?.decision_id ?? null,
    requiresIndependentReview: detail.lifecycle.actor_facts.second_actor.required,
    canPropose: actions.has("DECIDE_MATCH"),
    canReview:
      actions.has("DECIDE_MATCH") &&
      !detail.lifecycle.actor_facts.second_actor.self_review_denied,
    denialReasonCode:
      detail.lifecycle.actor_facts.second_actor.reason_code ??
      detail.lifecycle.actor_facts.denied_action_reasons.DECIDE_MATCH ??
      null,
    proposal: latest
      ? {
          outcomeAction: null,
          graphOperation: null,
          graphPlanId: latest.graph_plan?.plan_id ?? null,
          reason: "",
          riskAcknowledged: true,
        }
      : null,
  };
}

export function canonicalIdentityReceipt(
  detail: CanonicalIntakeRuntimeDetail,
): IdentityDecisionReceipt | null {
  const latest = detail.lifecycle.latest_decision_receipt;
  if (!latest?.decision_id) return null;
  const effect = detail.lifecycle.mutation_receipts.find(
    (entry) => entry.receipt.decision_id === latest.decision_id,
  );
  return {
    decisionId: latest.decision_id,
    status: latest.status as IdentityDecisionReceipt["status"],
    outcomeAction: null,
    graphOperation:
      latest.graph_plan &&
      ["MERGE", "SPLIT", "UNMERGE", "REVERSAL"].includes(
        latest.graph_plan.plan_type.toUpperCase(),
      )
        ? (latest.graph_plan.plan_type.toUpperCase() as IdentityDecisionReceipt["graphOperation"])
        : null,
    graphPlanId: latest.graph_plan?.plan_id ?? null,
    originalDecisionId: latest.graph_plan?.original_decision?.decision_id ?? null,
    matchCaseId: detail.match_case_id ?? "",
    proposer: identityActor(latest.proposer ?? detail.submitted_by, "proposer"),
    reviewer: latest.reviewer ? identityActor(latest.reviewer, "reviewer") : null,
    reason: effect?.receipt.reason ?? "",
    riskAcknowledged: true,
    occurredAt: latest.updated_at ?? latest.created_at ?? detail.updated_at,
    resourceVersions: { intake: detail.version },
    listingId: effect?.receipt.listing_id ?? null,
    listingRevisionId: effect?.receipt.listing_revision_id ?? null,
    effectiveEdgeIds: effect?.receipt.identity_edge_id
      ? [effect.receipt.identity_edge_id]
      : [],
    supersededEdgeIds:
      latest.graph_plan?.lineage_impact.superseded_edge_ids ?? [],
    redirectIds: [],
    auditEventId: effect?.receipt.audit_event_id ?? "",
    correlationId: latest.correlation_id ?? "",
    lineageImpact: latest.graph_plan
      ? [latest.graph_plan.lineage_impact.summary]
      : [],
  };
}

export function canonicalCommandReceiptToIdentity(
  receipt: CanonicalIdentityDecisionReceipt,
  matchCaseId: string,
): IdentityDecisionReceipt {
  const effect = receipt.effect_receipt;
  const runtime = effect?.runtime_receipt;
  const graphOperation =
    receipt.graph_plan &&
    ["MERGE", "SPLIT", "UNMERGE", "REVERSAL"].includes(
      receipt.graph_plan.plan_type.toUpperCase(),
    )
      ? (receipt.graph_plan.plan_type.toUpperCase() as IdentityDecisionReceipt["graphOperation"])
      : null;
  return {
    decisionId: receipt.decision_id,
    status: receipt.status as IdentityDecisionReceipt["status"],
    outcomeAction: null,
    graphOperation,
    graphPlanId: receipt.graph_plan?.plan_id ?? null,
    originalDecisionId:
      receipt.reverses_decision_id ??
      receipt.graph_plan?.original_decision?.decision_id ??
      null,
    matchCaseId,
    proposer: identityActor(receipt.proposer ?? "", "proposer"),
    reviewer: receipt.reviewer
      ? identityActor(receipt.reviewer, "reviewer")
      : null,
    reason: receipt.reason ?? runtime?.reason ?? "",
    riskAcknowledged: true,
    occurredAt: receipt.updated_at ?? receipt.created_at ?? effect?.issued_at ?? "",
    resourceVersions: receipt.resource_versions,
    listingId: runtime?.listing_id ?? null,
    listingRevisionId: runtime?.listing_revision_id ?? null,
    effectiveEdgeIds: effect?.identity_edge_ids ?? [],
    supersededEdgeIds:
      receipt.graph_plan?.lineage_impact.superseded_edge_ids ?? [],
    redirectIds: [],
    auditEventId: receipt.audit_event_id,
    correlationId: receipt.correlation_id,
    lineageImpact: receipt.graph_plan
      ? [receipt.graph_plan.lineage_impact.summary]
      : [],
  };
}
