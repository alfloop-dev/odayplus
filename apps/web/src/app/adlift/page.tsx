import { AdLiftWorkspace } from "../../../features/adlift/AdLiftWorkspace.tsx";

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function AdLiftPage({ searchParams }: PageProps) {
  return <AdLiftWorkspace searchParams={await searchParams} />;
}
