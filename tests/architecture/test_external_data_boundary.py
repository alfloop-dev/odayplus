"""Architecture tests for the frozen legacy external-data surface.

ODP-LEGACY-INVENTORY-001 / contract ``odayplus.legacy-external-data-disposition.v2``.

Two halves:

* **Live-tree tests** assert the real repository satisfies the acceptance
  criteria right now — every tracked file classified, every frozen surface at
  its exact inventory, no undeclared provider reference.
* **Synthetic tests** assert the validator would actually catch a regression.
  A boundary check that only ever passes proves nothing, so each blocked
  capability is exercised against a fabricated file tree that violates it.

Synthetic tests drive ``evaluate()`` with an explicit file list and reader, so
they never touch the working tree.
"""

from __future__ import annotations

import copy
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_external_data_boundary import (  # noqa: E402
    CHECKS,
    DEFAULT_POLICY,
    EXPECTED_CONTRACT,
    PolicyError,
    Report,
    classify,
    evaluate,
    evaluate_runtime_gate_assertion,
    glob_match,
    load_policy,
    main,
    make_reader,
    tracked_files,
    validate_policy_structure,
)

POLICY_PATH = REPO_ROOT / "docs/design/emgi/v0.4.1/LEGACY_EXTERNAL_DATA_DISPOSITION.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def policy() -> dict[str, Any]:
    return load_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def repo_files() -> list[str]:
    return tracked_files(REPO_ROOT)


@pytest.fixture(scope="module")
def live_report(policy: Mapping[str, Any], repo_files: Sequence[str]) -> Report:
    return evaluate(policy, repo_files, make_reader(REPO_ROOT))


def synthetic(
    policy: Mapping[str, Any],
    contents: Mapping[str, str],
    checks: Sequence[str] = CHECKS,
    *,
    extra_files: Sequence[str] = (),
) -> Report:
    """Evaluate a fabricated tree: ``contents`` maps path -> file text."""
    files = sorted({*contents, *extra_files})
    return evaluate(policy, files, lambda path: contents.get(path, ""), checks)


def codes(report: Report) -> set[str]:
    return {violation.code for violation in report.violations}


def paths_for(report: Report, code: str) -> set[str]:
    return {violation.path for violation in report.violations if violation.code == code}


# ---------------------------------------------------------------------------
# The disposition record itself
# ---------------------------------------------------------------------------


def test_policy_file_exists_and_declares_the_contract(policy: Mapping[str, Any]) -> None:
    assert POLICY_PATH.is_file()
    assert POLICY_PATH == DEFAULT_POLICY
    assert policy["contract"] == EXPECTED_CONTRACT
    assert policy["schema_version"] == 2


def test_runtime_gate_invariants_are_dispositioned_and_structured(
    policy: Mapping[str, Any], repo_files: Sequence[str]
) -> None:
    """Runtime closure gates belong to v2, not to a downstream audit registry."""
    section = policy["runtime_gate_invariants"]
    assert section["schema_version"] == 1
    entries = section["entries"]
    assert entries
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))

    dispositioned = {
        path
        for surface in policy["frozen_surfaces"]
        for path in surface.get("inventory", [])
    }
    dispositioned |= {
        path
        for capability in policy["blocked_capabilities"]
        for path in capability.get("grandfathered_paths", [])
    }
    for entry in entries:
        assert entry["paths"]
        assert set(entry["paths"]) <= dispositioned
        assert set(entry["paths"]) <= set(repo_files)
        assert entry["assertions"]
        assert {assertion["type"] for assertion in entry["assertions"]} <= {
            "contains",
            "ordered_tokens",
            "constant_equals",
        }


