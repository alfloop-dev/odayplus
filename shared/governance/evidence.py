"""Write-boundary enforcement for the governed evidence ladder (ADR-0004).

``shared/governance/vocabularies.py`` declares the ladder. It cannot declare
what happens at a boundary handed something that is not on it, and that gap is
where every fork so far has entered: ``evidence_level: str = "medium"`` accepted
``"medium"``, ``"pending"``, ``"high"`` and typos alike, and each one reached
persistence looking exactly like an evidence claim.

ADR-0004 D3 is a rule about absence. A request that says nothing about evidence
must produce a record that says nothing about evidence -- not one that claims a
middle tier. Holding that at a boundary takes two things, and this module is
both:

* ``None`` survives as ``None``. It is never widened to a rung, because
  "never assessed" is orthogonal to the ladder rather than its bottom.
* anything that is not a rung is rejected where it arrives, rather than stored
  verbatim for a downstream reader to interpret.

``CAUSAL_MIN_EVIDENCE`` lives here for the same reason. It is the threshold
``ODP-BR-AD-001`` gates causal claims on, and a second copy of it in a consuming
module is a policy fork that no test would catch -- the two would simply drift.
"""

from __future__ import annotations

from shared.governance.vocabularies import EvidenceLevel

# The ladder is ordered, and the enum already carries that order: members are
# declared weakest-first and their values sort the same way. Deriving the order
# from the enum keeps it impossible for a rung added to vocabularies.json to be
# missing here -- a hand-maintained tuple would have to be remembered.
EVIDENCE_ORDER: tuple[EvidenceLevel, ...] = tuple(EvidenceLevel)

# ODP-BR-AD-001 hard constraint: below this rung a causal claim is not
# permitted. ADR-0004 leaves the threshold at L3 and only clarifies what sits
# outside the ladder entirely.
CAUSAL_MIN_EVIDENCE: EvidenceLevel = EvidenceLevel.L3_DID_VALIDATED


class EvidenceLevelError(ValueError):
    """A write boundary was handed a value that names no rung of the ladder.

    Subclasses ``ValueError`` so the API layers that already translate
    ``ValueError`` into a 4xx keep doing so; an off-ladder evidence claim is a
    caller error, not a server fault.
    """


def coerce_evidence_level(
    value: EvidenceLevel | str | None,
    *,
    field: str = "evidence_level",
) -> EvidenceLevel | None:
    """Return the rung ``value`` names, or ``None`` when it names no rung at all.

    ``None`` passes through unchanged. That is the whole point of ADR-0004 D3:
    an unassessed claim keeps saying it is unassessed, all the way to storage.
    """
    if value is None:
        return None
    if isinstance(value, EvidenceLevel):
        return value
    if isinstance(value, str):
        try:
            return EvidenceLevel(value)
        except ValueError as exc:
            raise EvidenceLevelError(_rejection(field, value)) from exc
    raise EvidenceLevelError(_rejection(field, value))


def meets_causal_threshold(value: EvidenceLevel | str | None) -> bool:
    """Whether ``value`` is at or above :data:`CAUSAL_MIN_EVIDENCE`.

    Unrated is ``False``, and not because it ranks below ``L0``: it holds no
    rank at all. ``L0`` is a reading that came out weak; ``None`` is no reading,
    and the two must not converge into one "low confidence" bucket downstream.
    """
    level = coerce_evidence_level(value)
    if level is None:
        return False
    return EVIDENCE_ORDER.index(level) >= EVIDENCE_ORDER.index(CAUSAL_MIN_EVIDENCE)


def _rejection(field: str, value: object) -> str:
    rungs = ", ".join(level.value for level in EVIDENCE_ORDER)
    return (
        f"{field} must be one of {rungs}, or null when the evidence was never "
        f"assessed (ADR-0004 D3); got {value!r}"
    )


__all__ = [
    "CAUSAL_MIN_EVIDENCE",
    "EVIDENCE_ORDER",
    "EvidenceLevelError",
    "coerce_evidence_level",
    "meets_causal_threshold",
]
