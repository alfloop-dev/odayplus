import { OperationsWorkspace } from "../../../../../features/operations/OperationsWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function ForecastPage({ searchParams }: PageProps) {
  return <OperationsWorkspace view="forecast" searchParams={searchParams} />;
}
