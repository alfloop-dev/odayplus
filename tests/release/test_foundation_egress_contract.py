"""The sources-off proof must cover the instantiated foundation firewall."""

import shutil
from pathlib import Path

import pytest

from delivery_toolchain.release.release_manifest import (
    ROOT,
    SOURCES_OFF_EGRESS_CONTRACT_FILES,
    _sources_off_egress_contract_errors,
    compute_sources_off_egress_contract_digest,
)


@pytest.fixture
def contract_root(tmp_path: Path) -> Path:
    for relative in SOURCES_OFF_EGRESS_CONTRACT_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


def test_instantiated_foundation_contract_is_valid(contract_root: Path) -> None:
    assert _sources_off_egress_contract_errors(contract_root) == []


@pytest.mark.parametrize(
    "filename",
    ["main.tf", "variables.tf", "network.tf", "outputs.tf", "kms.tf", "database.tf"],
)
def test_module_inputs_are_bound_by_digest(contract_root: Path, filename: str) -> None:
    before = compute_sources_off_egress_contract_digest(contract_root)
    target = contract_root / "infra/terraform/modules/runtime_foundation" / filename
    target.write_text(target.read_text() + "\n# changed module input\n")
    assert compute_sources_off_egress_contract_digest(contract_root) != before


def test_missing_module_is_rejected(contract_root: Path) -> None:
    (contract_root / "infra/terraform/modules/runtime_foundation/network.tf").unlink()
    errors = _sources_off_egress_contract_errors(contract_root)
    assert any("incomplete" in e and "runtime_foundation/network.tf" in e for e in errors)


@pytest.mark.parametrize("replacement", [
    'source = "./modules/unrelated"',
    'source = "./modules/runtime_foundation"\n  count = 0',
    'source = "./modules/runtime_foundation"\n  for_each = {}',
])
def test_uninstantiated_firewall_is_rejected(contract_root: Path, replacement: str) -> None:
    target = contract_root / "infra/terraform/network.tf"
    target.write_text(target.read_text().replace(
        'source = "./modules/runtime_foundation"', replacement,
    ))
    assert _sources_off_egress_contract_errors(contract_root)


@pytest.mark.parametrize("rule", [
    "deny_all_egress", "allow_private_egress", "allow_restricted_google_apis",
])
def test_removed_module_rule_is_rejected(contract_root: Path, rule: str) -> None:
    target = contract_root / "infra/terraform/modules/runtime_foundation/network.tf"
    target.write_text(target.read_text().replace(f'"{rule}" {{', f'"removed_{rule}" {{'))
    assert any(
        f"missing required firewall rule '{rule}'" in e
        for e in _sources_off_egress_contract_errors(contract_root)
    )


@pytest.mark.parametrize("relative", ["network.tf", "modules/runtime_foundation/network.tf"])
def test_nat_in_caller_or_module_is_rejected(contract_root: Path, relative: str) -> None:
    target = contract_root / "infra/terraform" / relative
    target.write_text(target.read_text() + '\nresource "google_compute_router_nat" "escape" {\n}\n')
    assert any("Cloud NAT" in e for e in _sources_off_egress_contract_errors(contract_root))


def test_public_allow_in_module_is_rejected(contract_root: Path) -> None:
    target = contract_root / "infra/terraform/modules/runtime_foundation/network.tf"
    target.write_text(target.read_text().replace('"10.0.0.0/8"', '"0.0.0.0/0"'))
    assert any("non-RFC1918" in e for e in _sources_off_egress_contract_errors(contract_root))


def test_root_cannot_add_firewalls_outside_the_module(contract_root: Path) -> None:
    target = contract_root / "infra/terraform/network.tf"
    target.write_text(target.read_text() + '\nresource "google_compute_firewall" "escape" {\n}\n')
    assert any("keep firewall resources" in e for e in _sources_off_egress_contract_errors(contract_root))


@pytest.mark.parametrize("filename", ["extra.tf", "extra.tf.json"])
def test_additional_module_inputs_must_be_bound(contract_root: Path, filename: str) -> None:
    target = contract_root / "infra/terraform/modules/runtime_foundation" / filename
    target.write_text("{}\n")
    assert any("unbound runtime_foundation inputs" in e for e in _sources_off_egress_contract_errors(contract_root))
