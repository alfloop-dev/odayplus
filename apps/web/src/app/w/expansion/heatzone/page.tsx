import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function HeatZonePage({ searchParams }: PageProps) {
  return <ExpansionWorkspace view="heatzone" searchParams={searchParams} />;
}
