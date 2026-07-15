import type { Metadata } from "next";
import type { ReactNode } from "react";
// Token CSS variables first (single source of token values), then shell styles.
import "@oday-plus/design-tokens/tokens.css";
import "@oday-plus/ui/styles/shell.css";
import { OpsBoardFrame, type ShellEnvironment } from "./OpsBoardFrame.tsx";

export const metadata: Metadata = {
  title: "ODay Plus OpsBoard",
  description: "ODay Plus 營運決策平台 — 產品外殼",
};

const ENVIRONMENTS: ShellEnvironment[] = ["dev", "staging", "production"];

/**
 * The header's environment chip tells an operator whether a write is real.
 * An unrecognised ODP_ENV therefore falls back to "dev" rather than being
 * displayed verbatim: mislabelling production as something benign is the one
 * failure mode worth being conservative about.
 */
function resolveEnvironment(raw: string | undefined): ShellEnvironment {
  const value = raw?.trim();
  return ENVIRONMENTS.find((env) => env === value) ?? "dev";
}

/**
 * The layout deliberately does NOT fetch the header counts.
 *
 * Doing so made every route in the app — including heavy non-shell ones like
 * /expansion — block its server render on the shell's aggregate endpoint, which
 * slowed navigation across surfaces this task does not own. The counts are a
 * header ornament, not something worth putting on the critical path of every
 * page, so OpsBoardFrame loads them after mount instead.
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-Hant">
      <body>
        <OpsBoardFrame environment={resolveEnvironment(process.env.ODP_ENV)}>
          {children}
        </OpsBoardFrame>
      </body>
    </html>
  );
}
