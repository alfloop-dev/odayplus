import { OperatorConsole } from "../../../../../features/operator/OperatorConsole";
import { readDeploymentEnvironment } from "../../../../../features/operator/operatorEnvironment";

export default async function SupervisorReportPage({ params, searchParams }: {
  params: Promise<{ reportId: string }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <OperatorConsole deploymentEnvironment={readDeploymentEnvironment()} searchParams={(await searchParams) ?? {}}
    shellView="notifications" notificationReportId={(await params).reportId} />;
}
