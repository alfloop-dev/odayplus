"""Job handler registry (ODP-FLOW-011).

The runtime worker must dispatch durable jobs *without* a monolithic
``if/elif`` switch over ``job_type`` (ODP-SD-03 §11 ODP-AC-SD03-003: shared Job
service is not reimplemented per module, and ODP-SD-03 §2 "業務責任優先" keeps
domain jobs composable). A :class:`JobRegistry` maps a ``job_type`` string to a
handler callable so a new domain job composes by *registering* a handler rather
than editing a central dispatcher.

Handlers receive the claimed :class:`~shared.jobs.queue.JobRecord` and the
process :class:`~shared.infrastructure.persistence.factory.PersistenceBundle`
they should read/write through. A handler that raises is retried and finally
dead-lettered by the worker loop (ODP-SD-08 §3.2 job state machine); an unknown
``job_type`` raises :class:`UnknownJobTypeError` which the loop treats the same
way.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from shared.jobs.queue import JobRecord

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from shared.infrastructure.persistence.factory import PersistenceBundle

# A handler mutates durable state for one claimed job. It returns None; success
# or failure is signalled by returning normally or raising.
JobHandler = Callable[[JobRecord, "PersistenceBundle"], None]


class UnknownJobTypeError(ValueError):
    """Raised when no handler is registered for a job's ``job_type``."""

    def __init__(self, job_type: str) -> None:
        super().__init__(f"Unknown job_type: {job_type}")
        self.job_type = job_type


class JobRegistry:
    """A composable ``job_type`` -> handler map for the runtime worker."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(
        self, job_type: str, handler: JobHandler, *, replace: bool = False
    ) -> JobHandler:
        """Register ``handler`` for ``job_type``.

        Duplicate registration is rejected unless ``replace=True`` so two
        domains cannot silently claim the same ``job_type``.
        """
        if not job_type:
            raise ValueError("job_type must be a non-empty string")
        if job_type in self._handlers and not replace:
            raise ValueError(f"Job handler already registered for {job_type!r}")
        self._handlers[job_type] = handler
        return handler

    def handler(self, job_type: str) -> Callable[[JobHandler], JobHandler]:
        """Decorator form of :meth:`register`."""

        def decorator(fn: JobHandler) -> JobHandler:
            self.register(job_type, fn)
            return fn

        return decorator

    def get(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)

    def has(self, job_type: str) -> bool:
        return job_type in self._handlers

    def job_types(self) -> tuple[str, ...]:
        """Registered job types, sorted for deterministic listing."""
        return tuple(sorted(self._handlers))

    def handle(self, job: JobRecord, persistence: PersistenceBundle) -> None:
        """Dispatch ``job`` to its handler, raising if none is registered."""
        handler = self._handlers.get(job.job_type)
        if handler is None:
            raise UnknownJobTypeError(job.job_type)
        handler(job, persistence)


__all__ = ["JobHandler", "JobRegistry", "UnknownJobTypeError"]
