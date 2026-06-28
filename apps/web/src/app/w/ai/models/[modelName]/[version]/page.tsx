import { LearningHubWorkspace } from "../../../../../../../features/learninghub/LearningHubWorkspace.tsx";

type PageProps = {
  params: { modelName: string; version: string };
};

export default function AiModelVersionPage({ params }: PageProps) {
  return (
    <LearningHubWorkspace
      view="modelDetail"
      modelName={decodeURIComponent(params.modelName)}
      version={decodeURIComponent(params.version)}
    />
  );
}
