import { operatorFixturesAllowed, payloadContainsSeedData } from "./operatorDataMode";

export type FeatureFlagDto = {
  key: string;
  owner: string;
  enabled: boolean;
  readiness: "experimental" | "beta" | "ga" | "deprecated";
  high_risk: boolean;
  expires_on: string | null;
  description: string;
  approved_by: string[];
  is_active: boolean;
  is_expired: boolean;
};

export type FeatureFlagListResponse = {
  status: string;
  count: number;
  flags: FeatureFlagDto[];
};

export type FeatureFlagResponse = {
  status: string;
  flag: FeatureFlagDto;
  audit?: { event_id: string; delivery_status: string };
};

export type FeatureFlagCommandProof = {
  reason: string;
  riskAcknowledged: true;
};

export type FeatureFlagCommandResult = Pick<FeatureFlagResponse, "flag" | "audit">;

export const DEFAULT_FEATURE_FLAGS: FeatureFlagDto[] = [
  {
    key: "high_risk.priceops.execute",
    owner: "pricing_manager",
    enabled: false,
    readiness: "ga",
    high_risk: true,
    expires_on: null,
    description: "Enable PriceOps automatic price change execution (Kill-Switch)",
    approved_by: [],
    is_active: false,
    is_expired: false,
  },
  {
    key: "high_risk.adlift.approve",
    owner: "marketing_manager",
    enabled: false,
    readiness: "ga",
    high_risk: true,
    expires_on: null,
    description: "Enable AdLift ad budget increase approvals (Kill-Switch)",
    approved_by: [],
    is_active: false,
    is_expired: false,
  },
  {
    key: "high_risk.netplan.approve",
    owner: "executive",
    enabled: false,
    readiness: "ga",
    high_risk: true,
    expires_on: null,
    description: "Enable NetPlan MOVE/EXIT relocation approvals (Kill-Switch)",
    approved_by: [],
    is_active: false,
    is_expired: false,
  },
  {
    key: "high_risk.model.publish",
    owner: "release_owner",
    enabled: false,
    readiness: "ga",
    high_risk: true,
    expires_on: null,
    description: "Enable production ML model promotion and publishing (Kill-Switch)",
    approved_by: [],
    is_active: false,
    is_expired: false,
  },
  {
    key: "high_risk.sitescore.approve",
    owner: "site_reviewer",
    enabled: false,
    readiness: "beta",
    high_risk: true,
    expires_on: null,
    description: "Enable SiteScore site evaluation GO approvals (Kill-Switch)",
    approved_by: [],
    is_active: false,
    is_expired: false,
  },
];

function isFeatureFlag(value: unknown): value is FeatureFlagDto {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const flag = value as Record<string, unknown>;
  return (
    typeof flag.key === "string" && flag.key.trim().length > 0 &&
    typeof flag.owner === "string" && flag.owner.trim().length > 0 &&
    typeof flag.enabled === "boolean" &&
    typeof flag.readiness === "string" &&
    ["experimental", "beta", "ga", "deprecated"].includes(flag.readiness) &&
    typeof flag.high_risk === "boolean" &&
    (flag.expires_on === null || typeof flag.expires_on === "string") &&
    typeof flag.description === "string" &&
    Array.isArray(flag.approved_by) && flag.approved_by.every((item) => typeof item === "string") &&
    typeof flag.is_active === "boolean" && typeof flag.is_expired === "boolean"
  );
}

