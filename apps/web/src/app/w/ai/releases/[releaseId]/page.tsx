import { LearningHubWorkspace } from "../../../../../../features/learninghub/LearningHubWorkspace.tsx";
import { getServerApiClient } from "../../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../../lib/api/binding.ts";

type PageProps = {
  params: Promise<{ releaseId: string }>;
};

export default async function AiReleaseDetailPage({ params }: PageProps) {
  const { releaseId } = await params;
  const liveReleases = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listLearningReleases().then((response) => response.items),
  });
  return (
    <LearningHubWorkspace
      view="releaseDetail"
      releaseId={decodeURIComponent(releaseId)}
      liveReleases={liveReleases}
    />
  );
}
