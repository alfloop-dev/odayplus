import { LearningHubWorkspace } from "../../../../../../features/learninghub/LearningHubWorkspace.tsx";

type PageProps = {
  params: Promise<{ modelName: string }>;
};

export default async function AiModelHistoryPage({ params }: PageProps) {
  const { modelName } = await params;
  return <LearningHubWorkspace view="modelHistory" modelName={decodeURIComponent(modelName)} />;
}
