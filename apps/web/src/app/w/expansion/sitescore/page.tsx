import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function SiteScorePage({ searchParams }: PageProps) {
  return <ExpansionWorkspace view="sitescore" searchParams={await searchParams} />;
}
