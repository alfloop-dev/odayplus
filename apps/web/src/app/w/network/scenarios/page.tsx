import { NetPlanWorkspace } from "../../../../../features/netplan/NetPlanWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function NetworkScenariosPage({ searchParams }: PageProps) {
  return <NetPlanWorkspace view="scenarios" searchParams={searchParams} />;
}