export async function fetchFeatureFlags(baseUrl = ""): Promise<FeatureFlagDto[]> {
  try {
    const res = await fetch(`${baseUrl}/api/v1/admin/feature-flags`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`Feature Flag 讀取失敗 (HTTP ${res.status})`);
    const data: unknown = await res.json();
    if (typeof data !== "object" || data === null || Array.isArray(data)) {
      throw new Error("Feature Flag 資料格式無效");
    }
    if (!operatorFixturesAllowed() && payloadContainsSeedData(data)) {
      throw new Error("Feature Flag API 回傳非正式資料來源");
    }
    const response = data as Record<string, unknown>;
    if (
      response.status !== "ok" ||
      !Array.isArray(response.flags) ||
      response.count !== response.flags.length ||
      !response.flags.every(isFeatureFlag) ||
      new Set(response.flags.map((flag) => flag.key)).size !== response.flags.length
    ) {
      throw new Error("Feature Flag 資料格式無效");
    }
    return response.flags;
  } catch (err) {
    if (operatorFixturesAllowed()) return DEFAULT_FEATURE_FLAGS;
    throw err instanceof Error ? err : new Error("Feature Flag 資料讀取失敗");
  }
}

function commandProof(proof: FeatureFlagCommandProof, minimum: number): FeatureFlagCommandProof {
  if (typeof proof?.reason !== "string" || proof.reason.trim().length < minimum || proof.riskAcknowledged !== true) {
    throw new Error(minimum === 10 ? "核准理由至少需要 10 個字，並須確認操作風險。" : "請填寫操作理由並確認操作風險。");
  }
  return { reason: proof.reason.trim(), riskAcknowledged: true };
}

async function commandResult(res: Response, key: string): Promise<FeatureFlagCommandResult> {
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(errorBody && typeof errorBody.detail === "string" ? errorBody.detail : `HTTP ${res.status}`);
  }
  const data: unknown = await res.json();
  if (typeof data !== "object" || data === null || Array.isArray(data)) throw new Error("Feature Flag 回應格式無效");
  if (!operatorFixturesAllowed() && payloadContainsSeedData(data)) {
    throw new Error("Feature Flag API 回傳非正式資料來源");
  }
  const result = data as Record<string, unknown>;
  if (result.status !== "ok" || !isFeatureFlag(result.flag) || result.flag.key !== key) throw new Error("Feature Flag 回應格式無效");
  if (result.audit !== undefined) {
    if (typeof result.audit !== "object" || result.audit === null || Array.isArray(result.audit)) throw new Error("Feature Flag 稽核回應格式無效");
    const audit = result.audit as Record<string, unknown>;
    if (typeof audit.event_id !== "string" || !audit.event_id.trim() || typeof audit.delivery_status !== "string" || !audit.delivery_status.trim()) {
      throw new Error("Feature Flag 稽核回應格式無效");
    }
    return { flag: result.flag, audit: { event_id: audit.event_id, delivery_status: audit.delivery_status } };
  }
  return { flag: result.flag };
}

export async function enableFeatureFlag(
  key: string,
  approvals: string[],
  baseUrl: string,
  proof: FeatureFlagCommandProof,
): Promise<FeatureFlagCommandResult> {
  const validatedProof = commandProof(proof, 1);
  const res = await fetch(`${baseUrl}/api/v1/admin/feature-flags/${encodeURIComponent(key)}/enable`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(operatorFixturesAllowed() ? { approvals, ...validatedProof } : validatedProof),
  });
  return commandResult(res, key);
}

export async function disableFeatureFlag(key: string, baseUrl: string, proof: FeatureFlagCommandProof): Promise<FeatureFlagCommandResult> {
  const validatedProof = commandProof(proof, 1);
  const res = await fetch(`${baseUrl}/api/v1/admin/feature-flags/${encodeURIComponent(key)}/disable`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(validatedProof),
  });
  return commandResult(res, key);
}

export async function approveFeatureFlag(
  key: string,
  approver: string,
  baseUrl: string,
  proof: FeatureFlagCommandProof,
): Promise<FeatureFlagCommandResult> {
  const validatedProof = commandProof(proof, 10);
  const res = await fetch(`${baseUrl}/api/v1/admin/feature-flags/${encodeURIComponent(key)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(operatorFixturesAllowed() ? { approver, ...validatedProof } : validatedProof),
  });
  return commandResult(res, key);
}
