import type { Metadata } from "next";
import type { ReactNode } from "react";
// Token CSS variables first (single source of token values), then shell styles.
import "@oday-plus/design-tokens/tokens.css";
import "@oday-plus/ui/styles/shell.css";
import { OpsBoardFrame } from "./OpsBoardFrame.tsx";

export const metadata: Metadata = {
  title: "ODay Plus OpsBoard",
  description: "ODay Plus 營運決策平台 — OpsBoard shell (R0)",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-Hant">
      <body>
        <OpsBoardFrame>{children}</OpsBoardFrame>
      </body>
    </html>
  );
}
