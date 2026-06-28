import { ExpansionWorkspace } from "../../../../../../features/expansion/ExpansionWorkspace.tsx";

type PageProps = {
  params: { reportId: string };
};

export default function SiteScoreDetailPage({ params }: PageProps) {
  return <ExpansionWorkspace view="sitescoreDetail" reportId={params.reportId} />;
}
