import { AvmWorkspace } from "../../../../../features/avm/AvmWorkspace.tsx";
import { getServerApiClient } from "../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../lib/api/binding.ts";
import { headers } from "next/headers";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

// Server state must reflect live backend rows, so this route is dynamic.
export const dynamic = "force-dynamic";

export default async function DealRoomCasesPage({ searchParams }: PageProps) {
  const reqHeaders = await headers();
  const isProduction =
    process.env.NODE_ENV === "production" ||
    process.env.NEXT_PUBLIC_PRODUCTION_MODE === "true" ||
    reqHeaders.get("x-production-mode") === "true";

  const liveCases = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAvmCases().then((response) => response.items),
  });
  return (
    <AvmWorkspace
      view="cases"
      searchParams={await searchParams}
      liveCases={liveCases}
      isProduction={isProduction}
    />
  );
}
