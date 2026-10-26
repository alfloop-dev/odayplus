import { OperatorConsole } from "../../../features/operator/OperatorConsole";
import { readDeploymentEnvironment } from "../../../features/operator/operatorEnvironment";

export default async function TasksPage({ searchParams }: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  return <OperatorConsole deploymentEnvironment={readDeploymentEnvironment()} searchParams={(await searchParams) ?? {}} shellView="tasks" />;
}
