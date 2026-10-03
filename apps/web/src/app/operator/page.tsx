import type { Metadata } from "next";
import { cookies } from "next/headers";
import { readOperatorReleaseStatus } from "../../lib/auth/operatorReleaseStatus";
import { webSessionCookieName } from "../../lib/auth/session";
import {
  OperatorAdminConsole,
  OperatorConsole,
  OperatorPasswordChange,
} from "../../../features/operator";
import {
  MarketIntelligencePanel,
  shouldShowMarketIntelligence,
} from "../../features/market-intelligence";

export const metadata: Metadata = {
  title: "Operator Console | Oday Plus",
  description: "Oday Plus operator console design prototype",
};

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function OperatorPage({ searchParams }: PageProps) {
  const params = (await searchParams) ?? {};

  // Administration and first-login password rotation live on this canonical
  // page (behind the same Web session middleware) rather than new routes:
  // a bootstrap-only platform_admin holds no business read for the console.
  if (params.view === "admin") {
    const cookie = (await cookies()).get(webSessionCookieName)?.value;
    const releaseStatus = await readOperatorReleaseStatus(cookie);
    return <OperatorAdminConsole releaseStatus={releaseStatus} />;
  }
  if (params.view === "password") return <OperatorPasswordChange />;

  return (
    <>
      <OperatorConsole searchParams={params} />
      {shouldShowMarketIntelligence(params) ? (
        <MarketIntelligencePanel searchParams={params} />
      ) : null}
    </>
  );
}
