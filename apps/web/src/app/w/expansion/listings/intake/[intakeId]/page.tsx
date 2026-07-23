import { AssistedIntakeDetailPage } from "../../../../../../../features/operator/network/intake/AssistedIntakeSection.tsx";
import { getServerApiClient } from "../../../../../../lib/api/client.ts";
import { loadServerIntakeOperatorSession } from "../../../../../../lib/api/intakeOperatorSession.ts";

type PageProps = {
  params: Promise<{ intakeId: string }>;
};

export default async function IntakeDetailRoutePage({ params }: PageProps) {
  const [{ intakeId }, operatorSession] = await Promise.all([
    params,
    loadServerIntakeOperatorSession(await getServerApiClient()),
  ]);

  return (
    <AssistedIntakeDetailPage
      intakeId={decodeURIComponent(intakeId)}
      operatorSession={operatorSession}
    />
  );
}
