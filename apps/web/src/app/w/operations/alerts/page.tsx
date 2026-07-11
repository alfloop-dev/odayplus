import { OperationsWorkspace } from "../../../../../features/operations/OperationsWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function AlertsPage({ searchParams }: PageProps) {
  return <OperationsWorkspace view="alerts" searchParams={await searchParams} />;
}
