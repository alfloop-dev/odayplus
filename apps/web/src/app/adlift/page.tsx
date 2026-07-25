import { AdLiftWorkspace } from "../../../features/adlift/AdLiftWorkspace.tsx";
import { getServerApiClient } from "../../lib/api/client.ts";
import { loadApiBinding } from "../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function AdLiftPage({ searchParams }: PageProps) {
  const liveReports = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAdliftReports().then((response) => response.items),
  });
  return <AdLiftWorkspace searchParams={await searchParams} liveReports={liveReports} />;
}
