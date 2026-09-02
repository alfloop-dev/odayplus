"""The requirement-member check must be able to fail.

Its whole purpose is to catch a member quietly going missing, so a version that
cannot fail would be worse than none: it would put a green light on the exact
drift it was built to stop.
"""

from __future__ import annotations

import json
from pathlib import Path

from delivery_toolchain.governance.check_requirement_members import (
    MANIFEST_PATH,
    REPO_ROOT,
    check,
    resolve,
)


def _manifest(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _repo(tmp_path: Path, source: str = "class Thing:\n    field: int = 0\n") -> Path:
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "mod.py").write_text(source, encoding="utf-8")
    return tmp_path


class TestEvidenceMustPointSomewhereReal:
    def test_a_satisfied_member_whose_symbol_vanished_fails(self, tmp_path: Path) -> None:
        """The drift this exists to catch: an implementation renamed or deleted
        while the requirement still claims it is covered."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 1,
                        "members": [
                            {"name": "A", "status": "satisfied", "evidence": "pkg/mod.py::Gone"}
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert len(failures) == 1
        assert "defines no 'Gone'" in failures[0].problem

    def test_a_satisfied_member_pointing_at_a_missing_file_fails(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 1,
                        "members": [
                            {"name": "A", "status": "satisfied", "evidence": "pkg/nope.py::Thing"}
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert "no such file" in failures[0].problem

    def test_a_satisfied_member_with_no_evidence_at_all_fails(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 1,
                        "members": [{"name": "A", "status": "satisfied"}],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert "no evidence" in failures[0].problem

    def test_a_resolving_reference_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 2,
                        "members": [
                            {"name": "A", "status": "satisfied", "evidence": "pkg/mod.py::Thing"},
                            {"name": "B", "status": "satisfied", "evidence": "pkg/mod.py::Thing.field"},
                        ],
                    }
                ]
            },
        )
        failures, tally = check(root, manifest)
        assert failures == []
        assert tally["satisfied"] == 2


class TestAGapMustBeWrittenDown:
    def test_an_absent_member_with_no_note_fails(self, tmp_path: Path) -> None:
        """An unwritten gap is exactly the state ODP-SA-06's boilerplate
        acceptance columns left everything in."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 1,
                        "members": [{"name": "A", "status": "absent"}],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert "no note" in failures[0].problem

    def test_an_absent_member_with_a_note_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 1,
                        "members": [
                            {"name": "A", "status": "absent", "note": "needs a time dimension"}
                        ],
                    }
                ]
            },
        )
        failures, tally = check(root, manifest)
        assert failures == []
        assert tally["absent"] == 1


class TestTheListItselfIsGuarded:
    def test_a_dropped_member_is_caught_by_the_count(self, tmp_path: Path) -> None:
        """Without this, deleting a member from the list makes the requirement
        pass by shrinking -- which is the failure being modelled, one level up."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 3,
                        "members": [{"name": "A", "status": "absent", "note": "x"}],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert "member_count says 3" in failures[0].problem

    def test_a_requirement_with_no_member_count_is_refused(self, tmp_path: Path) -> None:
        """An optional count is not a weaker guard, it is no guard.

        The count is the only thing here that can see a member being deleted:
        after the deletion the list is still internally consistent, so every
        other rule in the file still passes. If omitting the count were allowed,
        the cheapest way to make a shrinking requirement go green would be to
        delete the count along with the member -- one edit, no failure.
        """
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [{"name": "A", "status": "absent", "note": "x"}],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("declares no member_count" in f.problem for f in failures)

    def test_dropping_a_member_and_its_count_together_still_fails(
        self, tmp_path: Path
    ) -> None:
        """The evasion the previous test exists to close, spelled out: a
        two-member requirement shrunk to one, with the count removed rather
        than corrected. Every member left in the list is well-formed."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {"name": "A", "status": "satisfied", "evidence": "pkg/mod.py::Thing"}
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert failures, "a requirement that shrank passed with no member_count"

    def test_a_non_integer_member_count_is_refused(self, tmp_path: Path) -> None:
        """`"1"` and `1.0` compare unequal to `len(members)` and would have
        been caught, but `True` equals `1` in Python -- a count of `true` would
        have satisfied a one-member list and then never bound again."""
        root = _repo(tmp_path)
        for bad in ("1", True, 1.0):
            manifest = _manifest(
                tmp_path,
                {
                    "requirements": [
                        {
                            "id": "R-1",
                            "member_count": bad,
                            "members": [{"name": "A", "status": "absent", "note": "x"}],
                        }
                    ]
                },
            )
            failures, _ = check(root, manifest)
            assert any(
                "member_count must be an integer" in f.problem for f in failures
            ), f"member_count={bad!r} was accepted"

    def test_a_matching_member_count_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 1,
                        "members": [{"name": "A", "status": "absent", "note": "x"}],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert failures == []

    def test_a_duplicate_member_is_caught(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 2,
                        "members": [
                            {"name": "A", "status": "absent", "note": "x"},
                            {"name": "A", "status": "absent", "note": "x"},
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("listed twice" in f.problem for f in failures)

    def test_an_unknown_status_is_refused(self, tmp_path: Path) -> None:
        """'partial' would be a way to record a member as neither done nor
        missing, which is the ambiguity this file removes."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": 1,
                        "members": [{"name": "A", "status": "partial"}],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert "not one of" in failures[0].problem

    def test_a_requirement_with_no_members_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(tmp_path, {"requirements": [{"id": "R-1", "members": []}]})
        failures, _ = check(root, manifest)
        assert "declares no members" in failures[0].problem


class TestNestedDefinitionsResolve:
    def test_a_class_declared_inside_a_function_is_found(self, tmp_path: Path) -> None:
        """`IntakeMethod` lives inside the router factory in
        apps/api/app/routes/listings.py. Missing it would push the manifest
        towards weaker evidence than the code actually offers."""
        root = _repo(
            tmp_path,
            "def build_router():\n    class IntakeMethod:\n        MANUAL = 'MANUAL'\n    return IntakeMethod\n",
        )
        assert resolve(root, "pkg/mod.py::IntakeMethod") is None


class TestTheCheckedInManifestHolds:
    def test_the_repository_manifest_passes(self) -> None:
        """If this fails, either a member's implementation moved without the
        manifest following, or a gap was recorded without saying what it is."""
        failures, _ = check(REPO_ROOT, MANIFEST_PATH)
        assert not failures, "\n".join(f.describe() for f in failures)

    def test_every_seeded_requirement_from_the_verification_is_present(self) -> None:
        """The five set-valued gaps in the FR report are what this file was
        seeded from; losing one would quietly narrow the check's coverage."""
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        ids = {entry["id"] for entry in payload["requirements"]}
        assert {
            "ODP-FR-NET-002",
            "ODP-FR-SITE-001",
            "ODP-FR-LH-003",
            "ODP-FR-INTV-006",
            "ODP-FR-SHARED-001",
        } <= ids
