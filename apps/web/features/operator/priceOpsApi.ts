import { operatorSecurityHeaders } from "./operatorSecurityHeaders";

export type PriceBand = { p10: number; p50: number; p90: number };
export type PriceSimulation = { price: number; demand: PriceBand; revenue: PriceBand; gross_margin: PriceBand };
export type PriceComparisonItem = {
  item_id: string; current_price: number; candidate_price: number;
  baseline_simulation: PriceSimulation; candidate_simulation: PriceSimulation;
  expected_gross_margin_change: number; constraint_status: string;
};
export type PriceComparison = {
  plan_id: string; plan_status: string; items: PriceComparisonItem[];
  is_approvable: boolean; is_feasible: boolean; rollback_ready: boolean;
  actor_id: string; policy_version: string; solver_version: string;
  created_by?: string | null; approval_actor_blockers?: string[];
  command_permissions: { simulate: boolean; writeback: boolean };
};
export type PriceScenario = {
  scenario_id: string; plan_id: string; is_feasible: boolean;
  items: { item_id: string; candidate_price: number; baseline_simulation: PriceSimulation;
    candidate_simulation: PriceSimulation; expected_gross_margin_change: number; is_feasible: boolean }[];
  audit_event_id: string; correlation_id: string;
};
export type PriceDecision = "approved" | "rejected" | "scenario_selected";
export type PriceWriteback = {
  decision_id: string; plan_id: string; actor: string; decision: PriceDecision; reason: string;
  selected_scenario_id: string | null; written_back_at: string;
  policy_version: string; solver_version: string; idempotency_key: string;
  audit_event_id: string; correlation_id: string;
};
export type PriceIntent = { key: string; correlationId: string };
export function priceIntent(): PriceIntent {
  return { key: `price-${crypto.randomUUID()}`, correlationId: `corr-price-${crypto.randomUUID()}` };
}
function nonempty(value: unknown): value is string { return typeof value === "string" && !!value.trim(); }
function simulation(value: PriceSimulation): boolean {
  return !!value && Number.isFinite(value.price) && [value.demand, value.revenue, value.gross_margin].every(
    band => !!band && [band.p10, band.p50, band.p90].every(Number.isFinite) && band.p10 <= band.p50 && band.p50 <= band.p90,
  );
}
async function request<T>(planId: string, suffix: string, body?: unknown, intent?: PriceIntent): Promise<T | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20_000);
  try {
    const response = await fetch(`/api/v1/priceops/plans/${encodeURIComponent(planId)}${suffix}`, {
      method: body === undefined ? "GET" : "POST", signal: controller.signal, cache: "no-store",
      headers: { "Content-Type": "application/json", ...operatorSecurityHeaders(),
        ...(intent ? { "Idempotency-Key": intent.key, "X-Correlation-Id": intent.correlationId } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return response.ok ? await response.json() as T : null;
  } catch { return null; } finally { clearTimeout(timeout); }
}
export async function fetchPriceComparison(planId: string, itemId: string): Promise<PriceComparison | null> {
  const value = await request<PriceComparison>(planId, "/comparison");
  if (!value || value.plan_id !== planId || !Array.isArray(value.items) ||
      !value.items.some(item => item.item_id === itemId) ||
      !value.items.every(item => simulation(item.baseline_simulation) && simulation(item.candidate_simulation)) ||
      !nonempty(value.actor_id) || !nonempty(value.policy_version) || !nonempty(value.solver_version) ||
      typeof value.command_permissions?.simulate !== "boolean" || typeof value.command_permissions?.writeback !== "boolean") return null;
  if (value.approval_actor_blockers !== undefined &&
      (!Array.isArray(value.approval_actor_blockers) || value.approval_actor_blockers.some(reason => typeof reason !== "string"))) return null;
  return value;
}
export async function simulatePriceScenario(planId: string, itemId: string, candidatePrice: number, intent: PriceIntent): Promise<PriceScenario | null> {
  const value = await request<PriceScenario>(planId, "/simulate-scenario", {
    candidate_prices: { [itemId]: candidatePrice }, reason: "Operator candidate scenario simulation",
  }, intent);
  const item = value?.items?.find(item => item.item_id === itemId);
  if (!value || value.plan_id !== planId || !nonempty(value.scenario_id) || !nonempty(value.audit_event_id) ||
      value.correlation_id !== intent.correlationId || typeof value.is_feasible !== "boolean" ||
      !item || item.candidate_price !== candidatePrice || !simulation(item.baseline_simulation) ||
      !simulation(item.candidate_simulation) || item.candidate_simulation.price !== candidatePrice || !Number.isFinite(item.expected_gross_margin_change) ||
      typeof item.is_feasible !== "boolean") return null;
  return value;
}
export async function writePriceDecision(params: {
  planId: string; actor: string; decision: PriceDecision; reason: string; scenarioId: string; intent: PriceIntent;
}): Promise<PriceWriteback | null> {
  const value = await request<PriceWriteback>(params.planId, "/decision-writeback", {
    actor: params.actor, decision: params.decision, reason: params.reason, selected_scenario_id: params.scenarioId,
  }, params.intent);
  if (!value || value.plan_id !== params.planId || value.actor !== params.actor || value.decision !== params.decision ||
      value.reason !== params.reason || value.selected_scenario_id !== params.scenarioId ||
      value.idempotency_key !== params.intent.key || value.correlation_id !== params.intent.correlationId ||
      !nonempty(value.decision_id) || !nonempty(value.audit_event_id) || !nonempty(value.policy_version) ||
      !nonempty(value.solver_version) || !Number.isFinite(Date.parse(value.written_back_at))) return null;
  return value;
}
