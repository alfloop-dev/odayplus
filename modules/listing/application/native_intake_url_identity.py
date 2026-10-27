"""One explicit URL identity rule; legacy v1.0 hashes are not reinterpreted.

This rule does not enable a registry entry or establish a Listing writer's
provenance. Future activation requires verification of that writer's hashes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from modules.external_data.security.assisted_listing_retrieval import (
    contains_sensitive_submission_material,
)
from modules.listing.application.native_intake_submission_validation import (
    validate_native_intake_url,
)

NATIVE_URL_RULE_VERSION = "native-url-exact-v2"


class NativeURLIdentityUnsupported(ValueError):
    pass


@dataclass(frozen=True)
class NativeURLIdentity:
    canonical_url: str
    source_host: str
    canonical_url_sha256: str
    rule_version: str
    rule_source_sha256: str


def normalize_native_intake_url(original_url: str) -> NativeURLIdentity:
    """Preserve the exact URL tail, folding only scheme/host/default port.

    Empty query/fragment delimiters also remain, rather than disappearing in a
    urlunsplit round trip. Unsupported forms fail closed without DNS or I/O.
    """
    if (not isinstance(original_url, str) or not 1 <= len(original_url) <= 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in original_url)
            or "\\" in original_url
            or contains_sensitive_submission_material({"original_url": original_url})):
        raise NativeURLIdentityUnsupported("NATIVE_IDENTITY_URL_UNSUPPORTED")
    validate_native_intake_url(original_url)
    parts = urlsplit(original_url)
    prefix_length = len(parts.scheme) + 3
    if original_url[:prefix_length].lower() != parts.scheme + "://":
        raise NativeURLIdentityUnsupported("NATIVE_IDENTITY_URL_UNSUPPORTED")
    authority = parts.netloc
    # DNS only: no userinfo, bracketed IP, percent-encoded host, wildcard,
    # Unicode U-label, trailing dot or implicit empty port interpretation.
    host, separator, port_text = authority.partition(":")
    host = host.lower()
    labels = host.split(".")
    if (not host.isascii() or len(host) > 253 or host.endswith(".")
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or all(label.isdigit() for label in labels)
            or host != parts.hostname):
        raise NativeURLIdentityUnsupported("NATIVE_IDENTITY_HOST_UNSUPPORTED")
    try:
        # Require valid ASCII A-labels; do not silently convert Unicode input
        # or accept a different IDNA round-trip spelling for recognition.
        if host.encode("ascii").decode("idna").encode("idna").decode("ascii").lower() != host:
            raise ValueError
        port = parts.port
    except (UnicodeError, ValueError) as exc:
        raise NativeURLIdentityUnsupported("NATIVE_IDENTITY_AUTHORITY_UNSUPPORTED") from exc
    suffix = ""
    if separator:
        if port is None or not 1 <= port <= 65535 or port_text != str(port):
            raise NativeURLIdentityUnsupported("NATIVE_IDENTITY_PORT_UNSUPPORTED")
        if port != {"http": 80, "https": 443}[parts.scheme]:
            suffix = ":" + port_text
    tail_start = prefix_length + len(authority)
    if original_url[prefix_length:tail_start] != authority:
        raise NativeURLIdentityUnsupported("NATIVE_IDENTITY_AUTHORITY_UNSUPPORTED")
    canonical = parts.scheme + "://" + host + suffix + original_url[tail_start:]
    return NativeURLIdentity(canonical, host, hashlib.sha256(canonical.encode()).hexdigest(),
                             NATIVE_URL_RULE_VERSION, NATIVE_URL_RULE_SOURCE_SHA256)


# Persist the reviewed rule bytes with the version. A pending command may not
# adopt changed compiled semantics under an unchanged human-readable label.
NATIVE_URL_RULE_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
