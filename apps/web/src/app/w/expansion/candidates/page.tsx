import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function CandidatesPage({ searchParams }: PageProps) {
  return <ExpansionWorkspace view="candidates" searchParams={searchParams} />;
}
