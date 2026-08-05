import type { Metadata } from "next";
import { FeatureFlagsAdminWorkspace } from "../../../../../features/operator/FeatureFlagsAdminWorkspace";

export const metadata: Metadata = {
  title: "Feature Flag Management | Oday Plus Admin",
  description: "FR-SHARED-004 / UX-SCR-ADMIN-002 Feature Flag Kill-Switch Control Panel",
};

export default function FeatureFlagsAdminPage() {
  return (
    <div style={{ minHeight: "100vh", background: "#0b0f17", padding: "2rem" }}>
      <FeatureFlagsAdminWorkspace />
    </div>
  );
}
