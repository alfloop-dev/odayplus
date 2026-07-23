import { ExistingListingDetailPage } from "../../../../../../features/operator/network/intake/ExistingListingDetailPage.tsx";
import { getServerApiClient } from "../../../../../lib/api/client.ts";
import { loadServerIntakeOperatorSession } from "../../../../../lib/api/intakeOperatorSession.ts";

type PageProps = {
  params: Promise<{ listingId: string }>;
};

export default async function ExistingListingRoutePage({ params }: PageProps) {
  const [{ listingId }, operatorSession] = await Promise.all([
    params,
    loadServerIntakeOperatorSession(await getServerApiClient()),
  ]);

  return (
    <ExistingListingDetailPage
      listingId={decodeURIComponent(listingId)}
      operatorSession={operatorSession}
    />
  );
}
