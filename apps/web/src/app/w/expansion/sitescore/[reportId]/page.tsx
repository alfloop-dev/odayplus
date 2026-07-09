import { ExpansionWorkspace } from "../../../../../../features/expansion/ExpansionWorkspace.tsx";

type PageProps = {
  params: Promise<{ reportId: string }>;
};

export default async function SiteScoreDetailPage({ params }: PageProps) {
  const { reportId } = await params;
  return <ExpansionWorkspace view="sitescoreDetail" reportId={reportId} />;
}
