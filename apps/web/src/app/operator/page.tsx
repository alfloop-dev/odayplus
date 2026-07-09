import type { Metadata } from "next";
import { OperatorConsole } from "../../../features/operator";

export const metadata: Metadata = {
  title: "Operator Console | Oday Plus",
  description: "Oday Plus operator console design prototype",
};

type PageProps = {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
};

export default async function OperatorPage({ searchParams }: PageProps) {
  return <OperatorConsole searchParams={await searchParams} />;
}
