"""Unavailable canonical audit policy/storage dependencies."""


class CanonicalAuditUnavailable(RuntimeError):
    """A command cannot commit its required canonical audit evidence."""
