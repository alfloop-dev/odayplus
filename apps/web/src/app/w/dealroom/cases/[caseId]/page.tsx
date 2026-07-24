import { AvmWorkspace } from "../../../../../../features/avm/AvmWorkspace.tsx";

import { getServerApiClient } from "../../../../../lib/api/client.ts";
import { loadApiBinding } from "../../../../../lib/api/binding.ts";
import { isProductionMode } from "../../../../../../features/shell/mode.ts";
import { headers } from "next/headers";

type PageProps = {
  params: Promise<{ caseId: string }>;
};

export const dynamic = "force-dynamic";

export default async function DealRoomCaseDetailPage({ params }: PageProps) {
  const { caseId } = await params;
  const reqHeaders = await headers();
  const requestedProductionMode = reqHeaders.get("x-production-mode");
  const isProduction =
    requestedProductionMode === "true" ||
    (requestedProductionMode !== "false" && isProductionMode());

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
