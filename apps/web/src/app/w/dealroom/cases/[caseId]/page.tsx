import { AvmWorkspace } from "../../../../../../features/avm/AvmWorkspace.tsx";

import { getServerApiClient } from "../../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../../lib/api/binding.ts";
import { headers } from "next/headers";

type PageProps = {
  params: Promise<{ caseId: string }>;
};

export const dynamic = "force-dynamic";

export default async function DealRoomCaseDetailPage({ params }: PageProps) {
  const { caseId } = await params;
  const reqHeaders = await headers();
  const isProduction =
    process.env.NODE_ENV === "production" ||
    process.env.NEXT_PUBLIC_PRODUCTION_MODE === "true" ||
    reqHeaders.get("x-production-mode") === "true";

  const liveCases = await loadApiBinding({
    client: await getServerApiClient(),
    fetcher: (client) => client.listAvmCases().then((response) => response.items),
  });

  const subjectId = reqHeaders.get("x-subject-id") || "product-ui-analyst";
  const roles = reqHeaders.get("x-roles") || "analyst,finance";
  const tenantId = reqHeaders.get("x-tenant-id") || undefined;

  return (
    <AvmWorkspace
      view="caseDetail"
      caseId={caseId}
      liveCases={liveCases}
      isProduction={isProduction}
      currentUser={{ subjectId, roles, tenantId }}
    />
  );
}