def test_runtime_gate_invariants_pass_on_live_tree(
    policy: Mapping[str, Any],
) -> None:
    """All runtime gate invariants pass against the live repository tree."""
    section = policy["runtime_gate_invariants"]
    reader = make_reader(REPO_ROOT)
    for entry in section["entries"]:
        entry_id = entry["id"]
        for path in entry["paths"]:
            content = reader(path)
            assert content, f"runtime gate invariant {entry_id} path {path} is empty or unreadable"
            for assertion in entry["assertions"]:
                assert evaluate_runtime_gate_assertion(assertion, content), (
                    f"runtime gate invariant {entry_id} assertion {assertion} failed on {path}"
                )


def test_runtime_gate_invariants_reject_scheduler_mutation(policy: Mapping[str, Any]) -> None:
    """Mutating scheduler to unconditionally enqueue external fetch fails the invariant."""
    entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "scheduler_no_external_fetch_default"
    )
    assertion = entry["assertions"][0]
    mutated_scheduler = """
    def recurring_job_types(self) -> tuple[str, ...]:
        return (EXTERNAL_FETCH_JOB_TYPE,)
    """
    assert not evaluate_runtime_gate_assertion(assertion, mutated_scheduler)


def test_runtime_gate_invariants_reject_command_mutations(policy: Mapping[str, Any]) -> None:
    """Mutating backfill commands to drop provider validation or guards fails invariants."""
    provider_entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "external_backfill_provider_validation"
    )
    feed_guard_entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "external_backfill_feed_url_guard"
    )
    geo_live_entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "geography_backfill_live_mode_guard"
    )
    geo_dsn_entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "geography_backfill_dsn_guard"
    )

    mutated_backfill = """
    def run_backfill():
        do_unvalidated_ingestion()
    """
    assert not evaluate_runtime_gate_assertion(provider_entry["assertions"][0], mutated_backfill)
    assert not evaluate_runtime_gate_assertion(feed_guard_entry["assertions"][0], mutated_backfill)

    mutated_geo = """
    def main():
        connect_unconditionally()
    """
    assert not evaluate_runtime_gate_assertion(geo_live_entry["assertions"][0], mutated_geo)
    assert not evaluate_runtime_gate_assertion(geo_dsn_entry["assertions"][0], mutated_geo)


def test_runtime_gate_invariants_reject_worker_facade_and_api_mutations(
    policy: Mapping[str, Any],
) -> None:
    """Mutating worker handler, facade cutover default, or API routes fails invariants."""
    order_entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "worker_gate_before_service_construction"
    )
    mutated_worker_order = """
    service = ExternalIngestionService()
    if not fetch_enabled:
        raise NonRetryableJobError()
    """
    assert not evaluate_runtime_gate_assertion(order_entry["assertions"][0], mutated_worker_order)

    gate_entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "worker_fetch_gate"
    )
    assert not evaluate_runtime_gate_assertion(
        gate_entry["assertions"][0], "service = ExternalIngestionService()"
    )

    facade_entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "facade_platform_primary_default"
    )
    mutated_facade = "DEFAULT_CUTOVER_MODE = CUTOVER_MODE_LEGACY_ONLY\n"
    assert not evaluate_runtime_gate_assertion(facade_entry["assertions"][0], mutated_facade)

    api_410_entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "api_external_fetch_http_410"
    )
    mutated_api = "raise ApiError(status.HTTP_404_NOT_FOUND, 'not found')\n"
    assert not evaluate_runtime_gate_assertion(api_410_entry["assertions"][0], mutated_api)

    gw_entry = next(
        e for e in policy["runtime_gate_invariants"]["entries"]
        if e["id"] == "provider_gateway_geocode_unconfigured"
    )
    mutated_gw = "return {'status': 'ok'}\n"
    assert not evaluate_runtime_gate_assertion(gw_entry["assertions"][0], mutated_gw)


def test_policy_supersedes_the_v1_diff_gate(policy: Mapping[str, Any]) -> None:
    superseded = {entry["path"] for entry in policy["supersedes"]}
    assert "delivery_toolchain/governance/emgi-consumer-boundary.json" in superseded
    # v1 must still be on disk: v2 documents it as the fast per-PR gate.
    assert (REPO_ROOT / "delivery_toolchain/governance/emgi-consumer-boundary.json").is_file()


