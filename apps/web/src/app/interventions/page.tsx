import { InterventionWorkspace } from "../../../features/intervention/InterventionWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function InterventionsPage({ searchParams }: PageProps) {
  return <InterventionWorkspace searchParams={await searchParams} />;
}
