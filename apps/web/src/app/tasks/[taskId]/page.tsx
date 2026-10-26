import { OperatorConsole } from "../../../../features/operator/OperatorConsole";
import { readDeploymentEnvironment } from "../../../../features/operator/operatorEnvironment";

export default async function TaskPage({ params, searchParams }: {
  params: Promise<{ taskId: string }>;
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { taskId } = await params;
  return <OperatorConsole deploymentEnvironment={readDeploymentEnvironment()}
    searchParams={{ ...((await searchParams) ?? {}), taskId }} shellView="tasks" />;
}
