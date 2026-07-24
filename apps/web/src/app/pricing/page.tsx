import { PriceOpsWorkspace } from "../../../features/priceops/PriceOpsWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function PricingPage({ searchParams }: PageProps) {
  const livePlans = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listPriceOpsPlans().then((response) => response.items),
  });
  return <PriceOpsWorkspace searchParams={await searchParams} livePlans={livePlans} />;
}
