"""Canonical integrity helpers shared by evidence and model receipts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_content_sha256(payload: dict[str, Any], *, envelope_key: str = "integrity") -> str:
    """Hash a JSON object after removing its mutable integrity envelope."""
    canonical = json.dumps(
        {key: value for key, value in payload.items() if key != envelope_key},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
