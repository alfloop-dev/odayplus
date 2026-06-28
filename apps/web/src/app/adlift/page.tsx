import { AdLiftWorkspace } from "../../../features/adlift/AdLiftWorkspace.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function AdLiftPage({ searchParams }: PageProps) {
  return <AdLiftWorkspace searchParams={searchParams} />;
}
