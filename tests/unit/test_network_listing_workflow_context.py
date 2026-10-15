from __future__ import annotations

from modules.opsboard.application.network_listings import NetworkListingService, _seed_state


def test_live_flow_keeps_six_steps_without_guessing_a_record_or_score_state() -> None:
    state = _seed_state()
    state["candidates"] = [
        {"id": "first-candidate", "listingId": "first-listing", "status": "scored"},
        {"id": "second-candidate", "listingId": "second-listing", "status": "scored"},
    ]
    state["siteReviews"] = [{"id": "first-review"}]
    service = NetworkListingService(initial_state=state, seed_fixtures=False)
    steps = service._expansion_steps(selected_id="HZ-01")
    assert [(step["id"], step["label"], step["tabIndex"]) for step in steps] == [
        ("find", "Find Area", 0), ("radar", "Listing Radar", 1),
        ("candidate", "Candidate", 2), ("sitescore", "SiteScore", 3),
        ("compare", "Compare", 4), ("review", "Review", 5),
    ]
    assert all(step["entityId"] is None for step in steps)
    assert all(step["state"] == "blocked" for step in steps)
    assert all("context" in step["summary"] for step in steps)


def test_fixture_flow_preserves_package10_golden_path() -> None:
    steps = NetworkListingService()._expansion_steps(selected_id="HZ-01")
    assert len(steps) == 6
    assert steps[0]["entityId"] == "HZ-01"
    assert steps[1]["entityId"] == "L-2024"
    assert steps[0]["state"] == "completed"
    assert steps[1]["state"] == "current"
