"""Generic durable aggregate store backed by :class:`SqliteEngine`.

The module repositories hold rich, frozen dataclass aggregates (with nested
dataclasses, ``datetime`` fields, and ``StrEnum`` members). Rather than force a
hand-written ``from_dict`` onto every domain type, aggregates are serialized
with :mod:`pickle`, which round-trips these structures exactly. The data is our
own and written only by our own process, so the usual untrusted-pickle concern
does not apply here.

Each row also carries plain index columns (``group_key``, ``seq``,
``ordinal``, ``correlation_id``) so the durable repositories can reproduce the
in-memory lookup/versioning semantics with real SQL queries.
"""

from __future__ import annotations

import pickle
from datetime import UTC, datetime
from typing import Any

from shared.infrastructure.persistence.engine import SqliteEngine


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SqliteDocumentStore:
    """Durable key/value + grouped-version store over ``durable_documents``."""

    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    # -- writes -----------------------------------------------------------

    def put(
        self,
        collection: str,
        doc_id: str,
        obj: Any,
        *,
        group_key: str | None = None,
        seq: int | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Upsert ``obj`` under ``(collection, doc_id)``.

        Insertion order (``ordinal``) is preserved across updates: a re-put of
        an existing ``doc_id`` keeps its original ordinal, matching the
        insertion-ordered iteration of the in-memory ``dict`` repositories.
        """
        blob = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        existing = self._engine.query_one(
            "SELECT ordinal FROM durable_documents WHERE collection = ? AND doc_id = ?",
            (collection, doc_id),
        )
        ordinal = (
            int(existing["ordinal"])
            if existing is not None
            else self._engine.next_ordinal(f"documents:{collection}")
        )
        self._engine.execute(
            "INSERT INTO durable_documents("
            "  collection, doc_id, group_key, seq, ordinal, correlation_id, data, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(collection, doc_id) DO UPDATE SET "
            "  group_key = excluded.group_key, "
            "  seq = excluded.seq, "
            "  correlation_id = excluded.correlation_id, "
            "  data = excluded.data",
            (
                collection,
                doc_id,
                group_key,
                seq,
                ordinal,
                correlation_id,
                blob,
                _now(),
            ),
        )

    def append_version(
        self,
        collection: str,
        doc_id: str,
        obj: Any,
        *,
        group_key: str,
        correlation_id: str | None = None,
    ) -> int:
        """Append a new version within ``group_key`` and return its 1-based seq."""
        next_seq = self.count_in_group(collection, group_key) + 1
        self.put(
            collection,
            doc_id,
            obj,
            group_key=group_key,
            seq=next_seq,
            correlation_id=correlation_id,
        )
        return next_seq

    def replace_latest_in_group(
        self,
        collection: str,
        obj: Any,
        *,
        group_key: str,
        correlation_id: str | None = None,
    ) -> int:
        """Overwrite the highest-seq row in ``group_key`` (or append if empty)."""
        row = self._engine.query_one(
            "SELECT doc_id, seq FROM durable_documents "
            "WHERE collection = ? AND group_key = ? "
            "ORDER BY seq DESC, ordinal DESC LIMIT 1",
            (collection, group_key),
        )
        if row is None:
            return self.append_version(
                collection, group_key, obj, group_key=group_key, correlation_id=correlation_id
            )
        self.put(
            collection,
            row["doc_id"],
            obj,
            group_key=group_key,
            seq=int(row["seq"]),
            correlation_id=correlation_id,
        )
        return int(row["seq"])

    # -- reads ------------------------------------------------------------

    def get(self, collection: str, doc_id: str) -> Any | None:
        row = self._engine.query_one(
            "SELECT data FROM durable_documents WHERE collection = ? AND doc_id = ?",
            (collection, doc_id),
        )
        return None if row is None else pickle.loads(row["data"])

    def list_all(self, collection: str) -> list[Any]:
        rows = self._engine.query(
            "SELECT data FROM durable_documents WHERE collection = ? ORDER BY ordinal",
            (collection,),
        )
        return [pickle.loads(r["data"]) for r in rows]

    def list_by_group(self, collection: str, group_key: str) -> list[Any]:
        rows = self._engine.query(
            "SELECT data FROM durable_documents "
            "WHERE collection = ? AND group_key = ? ORDER BY seq, ordinal",
            (collection, group_key),
        )
        return [pickle.loads(r["data"]) for r in rows]

    def latest_in_group(self, collection: str, group_key: str) -> Any | None:
        row = self._engine.query_one(
            "SELECT data FROM durable_documents "
            "WHERE collection = ? AND group_key = ? "
            "ORDER BY seq DESC, ordinal DESC LIMIT 1",
            (collection, group_key),
        )
        return None if row is None else pickle.loads(row["data"])

    def latest_per_group(self, collection: str) -> list[Any]:
        """Latest row of each group_key, ordered by first insertion."""
        rows = self._engine.query(
            "SELECT d.data FROM durable_documents d "
            "JOIN ("
            "  SELECT group_key, MAX(seq) AS max_seq, MIN(ordinal) AS first_ordinal "
            "  FROM durable_documents WHERE collection = ? AND group_key IS NOT NULL "
            "  GROUP BY group_key"
            ") g ON d.group_key = g.group_key AND d.seq = g.max_seq "
            "WHERE d.collection = ? "
            "ORDER BY g.first_ordinal",
            (collection, collection),
        )
        return [pickle.loads(r["data"]) for r in rows]

    def count_in_group(self, collection: str, group_key: str) -> int:
        row = self._engine.query_one(
            "SELECT COUNT(*) AS n FROM durable_documents "
            "WHERE collection = ? AND group_key = ?",
            (collection, group_key),
        )
        return 0 if row is None else int(row["n"])


__all__ = ["SqliteDocumentStore"]
