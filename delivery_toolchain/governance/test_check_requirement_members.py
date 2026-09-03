"""The requirement-member check must be able to fail.

Its whole purpose is to catch a member quietly going missing or a requirement
quietly slipping past governance, so a version that cannot fail would be worse
than none: it would put a green light on the exact drift it was built to stop.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from delivery_toolchain.governance.check_requirement_members import (
    MANIFEST_PATH,
    REPO_ROOT,
    STATUTORY_DECISION_FIELDS,
    VALID_DISPOSITION_STATES,
    WAIVER_SIGNAL_FIELDS,
    check,
    check_decision_date,
    find_handback_claim,
    find_nonimplementation_claim,
    is_ai_decider,
    resolve,
    validate_transition,
)


def _manifest(tmp_path: Path, payload: dict) -> Path:
    """Write a manifest, filling in member_count and default disposition where the case is not about it."""
    for entry in payload.get("requirements", []):
        if "member_count" not in entry and entry.get("members") is not None:
            entry["member_count"] = len(entry["members"])
        for member in entry.get("members", []):
            if member.get("status") == "absent" and "disposition" not in member:
                member["disposition"] = {
                    "state": "OPEN",
                    "rationale": member.get("note", "active gap"),
                    "assigned_to": "Triage Lead",
                    "next_review_date": "2026-10-01",
                }
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
                    {"id": "R-1", "members": [{"name": "A", "status": "satisfied", "evidence": "pkg/nope.py::Thing"}]}
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert "no such file" in failures[0].problem

    def test_a_satisfied_member_with_no_evidence_at_all_fails(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {"requirements": [{"id": "R-1", "members": [{"name": "A", "status": "satisfied"}]}]},
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
            {"requirements": [{"id": "R-1", "members": [{"name": "A", "status": "absent"}]}]},
        )
        failures, _ = check(root, manifest)
        assert any("no note" in f.problem for f in failures)

    def test_an_absent_member_declaring_no_disposition_fails(self, tmp_path: Path) -> None:
        """An absent member must have a structured disposition object."""
        root = _repo(tmp_path)
        path = tmp_path / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "requirements": [
                        {
                            "id": "R-1",
                            "member_count": 1,
                            "members": [{"name": "A", "status": "absent", "note": "needs data"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        failures, _ = check(root, path)
        assert any("declares no 'disposition'" in f.problem for f in failures)

    def test_an_absent_member_with_a_note_and_valid_disposition_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "needs a time dimension",
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "needs a time dimension",
                                    "assigned_to": "Architecture Lead",
                                },
                            }
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

    def test_a_requirement_without_a_member_count_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        path = tmp_path / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "requirements": [
                        {"id": "R-1", "members": [{"name": "A", "status": "absent", "note": "x"}]}
                    ]
                }
            ),
            encoding="utf-8",
        )
        failures, _ = check(root, path)
        assert any("declares no member_count" in f.problem for f in failures)

    def test_a_non_integer_member_count_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "member_count": "1",
                        "members": [{"name": "A", "status": "absent", "note": "x"}],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("must be an integer" in f.problem for f in failures)

    def test_every_seeded_requirement_declares_its_count(self) -> None:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        missing = [e["id"] for e in payload["requirements"] if "member_count" not in e]
        assert not missing, f"entries without member_count: {missing}"

    def test_a_duplicate_member_is_caught(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
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
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {"requirements": [{"id": "R-1", "members": [{"name": "A", "status": "partial"}]}]},
        )
        failures, _ = check(root, manifest)
        assert "not one of" in failures[0].problem

    def test_a_requirement_with_no_members_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps({"requirements": [{"id": "R-1", "members": []}]}), encoding="utf-8")
        manifest = path
        failures, _ = check(root, manifest)
        assert "declares no members" in failures[0].problem


class TestNestedDefinitionsResolve:
    def test_a_class_declared_inside_a_function_is_found(self, tmp_path: Path) -> None:
        root = _repo(
            tmp_path,
            "def build_router():\n    class IntakeMethod:\n        MANUAL = 'MANUAL'\n    return IntakeMethod\n",
        )
        assert resolve(root, "pkg/mod.py::IntakeMethod") is None


class TestDispositionSchemaAndTransitions:
    def test_all_five_states_are_valid_in_vocabulary(self) -> None:
        expected = {"OPEN", "BLOCKED_BY_EVIDENCE", "DECIDED", "IMPLEMENTATION_READY", "VERIFIED"}
        assert VALID_DISPOSITION_STATES == expected

    def test_invalid_disposition_state_fails(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "pending",
                                "disposition": {"state": "INVALID_STATE"},
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("is not one of" in f.problem for f in failures)

    def test_absent_cannot_claim_verified_disposition(self, tmp_path: Path) -> None:
        """Absent is an index of unfulfilled requirements and cannot claim VERIFIED."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "not actually implemented",
                                "disposition": {"state": "VERIFIED"},
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("absent member cannot have disposition state 'VERIFIED'" in f.problem for f in failures)

    def test_legal_and_illegal_transitions(self) -> None:
        # Legal transitions per documented lifecycle
        assert validate_transition("OPEN", "DECIDED") is True
        assert validate_transition("OPEN", "BLOCKED_BY_EVIDENCE") is True
        assert validate_transition("OPEN", "IMPLEMENTATION_READY") is True
        assert validate_transition("BLOCKED_BY_EVIDENCE", "OPEN") is True
        assert validate_transition("BLOCKED_BY_EVIDENCE", "DECIDED") is True
        assert validate_transition("BLOCKED_BY_EVIDENCE", "IMPLEMENTATION_READY") is True
        assert validate_transition("DECIDED", "IMPLEMENTATION_READY") is True
        assert validate_transition("DECIDED", "OPEN") is True
        assert validate_transition("IMPLEMENTATION_READY", "VERIFIED") is True
        assert validate_transition("IMPLEMENTATION_READY", "BLOCKED_BY_EVIDENCE") is True
        assert validate_transition("IMPLEMENTATION_READY", "OPEN") is True
        assert validate_transition("VERIFIED", "OPEN") is True
        assert validate_transition("VERIFIED", "BLOCKED_BY_EVIDENCE") is True

        # Illegal transitions
        assert validate_transition("OPEN", "VERIFIED") is False
        assert validate_transition("DECIDED", "BLOCKED_BY_EVIDENCE") is False
        assert validate_transition("IMPLEMENTATION_READY", "DECIDED") is False
        assert validate_transition("BLOCKED_BY_EVIDENCE", "VERIFIED") is False
        assert validate_transition("VERIFIED", "DECIDED") is False
        assert validate_transition("VERIFIED", "IMPLEMENTATION_READY") is False
        assert validate_transition("DECIDED", "VERIFIED") is False

    def test_illegal_transition_history_is_rejected(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "illegal transition jump",
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "jumped directly",
                                    "assigned_to": "Architecture Lead",
                                    "history": [
                                        {"state": "BLOCKED_BY_EVIDENCE", "date": "2026-09-01"},
                                        {"state": "VERIFIED", "date": "2026-09-02"},
                                    ],
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("illegal disposition transition in history" in f.problem for f in failures)

    def test_illegal_previous_state_transition_is_rejected(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        # Test OPEN -> VERIFIED illegal jump
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "satisfied",
                                "evidence": "pkg/mod.py::Thing",
                                "disposition": {
                                    "state": "VERIFIED",
                                    "previous_state": "OPEN",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("illegal disposition transition: OPEN -> VERIFIED" in f.problem for f in failures)

        # Test DECIDED -> BLOCKED_BY_EVIDENCE illegal jump
        manifest2 = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "blocked",
                                "disposition": {
                                    "state": "BLOCKED_BY_EVIDENCE",
                                    "previous_state": "DECIDED",
                                    "evidence_needed": "Data audit",
                                    "evidence_owner": "Data Lead",
                                    "next_review_date": "2026-10-01",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures2, _ = check(root, manifest2)
        assert any("illegal disposition transition: DECIDED -> BLOCKED_BY_EVIDENCE" in f.problem for f2 in failures2 for f in [f2])

        # Test IMPLEMENTATION_READY -> DECIDED illegal jump
        manifest3 = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "decided waiver",
                                "disposition": {
                                    "state": "DECIDED",
                                    "previous_state": "IMPLEMENTATION_READY",
                                    "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                    "decider": "Human/Ops",
                                    "scope": "Global",
                                    "risk_owner": "Platform Lead",
                                    "expiry": "2027-01-01",
                                    "reopen_trigger": "On review",
                                    "decision_date": "2026-09-03",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures3, _ = check(root, manifest3, reference_date=date(2026, 9, 3))
        assert any("illegal disposition transition: IMPLEMENTATION_READY -> DECIDED" in f.problem for f in failures3)

    def test_history_tip_and_previous_state_disagreement_is_rejected(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        # History says OPEN -> DECIDED, but previous_state claims BLOCKED_BY_EVIDENCE
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "disagreeing history and previous_state",
                                "disposition": {
                                    "state": "DECIDED",
                                    "previous_state": "BLOCKED_BY_EVIDENCE",
                                    "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                    "decider": "Human/Ops",
                                    "scope": "Global",
                                    "risk_owner": "Platform Lead",
                                    "decision_date": "2026-09-02",
                                    "expiry": "2027-01-01",
                                    "reopen_trigger": "On review",
                                    "history": [
                                        {"state": "OPEN", "date": "2026-09-01"},
                                        {"state": "DECIDED", "date": "2026-09-02"},
                                    ],
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("contradicts history" in f.problem for f in failures)

        # History ends with OPEN, current state is DECIDED, but previous_state claims BLOCKED_BY_EVIDENCE
        manifest2 = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "disagreeing history tip",
                                "disposition": {
                                    "state": "DECIDED",
                                    "previous_state": "BLOCKED_BY_EVIDENCE",
                                    "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                    "decider": "Human/Ops",
                                    "scope": "Global",
                                    "risk_owner": "Platform Lead",
                                    "decision_date": "2026-09-02",
                                    "expiry": "2027-01-01",
                                    "reopen_trigger": "On review",
                                    "history": [
                                        {"state": "OPEN", "date": "2026-09-01"},
                                    ],
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures2, _ = check(root, manifest2, reference_date=date(2026, 9, 3))
        assert any("contradicts history" in f.problem for f in failures2)

        # Consistent history and previous_state: OPEN -> BLOCKED_BY_EVIDENCE -> DECIDED
        manifest3 = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "consistent history and previous_state",
                                "disposition": {
                                    "state": "DECIDED",
                                    "previous_state": "BLOCKED_BY_EVIDENCE",
                                    "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                    "decider": "Human/Ops",
                                    "scope": "Global",
                                    "risk_owner": "Platform Lead",
                                    "decision_date": "2026-09-02",
                                    "expiry": "2027-01-01",
                                    "reopen_trigger": "On review",
                                    "history": [
                                        {"state": "OPEN", "date": "2026-09-01"},
                                        {"state": "BLOCKED_BY_EVIDENCE", "date": "2026-09-02"},
                                        {"state": "DECIDED", "date": "2026-09-03"},
                                    ],
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures3, _ = check(root, manifest3, reference_date=date(2026, 9, 3))
        assert failures3 == []


class TestDecidedDispositionRequirements:
    def test_decided_missing_statutory_fields_fails(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        # Missing decider, expiry, scope, risk_owner, reopen_trigger
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "decided without metadata",
                                "disposition": {
                                    "state": "DECIDED",
                                    "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("missing required statutory field(s)" in f.problem for f in failures)

    def test_decided_with_alias_fields_rejected_missing_canonical(self, tmp_path: Path) -> None:
        """DECIDED with legacy aliases (decision_ref, applicable_scope, expiry_date)
        must fail because canonical fields are missing."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "using alias fields instead of canonical fields",
                                "disposition": {
                                    "state": "DECIDED",
                                    "decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                    "decider": "Human/Ops",
                                    "applicable_scope": "Global",
                                    "risk_owner": "Platform Lead",
                                    "expiry_date": "2027-01-01",
                                    "reopen_trigger": "On review",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any(
            "DECIDED disposition missing required statutory field(s)" in f.problem
            and "formal_decision_ref" in f.problem
            and "scope" in f.problem
            and "expiry" in f.problem
            for f in failures
        )

    def test_decided_with_outside_repo_reference_fails(self, tmp_path: Path) -> None:
        """formal_decision_ref targeting files outside the repo must fail to enforce audit boundary."""
        root = _repo(tmp_path)
        for outside_ref in (
            "../outside_file.md",
            "../../etc/passwd",
            "/etc/passwd",
            "docs/../../outside.md",
            "..",
        ):
            manifest = _manifest(
                tmp_path,
                {
                    "requirements": [
                        {
                            "id": "R-1",
                            "members": [
                                {
                                    "name": "A",
                                    "status": "absent",
                                    "note": "outside repo waiver",
                                    "disposition": {
                                        "state": "DECIDED",
                                        "formal_decision_ref": outside_ref,
                                        "decider": "Human/Ops",
                                        "scope": "Global",
                                        "risk_owner": "Platform Lead",
                                        "decision_date": "2026-09-02",
                                        "expiry": "2027-01-01",
                                        "reopen_trigger": "On review",
                                    },
                                }
                            ],
                        }
                    ]
                },
            )
            failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
            assert any(
                "invalid formal_decision_ref" in f.problem or "cannot escape repository boundary" in f.problem
                for f in failures
            ), f"Failed to reject outside repo ref {outside_ref!r}"

    def test_decided_with_invalid_formal_decision_ref_fails(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        for bad_ref in ("not-a-reference", "docs/plans/nonexistent_doc.md", "random_string", ""):
            manifest = _manifest(
                tmp_path,
                {
                    "requirements": [
                        {
                            "id": "R-1",
                            "members": [
                                {
                                    "name": "A",
                                    "status": "absent",
                                    "note": "bad ref waiver",
                                    "disposition": {
                                        "state": "DECIDED",
                                        "formal_decision_ref": bad_ref,
                                        "decider": "Human/Ops (Architecture Board)",
                                        "decision_date": "2026-09-03",
                                        "scope": "Global",
                                        "risk_owner": "Platform Governance Lead",
                                        "expiry": "2027-09-01",
                                        "reopen_trigger": "When data pipeline is verified",
                                        "rationale": "Formally waived",
                                    },
                                }
                            ],
                        }
                    ]
                },
            )
            failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
            assert any(
                ("invalid formal_decision_ref" in f.problem or "missing required statutory field" in f.problem)
                for f in failures
            ), f"Failed to reject invalid decision_ref: {bad_ref!r}"

    def test_decided_with_all_statutory_fields_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "valid formal waiver",
                                "disposition": {
                                    "state": "DECIDED",
                                    "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md#r1-a",
                                    "decider": "Human/Ops (Architecture Board)",
                                    "decision_date": "2026-09-03",
                                    "scope": "All non-production batches",
                                    "risk_owner": "Platform Governance Lead",
                                    "expiry": "2027-09-01",
                                    "reopen_trigger": "When production data pipeline is verified",
                                    "rationale": "Formally waived per architecture decision",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, tally = check(root, manifest, reference_date=date(2026, 9, 3))
        assert failures == []
        assert tally["dispositions"]["DECIDED"] == 1

    def test_decided_with_url_or_pr_reference_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        for valid_ref in (
            "https://github.com/alfloop-dev/odayplus/pull/1133",
            "PR #1133",
            "RFC-042",
        ):
            manifest = _manifest(
                tmp_path,
                {
                    "requirements": [
                        {
                            "id": "R-1",
                            "members": [
                                {
                                    "name": "A",
                                    "status": "absent",
                                    "note": f"valid waiver with ref {valid_ref}",
                                    "disposition": {
                                        "state": "DECIDED",
                                        "formal_decision_ref": valid_ref,
                                        "decider": "Platform Governance Lead",
                                        "scope": "Batch 0",
                                        "risk_owner": "Platform Lead",
                                        "decision_date": "2026-09-02",
                                        "expiry": "2027-09-01",
                                        "reopen_trigger": "On quarterly audit",
                                    },
                                }
                            ],
                        }
                    ]
                },
            )
            failures, tally = check(root, manifest, reference_date=date(2026, 9, 3))
            assert failures == [], f"Failed on valid ref {valid_ref}: {failures}"


class TestAISelfSigningWaiversForbidden:
    def test_ai_agent_as_decider_is_rejected(self, tmp_path: Path) -> None:
        """AI agents cannot sign requirement amendments, waivers or risk acceptances,
        even when appending human authority titles (e.g. Codex2 Governance)."""
        root = _repo(tmp_path)
        for ai_name in (
            "Antigravity3",
            "Claude2",
            "Codex",
            "Gemini",
            "Copilot",
            "AI Agent",
            "Codex2 Governance",
            "Antigravity Architecture Lead",
            "Claude Product Lead",
            "Gemini Risk Committee",
            "GPT-4 Security Officer",
            "AI Agent Board",
            "AutoWorker Committee",
            "ai:orchestrator",
        ):
            manifest = _manifest(
                tmp_path,
                {
                    "requirements": [
                        {
                            "id": "R-1",
                            "members": [
                                {
                                    "name": "A",
                                    "status": "absent",
                                    "note": f"self signed by {ai_name}",
                                    "disposition": {
                                        "state": "DECIDED",
                                        "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                        "decider": ai_name,
                                        "scope": "Global",
                                        "risk_owner": "Platform Lead",
                                        "decision_date": "2026-09-02",
                                        "expiry": "2027-09-01",
                                        "reopen_trigger": "Never",
                                    },
                                }
                            ],
                        }
                    ]
                },
            )
            failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
            assert any("AI decider" in f.problem and "forbidden" in f.problem for f in failures), f"Failed to reject AI decider {ai_name!r}"

    def test_is_ai_decider_helper(self) -> None:
        # AI names and masquerading titles
        assert is_ai_decider("Antigravity") is True
        assert is_ai_decider("Antigravity3") is True
        assert is_ai_decider("Antigravity Architecture Lead") is True
        assert is_ai_decider("Claude") is True
        assert is_ai_decider("Claude2") is True
        assert is_ai_decider("Claude Product Lead") is True
        assert is_ai_decider("Codex2") is True
        assert is_ai_decider("Codex2 Governance") is True
        assert is_ai_decider("Gemini") is True
        assert is_ai_decider("Gemini Risk Committee") is True
        assert is_ai_decider("Copilot") is True
        assert is_ai_decider("GPT-4") is True
        assert is_ai_decider("GPT-4 Security Officer") is True
        assert is_ai_decider("ChatGPT") is True
        assert is_ai_decider("AI Lead") is True
        assert is_ai_decider("AutoWorker") is True
        assert is_ai_decider("Orchestrator") is True
        assert is_ai_decider("AI: AutoWorker") is True
        assert is_ai_decider("ai/agent-1") is True

        # Authorized human / organizational roles
        assert is_ai_decider("Human/Ops") is False
        assert is_ai_decider("Human/Ops (Architecture Board)") is False
        assert is_ai_decider("Platform Architecture Lead") is False
        assert is_ai_decider("Platform Governance Lead") is False
        assert is_ai_decider("Security Officer") is False
        assert is_ai_decider("Product Lead") is False
        assert is_ai_decider("Risk Committee") is False
        assert is_ai_decider("Optimization & Modeling Lead") is False
        assert is_ai_decider("Retail Operations Lead") is False
        assert is_ai_decider("ML Monitoring Lead") is False


class TestWaiverExpiryGate:
    def test_expired_waiver_is_rejected(self, tmp_path: Path) -> None:
        """Expired waivers must fail CI to prevent unbounded debt."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "expired waiver",
                                "disposition": {
                                    "state": "DECIDED",
                                    "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                    "decider": "Human/Ops",
                                    "scope": "Global",
                                    "risk_owner": "Platform Lead",
                                    "decision_date": "2025-01-01",
                                    "expiry": "2025-12-31",
                                    "reopen_trigger": "On quarterly audit",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        # Reference date is 2026-09-03, expiry was 2025-12-31
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("waiver expired on 2025-12-31" in f.problem for f in failures)

    def test_future_expiry_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "valid future waiver",
                                "disposition": {
                                    "state": "DECIDED",
                                    "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                    "decider": "Human/Ops",
                                    "scope": "Global",
                                    "risk_owner": "Platform Lead",
                                    "decision_date": "2026-09-02",
                                    "expiry": "2027-01-01",
                                    "reopen_trigger": "On quarterly audit",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert failures == []

    def test_expiry_with_invalid_date_format_fails(self, tmp_path: Path) -> None:
        """Expiry must strictly conform to ISO YYYY-MM-DD."""
        root = _repo(tmp_path)
        for bad_expiry in (
            "2027-01-01Tgarbage",
            "2027/01/01",
            "2027-1-1",
            "2027-02-30",
            "tomorrow",
            "",
            "2027-13-01",
        ):
            manifest = _manifest(
                tmp_path,
                {
                    "requirements": [
                        {
                            "id": "R-1",
                            "members": [
                                {
                                    "name": "A",
                                    "status": "absent",
                                    "note": "bad date waiver",
                                    "disposition": {
                                        "state": "DECIDED",
                                        "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
                                        "decider": "Human/Ops",
                                        "scope": "Global",
                                        "risk_owner": "Platform Lead",
                                        "decision_date": "2026-09-02",
                                        "expiry": bad_expiry,
                                        "reopen_trigger": "On quarterly audit",
                                    },
                                }
                            ],
                        }
                    ]
                },
            )
            failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
            assert any(
                "invalid ISO expiry date" in f.problem or "missing required statutory field" in f.problem
                for f in failures
            ), f"Failed to reject invalid expiry: {bad_expiry!r}"


class TestOpenDispositionRequirements:
    def test_open_missing_rationale_fails(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "some note",
                                "disposition": {
                                    "state": "OPEN",
                                    "assigned_to": "Triage Lead",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("OPEN disposition missing required field(s): rationale" in f.problem for f in failures)

    def test_open_missing_tracking_metadata_fails(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "some note",
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "investigating",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("OPEN disposition missing required field(s): assigned_to or next_review_date" in f.problem for f in failures)

    def test_open_with_assigned_to_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "active gap",
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "investigating gap",
                                    "assigned_to": "Architecture Lead",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, tally = check(root, manifest)
        assert failures == []
        assert tally["dispositions"]["OPEN"] == 1

    def test_open_with_next_review_date_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "active gap",
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "investigating gap",
                                    "next_review_date": "2026-10-01",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, tally = check(root, manifest)
        assert failures == []
        assert tally["dispositions"]["OPEN"] == 1


class TestOtherDispositionStates:
    def test_blocked_by_evidence_validation(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        # Missing evidence_needed
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "blocked",
                                "disposition": {
                                    "state": "BLOCKED_BY_EVIDENCE",
                                    "evidence_owner": "Data Ops Lead",
                                    "next_review_date": "2026-10-01",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("missing required field(s): evidence_needed" in f.problem for f in failures)

    def test_implementation_ready_validation(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "ready",
                                "disposition": {
                                    "state": "IMPLEMENTATION_READY",
                                    "assigned_to": "Engineer",
                                    "target_phase": "Batch 4a",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, tally = check(root, manifest)
        assert failures == []
        assert tally["dispositions"]["IMPLEMENTATION_READY"] == 1


def _waiver(**overrides: object) -> dict:
    """A complete, valid formal ruling. Tests remove or spoil one field at a time."""
    waiver = {
        "formal_decision_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md",
        "decider": "Human/Ops (Architecture Board)",
        "decision_date": "2026-09-02",
        "scope": "Global",
        "risk_owner": "Platform Lead",
        "expiry": "2027-09-01",
        "reopen_trigger": "On quarterly audit",
    }
    waiver.update(overrides)
    return {k: v for k, v in waiver.items() if v is not None}


class TestANoteIsNotAnAmendment:
    """Prose cannot close a MUST requirement.

    The failure this catches is the one the whole file exists for, one layer up:
    a member says in its note that somebody decided not to build the thing, and
    the disposition underneath it never names who, when, until when, or on what
    observation it reopens. The green check then reports a governed gap.
    """

    def test_an_english_ruling_in_a_note_under_open_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "DECIDED 2026-09-02: the batched form is not pursued.",
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "not worth the complexity",
                                    "assigned_to": "Platform Lead",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("not a requirement amendment" in f.problem for f in failures)

    def test_a_chinese_ruling_in_a_note_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "已裁決不做；方向是維持現狀。",
                                "disposition": {
                                    "state": "BLOCKED_BY_EVIDENCE",
                                    "evidence_needed": "usage figures",
                                    "evidence_owner": "Data Operations Lead",
                                    "next_review_date": "2026-10-01",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("not a requirement amendment" in f.problem for f in failures)

    def test_a_ruling_in_the_disposition_rationale_is_refused(self, tmp_path: Path) -> None:
        """Moving the sentence from note to rationale must not move it out of reach."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "no producer exists for this member",
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "Formally decided: this will not be implemented.",
                                    "next_review_date": "2026-10-01",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("not a requirement amendment" in f.problem for f in failures)

    def test_a_note_that_only_describes_an_absence_still_passes(self, tmp_path: Path) -> None:
        """The shipped BACKTEST and ADJUST notes, verbatim. A gate that cannot tell
        'nobody built it' from 'somebody ruled against building it' would force
        every honest gap into a waiver it does not have."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "BACKTEST",
                                "status": "absent",
                                "note": (
                                    "run_rolling_backtest exists in models/shared_ml/backtest.py and is used "
                                    "by the training pipeline, but modules/learninghub references it nowhere. "
                                    "It is not a release mode, so a release cannot be gated on a backtest result."
                                ),
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "not connected as a release gate",
                                    "next_review_date": "2026-10-01",
                                },
                            },
                            {
                                "name": "ADJUST",
                                "status": "absent",
                                "note": (
                                    "Recommendation is CONTINUE/SCALE/STOP/CHANGE_CHANNEL/INCONCLUSIVE, which "
                                    "leaves no place for Adjust, and there is no state for 'modify and continue'."
                                ),
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "pending business requirement clarification",
                                    "next_review_date": "2026-10-01",
                                },
                            },
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert failures == []

    def test_deleting_the_disposition_block_does_not_hide_the_claim(self, tmp_path: Path) -> None:
        """A satisfied member needs no disposition, so dropping the block is the
        cheapest way to carry a ruling with nothing to validate."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "satisfied",
                                "evidence": "pkg/mod.py::Thing",
                                "note": "The count cap is in. DECIDED 2026-09-02: the rest is not pursued.",
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("not a requirement amendment" in f.problem for f in failures)

    def test_the_claim_detector_reads_ruling_and_absence_apart(self) -> None:
        rulings = [
            "DECIDED 2026-09-02: not modelled, and not scheduled.",
            "decided not to implement the batched path",
            "the full pairwise form is not pursued",
            "this will not be implemented in the current model",
            "formally waived until the next planning cycle",
            "de-scoped by the architecture board",
            "已裁決不做",
            "決定不實作，維持現狀",
            "架構委員會裁定不做，2027-09-01 到期",
        ]
        absences = [
            "It is not a release mode, so a release cannot be gated on a backtest result.",
            "No such data source is wired to the solver.",
            "decided without metadata",
            "That is choosing a format, not converting an existing store from one to another.",
            "no job in the tree currently reports it",
            "查證後確認此項的權威裁決不存在，已阻擋至 Human/Ops",
            "本項尚未裁定，next_review_date 為 2026-10-01",
        ]
        for text in rulings:
            assert find_nonimplementation_claim(text), text
        for text in absences:
            assert find_nonimplementation_claim(text) is None, text


class TestAWaiverIsJudgedWhereverItSits:
    """A waiver parked on a non-DECIDED member used to escape every gate.

    ODP-FR-NET-002/DILUTION is the live shape: a satisfied member whose note
    rules out the rest of the requirement, carrying decider, expiry and reopen
    trigger under a VERIFIED disposition. Before this gate, none of those fields
    were read -- the expiry would have passed in 2027 with CI still green.
    """

    def test_a_waiver_on_a_verified_member_still_expires(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "satisfied",
                                "evidence": "pkg/mod.py::Thing",
                                "note": "the remainder is not pursued",
                                "disposition": dict(_waiver(expiry="2026-01-01"), state="VERIFIED"),
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("expired" in f.problem for f in failures)

    def test_a_half_recorded_waiver_on_another_state_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "gap indexed here",
                                "disposition": {
                                    "state": "OPEN",
                                    "rationale": "carrying half a ruling",
                                    "next_review_date": "2026-10-01",
                                    "decider": "Human/Ops (Architecture Board)",
                                    "expiry": "2027-09-01",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any(
            "carrying decision fields" in f.problem and "formal_decision_ref" in f.problem
            for f in failures
        )

    def test_a_waiver_on_another_state_cannot_be_ai_signed(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "gap indexed here",
                                "disposition": dict(
                                    _waiver(decider="Claude2"),
                                    state="IMPLEMENTATION_READY",
                                    assigned_to="Platform Lead",
                                    target_phase="Batch 6",
                                ),
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("AI decider" in f.problem for f in failures)

    def test_a_complete_waiver_on_a_verified_member_passes(self, tmp_path: Path) -> None:
        """The legitimate case must stay legitimate: a member satisfied in part,
        whose remainder carries a dated, owned, expiring human ruling."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "satisfied",
                                "evidence": "pkg/mod.py::Thing",
                                "note": "DECIDED 2026-09-02: the pairwise form is not pursued.",
                                "disposition": dict(_waiver(), state="VERIFIED"),
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert failures == []


def _handback(**overrides: object) -> dict:
    """A gap handed back to human governance undecided, in the shape dev uses.

    Modelled on ODP-FR-SITE-001/BRAND_TRANSFER: no data source exists, no AI may
    waive the requirement, so the gap goes back with the evidence named, an owner
    named, a review date, a package a reviewer can open, and the observation that
    would unblock it. Nobody has ruled, so there is no decider and no expiry.
    """
    handback = {
        "state": "BLOCKED_BY_EVIDENCE",
        "evidence_needed": "External consumer panel feed with versioned schema and producer SLA",
        "evidence_owner": "Market Intelligence Lead",
        "next_review_date": "2026-10-01",
        "rationale": "Repo holds static brand metadata and a synthetic mock view; no real producer exists.",
        "formal_handback_ref": "docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md#odp-fr-site-001",
        "reopen_trigger": "A cross-brand POS loyalty dataset is contracted and ingested with a verified SLA.",
    }
    handback.update(overrides)
    return {k: v for k, v in handback.items() if v is not None}


def _one_member(disposition: dict, **member_overrides: object) -> dict:
    member = {
        "name": "A",
        "status": "absent",
        "note": "gap indexed here",
        "disposition": disposition,
    }
    member.update(member_overrides)
    return {"requirements": [{"id": "R-1", "members": [member]}]}


class TestAHandbackIsNotAnIncompleteWaiver:
    """The honest exit from an un-waivable MUST must not be refused.

    A gap an AI may not waive goes back to human governance undecided:
    BLOCKED_BY_EVIDENCE plus an unsigned handback. That shape names the
    observation that would unblock it, for the same reason a waiver names what
    would reopen it. Counting ``reopen_trigger`` as a signal that a ruling had
    been made turned the sanctioned shape into an incomplete waiver missing a
    decider and an expiry -- fields an undecided gap must not be asked to
    invent, since inventing them is the AI self-signing this file forbids.
    """

    def test_a_handback_carrying_a_reopen_trigger_passes(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(tmp_path, _one_member(_handback()))
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert failures == []

    def test_reopen_trigger_is_statutory_but_is_not_a_waiver_signal(self) -> None:
        """It is required of a ruling, and required of a handback; carrying one
        therefore says nothing about whether a ruling exists."""
        assert "reopen_trigger" in STATUTORY_DECISION_FIELDS
        assert "reopen_trigger" not in WAIVER_SIGNAL_FIELDS
        assert set(WAIVER_SIGNAL_FIELDS) == set(STATUTORY_DECISION_FIELDS) - {"reopen_trigger"}

    def test_a_decided_ruling_still_owes_its_reopen_trigger(self, tmp_path: Path) -> None:
        """Narrowing the signal must not narrow the DECIDED gate."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path, _one_member(dict(_waiver(reopen_trigger=None), state="DECIDED"))
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("reopen_trigger" in f.problem for f in failures)

    def test_a_real_waiver_signal_beside_a_reopen_trigger_still_demands_the_rest(
        self, tmp_path: Path
    ) -> None:
        """The parking gate stays armed: a decider under BLOCKED_BY_EVIDENCE is
        a ruling somebody made, and it still owes the other six fields."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path, _one_member(_handback(decider="Human/Ops (Architecture Board)"))
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any(
            "carrying decision fields" in f.problem and "expiry" in f.problem for f in failures
        )

    def test_an_expiry_parked_on_a_handback_still_comes_due(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(tmp_path, _one_member(_handback(expiry="2026-01-01")))
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("expired" in f.problem for f in failures)

    def test_the_live_handbacks_are_the_shape_this_protects(self) -> None:
        """Pin the manifest shape, so a future edit cannot quietly make the
        regression test above vacuous: these members carry a reopen trigger and
        no decider, and the repository check must accept them."""
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        members = {
            member["name"]: member
            for entry in payload["requirements"]
            if entry["id"] == "ODP-FR-SITE-001"
            for member in entry["members"]
        }
        for name in ("BRAND_TRANSFER", "FORMAT_CONVERSION"):
            disposition = members[name]["disposition"]
            assert disposition["state"] == "BLOCKED_BY_EVIDENCE"
            assert disposition["reopen_trigger"]
            assert not any(disposition.get(field) for field in WAIVER_SIGNAL_FIELDS)


class TestAHandbackNeedsSomethingToOpen:
    """A handback is a governance act, so it is forgeable in prose.

    It is the sanctioned way past a MUST no AI may waive, which makes
    "submitted to Human/Ops" the next free pass after "decided not to do" was
    closed -- and a cheaper one, because it parks the member in
    BLOCKED_BY_EVIDENCE indefinitely with nobody named as having received it.
    """

    def test_a_handback_claimed_in_a_note_with_no_ref_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            _one_member(
                _handback(formal_handback_ref=None),
                note=(
                    "No producer exists in the repo. Formal handback "
                    "HB-SITE001-BRAND-TRANSFER-001 submitted to Human/Ops."
                ),
            ),
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("formal_handback_ref" in f.problem for f in failures)

    def test_a_handback_claimed_in_a_rationale_with_no_ref_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            _one_member(
                _handback(
                    formal_handback_ref=None,
                    rationale="No producer exists; 已移交 Human/Ops 與 Architecture Board 裁決。",
                )
            ),
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("not a handback" in f.problem for f in failures)

    def test_a_handback_ref_pointing_at_nothing_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            _one_member(_handback(formal_handback_ref="docs/evidence/never_written.md")),
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("invalid formal_handback_ref" in f.problem for f in failures)

    def test_a_handback_ref_cannot_escape_the_repository(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            _one_member(_handback(formal_handback_ref="../../etc/passwd")),
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("cannot escape repository boundary" in f.problem for f in failures)

    def test_deleting_the_disposition_does_not_hide_a_handback_claim(self, tmp_path: Path) -> None:
        """A satisfied member may omit the block, so omitting it is the cheapest
        place to leave an unbacked claim."""
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "satisfied",
                                "evidence": "pkg/mod.py::Thing",
                                "note": "The remainder was handed back to the Architecture Board.",
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("not a handback" in f.problem for f in failures)

    def test_the_handback_detector_reads_a_submission_and_an_intention_apart(self) -> None:
        """An act already performed must be backed; what is still owed must not
        be, or every honestly-recorded block gets pushed into inventing one."""
        submissions = [
            "Formal handback HB-SITE001-BRAND-TRANSFER-001 submitted to Human/Ops.",
            "handback package filed with the Architecture Board",
            "The pairwise remainder was handed back to Platform Governance.",
            "已移交 Human/Ops 裁決",
            "人類授權移交單已提報至 Architecture Board",
        ]
        intentions = [
            "Awaiting Batch 0 data source audit before scheduling solver integration or formal waiver.",
            "No such data source is wired to the solver.",
            "Needs a per-option admissibility check before this can be scheduled.",
            "查證後確認此項的權威裁決不存在，已阻擋至 Human/Ops",
            "待資料源確立後轉為 IMPLEMENTATION_READY 或由人類授權人簽署",
        ]
        for text in submissions:
            assert find_handback_claim(text), text
        for text in intentions:
            assert find_handback_claim(text) is None, text


class TestADecisionMustCarryItsDate:
    def test_decision_date_is_statutory(self, tmp_path: Path) -> None:
        assert "decision_date" in STATUTORY_DECISION_FIELDS
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "undated ruling",
                                "disposition": dict(_waiver(decision_date=None), state="DECIDED"),
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any(
            "missing required statutory field(s): decision_date" in f.problem for f in failures
        )

    def test_a_decision_dated_in_the_future_is_refused(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        manifest = _manifest(
            tmp_path,
            {
                "requirements": [
                    {
                        "id": "R-1",
                        "members": [
                            {
                                "name": "A",
                                "status": "absent",
                                "note": "ruling dated after the run",
                                "disposition": dict(_waiver(decision_date="2027-01-01"), state="DECIDED"),
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest, reference_date=date(2026, 9, 3))
        assert any("is in the future" in f.problem for f in failures)

    def test_an_inverted_waiver_cannot_pass_from_any_run_date(self, tmp_path: Path) -> None:
        """A ruling that expires before it was made needs no ordering rule of its
        own. Whenever CI runs, one of the two dates is already wrong relative to
        that run: before the expiry the decision is in the future, after it the
        waiver has lapsed."""
        root = _repo(tmp_path)

        def _inverted() -> Path:
            return _manifest(
                tmp_path,
                {
                    "requirements": [
                        {
                            "id": "R-1",
                            "members": [
                                {
                                    "name": "A",
                                    "status": "absent",
                                    "note": "ruling that lapsed before it was made",
                                    "disposition": dict(
                                        _waiver(decision_date="2026-09-02", expiry="2026-09-01"),
                                        state="DECIDED",
                                    ),
                                }
                            ],
                        }
                    ]
                },
            )

        early, _ = check(root, _inverted(), reference_date=date(2026, 8, 1))
        assert any("is in the future" in f.problem for f in early)

        late, _ = check(root, _inverted(), reference_date=date(2026, 9, 3))
        assert any("expired" in f.problem for f in late)

    def test_check_decision_date_helper(self) -> None:
        assert check_decision_date("2026-09-02", date(2026, 9, 3)) == (True, None)
        assert check_decision_date("2026-09-04", date(2026, 9, 3))[0] is False
        assert check_decision_date("02/09/2026", date(2026, 9, 3))[0] is False
        assert check_decision_date("2026-13-01", date(2026, 9, 3))[0] is False
        assert check_decision_date(None, date(2026, 9, 3))[0] is False


class TestTheCheckedInManifestHolds:
    def test_the_repository_manifest_passes(self) -> None:
        """If this fails, either a member's implementation moved without the
        manifest following, or a disposition was recorded improperly."""
        failures, _ = check(REPO_ROOT, MANIFEST_PATH, reference_date=date(2026, 9, 3))
        assert not failures, "\n".join(f.describe() for f in failures)

    def test_every_seeded_requirement_from_the_verification_is_present(self) -> None:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        ids = {entry["id"] for entry in payload["requirements"]}
        assert {
            "ODP-FR-NET-002",
            "ODP-FR-SITE-001",
            "ODP-FR-LH-003",
            "ODP-FR-INTV-006",
            "ODP-FR-SHARED-001",
            "ODP-FR-LH-005",
        } <= ids