def test_policy_points_at_the_pinned_producer_catalog(policy: Mapping[str, Any]) -> None:
    platform = policy["authoritative_platform"]
    assert platform["repository"] == "alfloop-dev/oday-data-platform"
    assert platform["catalog_version"] == "0.4.1"
    assert len(platform["source_commit_sha"]) == 40


def test_every_disposition_is_used_by_at_least_one_rule(policy: Mapping[str, Any]) -> None:
    declared = {entry["id"] for entry in policy["dispositions"]}
    used = {rule["disposition"] for rule in policy["classification"]["rules"]}
    used |= {ref["disposition"] for ref in policy["provider_references"]["declared"]}
    assert declared == used, f"unused dispositions: {sorted(declared - used)}"


def test_classification_has_no_catch_all_rule(policy: Mapping[str, Any]) -> None:
    """A catch-all would defeat the point: novel surfaces must be named."""
    for rule in policy["classification"]["rules"]:
        assert "**" not in rule["include"], f"rule {rule['id']} is a catch-all"


# ---------------------------------------------------------------------------
# Acceptance 1 — classify every tracked file and every provider reference
# ---------------------------------------------------------------------------


def test_every_tracked_file_is_classified(
    policy: Mapping[str, Any], repo_files: Sequence[str]
) -> None:
    assigned = classify(repo_files, policy)
    missing = sorted(set(repo_files) - set(assigned))
    assert not missing, f"{len(missing)} tracked file(s) have no disposition: {missing[:20]}"


def test_classification_covers_more_than_the_known_directories(
    policy: Mapping[str, Any], repo_files: Sequence[str]
) -> None:
    """v1 only knew about modules/external_data; v2 has to reach the whole tree."""
    assigned = classify(repo_files, policy)
    assert len(assigned) == len(repo_files) > 2000
    top_level = {path.split("/", 1)[0] for path in assigned}
    for expected in ("modules", "apps", "shared", "services", "product_ops", "infra", "tests"):
        assert expected in top_level


def test_external_data_footprint_outside_the_module_is_classified_as_such(
    policy: Mapping[str, Any], repo_files: Sequence[str]
) -> None:
    """The surfaces a directory-only inventory would have missed."""
    assigned = classify(repo_files, policy)
    rules = {rule["id"]: rule["disposition"] for rule in policy["classification"]["rules"]}
    expected = {
        "services/provider-gateway/app.py": "frozen_legacy_producer",
        "shared/infrastructure/persistence/external_data.py": "frozen_legacy_producer",
        "product_ops/external_data_backfill.py": "frozen_legacy_producer",
        "packages/schemas/source_contracts/external/poi_snapshot.json": "frozen_legacy_producer",
        "apps/api/app/routes/external_data.py": "migrating_to_platform_client",
        "modules/integration/connectors/base.py": "migrating_to_platform_client",
    }
    for path, disposition in expected.items():
        assert path in assigned, f"{path} is not tracked any more; update the disposition record"
        assert rules[assigned[path]] == disposition, path


def test_no_dead_classification_rules(live_report: Report) -> None:
    assert not paths_for(live_report, "dead_classification_rule")


def test_every_detected_provider_reference_is_declared(live_report: Report) -> None:
    undeclared = sorted(paths_for(live_report, "undeclared_provider_reference"))
    assert not undeclared, f"undeclared provider references in: {undeclared[:20]}"
    assert live_report.stats["provider_reference_hits"] > 0


def test_no_dead_provider_declarations(live_report: Report) -> None:
    assert not paths_for(live_report, "dead_provider_declaration")


