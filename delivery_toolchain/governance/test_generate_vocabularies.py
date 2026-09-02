"""The vocabulary check must be able to fail.

It exists to catch a second definition of a governed concept appearing. A
version that could not fail would put a green light on exactly the drift it was
built to stop -- and this concept has drifted before: Evidence Level was aligned
once by hand under ODP-EVIDENCE-LEVEL-ALIGNMENT-001 and still ended up with a
fourth vocabulary in the frontend contracts, because a one-off sweep is not a
standing constraint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from delivery_toolchain.governance import generate_vocabularies as gen


@pytest.fixture
def payload() -> dict:
    return gen.load()


class TestTheSourceAndTheGeneratedModuleAgree:
    def test_the_checked_in_module_is_not_stale(self, payload: dict) -> None:
        """If this fails, vocabularies.json was edited without re-running
        --write, and the two have started to disagree."""
        assert gen.GENERATED.read_text(encoding="utf-8") == gen.render(payload)

    def test_the_canonical_typescript_still_matches(self, payload: dict) -> None:
        assert gen._typescript_mismatch(payload) == []

    def test_the_repository_has_no_unrecorded_fork(self, payload: dict) -> None:
        forks = gen.find_forks(payload)
        assert not forks, "\n".join(f.describe() for f in forks)


class TestItDetectsANewFork:
    def test_a_second_definition_is_reported(self, tmp_path: Path, monkeypatch) -> None:
        """The case this exists for: someone needs the type in a layer that does
        not import it, and writes their own."""
        module = tmp_path / "modules" / "sample"
        module.mkdir(parents=True)
        (module / "models.py").write_text(
            "from enum import StrEnum\n"
            "\n"
            "class EvidenceLevel(StrEnum):\n"
            '    L0_ANECDOTAL = "L0"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(gen, "GENERATED", tmp_path / "shared" / "governance" / "vocabularies.py")

        forks = gen.find_forks({"vocabularies": [{"name": "EvidenceLevel", "members": []}]})
        assert len(forks) == 1
        assert forks[0].vocabulary == "EvidenceLevel"
        assert "modules/sample/models.py" in forks[0].path

    def test_a_recorded_fork_is_not_reported_again(self, tmp_path: Path, monkeypatch) -> None:
        """Existing forks are written down with a note rather than silently
        tolerated; the check is about stopping new ones."""
        module = tmp_path / "modules" / "sample"
        module.mkdir(parents=True)
        (module / "models.py").write_text(
            "from enum import StrEnum\n"
            "\n"
            "class EvidenceLevel(StrEnum):\n"
            '    L0_ANECDOTAL = "L0"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(gen, "GENERATED", tmp_path / "shared" / "governance" / "vocabularies.py")

        forks = gen.find_forks(
            {
                "vocabularies": [
                    {
                        "name": "EvidenceLevel",
                        "members": [],
                        "known_forks": [
                            {
                                "location": "modules/sample/models.py",
                                "definition": "...",
                                "note": "recorded",
                            }
                        ],
                    }
                ]
            }
        )
        assert forks == []

    def test_the_generated_module_is_not_reported_as_its_own_fork(self, payload: dict) -> None:
        assert all(fork.path != str(gen.GENERATED.relative_to(gen.REPO_ROOT)) for fork in gen.find_forks(payload))


class TestTheTypeScriptComparisonIsReal:
    def test_a_diverging_typescript_type_is_caught(self) -> None:
        problems = gen._typescript_mismatch(
            {
                "vocabularies": [
                    {
                        "name": "EvidenceLevel",
                        "typescript_type": "EvidenceLevel",
                        "members": [{"name": "X", "value": "X0"}],
                    }
                ]
            }
        )
        assert problems and "vocabularies.json says" in problems[0]

    def test_a_missing_typescript_type_is_caught(self) -> None:
        problems = gen._typescript_mismatch(
            {
                "vocabularies": [
                    {
                        "name": "Nope",
                        "typescript_type": "NoSuchTypeAnywhere",
                        "members": [{"name": "X", "value": "X0"}],
                    }
                ]
            }
        )
        assert problems and "declares no" in problems[0]


class TestTheGeneratedEnumIsUsable:
    def test_the_ladder_is_ordered_and_complete(self) -> None:
        from shared.governance.vocabularies import EvidenceLevel

        assert [member.value for member in EvidenceLevel] == ["L0", "L1", "L2", "L3", "L4", "L5"]

    def test_job_status_carries_partial(self) -> None:
        """The member ODP-FR-SHARED-001 asks for and neither existing enum has.
        A job that half-succeeded has to be recorded as succeeded or failed
        without it, and both are wrong in a way the caller cannot see."""
        from shared.governance.vocabularies import JobStatus

        assert JobStatus.PARTIAL.value == "partial"

    def test_the_domains_that_used_to_declare_their_own_now_share_one(self) -> None:
        """Identical copies drift the moment one is edited. These two were
        identical, so consolidating them changes nothing today and prevents the
        divergence tomorrow."""
        from modules.adlift.domain.incrementality import EvidenceLevel as adlift_level
        from modules.intervention.domain.lifecycle import EvidenceLevel as intervention_level
        from shared.governance.vocabularies import EvidenceLevel as canonical

        assert adlift_level is canonical
        assert intervention_level is canonical
