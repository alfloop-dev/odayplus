"use client";

import Link from "next/link";

export default function ExistingListingError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main data-testid="listing-route-error">
      <h1>既有 Listing 載入失敗</h1>
      <button onClick={reset} type="button">重新載入</button>
      <Link href="/w/expansion/listings">返回 Listing Inbox</Link>
    </main>
  );
}
