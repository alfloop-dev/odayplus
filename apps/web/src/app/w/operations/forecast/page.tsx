import { OperationsWorkspace } from "../../../../../features/operations/OperationsWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function ForecastPage({ searchParams }: PageProps) {
  return <OperationsWorkspace view="forecast" searchParams={await searchParams} />;
}
