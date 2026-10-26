"""The sources-off proof must cover the instantiated foundation firewall."""

import shutil
import subprocess
from pathlib import Path

import pytest

from delivery_toolchain.release.release_manifest import (
    ROOT,
    _sources_off_egress_contract_errors,
    compute_sources_off_egress_contract_digest,
    resolve_sources_off_egress_contract_files,
)


@pytest.fixture
def contract_root(tmp_path: Path) -> Path:
    contract_files = resolve_sources_off_egress_contract_files(ROOT)
    for relative in contract_files:
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


def test_pre_module_candidate_passes_with_six_file_digest() -> None:
    """A pre-module candidate (e.g. 596b9c9a) passes egress validation and uses 6-file digest."""
    candidate_sha = "596b9c9a1788d952811a2bf8d4bba8a4e4d76b12"
    errors = _sources_off_egress_contract_errors(candidate_sha=candidate_sha)
    assert errors == []
    digest = compute_sources_off_egress_contract_digest(candidate_sha=candidate_sha)
    assert digest == "sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09"


def test_module_candidate_relaxation_changes_digest(tmp_path: Path) -> None:
    """When a candidate instantiates runtime_foundation, relaxing a rule changes digest and reports error."""
    repo = tmp_path / "candidate-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)

    contract_files = resolve_sources_off_egress_contract_files(ROOT)
    for relative in contract_files:
        dest = repo / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT / relative).read_bytes())

    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=ODP Test Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Initial valid module commit"],
        cwd=repo, check=True, capture_output=True,
    )
    valid_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    valid_digest = compute_sources_off_egress_contract_digest(root=repo, candidate_sha=valid_sha)
    assert _sources_off_egress_contract_errors(root=repo, candidate_sha=valid_sha) == []

    # Relax firewall in module
    mod_net = repo / "infra/terraform/modules/runtime_foundation/network.tf"
    mod_net.write_text(mod_net.read_text().replace('"10.0.0.0/8"', '"0.0.0.0/0"'))
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=ODP Test Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Relaxed module firewall"],
        cwd=repo, check=True, capture_output=True,
    )
    relaxed_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    relaxed_digest = compute_sources_off_egress_contract_digest(root=repo, candidate_sha=relaxed_sha)
    assert relaxed_digest != valid_digest
    relaxed_errors = _sources_off_egress_contract_errors(root=repo, candidate_sha=relaxed_sha)
    assert any("non-RFC1918" in e for e in relaxed_errors)
