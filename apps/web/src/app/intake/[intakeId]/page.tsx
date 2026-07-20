import { redirect } from "next/navigation";

type PageProps = {
  params: Promise<{ intakeId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function IntakeRoutePage({ params, searchParams }: PageProps) {
  const { intakeId } = await params;
  const resolvedSearchParams = await searchParams;
  
  const query = new URLSearchParams();
  for (const [key, val] of Object.entries(resolvedSearchParams)) {
    if (val !== undefined) {
      if (Array.isArray(val)) {
        val.forEach((v) => query.append(key, v));
      } else {
        query.set(key, val);
      }
    }
  }
  
  query.set("selected", intakeId);
  query.set("dialog", "detail");
  
  redirect(`/w/expansion/listings?${query.toString()}`);
}
