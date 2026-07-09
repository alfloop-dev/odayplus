import type { Metadata } from "next";
import { OperatorConsole } from "../../../features/operator";

export const metadata: Metadata = {
  title: "Operator Console | Oday Plus",
  description: "Oday Plus operator console design prototype",
};

export default function OperatorPage() {
  return <OperatorConsole />;
}

