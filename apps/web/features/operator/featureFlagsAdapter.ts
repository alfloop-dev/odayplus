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
};

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

export async function enableFeatureFlag(
  key: string,
  approvals: string[] = [],
  baseUrl = ""
): Promise<FeatureFlagDto> {
  const res = await fetch(`${baseUrl}/api/v1/admin/feature-flags/${encodeURIComponent(key)}/enable`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(operatorFixturesAllowed() ? { approvals } : {}),
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: "Failed to enable feature flag" }));
    throw new Error(errorBody.detail || `HTTP ${res.status}`);
  }
  const data = (await res.json()) as FeatureFlagResponse;
  return data.flag;
}

export async function disableFeatureFlag(key: string, baseUrl = ""): Promise<FeatureFlagDto> {
  const res = await fetch(`${baseUrl}/api/v1/admin/feature-flags/${encodeURIComponent(key)}/disable`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: "Failed to disable feature flag" }));
    throw new Error(errorBody.detail || `HTTP ${res.status}`);
  }
  const data = (await res.json()) as FeatureFlagResponse;
  return data.flag;
}

export async function approveFeatureFlag(
  key: string,
  approver: string,
  baseUrl = ""
): Promise<FeatureFlagDto> {
  const res = await fetch(`${baseUrl}/api/v1/admin/feature-flags/${encodeURIComponent(key)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(operatorFixturesAllowed() ? { approver } : {}),
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: "Failed to add approval" }));
    throw new Error(errorBody.detail || `HTTP ${res.status}`);
  }
  const data = (await res.json()) as FeatureFlagResponse;
  return data.flag;
}
