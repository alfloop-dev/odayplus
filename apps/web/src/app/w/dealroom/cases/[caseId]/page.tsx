import { AvmWorkspace } from "../../../../../../features/avm/AvmWorkspace.tsx";

type PageProps = {
  params: Promise<{ caseId: string }>;
};

export default async function DealRoomCaseDetailPage({ params }: PageProps) {
  const { caseId } = await params;
  return <AvmWorkspace view="caseDetail" caseId={caseId} />;
}
