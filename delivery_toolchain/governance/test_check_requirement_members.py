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
    VALID_DISPOSITION_STATES,
    check,
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
