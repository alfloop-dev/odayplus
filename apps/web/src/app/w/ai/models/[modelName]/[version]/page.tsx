import { LearningHubWorkspace } from "../../../../../../../features/learninghub/LearningHubWorkspace.tsx";

type PageProps = {
  params: Promise<{ modelName: string; version: string }>;
};

export default async function AiModelVersionPage({ params }: PageProps) {
  const { modelName, version } = await params;
  return (
    <LearningHubWorkspace
      view="modelDetail"
      modelName={decodeURIComponent(modelName)}
      version={decodeURIComponent(version)}
    />
  );
}