def test_provider_reference_scan_reaches_beyond_python(policy: Mapping[str, Any]) -> None:
    include = policy["provider_references"]["scan"]["include"]
    for suffix in (".py", ".ts", ".tsx", ".yaml", ".tf", ".sh", ".json", ".sql"):
        assert any(pattern.endswith(suffix) for pattern in include), suffix


# ---------------------------------------------------------------------------
# Acceptance 2 — the freeze and the blocked capabilities actually block
# ---------------------------------------------------------------------------


def test_live_tree_has_no_boundary_violations(live_report: Report) -> None:
    assert live_report.ok, "\n".join(v.render() for v in live_report.violations)


def test_frozen_inventories_match_the_working_tree(live_report: Report) -> None:
    assert not paths_for(live_report, "frozen_surface_addition")
    assert not paths_for(live_report, "frozen_surface_inventory_stale")
    assert live_report.stats["frozen_files"] > 0


def test_forbidden_paths_from_the_task_definition_are_frozen(policy: Mapping[str, Any]) -> None:
    """consumer-a.json forbids these to every ODayPlus Consumer task."""
    frozen_globs = [glob for surface in policy["frozen_surfaces"] for glob in surface["include"]]
    for forbidden in (
        "modules/external_data/providers/live.py",
        "modules/external_data/connectors/provider_registry.py",
        "modules/external_data/workers/scheduled_fetch.py",
    ):
        assert any(glob_match(forbidden, glob) for glob in frozen_globs), forbidden


def test_a_new_file_under_a_frozen_surface_is_rejected(
    policy: Mapping[str, Any], repo_files: Sequence[str]
) -> None:
    added = "modules/external_data/providers/new_partner_api.py"
    report = evaluate(policy, [*repo_files, added], make_reader(REPO_ROOT), ("freeze",))
    assert added in paths_for(report, "frozen_surface_addition")


def test_retiring_a_frozen_file_without_updating_the_record_is_rejected(
    policy: Mapping[str, Any], repo_files: Sequence[str]
) -> None:
    removed = "modules/external_data/workers/scheduled_fetch.py"
    remaining = [path for path in repo_files if path != removed]
    report = evaluate(policy, remaining, make_reader(REPO_ROOT), ("freeze",))
    assert removed in paths_for(report, "frozen_surface_inventory_stale")


def test_every_blocked_capability_from_the_acceptance_criteria_exists(
    policy: Mapping[str, Any],
) -> None:
    ids = {capability["id"] for capability in policy["blocked_capabilities"]}
    assert ids == {
        "new_provider_connector",
        "new_provider_credential",
        "new_source_scheduler",
        "new_raw_evidence_store",
        "new_canonical_market_writer",
        "direct_provider_reference",
        "direct_provider_fetch",
    }


@pytest.mark.parametrize(
    ("capability", "path", "content"),
    [
        (
            "new_provider_connector",
            "modules/listing/infrastructure/rakuten_provider.py",
            "class RakutenProvider:\n    pass\n",
        ),
        (
            "new_provider_credential",
            "modules/listing/application/feed.py",
            'API_KEY_ENV = "ODP_LISTING_PROVIDER_API_KEY_V2"\n',
        ),
        (
            "new_source_scheduler",
            "apps/worker/oday_worker/nightly.py",
            "from x import ExternalFetchScheduler\n",
        ),
        (
            "new_raw_evidence_store",
            "shared/infrastructure/persistence/feed_snapshots.py",
            "class MyStore(ListingFeedIngestionStore):\n    pass\n",
        ),
        (
            "new_canonical_market_writer",
            "modules/listing/infrastructure/writer.py",
            'SQL = "INSERT INTO external_data.real_estate_transactions (id) VALUES (%s)"\n',
        ),
        (
            "direct_provider_reference",
            "modules/sitescore/application/enrich.py",
            'URL = "https://rent.591.com.tw/list"\n',
        ),
        (
            "direct_provider_fetch",
            "modules/external_data/geo/refresh.py",
            "import httpx\n\nhttpx.get(URL)\n",
        ),
    ],
)
def test_blocked_capability_rejects_new_code(
    policy: Mapping[str, Any], capability: str, path: str, content: str
) -> None:
    report = synthetic(policy, {path: content}, ("capabilities",))
    assert f"blocked_capability:{capability}" in codes(report), (
        f"{capability} did not fire for {path}; violations={sorted(codes(report))}"
    )


