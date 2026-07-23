import type { OdpApiClient } from "@oday-plus/openapi-client";
import {
  parseIntakeOperatorSession,
  unavailableIntakeOperatorSession,
  type IntakeOperatorSession,
} from "../../../features/operator/network/intake/intakeOperatorSession.ts";

export async function loadServerIntakeOperatorSession(
  client: OdpApiClient | null,
): Promise<IntakeOperatorSession> {
  if (!client) return unavailableIntakeOperatorSession("OPERATOR_BOOTSTRAP_UNAVAILABLE");

  try {
    const bootstrap = await client.getOperatorBootstrap();
    return parseIntakeOperatorSession(bootstrap);
  } catch {
    return unavailableIntakeOperatorSession("OPERATOR_BOOTSTRAP_UNAVAILABLE");
  }
}
