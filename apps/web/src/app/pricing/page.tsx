import { PriceOpsWorkspace } from "../../../features/priceops/PriceOpsWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function PricingPage({ searchParams }: PageProps) {
  return <PriceOpsWorkspace searchParams={searchParams} />;
}
