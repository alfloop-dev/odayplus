import { ExpansionWorkspace } from "../../../../../features/expansion/ExpansionWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function ListingsPage({ searchParams }: PageProps) {
  return <ExpansionWorkspace view="listings" searchParams={searchParams} />;
}
