"""The native submission URL boundary, shared by HTTP and native workers.

Validation leaves accepted original URL evidence unchanged. It does not fetch,
resolve DNS, identify a provider or authorize retrieval.
"""

from urllib.parse import urlsplit

from modules.external_data.security.assisted_listing_retrieval import (
    validate_submitted_listing_url,
)


def validate_native_intake_url(original_url: str | None) -> None:
    url = urlsplit(original_url or "")
    if (url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password
            or any(char.isspace() for char in original_url or "")):
        raise ValueError("A URL Intake requires an HTTP(S) URL without credentials")
    validate_submitted_listing_url(original_url)
    if url.fragment:
        # Both standard fragments and hash-router query suffixes may carry keys.
        for fragment_query in (url.fragment, url.fragment.split("?", 1)[-1]):
            validate_submitted_listing_url(url._replace(query=fragment_query, fragment="").geturl())
