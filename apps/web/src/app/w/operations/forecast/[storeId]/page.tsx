import { OperationsWorkspace } from "../../../../../../features/operations/OperationsWorkspace.tsx";

type PageProps = {
  params: Promise<{ storeId: string }>;
};

export default async function StoreForecastDetailPage({ params }: PageProps) {
  const { storeId } = await params;
  return <OperationsWorkspace view="storeDetail" storeId={storeId} />;
}
