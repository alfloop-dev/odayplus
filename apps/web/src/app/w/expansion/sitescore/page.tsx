import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function SiteScorePage({ searchParams }: PageProps) {
  const liveSiteScores = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listSiteScoreReports().then((response) => response.items),
  });
  return <ExpansionWorkspace view="sitescore" searchParams={await searchParams} liveSiteScores={liveSiteScores} />;
}
