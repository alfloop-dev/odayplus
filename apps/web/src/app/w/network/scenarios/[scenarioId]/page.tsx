import { NetPlanWorkspace } from "../../../../../../features/netplan/NetPlanWorkspace.tsx";

type PageProps = {
  params: { scenarioId: string };
};

export default function NetworkScenarioDetailPage({ params }: PageProps) {
  return <NetPlanWorkspace view="scenarioDetail" scenarioId={params.scenarioId} />;
}
