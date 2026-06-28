import { LearningHubWorkspace } from "../../../../../../features/learninghub/LearningHubWorkspace.tsx";

type PageProps = {
  params: { releaseId: string };
};

export default function AiReleaseDetailPage({ params }: PageProps) {
  return <LearningHubWorkspace view="releaseDetail" releaseId={decodeURIComponent(params.releaseId)} />;
}
