import { NetPlanWorkspace } from "../../../../../features/netplan/NetPlanWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function NetworkScenariosPage({ searchParams }: PageProps) {
  return <NetPlanWorkspace view="scenarios" searchParams={await searchParams} />;
}
