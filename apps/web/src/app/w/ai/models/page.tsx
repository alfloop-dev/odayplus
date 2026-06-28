import { LearningHubWorkspace } from "../../../../../features/learninghub/LearningHubWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function AiModelsPage({ searchParams }: PageProps) {
  return <LearningHubWorkspace view="models" searchParams={await searchParams} />;
}
