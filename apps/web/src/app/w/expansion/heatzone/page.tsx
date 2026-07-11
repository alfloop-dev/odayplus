import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function HeatZonePage({ searchParams }: PageProps) {
  return <ExpansionWorkspace view="heatzone" searchParams={await searchParams} />;
}
