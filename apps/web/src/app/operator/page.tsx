import type { Metadata } from "next";
import { OperatorConsole } from "../../../features/operator";

export const metadata: Metadata = {
  title: "Operator Console | Oday Plus",
  description: "Oday Plus operator console design prototype",
};

type PageProps = {
  searchParams?: Record<string, string | string[] | undefined>;
};

export default function OperatorPage({ searchParams }: PageProps) {
  return <OperatorConsole searchParams={searchParams} />;
}
