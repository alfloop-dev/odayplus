import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";

export const dynamic = "force-dynamic";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function CandidatesPage({ searchParams }: PageProps) {
  const liveCandidates = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listCandidates().then((response) => response.candidates),
  });
  return <ExpansionWorkspace view="candidates" searchParams={await searchParams} liveCandidates={liveCandidates} />;
}
