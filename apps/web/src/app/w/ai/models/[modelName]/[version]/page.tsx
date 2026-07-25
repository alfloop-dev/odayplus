import { LearningHubWorkspace } from "../../../../../../../features/learninghub/LearningHubWorkspace.tsx";
import { getServerApiClient } from "../../../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ modelName: string; version: string }>;
};

export default async function AiModelVersionPage({ params }: PageProps) {
  const { modelName, version } = await params;
  const liveModels = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listLearningModels().then((response) => response.items),
  });
  return (
    <LearningHubWorkspace
      view="modelDetail"
      modelName={decodeURIComponent(modelName)}
      version={decodeURIComponent(version)}
      liveModels={liveModels}
    />
  );
}
