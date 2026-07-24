import { OperationsWorkspace } from "../../../../../features/operations/OperationsWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function ForecastPage({ searchParams }: PageProps) {
  const liveForecasts = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listForecasts().then((response) => response.items),
  });
  return (
    <OperationsWorkspace
      view="forecast"
      searchParams={await searchParams}
      liveForecasts={liveForecasts}
    />
  );
}