def test_importing_the_frozen_producer_packages_from_product_code_is_rejected(
    policy: Mapping[str, Any],
) -> None:
    path = "modules/heatzone/application/scoring.py"
    report = synthetic(
        policy,
        {path: "from modules.external_data.providers import live\n"},
        ("capabilities",),
    )
    assert "blocked_capability:direct_provider_reference" in codes(report)
    assert path in paths_for(report, "blocked_capability:direct_provider_reference")


def test_grandfathered_paths_must_still_exist(policy: Mapping[str, Any]) -> None:
    """Retiring legacy code has to shrink the freeze list, not orphan it."""
    report = synthetic(policy, {}, ("capabilities",))
    stale = paths_for(report, "stale_grandfathered_path")
    # Against an empty tree, every grandfathered path is reported stale, which
    # is how the live tree proves each one is still real.
    assert stale, "no grandfathered paths are being liveness-checked"


def test_a_new_provider_reference_in_an_undeclared_file_is_rejected(
    policy: Mapping[str, Any],
) -> None:
    path = "modules/netplan/application/lookup.py"
    report = synthetic(
        policy,
        {path: 'HOST = "www.sinyi.com.tw"\n'},
        ("provider_references",),
    )
    assert path in paths_for(report, "undeclared_provider_reference")


def test_a_declared_reference_in_the_wrong_place_is_still_rejected(
    policy: Mapping[str, Any],
) -> None:
    """Declarations are (text, path) pairs; the text alone is not a licence."""
    path = "modules/sitescore/domain/model.py"
    report = synthetic(
        policy,
        {path: 'SOURCE = "www.591.com.tw"\n'},
        ("provider_references",),
    )
    assert path in paths_for(report, "undeclared_provider_reference")


# ---------------------------------------------------------------------------
# Acceptance 3 — assisted intake and product review keep working
# ---------------------------------------------------------------------------


def test_allowed_surfaces_cover_intake_and_review(policy: Mapping[str, Any]) -> None:
    surfaces = {surface["id"] for surface in policy["allowed_surfaces"]}
    assert surfaces == {"assisted_listing_intake", "product_review_and_promotion"}


@pytest.mark.parametrize(
    "path",
    [
        "modules/external_data/application/assisted_intake.py",
        "modules/external_data/application/xlsx_import.py",
        "modules/external_data/security/assisted_listing_retrieval.py",
        "apps/worker/assisted_listing_intake/worker.py",
        "apps/web/features/operator/network/intake/AddListingFromUrlDialog.tsx",
        "modules/opsboard/application/network_listings.py",
        "apps/api/app/routes/operator_modules/network_listings.py",
    ],
)
def test_allowed_workflow_files_are_classified_as_allowed(
    policy: Mapping[str, Any], repo_files: Sequence[str], path: str
) -> None:
    assert path in repo_files, f"{path} is not tracked any more; update the disposition record"
    rules = {rule["id"]: rule["disposition"] for rule in policy["classification"]["rules"]}
    disposition = rules[classify([path], policy)[path]]
    assert disposition in {"assisted_intake_workflow", "product_review_workflow"}


def test_assisted_intake_may_keep_naming_its_listing_sources(policy: Mapping[str, Any]) -> None:
    """The intake gate must recognise 591/sinyi/rakuya to refuse to fetch them."""
    path = "modules/external_data/application/assisted_intake.py"
    report = synthetic(
        policy,
        {path: 'HOSTS = ("www.591.com.tw", "www.sinyi.com.tw", "www.rakuya.com.tw")\n'},
        ("capabilities", "provider_references"),
    )
    assert not [v for v in report.violations if v.path == path], (
        "the allowed intake workflow was blocked: "
        + "; ".join(v.render() for v in report.violations if v.path == path)
    )


