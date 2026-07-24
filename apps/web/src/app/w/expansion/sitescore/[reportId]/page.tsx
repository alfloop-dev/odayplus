import { ExpansionWorkspace } from "../../../../../../features/expansion/ExpansionWorkspace.tsx";
import { getServerApiClient } from "../../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ reportId: string }>;
};

export default async function SiteScoreDetailPage({ params }: PageProps) {
  const { reportId } = await params;
  const liveSiteScores = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listSiteScoreReports().then((response) => response.items),
  });
  return <ExpansionWorkspace view="sitescoreDetail" reportId={reportId} liveSiteScores={liveSiteScores} />;
}
