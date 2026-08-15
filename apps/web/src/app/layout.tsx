import type { Metadata } from "next";
import type { ReactNode } from "react";
// Token CSS variables first (single source of token values), then shell styles.
import "@oday-plus/design-tokens/tokens.css";
import "@oday-plus/ui/styles/shell.css";

export const metadata: Metadata = {
  title: "Oday Plus",
  description: "Oday Plus 營運管理平台",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
