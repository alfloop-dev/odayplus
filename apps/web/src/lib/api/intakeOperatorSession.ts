import type { OdpApiClient } from "@oday-plus/openapi-client";
import {
  parseCanonicalIntakeOperatorSession,
  parseIntakeOperatorSession,
  unavailableIntakeOperatorSession,
  type IntakeOperatorSession,
} from "../../../features/operator/network/intake/intakeOperatorSession.ts";

export async function loadServerIntakeOperatorSession(
  client: OdpApiClient | null,
): Promise<IntakeOperatorSession> {
  if (!client) return unavailableIntakeOperatorSession("OPERATOR_BOOTSTRAP_UNAVAILABLE");

  try {
    const bootstrap = await client.getIntakeInboxBootstrap();
    return parseCanonicalIntakeOperatorSession(bootstrap);
  } catch {
    try {
      const legacyBootstrap = await client.getOperatorBootstrap();
      return parseIntakeOperatorSession(legacyBootstrap);
    } catch {
      return unavailableIntakeOperatorSession("OPERATOR_BOOTSTRAP_UNAVAILABLE");
    }
  }
}
