import { OperationsWorkspace } from "../../../../../features/operations/OperationsWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function AlertsPage({ searchParams }: PageProps) {
  return <OperationsWorkspace view="alerts" searchParams={searchParams} />;
}
