import { NetPlanWorkspace } from "../../../../../../features/netplan/NetPlanWorkspace.tsx";

type PageProps = {
  params: Promise<{ scenarioId: string }>;
};

export default async function NetworkScenarioDetailPage({ params }: PageProps) {
  const { scenarioId } = await params;
  return <NetPlanWorkspace view="scenarioDetail" scenarioId={scenarioId} />;
}
