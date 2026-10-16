import type { Tone } from "./componentPrimitives";
import { WORKSPACES, type WorkspaceId } from "./navigation";
import type { OperatorTask } from "./OperatorConsole";

type TaskCenterSource = "api" | "fixture";

function coerceText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function getRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function getNestedText(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    const text = coerceText(value);
    if (text) return text;
  }
  return "";
}

function inferWorkspace(record: Record<string, unknown>): WorkspaceId | undefined {
  const direct = getNestedText(record, ["workspace", "workspaceId", "module", "routeKey", "area"]).toLowerCase();
  if (WORKSPACES.some((workspace) => workspace.id === direct)) return direct as WorkspaceId;

  const artifacts = Array.isArray(record.artifacts) ? record.artifacts.join(" ") : "";
  const text = `${direct} ${artifacts} ${getNestedText(record, ["title", "summary", "summary_zh", "description", "next"])}`.toLowerCase();
  if (text.includes("store") || text.includes("operator") || text.includes("forecast") || text.includes("issue")) return "store";
  if (text.includes("growth") || text.includes("price") || text.includes("adlift") || text.includes("campaign")) return "growth";
  if (text.includes("network") || text.includes("site") || text.includes("listing") || text.includes("heatzone") || text.includes("netplan")) return "network";
  if (text.includes("govern") || text.includes("audit") || text.includes("approval") || text.includes("learning")) return "govern";
  return undefined;
}

function getTaskTone(status: string, priority: string): Tone {
  const value = `${status} ${priority}`.toLowerCase();
  if (value.includes("blocked") || value.includes("overdue") || value.includes("p0") || value.includes("critical")) return "danger";
  if (value.includes("review") || value.includes("wait") || value.includes("need") || value.includes("p1")) return "warning";
  if (value.includes("done") || value.includes("complete") || value.includes("closed")) return "success";
  if (value.includes("progress") || value.includes("draft") || value.includes("todo")) return "info";
  return "neutral";
}

function getTaskStatusLabel(status: string): string {
  const value = status.trim();
  if (!value) return "Todo";
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function normalizeTaskRecord(value: unknown, index: number, source: TaskCenterSource): OperatorTask | null {
  const record = getRecord(value);
  if (!record) return null;

  const id = getNestedText(record, ["id", "task_id", "taskId", "key", "uuid"]) || `TASK-${index + 1}`;
  const title =
    getNestedText(record, ["title", "name", "summary_zh", "summary", "message", "next"]) || `Task ${index + 1}`;
  const summary =
    getNestedText(record, ["description", "summary", "summary_zh", "next", "message"]) || "No task summary available.";
  const owner = getNestedText(record, ["owner", "assignee", "agent", "role"]) || "Unassigned";
  const rawStatus = getNestedText(record, ["status", "state", "stage"]) || "todo";
  const priority = getNestedText(record, ["priority", "severity", "rank"]) || "P2";
  const dueLabel =
    getNestedText(record, ["dueLabel", "due_label", "sla", "due", "due_at", "dueAt", "deadline"]) || "No SLA";
  const workspace = inferWorkspace(record);
  const href =
    getNestedText(record, ["href", "url"]) ||
    (workspace ? `/operator?ws=${workspace}` : "/operator");
  const tone = getTaskTone(rawStatus, priority);

  return {
    dueLabel,
    href,
    id,
    owner,
    priority,
    source,
    status: getTaskStatusLabel(rawStatus),
    summary,
    title,
    tone,
    workspace,
  };
}

export function normalizeTasksPayload(payload: unknown, source: TaskCenterSource): OperatorTask[] {
  const record = getRecord(payload);
  const rawItems =
    (Array.isArray(payload) && payload) ||
    (Array.isArray(record?.items) && record.items) ||
    (Array.isArray(record?.tasks) && record.tasks) ||
    (Array.isArray(record?.data) && record.data) ||
    (Array.isArray(record?.results) && record.results) ||
    [];

  return rawItems
    .map((item, index) => normalizeTaskRecord(item, index, source))
    .filter((item): item is OperatorTask => Boolean(item));
}
