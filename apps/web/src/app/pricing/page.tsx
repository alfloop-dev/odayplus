import { PriceOpsWorkspace } from "../../../features/priceops/PriceOpsWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function PricingPage({ searchParams }: PageProps) {
  return <PriceOpsWorkspace searchParams={await searchParams} />;
}
