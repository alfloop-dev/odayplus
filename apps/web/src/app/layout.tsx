import type { Metadata } from "next";
import { IBM_Plex_Mono, Noto_Sans_TC } from "next/font/google";
import type { ReactNode } from "react";
// Token CSS variables first (single source of token values), then shell styles.
import "@oday-plus/design-tokens/tokens.css";
import "@oday-plus/ui/styles/shell.css";

// Package 10 typography, self-hosted at build time (no runtime third-party
// request). The fonts are exposed as CSS variables; product surfaces opt in.
const notoSansTc = Noto_Sans_TC({
  display: "swap",
  preload: false,
  subsets: ["latin"],
  variable: "--font-noto-sans-tc",
});

const ibmPlexMono = IBM_Plex_Mono({
  display: "swap",
  preload: false,
  subsets: ["latin"],
  variable: "--font-ibm-plex-mono",
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Oday Plus",
  description: "Oday Plus 營運管理平台",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html className={`${notoSansTc.variable} ${ibmPlexMono.variable}`} lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
