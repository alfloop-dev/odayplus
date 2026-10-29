"""The native submission URL boundary, shared by HTTP and native workers.

Validation leaves accepted original URL evidence unchanged. It does not fetch,
resolve DNS, identify a provider or authorize retrieval.
"""

from modules.external_data.security.assisted_listing_retrieval import (
    validate_submitted_listing_url,
)


def validate_native_intake_url(original_url: str | None) -> None:
    if not isinstance(original_url, str) or any(char.isspace() for char in original_url):
        raise ValueError("A URL Intake requires an HTTP(S) URL without credentials")
    validate_submitted_listing_url(original_url)
