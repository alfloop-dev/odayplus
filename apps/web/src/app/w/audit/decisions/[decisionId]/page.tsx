import { AuditWorkspace } from "../../../../../../features/audit/AuditWorkspace.tsx";

type PageProps = {
  params: Promise<{ decisionId: string }>;
};

export default async function AuditDecisionDetailPage({ params }: PageProps) {
  const { decisionId } = await params;
  return <AuditWorkspace view="decisionDetail" decisionId={decodeURIComponent(decisionId)} />;
}
