import { AvmWorkspace } from "../../../../../features/avm/AvmWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function DealRoomCasesPage({ searchParams }: PageProps) {
  return <AvmWorkspace view="cases" searchParams={searchParams} />;
}