def test_product_review_workflow_is_not_blocked(policy: Mapping[str, Any]) -> None:
    path = "modules/opsboard/application/promotion.py"
    report = synthetic(
        policy,
        {path: "class PromotionDecisionProvider:\n    pass\n"},
        ("capabilities",),
    )
    assert not [v for v in report.violations if v.path == path]


def test_allowed_surface_exemptions_are_narrow(policy: Mapping[str, Any]) -> None:
    """Intake is allowed to name sources, not to grow credentials or schedulers."""
    for surface in policy["allowed_surfaces"]:
        exemptions = set(surface.get("capability_exemptions") or ())
        assert "new_provider_credential" not in exemptions, surface["id"]
        assert "new_source_scheduler" not in exemptions, surface["id"]
        assert "new_canonical_market_writer" not in exemptions, surface["id"]


def test_intake_may_not_grow_a_scheduler(policy: Mapping[str, Any]) -> None:
    path = "apps/worker/assisted_listing_intake/worker.py"
    report = synthetic(
        policy,
        {path: "from x import ExternalFetchScheduler\n"},
        ("capabilities",),
    )
    assert path in paths_for(report, "blocked_capability:new_source_scheduler")


# ---------------------------------------------------------------------------
# Validator mechanics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("modules/external_data/providers/live.py", "modules/external_data/providers/**", True),
        ("modules/external_data/providers", "modules/external_data/providers/**", True),
        ("modules/external_data/geo/pipeline.py", "modules/external_data/providers/**", False),
        ("apps/a/b/c.py", "apps/**", True),
        ("apps/a.py", "apps/*.py", True),
        ("apps/a/b.py", "apps/*.py", False),
        ("tests/unit/test_a.py", "**/test_*.py", True),
        ("test_a.py", "**/test_*.py", True),
        ("a/b/c/d.py", "a/**/d.py", True),
        ("a/d.py", "a/**/d.py", True),
        ("root.zip", "*.zip", True),
        ("nested/root.zip", "*.zip", False),
    ],
)
def test_glob_semantics(path: str, pattern: str, expected: bool) -> None:
    assert glob_match(path, pattern) is expected


def test_star_does_not_cross_a_path_separator() -> None:
    """fnmatch would say True here, which is why the validator does not use it."""
    assert not glob_match("modules/external_data/providers/live.py", "modules/*/live.py")
    assert glob_match("modules/external_data/providers/live.py", "modules/*/*/live.py")


def test_classification_is_first_match_wins(policy: Mapping[str, Any]) -> None:
    """assisted_intake_domain precedes external_data_module_remainder."""
    assigned = classify(["modules/external_data/application/assisted_intake.py"], policy)
    assert assigned["modules/external_data/application/assisted_intake.py"] == (
        "assisted_intake_domain"
    )


def test_unclassified_file_is_a_violation(policy: Mapping[str, Any]) -> None:
    report = synthetic(policy, {"brand_new_surface/thing.py": ""}, ("classification",))
    assert "brand_new_surface/thing.py" in paths_for(report, "unclassified_file")


def test_evaluate_rejects_an_unknown_check(policy: Mapping[str, Any]) -> None:
    with pytest.raises(PolicyError, match="unknown check"):
        evaluate(policy, [], lambda _: "", ("not_a_check",))


