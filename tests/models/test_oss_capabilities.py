from models.shared_ml import (
    CAPABILITY_PACKAGES,
    OssCapability,
    inspect_oss_capability,
    inspect_oss_stack,
    require_oss_capability,
)


def test_all_specified_oss_capabilities_are_installed() -> None:
    statuses = inspect_oss_stack()

    assert {status.capability for status in statuses} == set(OssCapability)
    assert all(status.available for status in statuses), [
        status.to_dict() for status in statuses if not status.available
    ]


def test_capability_report_contains_real_package_versions() -> None:
    status = require_oss_capability(OssCapability.MODEL_TRAINING)

    assert set(status.packages) == set(CAPABILITY_PACKAGES[OssCapability.MODEL_TRAINING])
    assert all(
        package_version not in {None, "installed"} for package_version in status.packages.values()
    )


def test_single_capability_serializes_for_readiness_surfaces() -> None:
    payload = inspect_oss_capability(OssCapability.OPTIMIZATION).to_dict()

    assert payload["capability"] == "optimization"
    assert payload["available"] is True
    assert payload["missing_packages"] == []
