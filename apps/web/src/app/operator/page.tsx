import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Operator Console (rebuilding) | Oday Plus",
  description: "Operator Console is being rebuilt from the R7 / Package 10 design.",
};

// The R5/Package-7 operator console shell was retired on 2026-07-25 per owner
// decision, pending a clean R7/Package 10 API-connected rebuild
// (docs/design/OPERATOR_CONSOLE_R7_REBUILD_EXECUTION_TASKS_2026-07-25.md).
// Shared operator modules used by other features (network client, assisted-intake
// section, role types) are intentionally retained under apps/web/features/operator/network/.
export default function OperatorPage() {
  return (
    <main style={{ padding: "3rem", maxWidth: 720, margin: "0 auto", lineHeight: 1.6 }}>
      <h1>Operator Console — rebuilding</h1>
      <p>
        The Operator Console is being rebuilt from the canonical <strong>R7 / Package 10</strong>{" "}
        Claude Design, wired to the live <code>/api/v1/operator/*</code> API. The previous R5
        console shell was retired; see the rebuild tasks (ODP-OC-R7-FE-001 / AUTH-001 / VDC-001).
      </p>
    </main>
  );
}