def test_loader_rejects_a_wrong_contract(tmp_path: Path) -> None:
    bad = tmp_path / "policy.yaml"
    bad.write_text("contract: something.else\nschema_version: 2\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="contract must be"):
        load_policy(bad)


def test_loader_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="cannot read"):
        load_policy(tmp_path / "absent.yaml")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda p: p["classification"]["rules"].append(
                {"id": "x", "disposition": "nope", "include": ["a"], "rationale": "r"}
            ),
            "unknown disposition",
        ),
        (
            lambda p: p["classification"]["rules"].append(
                {"id": "legacy_provider_adapters", "disposition": "archived",
                 "include": ["a"], "rationale": "r"}
            ),
            "duplicate classification rule",
        ),
        (
            lambda p: p["allowed_surfaces"][0]["capability_exemptions"].append("ghost"),
            "unknown capability",
        ),
        (
            lambda p: p["provider_references"]["signals"].append(
                {"id": "broken", "pattern": "([unclosed"}
            ),
            "invalid regex",
        ),
        (
            lambda p: p["provider_references"]["declared"].append(
                {"id": "x", "signal": "ghost", "disposition": "archived",
                 "matches": ["a"], "paths": ["b"], "rationale": "r"}
            ),
            "unknown signal",
        ),
        (
            lambda p: p["runtime_gate_invariants"]["entries"].append({
                "id": "bad_assertion",
                "paths": ["modules/external_data/application/market_data_facade.py"],
                "assertions": [{"type": "unknown_type"}],
            }),
            "unknown assertion type",
        ),
        (
            lambda p: p["runtime_gate_invariants"]["entries"].append({
                "id": "facade_platform_primary_default",
                "paths": ["modules/external_data/application/market_data_facade.py"],
                "assertions": [{"type": "contains", "text": "foo"}],
            }),
            "duplicate runtime gate invariant id",
        ),
        (
            lambda p: p["runtime_gate_invariants"]["entries"].append({
                "id": "undispositioned_path_entry",
                "paths": ["apps/web/features/operator/network/intake/AddListingFromUrlDialog.tsx"],
                "assertions": [{"type": "contains", "text": "foo"}],
            }),
            "not dispositioned in inventory or grandfathered_paths",
        ),
        (
            lambda p: p["runtime_gate_invariants"]["entries"].append({
                "id": "no_assertions_entry",
                "paths": ["modules/external_data/application/market_data_facade.py"],
                "assertions": [],
            }),
            "assertions must not be empty",
        ),
        (
            lambda p: p["runtime_gate_invariants"]["entries"].append({
                "id": "no_paths_entry",
                "paths": [],
                "assertions": [{"type": "contains", "text": "foo"}],
            }),
            "must declare a non-empty paths list",
        ),
        (
            lambda p: p["runtime_gate_invariants"].update({"schema_version": 99}),
            "schema_version must be 1",
        ),
    ],
)
def test_structural_validation_rejects_an_unenforceable_policy(
    policy: Mapping[str, Any], mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    broken = copy.deepcopy(dict(policy))
    mutate(broken)
    with pytest.raises(PolicyError, match=message):
        validate_policy_structure(broken)


def test_shipped_policy_is_valid_yaml_and_structurally_sound() -> None:
    document = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    validate_policy_structure(document, source=str(POLICY_PATH))


# ---------------------------------------------------------------------------
# Command-line contract (what the task's verification commands run)
# ---------------------------------------------------------------------------


def test_cli_exits_zero_on_the_live_tree(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "external-data boundary: OK" in capsys.readouterr().out


def test_cli_json_report_is_machine_readable(capsys: pytest.CaptureFixture[str]) -> None:
    import json

    assert main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["contract"] == EXPECTED_CONTRACT
    assert payload["ok"] is True
    assert payload["violations"] == []
    assert payload["stats"]["unclassified"] == 0


def test_cli_reports_policy_errors_as_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "policy.yaml"
    bad.write_text("contract: wrong\nschema_version: 2\n", encoding="utf-8")
    assert main(["--policy", str(bad)]) == 2
    assert "policy error" in capsys.readouterr().err


def test_validator_runs_as_a_script() -> None:
    """The exact command the task definition lists as verification."""
    completed = subprocess.run(
        [sys.executable, "scripts/validate_external_data_boundary.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
