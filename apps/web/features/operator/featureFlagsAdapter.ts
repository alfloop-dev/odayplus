import { operatorFixturesAllowed, payloadContainsSeedData } from "./operatorDataMode";

export type FeatureFlagScope = "global" | "per-tenant" | "per-tenant-per-source" | "global-per-tenant";
export type FeatureFlagBinding = { tenant_id: string; source_id: string | null };

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
  // Legacy global responses omit this entire metadata group.
  scope?: FeatureFlagScope;
  bindings?: FeatureFlagBinding[];
  configuration_version?: number;
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
  configuration_version?: number;
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

function validScopeMetadata(flag: Record<string, unknown>): boolean {
  if (!["scope", "bindings", "configuration_version"].some((key) => key in flag)) return true;
  if (
    typeof flag.scope !== "string" ||
    !["global", "per-tenant", "per-tenant-per-source", "global-per-tenant"].includes(flag.scope) ||
    !Number.isSafeInteger(flag.configuration_version) || Number(flag.configuration_version) < 1 ||
    !Array.isArray(flag.bindings)
  ) return false;
  if (flag.scope === "global") return flag.bindings.length === 0;
  const units = new Set<string>();
  return flag.bindings.every((value: unknown) => {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
    const binding = value as Record<string, unknown>;
    if (
      Object.keys(binding).some((key) => key !== "tenant_id" && key !== "source_id") ||
      typeof binding.tenant_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(binding.tenant_id)
    ) return false;
    if (flag.scope === "per-tenant-per-source") {
      if (typeof binding.source_id !== "string" || !binding.source_id.trim()) return false;
    } else if (binding.source_id !== null) return false;
    const unit = JSON.stringify([binding.tenant_id.toLowerCase(), binding.source_id]);
    if (units.has(unit)) return false;
    units.add(unit);
    return true;
  });
}

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
    typeof flag.is_active === "boolean" && typeof flag.is_expired === "boolean" &&
    validScopeMetadata(flag)
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

function commandProof(proof: FeatureFlagCommandProof, minimum: number, versioned = false): FeatureFlagCommandProof {
  if (typeof proof?.reason !== "string" || proof.reason.trim().length < minimum || proof.riskAcknowledged !== true) {
    throw new Error(minimum === 10 ? "核准理由至少需要 10 個字，並須確認操作風險。" : "請填寫操作理由並確認操作風險。");
  }
  const validated: FeatureFlagCommandProof = { reason: proof.reason.trim(), riskAcknowledged: true };
  if (versioned && proof.configuration_version !== undefined) {
    if (!Number.isSafeInteger(proof.configuration_version) || proof.configuration_version < 1) {
      throw new Error("Feature Flag 設定版本無效");
    }
    validated.configuration_version = proof.configuration_version;
  }
  return validated;
}

async function commandResult(res: Response, key: string, reviewedVersion?: number): Promise<FeatureFlagCommandResult> {
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
  if (
    result.status !== "ok" || !isFeatureFlag(result.flag) || result.flag.key !== key ||
    (reviewedVersion !== undefined && result.flag.configuration_version !== reviewedVersion)
  ) throw new Error("Feature Flag 回應格式無效");
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
  const validatedProof = commandProof(proof, 1, true);
  const res = await fetch(`${baseUrl}/api/v1/admin/feature-flags/${encodeURIComponent(key)}/enable`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(operatorFixturesAllowed() ? { approvals, ...validatedProof } : validatedProof),
  });
  return commandResult(res, key, validatedProof.configuration_version);
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
  const validatedProof = commandProof(proof, 10, true);
  const res = await fetch(`${baseUrl}/api/v1/admin/feature-flags/${encodeURIComponent(key)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(operatorFixturesAllowed() ? { approver, ...validatedProof } : validatedProof),
  });
  return commandResult(res, key, validatedProof.configuration_version);
}
