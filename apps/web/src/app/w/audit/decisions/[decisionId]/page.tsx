import { AuditWorkspace } from "../../../../../../../features/audit/AuditWorkspace.tsx";

type PageProps = {
  params: { decisionId: string };
};

export default function AuditDecisionDetailPage({ params }: PageProps) {
  return <AuditWorkspace view="decisionDetail" decisionId={decodeURIComponent(params.decisionId)} />;
}
