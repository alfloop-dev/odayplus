import { OperatorConsole } from "../../../features/operator/OperatorConsole.tsx";

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function OperatorConsolePage({ searchParams }: PageProps) {
  return <OperatorConsole searchParams={searchParams} />;
}
