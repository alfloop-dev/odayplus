"""Scoped Store Ops aggregate edits inside an existing PostgreSQL transaction."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from typing import Any

from modules.opsboard.application.store_ops import (
    DurableStoreOpsRepository,
    StoreOpsConflict,
    StoreOpsLiveDataUnavailable,
)


def binding_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class ScopedStoreOpsRepository:
    """Merge authorized edits while preserving the rest of the tenant aggregate.

    The caller holds the tenant's transaction/advisory lock across the complete
    service call. A filtered aggregate must never replace the full document.
    """

    is_live = True

    def __init__(
        self,
        repository: DurableStoreOpsRepository,
        *,
        store_ids: frozenset[str],
        subject_id: str,
        scope_hash: str,
        request_hash: str,
        operation: str,
    ) -> None:
        self._repository = repository
        self._store_ids = store_ids
        self._subject_id = subject_id
        self._scope_hash = scope_hash
        self._request_hash = request_hash
        self._operation = operation
        self._state: dict[str, Any] | None = None
        self.new_audits: list[dict[str, Any]] = []

    def _full_state(self) -> dict[str, Any]:
        if self._state is None:
            self._state = self._repository.get_state()
        for key in ("stores", "issues", "evidence", "auditEvents"):
            rows = self._state.get(key)
            if not isinstance(rows, list) or not all(
                isinstance(row, dict) and isinstance(row.get("id"), str) for row in rows
            ):
                raise StoreOpsLiveDataUnavailable(
                    f"Store Ops {key} has no valid explicit identifiers"
                )
            if len({row["id"] for row in rows}) != len(rows):
                raise StoreOpsLiveDataUnavailable(f"Store Ops {key} has duplicate identifiers")
        if not all(
            isinstance(issue.get("storeId"), str) and issue["storeId"].strip()
            for issue in self._state["issues"]
        ):
            raise StoreOpsLiveDataUnavailable("Store Ops issue has no explicit store scope")
        if not all(
            isinstance(issue.get("evidenceIds", []), list)
            and all(isinstance(key, str) for key in issue.get("evidenceIds", []))
            for issue in self._state["issues"]
        ):
            raise StoreOpsLiveDataUnavailable("Store Ops issue evidence references are invalid")
        attachments = self._state.get("attachments", [])
        if not isinstance(attachments, list) or not all(
            isinstance(row, dict) and isinstance(row.get("id"), str) for row in attachments
        ):
            raise StoreOpsLiveDataUnavailable(
                "Store Ops attachments have no valid explicit identifiers"
            )
        return self._state

    def get_state(self) -> dict[str, Any]:
        state = deepcopy(self._full_state())
        state["stores"] = [row for row in state["stores"] if row["id"] in self._store_ids]
        state["issues"] = [row for row in state["issues"] if row["storeId"] in self._store_ids]
        issue_ids = {row["id"] for row in state["issues"]}
        evidence_ids = {key for row in state["issues"] for key in row.get("evidenceIds", [])}
        state["evidence"] = [row for row in state["evidence"] if row["id"] in evidence_ids]
        state["auditEvents"] = [
            row
            for row in state["auditEvents"]
            if str(row.get("targetId") or row.get("metadata", {}).get("issueId") or "") in issue_ids
        ]
        state["attachments"] = [
            row for row in state.get("attachments", []) if row.get("issueId") in issue_ids
        ]
        return state

    def save_state(self, state: Mapping[str, Any]) -> None:
        full = deepcopy(self._full_state())
        before = self.get_state()
        original_issues = {row["id"]: row for row in before["issues"]}
        incoming_issues = {row["id"]: deepcopy(row) for row in state["issues"]}
        if len(incoming_issues) != len(state["issues"]) or set(incoming_issues) != set(
            original_issues
        ):
            raise StoreOpsConflict("Store Ops cannot add or remove issues through a scoped edit")
        if any(
            row.get("storeId") != original_issues[key]["storeId"]
            for key, row in incoming_issues.items()
        ):
            raise StoreOpsConflict("Store Ops issue scope cannot change through a transition")
        if any(
            row.get("evidenceIds", []) != original_issues[key].get("evidenceIds", [])
            for key, row in incoming_issues.items()
        ):
            raise StoreOpsConflict(
                "Store Ops evidence references cannot change through a transition"
            )
        if state["stores"] != before["stores"]:
            raise StoreOpsConflict("Store Ops transitions cannot rewrite the store master")
        full["issues"] = [incoming_issues.get(row["id"], row) for row in full["issues"]]

        original_evidence = {row["id"]: row for row in before["evidence"]}
        incoming_evidence = {row["id"]: deepcopy(row) for row in state["evidence"]}
        if len(incoming_evidence) != len(state["evidence"]) or set(incoming_evidence) != set(
            original_evidence
        ):
            raise StoreOpsConflict(
                "Store Ops evidence membership cannot change through a transition"
            )
        hidden_references = {
            key
            for row in full["issues"]
            if row["id"] not in original_issues
            for key in row.get("evidenceIds", [])
        }
        if any(
            key in hidden_references and value != original_evidence[key]
            for key, value in incoming_evidence.items()
        ):
            raise StoreOpsConflict("Evidence also belongs to an issue outside the authorized scope")
        full["evidence"] = [incoming_evidence.get(row["id"], row) for row in full["evidence"]]

        original_audits = {row["id"]: row for row in before["auditEvents"]}
        incoming_audits = {row["id"]: deepcopy(row) for row in state["auditEvents"]}
        if len(incoming_audits) != len(state["auditEvents"]) or any(
            incoming_audits.get(key) != row for key, row in original_audits.items()
        ):
            raise StoreOpsConflict("Store Ops audit history cannot be rewritten")
        existing_audit_ids = {row["id"] for row in full["auditEvents"]}
        new_audits = []
        for key, row in incoming_audits.items():
            if key in original_audits:
                continue
            if key in existing_audit_ids or row.get("targetId") not in original_issues:
                raise StoreOpsConflict("New audit row is outside the authorized issue scope")
            row["actorSubjectId"] = self._subject_id
            new_audits.append(row)
        full["auditEvents"].extend(new_audits)
        expected_ordinal = int(full.get("nextAuditOrdinal") or 1) + len(new_audits)
        if int(state.get("nextAuditOrdinal") or 1) != expected_ordinal:
            raise StoreOpsConflict("Store Ops audit ordinal does not match the appended events")
        full["nextAuditOrdinal"] = expected_ordinal

        incoming_attachments = list(state.get("attachments", []))
        if any(row.get("issueId") not in original_issues for row in incoming_attachments):
            raise StoreOpsConflict("Attachment is outside the authorized issue scope")
        visible_ids = {row["id"] for row in before.get("attachments", [])}
        hidden = [
            row for row in full.get("attachments", []) if row.get("issueId") not in original_issues
        ]
        if len({row["id"] for row in incoming_attachments}) != len(incoming_attachments) or any(
            row["id"] in {item["id"] for item in hidden} and row["id"] not in visible_ids
            for row in incoming_attachments
        ):
            raise StoreOpsConflict("Attachment identifier collides with another issue")
        full["attachments"] = [*hidden, *deepcopy(incoming_attachments)]
        self._repository.save_state(full)
        self._state = full
        self.new_audits.extend(deepcopy(new_audits))

    def _receipt_key(self, key: str) -> str:
        return binding_hash([self._subject_id, self._operation, key])

    def get_idempotency_result(self, key: str) -> dict[str, Any] | None:
        record = self._repository.get_idempotency_result(self._receipt_key(key))
        if record is None:
            return None
        if (
            record.get("requestHash") != self._request_hash
            or record.get("scopeHash") != self._scope_hash
        ):
            raise StoreOpsConflict(
                "Idempotency key is bound to different request or authorization scope"
            )
        if not isinstance(record.get("result"), dict):
            raise StoreOpsConflict("Idempotency record is not bound to a scoped command")
        return deepcopy(record["result"])

    def save_idempotency_result(self, key: str, result: Mapping[str, Any]) -> None:
        self._repository.save_idempotency_result(
            self._receipt_key(key),
            {
                "requestHash": self._request_hash,
                "scopeHash": self._scope_hash,
                "result": deepcopy(dict(result)),
            },
        )
