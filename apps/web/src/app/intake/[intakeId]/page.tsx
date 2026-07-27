import { OperatorConsole } from "../../../../features/operator/OperatorConsole.tsx";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ intakeId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function IntakeRoutePage({ params, searchParams }: PageProps) {
  const { intakeId } = await params;
  const resolvedSearchParams = await searchParams;

  return (
    <OperatorConsole
      searchParams={{
        ...resolvedSearchParams,
        ws: "network",
        tab: "radar",
        selected: intakeId,
        dialog: "detail",
      }}
    />
  );
}
