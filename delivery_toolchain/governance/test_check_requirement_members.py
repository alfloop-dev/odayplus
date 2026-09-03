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
        assert validate_transition("OPEN", "DECIDED") is True
        assert validate_transition("OPEN", "BLOCKED_BY_EVIDENCE") is True
        assert validate_transition("BLOCKED_BY_EVIDENCE", "IMPLEMENTATION_READY") is True
        assert validate_transition("IMPLEMENTATION_READY", "VERIFIED") is True
        assert validate_transition("DECIDED", "IMPLEMENTATION_READY") is True
        assert validate_transition("VERIFIED", "OPEN") is True

        # Illegal transitions
        assert validate_transition("VERIFIED", "DECIDED") is False
        assert validate_transition("BLOCKED_BY_EVIDENCE", "VERIFIED") is False

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
                                    "formal_decision_ref": "docs/plans/decision.md",
                                },
                            }
                        ],
                    }
                ]
            },
        )
        failures, _ = check(root, manifest)
        assert any("missing required statutory field(s)" in f.problem for f in failures)

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


class TestAISelfSigningWaiversForbidden:
    def test_ai_agent_as_decider_is_rejected(self, tmp_path: Path) -> None:
        """AI agents cannot sign requirement amendments, waivers or risk acceptances."""
        root = _repo(tmp_path)
        for ai_name in ("Antigravity3", "Claude2", "Codex", "Gemini", "Copilot", "AI Agent"):
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
                                        "risk_owner": "Lead",
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
            assert any("AI decider" in f.problem and "forbidden" in f.problem for f in failures), f"Failed to reject {ai_name}"

    def test_is_ai_decider_helper(self) -> None:
        assert is_ai_decider("Antigravity") is True
        assert is_ai_decider("Antigravity3") is True
        assert is_ai_decider("Claude") is True
        assert is_ai_decider("Claude2") is True
        assert is_ai_decider("Codex2") is True
        assert is_ai_decider("Gemini") is True
        assert is_ai_decider("Copilot") is True
        assert is_ai_decider("AI: AutoWorker") is True
        assert is_ai_decider("ai/agent-1") is True

        # Authorized human / organizational roles
        assert is_ai_decider("Human/Ops") is False
        assert is_ai_decider("Human/Ops (Architecture Board)") is False
        assert is_ai_decider("Platform Architecture Lead") is False
        assert is_ai_decider("Security Officer") is False
        assert is_ai_decider("Product Lead") is False
        assert is_ai_decider("Risk Committee") is False


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
