import { AvmWorkspace } from "../../../../../features/avm/AvmWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";
import { isProductionMode } from "../../../../../features/shell/mode.ts";
import { headers } from "next/headers";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

// Server state must reflect live backend rows, so this route is dynamic.
export const dynamic = "force-dynamic";

export default async function DealRoomCasesPage({ searchParams }: PageProps) {
  const reqHeaders = await headers();
  const requestedProductionMode = reqHeaders.get("x-production-mode");
  const isProduction =
    requestedProductionMode === "true" ||
    isProductionMode();

  const liveCases = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAvmCases().then((response) => response.items),
  });
  const subjectId = reqHeaders.get("x-subject-id");
  const roles = reqHeaders.get("x-roles");
  const tenantId = reqHeaders.get("x-tenant-id") || undefined;
  const currentUser = subjectId && roles ? { subjectId, roles, tenantId } : undefined;

  return (
    <AvmWorkspace
      view="cases"
      searchParams={await searchParams}
      liveCases={liveCases}
      isProduction={isProduction}
      currentUser={currentUser}
    />
  );
}
