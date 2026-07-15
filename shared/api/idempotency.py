"""One idempotency policy for mutations.

Five in-memory ``_idempotency_index`` dicts had been copied across
``priceops``, ``forecastops``, ``avm`` and the operator services, each with its
own replay signal (``created: False`` vs an audit outcome of
``idempotent_replay`` vs ``{"idempotentReplay": true}``), so a client could not
detect a replay without knowing which router it was talking to. 52 of 86
mutations had no policy at all -- and the unguarded set was exactly the
dangerous one: every ``approve``, ``execute``, ``activate`` and ``rollback``
transition, where a double-submit is a real state change rather than a
duplicate row.

This module supplies the one policy:

* **Replay** -- same key, same request fingerprint: return the stored response
  verbatim with ``idempotent_replay: true``. Safe and repeatable.
* **Conflict** -- same key, *different* fingerprint: raise 409
  ``idempotency_conflict``. The old per-router dicts keyed on the bare key and
  would have replayed the first response for a genuinely different request,
  silently acknowledging a mutation that never happened. That is the failure
  this store exists to prevent.
* **Absent key** -- no ``Idempotency-Key`` header: execute normally. The header
  stays optional so the 34 endpoints that already accept it keep their current
  contract.

Scope is part of the key. Two routers may legitimately receive the same client
key for different operations, so entries are namespaced by ``scope`` (the
route's operation id) and never collide across endpoints.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any

__all__ = [
    "IdempotencyStore",
    "IdempotencyConflictError",
    "IdempotencyOutcome",
    "request_fingerprint",
    "REPLAY_FIELD",
]

# The single uniform replay signal, replacing `created:false` /
# `idempotent_replay` / `idempotentReplay`.
REPLAY_FIELD = "idempotent_replay"


class IdempotencyConflictError(Exception):
    """Same key reused for a materially different request."""

    def __init__(self, key: str, scope: str) -> None:
        super().__init__(
            f"Idempotency-Key {key!r} was already used for a different request payload "
            f"on {scope!r}."
        )
        self.key = key
        self.scope = scope


@dataclass(frozen=True)
class IdempotencyOutcome:
    """Result of a guarded mutation."""

    value: Any
    replayed: bool


def request_fingerprint(payload: Any) -> str:
    """Stable hash of a request payload.

    ``sort_keys`` makes the hash independent of JSON key order, and ``default=str``
    keeps it total over the datetimes/enums/Decimals that reach these payloads --
    a fingerprint that raises would turn a correct replay into a 500.
    """
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class IdempotencyStore:
    """Thread-safe fingerprinted idempotency records.

    Bounded by ``max_entries`` with FIFO eviction. The stores this replaces were
    unbounded dicts that grew for the process lifetime -- a slow leak on any
    long-running API process.
    """

    def __init__(self, *, max_entries: int = 10_000) -> None:
        self._entries: dict[tuple[str, str], tuple[str, Any]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def lookup(self, *, key: str, scope: str, fingerprint: str) -> Any | None:
        """Return the stored response for a replay, or ``None`` to execute.

        Raises :class:`IdempotencyConflictError` when the key was used for a
        different payload.
        """
        with self._lock:
            record = self._entries.get((scope, key))
            if record is None:
                return None
            stored_fingerprint, value = record
            if stored_fingerprint != fingerprint:
                raise IdempotencyConflictError(key, scope)
            return value

    def remember(self, *, key: str, scope: str, fingerprint: str, value: Any) -> None:
        with self._lock:
            if len(self._entries) >= self._max_entries:
                # dict preserves insertion order; drop the oldest record.
                oldest = next(iter(self._entries))
                self._entries.pop(oldest, None)
            self._entries[(scope, key)] = (fingerprint, value)

    def run(
        self,
        *,
        key: str | None,
        scope: str,
        payload: Any,
        operation: Any,
    ) -> IdempotencyOutcome:
        """Execute ``operation`` under the idempotency policy.

        ``operation`` is a zero-arg callable so it is never invoked on a replay.
        """
        if not key:
            return IdempotencyOutcome(value=operation(), replayed=False)

        fingerprint = request_fingerprint(payload)
        existing = self.lookup(key=key, scope=scope, fingerprint=fingerprint)
        if existing is not None:
            return IdempotencyOutcome(value=existing, replayed=True)

        result = operation()
        self.remember(key=key, scope=scope, fingerprint=fingerprint, value=result)
        return IdempotencyOutcome(value=result, replayed=False)


def apply_replay_marker(value: Any, *, replayed: bool) -> Any:
    """Stamp the uniform replay signal onto a dict response.

    Non-dict responses pass through untouched -- there is nowhere to put the
    marker, and the ``X-Idempotent-Replay`` response header carries the same
    fact for those callers.
    """
    if isinstance(value, dict):
        return {**value, REPLAY_FIELD: replayed}
    return value
