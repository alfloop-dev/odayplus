"""Columnar durable repository for Operator comments."""

from __future__ import annotations

import json
from typing import Any

from modules.opsboard.application.comments import CommentForbidden, CommentPersistenceUnavailable


class DurableCommentRepository:
    """Persist comments in the tenant-partitioned runtime database.

    The repository accepts the engine rather than a request-derived tenant
    store. Every query binds ``tenant_id`` explicitly, which keeps the same
    implementation safe for local SQLite and production PostgreSQL requests.
    """

    def __init__(self, engine: Any | None) -> None:
        self._engine = engine

    @property
    def table(self) -> str:
        if str(getattr(self._engine, "dialect", "")).lower() == "postgresql":
            return "odp_runtime.operator_comments"
        return "operator_comments"

    def _require_engine(self) -> Any:
        if self._engine is None:
            raise CommentPersistenceUnavailable(
                "durable Operator comments persistence is not configured"
            )
        return self._engine

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        history = row["history_json"]
        if isinstance(history, (bytes, bytearray)):
            history = history.decode("utf-8")
        try:
            decoded_history = json.loads(history or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            raise CommentPersistenceUnavailable(
                "durable comment history is not valid JSON"
            ) from exc
        return {
            "id": row["comment_id"],
            "tenantId": row["tenant_id"],
            "targetType": row["target_type"],
            "targetId": row["target_id"],
            "content": row["content"],
            "createdBy": row["created_by"],
            "createdAt": row["created_at"],
            "updatedBy": row["updated_by"],
            "updatedAt": row["updated_at"],
            "edited": bool(row["edited"]),
            "editCount": int(row["edit_count"] or 0),
            "idempotencyKey": row["idempotency_key"],
            "correlationId": row["correlation_id"],
            "history": decoded_history,
        }

    def list_comments(
        self, tenant_id: str, target_type: str, target_id: str
    ) -> list[dict[str, Any]]:
        engine = self._require_engine()
        rows = engine.query(
            f"SELECT * FROM {self.table} "
            "WHERE tenant_id = ? AND target_type = ? AND target_id = ? "
            "ORDER BY created_at, comment_id",
            (tenant_id, target_type, target_id),
        )
        return [self._decode(row) for row in rows]

    def get_comment(self, tenant_id: str, comment_id: str) -> dict[str, Any] | None:
        engine = self._require_engine()
        row = engine.query_one(
            f"SELECT * FROM {self.table} WHERE tenant_id = ? AND comment_id = ?",
            (tenant_id, comment_id),
        )
        return None if row is None else self._decode(row)

    def find_by_idempotency(
        self, tenant_id: str, actor_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        engine = self._require_engine()
        row = engine.query_one(
            f"SELECT * FROM {self.table} "
            "WHERE tenant_id = ? AND created_by = ? AND idempotency_key = ?",
            (tenant_id, actor_id, idempotency_key),
        )
        return None if row is None else self._decode(row)

    def save_comment(self, tenant_id: str, comment: dict[str, Any]) -> None:
        engine = self._require_engine()
        if str(comment.get("tenantId") or "").strip() != tenant_id:
            raise CommentForbidden("comment tenant does not match the verified tenant")
        engine.execute(
            f"INSERT INTO {self.table} ("
            "tenant_id, comment_id, target_type, target_id, content, created_by, "
            "created_at, updated_by, updated_at, edited, edit_count, "
            "idempotency_key, correlation_id, history_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (tenant_id, comment_id) DO UPDATE SET "
            "target_type = excluded.target_type, target_id = excluded.target_id, "
            "content = excluded.content, created_by = excluded.created_by, "
            "created_at = excluded.created_at, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at, edited = excluded.edited, "
            "edit_count = excluded.edit_count, idempotency_key = excluded.idempotency_key, "
            "correlation_id = excluded.correlation_id, history_json = excluded.history_json",
            (
                tenant_id,
                comment["id"],
                comment["targetType"],
                comment["targetId"],
                comment["content"],
                comment["createdBy"],
                comment["createdAt"],
                comment.get("updatedBy"),
                comment.get("updatedAt"),
                int(bool(comment.get("edited"))),
                int(comment.get("editCount", 0) or 0),
                comment.get("idempotencyKey"),
                comment.get("correlationId"),
                json.dumps(comment.get("history", []), ensure_ascii=False),
            ),
        )


__all__ = ["DurableCommentRepository"]
