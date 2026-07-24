import { LearningHubWorkspace } from "../../../../../features/learninghub/LearningHubWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function AiModelsPage({ searchParams }: PageProps) {
  const liveModels = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listLearningModels().then((response) => response.items),
  });
  return (
    <LearningHubWorkspace
      view="models"
      searchParams={await searchParams}
      liveModels={liveModels}
    />
  );
}
