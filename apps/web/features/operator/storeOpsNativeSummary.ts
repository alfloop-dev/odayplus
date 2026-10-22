type UnavailableFact = {
  status: "unavailable";
  value: null;
  reason: string;
  source: null;
};

export type NativeStoreSummary = {
  storeId: string;
  observedAt: string;
  storeFormat: UnavailableFact | {
    status: "recorded";
    value: string;
    source: "core.stores.store_format_code";
    recordId: string;
    effectiveFrom: string;
    effectiveTo: string;
  };
  machines: UnavailableFact | {
    status: "recorded";
    value: number;
    source: "core.machines";
    basis: "current_nonretired_registered_records";
    recordIds: string[];
    physicalInventoryComplete: null;
  };
  todayRevenue: UnavailableFact & { currency: null; window: null };
};

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

// Match the aware, microsecond datetime.isoformat wire values supplied by the
// native producer. Borrow the Intervention parser's calendar round-trip and
// exact integer arithmetic, without inferring an IANA zone from an offset.
const instant = (value: unknown): bigint | null => {
  if (typeof value !== "string") return null;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|([+-])(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)$/.exec(value);
  if (!parts) return null;
  const [year, month, day, hour, minute, second] = parts.slice(1, 7).map(Number);
  const offsetHour = Number(parts[10] ?? 0);
  const offsetMinute = Number(parts[11] ?? 0);
  const offsetSecond = Number(parts[12] ?? 0);
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > 31
    || hour > 23 || minute > 59 || second > 59
    || offsetHour > 23 || offsetMinute > 59 || offsetSecond > 59) return null;
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day);
  calendar.setUTCHours(hour, minute, second, 0);
  if (!Number.isSafeInteger(calendar.getTime()) || calendar.getUTCFullYear() !== year
    || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day) return null;
  const scale = 1_000_000n;
  const fraction = BigInt((parts[7] ?? "").padEnd(6, "0"));
  const offsetFraction = BigInt((parts[13] ?? "").padEnd(6, "0"));
  const offsetSeconds = offsetHour * 3600 + offsetMinute * 60 + offsetSecond;
  // Negative zero denotes an unknown offset in RFC 3339 and is not emitted by
  // this native producer. Python fromisoformat also normalizes a subsecond-only
  // offset to UTC, so that non-round-tripping wire form is unavailable here.
  if (offsetSeconds === 0 && (parts[9] === "-" || offsetFraction !== 0n)) return null;
  const offset = BigInt(offsetSeconds) * scale + offsetFraction;
  const local = BigInt(calendar.getTime()) * 1000n + fraction;
  return local - (parts[9] === "-" ? -offset : offset);
};

/** Untrusted response fields must carry the exact currently selected store. */
export function nativeStoreLabels(value: unknown, storeId: string) {
  const labels = {
    format: "不可用",
    machines: "不可用",
    machineDescription: "原生登錄設備資料不可用；不代表實體機台為零。",
  };
  if (!object(value) || value.storeId !== storeId || !storeId) return labels;
  const observedAt = instant(value.observedAt);
  if (observedAt === null) return labels;
  const format = value.storeFormat;
  if (object(format) && format.status === "recorded"
    && format.source === "core.stores.store_format_code" && format.recordId === storeId
    && typeof format.value === "string" && format.value.trim()) {
    const start = instant(format.effectiveFrom);
    const end = instant(format.effectiveTo);
    if (start !== null && end !== null && start <= observedAt && observedAt < end) {
      labels.format = format.value.trim();
    }
  }
  const machines = value.machines;
  if (object(machines) && machines.status === "recorded" && machines.source === "core.machines"
    && machines.basis === "current_nonretired_registered_records"
    && machines.physicalInventoryComplete === null
    && typeof machines.value === "number" && Number.isSafeInteger(machines.value) && machines.value > 0
    && Array.isArray(machines.recordIds) && machines.recordIds.length === machines.value
    && machines.recordIds.every((id) => typeof id === "string" && id.trim())
    && new Set(machines.recordIds).size === machines.recordIds.length) {
    labels.machines = `${machines.value} 台（登錄）`;
    labels.machineDescription = "目前有效且未退役的原生登錄設備筆數；尚無完整實體盤點證明。";
  }
  return labels;
}
