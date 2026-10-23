"""Declared listing dates must remain valid, change-sensitive job inputs."""
from dataclasses import replace
from datetime import date

from modules.listing.application.intake_score_delivery import _digest, _jsonable
from shared.domain import Listing


def test_listing_availability_date_is_serializable_and_fenced():
    listing = Listing(listing_id="recorded-listing", available_from=date(2026, 10, 20))
    encoded = _jsonable(listing)
    assert encoded["available_from"] == "2026-10-20"
    assert _digest(listing) == _digest(encoded)
    assert _digest(listing) != _digest(replace(listing, available_from=date(2026, 10, 21)))
    assert _digest(listing) != _digest(replace(listing, available_from=None))
