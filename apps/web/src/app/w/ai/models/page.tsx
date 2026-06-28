import { LearningHubWorkspace } from "../../../../../features/learninghub/LearningHubWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function AiModelsPage({ searchParams }: PageProps) {
  return <LearningHubWorkspace view="models" searchParams={searchParams} />;
}
