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


@pytest.mark.parametrize("discovery", ["values", "explicit", "sensitive-keys"])
def test_known_secret_keys_paths_and_collisions_are_safe(discovery):
    alpha,beta="opaqueFixtureAlpha78234","opaqueFixtureBeta98745"
    details={"lookup":{f"prefix-{alpha}":{"password":"fixture-nested-password", "public":"one"},
        f"prefix-{beta}":{"public":"two"}, "prefix-[REDACTED]":{"public":"three"}}}
    if discovery == "values":
        details["access_token"]=[alpha,beta]
    elif discovery == "sensitive-keys":
        details["access_token"]={alpha:"fixture-box-value",beta:"fixture-box-value"}
    before=deepcopy(details)
    result=receipt(details=details,secret_values=[alpha,beta] if discovery=="explicit" else [])
    assert alpha not in json.dumps(result) and beta not in json.dumps(result)
    lookup=result["details"]["lookup"]
    assert len(lookup)==3 and {entry["public"] for entry in lookup.values()}=={"one","two","three"}
    assert lookup["prefix-[REDACTED]"]["public"]=="three"
    assert result["redaction"]["redacted_fields"] and details==before


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
