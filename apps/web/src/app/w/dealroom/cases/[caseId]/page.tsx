import { AvmWorkspace } from "../../../../../../features/avm/AvmWorkspace.tsx";

type PageProps = {
  params: { caseId: string };
};

export default function DealRoomCaseDetailPage({ params }: PageProps) {
  return <AvmWorkspace view="caseDetail" caseId={params.caseId} />;
}
