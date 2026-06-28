import { LearningHubWorkspace } from "../../../../../../features/learninghub/LearningHubWorkspace.tsx";

type PageProps = {
  params: { modelName: string };
};

export default function AiModelHistoryPage({ params }: PageProps) {
  return <LearningHubWorkspace view="modelHistory" modelName={decodeURIComponent(params.modelName)} />;
}
