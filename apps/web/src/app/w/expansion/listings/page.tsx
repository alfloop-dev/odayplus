import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadServerIntakeOperatorSession } from "../../../../lib/api/intakeOperatorSession.ts";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function ListingsPage({ searchParams }: PageProps) {
  const operatorSession = await loadServerIntakeOperatorSession(
    await getServerApiClient(),
  );
  return (
    <ExpansionWorkspace
      operatorSession={operatorSession}
      searchParams={await searchParams}
      view="listings"
    />
  );
}
