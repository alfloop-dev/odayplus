"""The measurement-default check must be able to fail.

A gate that has never been shown to refuse anything is indistinguishable from
one that cannot. That is not an abstract worry here: the assisted-listing schema
validator sat with a broken path from commit 549ce261 onwards and nobody knew,
because the only test that would have reported it was excluded from CI by a
marker expression. This file exists so the same thing cannot be said of this
check.

Each case plants one specific input and asserts the outcome, including the
cases where the check must stay quiet -- a rule that fires on legitimate code
gets a blanket exemption and then guards nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from delivery_toolchain.governance.check_measurement_defaults import (
    load_exemptions,
    scan,
)


def _tree(tmp_path: Path, source: str, *, package: str = "modules") -> Path:
    module_dir = tmp_path / package / "sample" / "domain"
    module_dir.mkdir(parents=True)
    (module_dir / "models.py").write_text(source, encoding="utf-8")
    return tmp_path


class TestItRefusesTheDefectItExistsFor:
    def test_a_quality_score_defaulting_to_perfect_is_reported(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Observation:\n"
            "    store_id: str\n"
            "    data_quality_score: float = 1.0\n",
        )
        violations = scan(root)
        assert len(violations) == 1
        assert violations[0].field_name == "data_quality_score"
        assert violations[0].class_name == "Observation"

    def test_confidence_and_coverage_ratio_are_caught_too(self, tmp_path: Path) -> None:
        """The two shapes that disable heat zone's abstention gate."""
        root = _tree(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Feature:\n"
            "    confidence: float = 1.0\n"
            "    coverage_ratio: float = 1.0\n",
        )
        assert {v.field_name for v in scan(root)} == {"confidence", "coverage_ratio"}

    def test_frozen_and_slots_dataclasses_are_not_a_way_around_it(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass(frozen=True, slots=True)\n"
            "class Feature:\n"
            "    quality_score: float = 1.0\n",
        )
        assert len(scan(root)) == 1


class TestItStaysQuietWhereItShould:
    """Precision is what keeps a rule enforceable. Each of these is a case the
    broad version of this rule flagged, and each is legitimate."""

    def test_an_explicitly_absent_score_is_accepted(self, tmp_path: Path) -> None:
        """`None` is the correct way to say the figure was not supplied, which
        is the whole point -- the check must not push authors away from it."""
        root = _tree(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Observation:\n"
            "    data_quality_score: float | None = None\n",
        )
        assert scan(root) == []

    def test_a_score_defaulting_to_the_bottom_of_the_range_is_accepted(
        self, tmp_path: Path
    ) -> None:
        """0.0 fails closed: an unmeasured record reads as unusable, not as
        flawless. That is the opposite defect and not this rule's business."""
        root = _tree(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Observation:\n"
            "    confidence: float = 0.0\n",
        )
        assert scan(root) == []

    def test_configuration_parameters_are_not_flagged(self, tmp_path: Path) -> None:
        """srid, limit and horizon are knobs, not measurements. Flagging them is
        how a rule earns a blanket exemption."""
        root = _tree(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Query:\n"
            "    srid: int = 4326\n"
            "    limit: int = 100\n"
            "    horizon_days: int = 28\n"
            "    risk_per_flag: float = 1.0\n",
        )
        assert scan(root) == []

    def test_a_plain_class_is_not_a_dataclass(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            "class Observation:\n    data_quality_score: float = 1.0\n",
        )
        assert scan(root) == []

    def test_test_files_are_not_scanned(self, tmp_path: Path) -> None:
        """Fixtures legitimately construct perfect-quality records."""
        module_dir = tmp_path / "modules" / "sample" / "tests"
        module_dir.mkdir(parents=True)
        (module_dir / "test_sample.py").write_text(
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Fixture:\n"
            "    data_quality_score: float = 1.0\n",
            encoding="utf-8",
        )
        assert scan(tmp_path) == []


class TestExemptionsStayAttributable:
    def test_an_exemption_without_an_owner_is_refused(self, tmp_path: Path) -> None:
        """An unattributed exemption is how the debt goes back to being
        invisible, which is the state this check was written to leave."""
        path = tmp_path / "exemptions.json"
        path.write_text(
            json.dumps({"exemptions": [{"field": "a.py::C.f", "reason": "later"}]}),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as excinfo:
            load_exemptions(path)
        assert "owner" in str(excinfo.value)

    def test_an_exemption_without_a_reason_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "exemptions.json"
        path.write_text(
            json.dumps({"exemptions": [{"field": "a.py::C.f", "owner": "Ops"}]}),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as excinfo:
            load_exemptions(path)
        assert "reason" in str(excinfo.value)

    def test_a_complete_exemption_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "exemptions.json"
        path.write_text(
            json.dumps(
                {
                    "exemptions": [
                        {"field": "a.py::C.f", "owner": "Ops", "reason": "documented"}
                    ]
                }
            ),
            encoding="utf-8",
        )
        assert load_exemptions(path)["a.py::C.f"]["owner"] == "Ops"


class TestTheCheckedInRepositoryStaysClean:
    def test_every_live_violation_is_exempted_with_an_owner(self) -> None:
        """The check is only a gate while the repository satisfies it. If this
        fails, a new bounded score defaulting to perfect has been introduced."""
        from delivery_toolchain.governance.check_measurement_defaults import (
            EXEMPTIONS_PATH,
            REPO_ROOT,
        )

        violations = scan(REPO_ROOT)
        exemptions = load_exemptions(EXEMPTIONS_PATH)
        unexempted = [v.describe() for v in violations if v.key not in exemptions]
        assert not unexempted, "\n".join(unexempted)

    def test_no_exemption_outlives_the_field_it_covers(self) -> None:
        """A stale exemption reads as live debt that is already paid, which
        makes the list less trustworthy the longer it sits."""
        from delivery_toolchain.governance.check_measurement_defaults import (
            EXEMPTIONS_PATH,
            REPO_ROOT,
        )

        live = {v.key for v in scan(REPO_ROOT)}
        stale = sorted(set(load_exemptions(EXEMPTIONS_PATH)) - live)
        assert not stale, f"exemptions for fields that no longer violate: {stale}"
