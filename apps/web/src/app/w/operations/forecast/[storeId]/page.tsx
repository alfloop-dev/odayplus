import { OperationsWorkspace } from "../../../../../../features/operations/OperationsWorkspace.tsx";
import { getServerApiClient } from "../../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ storeId: string }>;
};

export default async function StoreForecastDetailPage({ params }: PageProps) {
  const { storeId } = await params;
  const liveForecasts = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listForecasts().then((response) => response.items),
  });
  return (
    <OperationsWorkspace
      view="storeDetail"
      storeId={decodeURIComponent(storeId)}
      liveForecasts={liveForecasts}
    />
  );
}
