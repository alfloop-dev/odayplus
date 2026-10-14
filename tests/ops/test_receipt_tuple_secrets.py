"""Receipt builder must discover secrets in every supported container."""

import json
from copy import deepcopy

import pytest

from tests.ops.test_release_receipts import receipt


@pytest.mark.parametrize("container", [list, tuple, lambda items: {"neutral": items}])
def test_sensitive_container_descendants_and_echo_are_redacted(container):
    secret = "fixture-sensitive-container-value"
    details = {"access_token": container([secret]), "echo": secret, "public": "visible"}
    original = deepcopy(details)
    result = receipt(details=details, secret_values=[])
    assert secret not in json.dumps(result)
    assert result["details"]["public"] == "visible" and details == original


def test_tuple_held_secret_and_neutral_echo_are_both_redacted():
    details = {
        "rows": ({"access_token": "fixture-tuple-secret-value"},),
        "echo": "fixture-tuple-secret-value",
        "nested": [({"password": "fixture-password-value"},)],
        "other": "fixture-password-value",
        "public": "visible",
    }
    before = deepcopy(details)
    result = receipt(details=details, secret_values=[])
    assert result["redaction"]["secret_values_redacted"] is True
    assert "fixture-tuple-secret-value" not in json.dumps(result)
    assert "fixture-password-value" not in json.dumps(result)
    assert result["details"]["public"] == "visible" and details == before
