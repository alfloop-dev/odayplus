import { LearningHubWorkspace } from "../../../../../../features/learninghub/LearningHubWorkspace.tsx";

type PageProps = {
  params: Promise<{ releaseId: string }>;
};

export default async function AiReleaseDetailPage({ params }: PageProps) {
  const { releaseId } = await params;
  return <LearningHubWorkspace view="releaseDetail" releaseId={decodeURIComponent(releaseId)} />;
}
