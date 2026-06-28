import { InterventionWorkspace } from "../../../features/intervention/InterventionWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function InterventionsPage({ searchParams }: PageProps) {
  return <InterventionWorkspace searchParams={searchParams} />;
}
