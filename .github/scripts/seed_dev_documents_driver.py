"""One-off dev seeding driver: populate the Operator Console *document-store*
expansion panels (展店 network listings + candidate sites) for the seeded tenant.

The data-plane backfill already filled ``core.*`` (門市/營收). This driver fills
the separate tenant-scoped document store that the 展店 panel reads, writing
through the production ``DurableListingRepository`` + ``DurableAssistedIntakeRepository``
wrapped in the identical ``TenantScopedDocumentStore(store, tenant)`` that the
live read path applies.

Runs inside the seed-dev-documents GitHub Actions job: GCP auth via WIF, dev
Cloud SQL reached through the Cloud SQL Auth Proxy on 127.0.0.1:5432. Reads:
  RAW_DB_URL    - dev DB URL fetched from Secret Manager
  TARGET_TENANT - uuid5-derived tenant for the synthetic merchant
No secrets are printed.
"""

from __future__ import annotations

import os
import urllib.parse


def _proxy_dsn(raw: str) -> str:
    u = urllib.parse.urlparse(raw)
    user = urllib.parse.quote(urllib.parse.unquote(u.username or ""), safe="")
    pw = urllib.parse.quote(urllib.parse.unquote(u.password or ""), safe="")
    db = (u.path or "/").lstrip("/") or "postgres"
    return f"postgresql://{user}:{pw}@127.0.0.1:5432/{db}"


def main() -> int:
    target = os.environ["TARGET_TENANT"].strip()
    dsn = _proxy_dsn(os.environ["RAW_DB_URL"])

    from product_ops.data_platform.seed_operator_documents import (
        OperatorDocsConfig,
        seed_documents,
    )
    from shared.infrastructure.persistence.operator_domains import (
        TenantScopedDocumentStore,
    )
    from shared.infrastructure.persistence.postgresql import (
        PostgresDocumentStore,
        PostgresEngine,
    )
    from shared.infrastructure.persistence.repositories import DurableListingRepository

    engine = PostgresEngine(dsn)
    try:
        store = PostgresDocumentStore(engine)

        before = DurableListingRepository(TenantScopedDocumentStore(store, target))
        print(
            "PREFLIGHT before: "
            f"listings={len(before.list_listings())} "
            f"candidates={len(before.list_candidates())}",
            flush=True,
        )

        config = OperatorDocsConfig(tenant_id=target)
        counts = seed_documents(TenantScopedDocumentStore(store, target), config)
        print(f"SEED wrote: {counts}", flush=True)

        # Re-resolve through a *fresh* tenant wrapper over the same engine, the
        # way OperatorLiveRepository does, to prove the rows are in the tenant
        # partition the console reads rather than just this instance's cache.
        reader = DurableListingRepository(TenantScopedDocumentStore(store, target))
        n_listings = len(reader.list_listings())
        n_candidates = len(reader.list_candidates())
        print(
            f"VERIFY tenant={target} listings={n_listings} candidates={n_candidates}",
            flush=True,
        )
        if n_listings == 0 or n_candidates == 0:
            print("RESULT: FAILED (no document rows visible after seeding)", flush=True)
            return 1
        print("RESULT: OK", flush=True)
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
