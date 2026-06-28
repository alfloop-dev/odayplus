import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function SiteScorePage({ searchParams }: PageProps) {
  return <ExpansionWorkspace view="sitescore" searchParams={searchParams} />;
}
