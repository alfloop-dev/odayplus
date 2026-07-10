import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function CandidatesPage({ searchParams }: PageProps) {
  return <ExpansionWorkspace view="candidates" searchParams={await searchParams} />;
}
