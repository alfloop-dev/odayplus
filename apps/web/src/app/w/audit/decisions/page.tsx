import { AuditWorkspace } from "../../../../../../features/audit/AuditWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function AuditDecisionsPage({ searchParams }: PageProps) {
  return <AuditWorkspace view="decisions" searchParams={searchParams} />;
}
