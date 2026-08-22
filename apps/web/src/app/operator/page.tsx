import type { Metadata } from "next";
import { OperatorConsole } from "../../../features/operator";
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

  return (
    <>
      <OperatorConsole searchParams={params} />
      {shouldShowMarketIntelligence(params) ? (
        <MarketIntelligencePanel searchParams={params} />
      ) : null}
    </>
  );
}
