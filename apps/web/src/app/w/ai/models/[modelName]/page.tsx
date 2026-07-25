import { LearningHubWorkspace } from "../../../../../../features/learninghub/LearningHubWorkspace.tsx";
import { getServerApiClient } from "../../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ modelName: string }>;
};

export default async function AiModelHistoryPage({ params }: PageProps) {
  const { modelName } = await params;
  const liveModels = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listLearningModels().then((response) => response.items),
  });
  return (
    <LearningHubWorkspace
      view="modelHistory"
      modelName={decodeURIComponent(modelName)}
      liveModels={liveModels}
    />
  );
}
