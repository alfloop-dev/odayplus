import type { OperatorRoleId } from "../../../../../../../features/operator/navigation.tsx";
import { AssistedIntakeDetailPage } from "../../../../../../../features/operator/network/intake/AssistedIntakeSection.tsx";

type SearchParams = Record<string, string | string[] | undefined>;

type PageProps = {
  params: Promise<{ intakeId: string }>;
  searchParams?: Promise<SearchParams>;
};

const INTAKE_ROUTE_ROLES: readonly OperatorRoleId[] = [
  "ops-lead",
  "cs-lead",
  "field-lead",
  "marketing-manager",
  "expansion-manager",
  "pm-audit",
];

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function routeRole(value: string | undefined): OperatorRoleId {
  return INTAKE_ROUTE_ROLES.includes(value as OperatorRoleId)
    ? (value as OperatorRoleId)
    : "expansion-manager";
}

export default async function IntakeDetailRoutePage({ params, searchParams }: PageProps) {
  const [{ intakeId }, query = {}] = await Promise.all([params, searchParams]);

  return (
    <AssistedIntakeDetailPage
      activeRoleId={routeRole(first(query.role))}
      activeSubjectId={first(query.subject)}
      intakeId={decodeURIComponent(intakeId)}
    />
  );
}
