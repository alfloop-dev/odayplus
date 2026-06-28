import { AvmWorkspace } from "../../../../../features/avm/AvmWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function DealRoomCasesPage({ searchParams }: PageProps) {
  return <AvmWorkspace view="cases" searchParams={await searchParams} />;
}
