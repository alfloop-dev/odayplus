// Shared native transport primitives. No domain validation or command state
// belongs here: Console and page polling bind their existing distinct leases.
export type CanonicalIntakeReadContext = {
  fetchImpl: typeof fetch;
  contextKey: string;
  headers: HeadersInit;
  // Namespace for non-authoritative receipt references. Console supplies the
  // freshly verified account and bootstrap tenant; every GET reauthorizes.
  receiptOwner?: { subject: string; tenantId: string; roleId: string };
};

/** Bind HTTP and deferred bodies to the caller's existing authenticated lease.
 * Commands use the Console lease; score polling keeps its shorter page lease. */
export function bindCanonicalIntakeFetch(fetchImpl: typeof fetch, current: () => Promise<boolean>): typeof fetch {
  const requireCurrent = async () => {
    if (!await current()) throw new Error("Network read account or persona changed");
  };
  const guardBody = (response: Response): Response => {
    const json = response.json.bind(response);
    const text = response.text.bind(response);
    const clone = response.clone.bind(response);
    response.json = async () => { await requireCurrent(); const body: unknown = await json(); await requireCurrent(); return body; };
    response.text = async () => { await requireCurrent(); const body = await text(); await requireCurrent(); return body; };
    response.clone = () => guardBody(clone());
    return response;
  };
  return async (input, init) => {
    await requireCurrent();
    const response = await fetchImpl(input, init);
    await requireCurrent();
    return guardBody(response);
  };
}

export class CanonicalIntakeReadError extends Error {
  constructor(public status: number, public code: string, public correlationId: string) {
    super(code);
  }
}

