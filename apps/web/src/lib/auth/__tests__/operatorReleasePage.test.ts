import { afterEach, describe, expect, it, vi } from "vitest";
import OperatorPage from "../../../app/operator/page";
import { cookies } from "next/headers";
import { readOperatorReleaseStatus } from "../operatorReleaseStatus";
import { OperatorAdminConsole, OperatorPasswordChange } from "../../../../features/operator";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("../operatorReleaseStatus", () => ({ readOperatorReleaseStatus: vi.fn() }));
vi.mock("../session", () => ({ webSessionCookieName: "__Host-oday_web_session" }));
vi.mock("../../../../features/operator", () => ({
  OperatorAdminConsole: vi.fn(), OperatorConsole: vi.fn(), OperatorPasswordChange: vi.fn(),
}));
vi.mock("../../../features/market-intelligence", () => ({
  MarketIntelligencePanel: vi.fn(), shouldShowMarketIntelligence: () => false,
}));

afterEach(() => vi.resetAllMocks());

describe("canonical Operator page release notice wiring", () => {
  it("passes server-observed runtime status to the pure-admin page", async () => {
    const get = vi.fn().mockReturnValue({ value: "sealed-cookie" });
    vi.mocked(cookies).mockResolvedValue({ get } as unknown as Awaited<ReturnType<typeof cookies>>);
    const status = { profile: "dev-admin" as const, models: "limited" as const, unavailableServices: ["ForecastOps"] };
    vi.mocked(readOperatorReleaseStatus).mockResolvedValue(status);
    const page = await OperatorPage({ searchParams: Promise.resolve({ view: "admin" }) });
    expect(get).toHaveBeenCalledWith("__Host-oday_web_session");
    expect(readOperatorReleaseStatus).toHaveBeenCalledWith("sealed-cookie");
    expect(page.type).toBe(OperatorAdminConsole);
    expect(page.props.releaseStatus).toEqual(status);
  });

  it.each(["password", "business"])("does not change %s routing or require runtime probes there", async view => {
    const page = await OperatorPage({ searchParams: Promise.resolve({ view }) });
    if (view === "password") expect(page.type).toBe(OperatorPasswordChange);
    expect(readOperatorReleaseStatus).not.toHaveBeenCalled();
    expect(cookies).not.toHaveBeenCalled();
  });
});
