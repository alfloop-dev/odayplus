"""Shared secret redaction for release and product-operation evidence.

The scheduler recovery publisher and release receipt builder use this same
implementation. Receipt envelopes and release admission remain in the delivery
layer; product operations can use the redaction without depending on that layer.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"

SAFE_SECRET_KEY_PARTS = frozenset(
    {
        "configured",
        "env",
        "environment",
        "id",
        "name",
        "owner",
        "ref",
        "reference",
        "selector",
        "selectors",
        "status",
        "version",
    }
)

SAFE_SECRET_KEY_RE = re.compile(
    r"^(?:secret|credential)(?:[_-](?:configured|env|environment|id|name|owner|ref|"
    r"reference|selector|selectors|status|values_redacted|version))+$|"
    r"^(?:required[_-])?secret[_-]env[_-]vars$",
    re.IGNORECASE,
)

SECRET_KEY_RE = re.compile(
    r"(?:^|[_-])(access[_-]?token|api[_-]?key|authorization|bearer|client[_-]?secret|"
    r"connection[_-]?string|cookie|credential|database[_-]?url|dsn|password|passwd|"
    r"private[_-]?key|refresh[_-]?token|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)

SECRET_STRING_PATTERNS = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)([?&](?:access_token|api_key|apikey|client_secret|password|secret|token)=)[^&#\s]+"), r"\1" + REDACTED),
    (re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+(@)"), r"\1" + REDACTED + r"\2"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.|\n)*?-----END [A-Z ]*PRIVATE KEY-----"), REDACTED),
)

def _key_parts(key: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", key.lower()) if part}

def _is_sensitive_key(key: str) -> bool:
    if SAFE_SECRET_KEY_RE.fullmatch(key):
        return False
    parts = _key_parts(key)
    if not parts or parts & SAFE_SECRET_KEY_PARTS == parts:
        return False
    return bool(SECRET_KEY_RE.search(key))

def _redact_string(value: str, secret_values: Sequence[str]) -> str:
    result = value
    # Explicit values are collected from caller-provided secrets and from
    # sensitive keyed fields before recursion.  Longest-first avoids replacing
    # a short token inside a longer token in a surprising order.
    for secret in sorted({item for item in secret_values if item}, key=len, reverse=True):
        result = result.replace(secret, REDACTED)
    for pattern, replacement in SECRET_STRING_PATTERNS:
        result = pattern.sub(replacement, result)
    return result

def _collect_keyed_secret_values(value: Any, output: list[str], *, key: str = "") -> None:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            child_name = str(child_key)
            if _is_sensitive_key(child_name) and isinstance(child_value, str):
                output.append(child_value)
            _collect_keyed_secret_values(child_value, output, key=child_name)
    elif isinstance(value, list):
        for child in value:
            _collect_keyed_secret_values(child, output, key=key)

def _redact_value(value: Any, secret_values: Sequence[str], redacted_paths: list[str], path: str) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if _is_sensitive_key(key):
                result[key] = REDACTED
                redacted_paths.append(child_path)
            else:
                result[key] = _redact_value(child, secret_values, redacted_paths, child_path)
        return result
    if isinstance(value, list):
        return [
            _redact_value(child, secret_values, redacted_paths, f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if isinstance(value, tuple):
        return [
            _redact_value(child, secret_values, redacted_paths, f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if isinstance(value, str):
        return _redact_string(value, secret_values)
    return copy.deepcopy(value)

def redact_secrets(value: Any, *, secret_values: Iterable[str] = ()) -> tuple[Any, dict[str, Any]]:
    """Return a deep-redacted copy and a non-sensitive redaction summary.

    ``secret_values`` is useful when a provider returns an opaque token under
    a neutral key.  Sensitive-looking keys and common bearer/query/PEM forms
    are redacted even when the caller does not know the concrete value.  The
    input is never mutated and the summary contains paths/counts only.
    """

    explicit = [str(item) for item in secret_values if isinstance(item, str) and item]
    _collect_keyed_secret_values(value, explicit)
    redacted_paths: list[str] = []
    result = _redact_value(value, explicit, redacted_paths, "")
    return result, {
        "secret_values_redacted": True,
        "redacted_field_count": len(redacted_paths),
        "redacted_fields": sorted(set(redacted_paths)),
    }

def redact(value: Any, *, secret_values: Iterable[str] = ()) -> Any:
    """Convenience wrapper returning only the redacted value."""

    return redact_secrets(value, secret_values=secret_values)[0]
