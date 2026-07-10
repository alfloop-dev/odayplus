import { AuditWorkspace } from "../../../../../features/audit/AuditWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function AuditDecisionsPage({ searchParams }: PageProps) {
  return <AuditWorkspace view="decisions" searchParams={await searchParams} />;
}
