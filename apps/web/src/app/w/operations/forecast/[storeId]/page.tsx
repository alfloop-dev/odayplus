import { OperationsWorkspace } from "../../../../../../features/operations/OperationsWorkspace.tsx";

type PageProps = {
  params: { storeId: string };
};

export default function StoreForecastDetailPage({ params }: PageProps) {
  return <OperationsWorkspace view="storeDetail" storeId={params.storeId} />;
}
