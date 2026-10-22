"""Validate the actual native payload boundary; no route, approval or DB proof."""

import pytest
from pydantic import ValidationError

from apps.api.app.routes.sitescore import DecisionPayload


@pytest.mark.parametrize(
    "version",
    [True, False, "2", 2.0, 2.5],
    ids=["bool-true", "bool-false", "numeric-string", "integral-float", "fractional-float"],
)
def test_native_decision_rejects_non_integer_json_version(version):
    with pytest.raises(ValidationError) as error:
        DecisionPayload.model_validate(
            {"action": "APPROVE", "actor": "local-schema-control", "expected_version": version}
        )
    assert any(item["loc"] == ("expected_version",) for item in error.value.errors())


def test_native_decision_keeps_exact_integer_version():
    payload = DecisionPayload.model_validate(
        {"action": "APPROVE", "actor": "local-schema-control", "expected_version": 2}
    )
    assert payload.expected_version == 2
    assert type(payload.expected_version) is int


def test_native_decision_keeps_optional_version_for_legacy_schema():
    payload = DecisionPayload.model_validate({"action": "APPROVE", "actor": "local-schema-control"})
    assert payload.expected_version is None
