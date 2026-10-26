"""Durable comments attached to Operator task and governance records.

Comments are an audit-sidecar for an existing task, decision, or approval.  They
are deliberately not a second decision channel: the target identity is fixed
when a comment is created and the edit path only changes comment content while
retaining an actor/time history.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from shared.audit import AuditEvent

COMMENT_TARGET_TYPES = ("task", "decision", "approval")
MAX_COMMENT_LENGTH = 2_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CommentNotFound(Exception):
    """The comment or its canonical target is not visible in this tenant."""


class CommentForbidden(Exception):
    """The verified actor cannot perform the requested comment operation."""


class CommentPolicyError(Exception):
    """The comment request violates the comments contract."""


class CommentPersistenceUnavailable(RuntimeError):
    """The configured durable comment store cannot serve the request."""


class CommentRepository(Protocol):
    def list_comments(
        self, tenant_id: str, target_type: str, target_id: str
    ) -> list[dict[str, Any]]: ...

    def get_comment(self, tenant_id: str, comment_id: str) -> dict[str, Any] | None: ...

    def find_by_idempotency(
        self, tenant_id: str, actor_id: str, idempotency_key: str
    ) -> dict[str, Any] | None: ...

    def save_comment(self, tenant_id: str, comment: dict[str, Any]) -> None: ...


class InMemoryCommentRepository:
    """Small repository double used by local fixture mode and unit tests."""

    def __init__(self) -> None:
        self._comments: dict[tuple[str, str], dict[str, Any]] = {}

    def list_comments(
        self, tenant_id: str, target_type: str, target_id: str
    ) -> list[dict[str, Any]]:
        rows = [
            value
            for (row_tenant, _), value in self._comments.items()
            if row_tenant == tenant_id
            and value.get("targetType") == target_type
            and value.get("targetId") == target_id
        ]
        rows.sort(key=lambda value: (str(value.get("createdAt", "")), value["id"]))
        return deepcopy(rows)

    def get_comment(self, tenant_id: str, comment_id: str) -> dict[str, Any] | None:
        value = self._comments.get((tenant_id, comment_id))
        return None if value is None else deepcopy(value)

    def find_by_idempotency(
        self, tenant_id: str, actor_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        for value in self._comments.values():
            if (
                value.get("tenantId") == tenant_id
                and value.get("createdBy") == actor_id
                and value.get("idempotencyKey") == idempotency_key
            ):
                return deepcopy(value)
        return None

    def save_comment(self, tenant_id: str, comment: dict[str, Any]) -> None:
        if comment.get("tenantId") != tenant_id:
            raise CommentForbidden("comment tenant does not match the verified tenant")
        self._comments[(tenant_id, str(comment["id"]))] = deepcopy(comment)


class CommentsService:
    """Application service for tenant-scoped, auditable comment sidecars."""

    def __init__(
        self,
        *,
        repository: CommentRepository | None = None,
        audit_log: Any | None = None,
        target_exists: Callable[[str, str, str], bool] | None = None,
        clock: Callable[[], str] = _now,
    ) -> None:
        self._repository = repository or InMemoryCommentRepository()
        self._audit_log = audit_log
        self._target_exists = target_exists
        self._clock = clock

    @staticmethod
    def _tenant(tenant_id: str) -> str:
        value = str(tenant_id or "").strip()
        if not value:
            raise CommentForbidden("verified tenant scope is required for comments")
        return value

    @staticmethod
    def _actor(actor_id: str) -> str:
        value = str(actor_id or "").strip()
        if not value:
            raise CommentForbidden("verified actor identity is required for comments")
        return value

    @staticmethod
    def _target(target_type: str, target_id: str) -> tuple[str, str]:
        normalized_type = str(target_type or "").strip().lower()
        normalized_id = str(target_id or "").strip()
        if normalized_type not in COMMENT_TARGET_TYPES:
            raise CommentPolicyError(
                "targetType must be one of task, decision, or approval"
            )
        if not normalized_id or len(normalized_id) > 255:
            raise CommentPolicyError("targetId must be between 1 and 255 characters")
        return normalized_type, normalized_id

    @staticmethod
    def _content(content: str) -> str:
        normalized = str(content or "").strip()
        if not normalized:
            raise CommentPolicyError("comment content must not be empty")
        if len(normalized) > MAX_COMMENT_LENGTH:
            raise CommentPolicyError(
                f"comment content must be at most {MAX_COMMENT_LENGTH} characters"
            )
        return normalized

    def _require_target(self, tenant_id: str, target_type: str, target_id: str) -> None:
        if self._target_exists is not None and not self._target_exists(
            tenant_id, target_type, target_id
        ):
            raise CommentNotFound(f"{target_type} {target_id} not found")

    def _audit(
        self,
        *,
        action: str,
        actor_id: str,
        tenant_id: str,
        target_type: str,
        target_id: str,
        comment_id: str,
        correlation_id: str | None,
        content_length: int,
    ) -> dict[str, Any]:
        event = AuditEvent(
            event_type="operator.comment",
            actor=actor_id,
            action=action,
            resource=f"{target_type}:{target_id}",
            outcome="success",
            correlation_id=str(correlation_id or "unknown"),
            metadata={
                "tenant_id": tenant_id,
                "comment_id": comment_id,
                "target_type": target_type,
                "target_id": target_id,
                "content_length": content_length,
            },
        )
        if self._audit_log is not None:
            self._audit_log.record(event)
        return event.to_dict()

    def list_comments(
        self,
        *,
        tenant_id: str,
        target_type: str,
        target_id: str,
    ) -> list[dict[str, Any]]:
        tenant = self._tenant(tenant_id)
        normalized_type, normalized_id = self._target(target_type, target_id)
        self._require_target(tenant, normalized_type, normalized_id)
        return self._repository.list_comments(tenant, normalized_type, normalized_id)

    def create_comment(
        self,
        *,
        tenant_id: str,
        target_type: str,
        target_id: str,
        content: str,
        actor_id: str,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        actor = self._actor(actor_id)
        normalized_type, normalized_id = self._target(target_type, target_id)
        normalized_content = self._content(content)
        self._require_target(tenant, normalized_type, normalized_id)

        key = str(idempotency_key or "").strip() or None
        if key:
            replay = self._repository.find_by_idempotency(tenant, actor, key)
            if replay is not None:
                return {
                    "comment": replay,
                    "auditEvent": None,
                    "idempotentReplay": True,
                }

        timestamp = self._clock()
        comment = {
            "id": f"cmt-{uuid4()}",
            "tenantId": tenant,
            "targetType": normalized_type,
            "targetId": normalized_id,
            "content": normalized_content,
            "createdBy": actor,
            "createdAt": timestamp,
            "updatedBy": None,
            "updatedAt": None,
            "edited": False,
            "editCount": 0,
            "idempotencyKey": key,
            "correlationId": correlation_id,
            "history": [
                {
                    "action": "created",
                    "actorId": actor,
                    "occurredAt": timestamp,
                    "content": normalized_content,
                }
            ],
        }
        self._repository.save_comment(tenant, comment)
        audit = self._audit(
            action="comment.created",
            actor_id=actor,
            tenant_id=tenant,
            target_type=normalized_type,
            target_id=normalized_id,
            comment_id=comment["id"],
            correlation_id=correlation_id,
            content_length=len(normalized_content),
        )
        return {
            "comment": deepcopy(comment),
            "auditEvent": audit,
            "idempotentReplay": False,
        }

    def edit_comment(
        self,
        *,
        tenant_id: str,
        comment_id: str,
        content: str,
        actor_id: str,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        tenant = self._tenant(tenant_id)
        actor = self._actor(actor_id)
        normalized_content = self._content(content)
        comment = self._repository.get_comment(tenant, str(comment_id).strip())
        if comment is None:
            raise CommentNotFound(f"comment {comment_id} not found")
        if comment.get("createdBy") != actor:
            raise CommentForbidden("only the comment author may edit this comment")
        key = str(idempotency_key or "").strip() or None
        history = list(comment.get("history") or [])
        if key and any(
            entry.get("action") == "edited" and entry.get("idempotencyKey") == key
            for entry in history
        ):
            return {"comment": comment, "auditEvent": None, "idempotentReplay": True}
        if comment.get("content") == normalized_content:
            return {"comment": comment, "auditEvent": None, "idempotentReplay": True}

        timestamp = self._clock()
        history_entry = {
            "action": "edited",
            "actorId": actor,
            "occurredAt": timestamp,
            "previousContent": comment.get("content"),
            "content": normalized_content,
        }
        if key:
            history_entry["idempotencyKey"] = key
        history.append(history_entry)
        # Only the comment body and edit metadata change. targetType/targetId
        # are intentionally copied through, so an edit cannot retarget a
        # comment or masquerade as a decision/approval mutation.
        comment["content"] = normalized_content
        comment["updatedBy"] = actor
        comment["updatedAt"] = timestamp
        comment["edited"] = True
        comment["editCount"] = int(comment.get("editCount", 0) or 0) + 1
        comment["history"] = history
        if correlation_id:
            comment["correlationId"] = correlation_id
        self._repository.save_comment(tenant, comment)
        audit = self._audit(
            action="comment.edited",
            actor_id=actor,
            tenant_id=tenant,
            target_type=str(comment["targetType"]),
            target_id=str(comment["targetId"]),
            comment_id=str(comment["id"]),
            correlation_id=correlation_id,
            content_length=len(normalized_content),
        )
        return {
            "comment": deepcopy(comment),
            "auditEvent": audit,
            "idempotentReplay": False,
        }


__all__ = [
    "COMMENT_TARGET_TYPES",
    "CommentForbidden",
    "CommentNotFound",
    "CommentPersistenceUnavailable",
    "CommentPolicyError",
    "CommentRepository",
    "CommentsService",
    "InMemoryCommentRepository",
    "MAX_COMMENT_LENGTH",
]
