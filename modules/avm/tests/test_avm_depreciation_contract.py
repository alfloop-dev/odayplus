"""Executable spec for the AVM asset depreciation contract.

Contract: docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md
Task: ODP-AVM-DEPRECIATION-CONTRACT-001

`ODP-FR-AVM-001` enumerates six members; five are implemented and depreciation
is not. It exists in `modules/site_economics`, where it is a tax shield on a
greenfield store's projected cash flow -- a different quantity, on a different
clock, for a different consumer. The contract's verdict is therefore an
AVM-specific model with no import of the simulator, and this file is that
verdict in executable form.

Every contract test below is `xfail(strict=True)`. That is deliberate: the
suite stays green while the gap is open, and the moment the depreciation model
lands these tests XPASS, which `strict` reports as a failure. The implementer
has to come back and remove the markers, so the contract cannot be satisfied
silently or bypassed silently.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import pytest

from modules.avm.application.calibration import DealOutcomeCalibrationReport
from modules.avm.domain.valuation import (
    ValuationCase,
    ValuationReport,
    build_valuation_view,
    normalize_margin,
    value_store,
)

DOC = "docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md"
REPO_ROOT = Path(__file__).resolve().parents[3]

# Two cards must be comparable in every respect except depreciation, so every
# non-depreciation input is pinned here -- including prediction_origin_time,
# which otherwise defaults to "now" and would make the two runs incomparable
# for a reason that has nothing to do with depreciation.
BASE_INPUT: dict[str, Any] = {
    "store_id": "store-avm-depreciation-contract",
    "gm_ttm": 4_800_000.0,
    "forecast_gm_next_12m": 5_100_000.0,
    "asset_book_value": 3_000_000.0,
    "equipment_fair_value": 6_000_000.0,
    "working_capital": 400_000.0,
    "lease_liability": 900_000.0,
    "comparable_multiples": [2.3, 2.6, 2.9],
    "liquidity_discount": 0.12,
    "quality_score": 0.95,
    "source_snapshot_ids": ["snap-avm-depreciation-001"],
    "prediction_origin_time": "2026-09-03T00:00:00+00:00",
}

# Contract section C-2. Every field is stated; nothing is left to a default.
DEPRECIATION_CONTRACT: dict[str, Any] = {
    "equipment_depreciation_basis": "original_cost",
    "equipment_original_cost": 6_000_000.0,
    "asset_book_value_includes_equipment": False,
    "useful_life_months": 84,
    "residual_value_ratio": 0.10,
    "depreciation_method": "straight_line",
    "depreciation_effective_date": "2026-09-03",
}

CONTRACT_FIELDS = tuple(DEPRECIATION_CONTRACT) + ("asset_in_service_date",)


def _payload(**overrides: Any) -> dict[str, Any]:
    return {**BASE_INPUT, **DEPRECIATION_CONTRACT, **overrides}


def _case(payload: dict[str, Any]) -> ValuationCase:
    return ValuationCase.create(
        build_valuation_view(payload),
        created_by="avm-depreciation-contract-spec",
        correlation_id="corr-avm-depreciation-contract",
    )


def _report(payload: dict[str, Any], *, pin: str | None = None) -> ValuationReport:
    case = _case(payload)
    margin = normalize_margin(case)
    if pin is None:
        return value_store(case, margin)
    return value_store(case, margin, depreciation_version_pin=pin)


def _try_report(
    payload: dict[str, Any], *, pin: str | None = None
) -> tuple[ValuationReport | None, Exception | None]:
    """Report-or-error, so an unimplemented surface fails as an assertion.

    These specs describe an API that does not exist yet, so calling it raises
    TypeError as often as it returns a wrong number. Funnelling both into an
    assertion keeps every test in this file failing for the one reason the
    xfail marker claims: the contract is not implemented.
    """
    try:
        return _report(payload, pin=pin), None
    except Exception as exc:
        return None, exc


def _lens(report: ValuationReport, name: str) -> Any:
    for lens in report.lenses:
        if lens.lens == name:
            return lens
    raise AssertionError(f"report carries no {name!r} lens")


def _domain_constant(name: str) -> Any:
    import modules.avm.domain.valuation as valuation_domain

    value = getattr(valuation_domain, name, None)
    assert value is not None, (
        f"modules/avm/domain/valuation.py defines no {name}; see {DOC} section C-4"
    )
    return value


class TestTheVerdictIsAVMSpecific:
    """The contract's judgement: an AVM-specific model, not a shared one.

    This is the only test in the file that is green today, and it must stay
    green after the model lands. `config/code-boundaries.yaml` puts both
    modules in `product_system`, so the boundary check does not forbid this
    import -- nothing but this test does.
    """

    def test_avm_does_not_import_site_economics(self) -> None:
        offenders: list[str] = []
        for path in sorted((REPO_ROOT / "modules" / "avm").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(name.split(".")[0:2] == ["modules", "site_economics"] for name in names):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert not offenders, (
            "modules/avm imports modules/site_economics at "
            + ", ".join(offenders)
            + f". The simulator's depreciation is a tax shield on a greenfield "
            f"projection, not the carrying-value decline a valuation needs; see {DOC}."
        )


class TestTheDepreciationContractIsNotImplementedYet:
    """Contract sections C-1 through C-5, L-1/L-2/L-4 and R-1, as specs.

    Remove the marker on a test when the behaviour it describes lands. Leaving
    it on turns the suite red via strict XPASS.
    """

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=f"{DOC} C-3: depreciation is not in the valuation path (ODP-FR-AVM-001)",
    )
    def test_two_inputs_differing_only_in_depreciation_produce_different_valuation(self) -> None:
        """The acceptance test for the whole batch.

        Same store, same margins, same comparables, same origin time; the only
        difference is 60 months of equipment age. Today both cards are
        identical, which is exactly the defect: depreciation can be stored and
        still not be computed with.
        """
        young, young_error = _try_report(_payload(asset_in_service_date="2026-03-03"))
        old, old_error = _try_report(_payload(asset_in_service_date="2021-03-03"))
        assert young_error is None, f"valuation refused the young-asset input: {young_error}"
        assert old_error is None, f"valuation refused the aged-asset input: {old_error}"
        assert young is not None and old is not None

        assert _lens(old, "asset").p50 < _lens(young, "asset").p50, (
            "60 months of equipment age did not lower the asset lens: "
            f"{_lens(old, 'asset').p50} vs {_lens(young, 'asset').p50}"
        )
        assert old.fair_price.p50 != young.fair_price.p50, (
            "two inputs differing only in depreciation produced the same fair price "
            f"({young.fair_price.p50}); depreciation is not reaching the calculation"
        )
        assert old.reserve_price != young.reserve_price
        assert old.asking_price != young.asking_price

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=f"{DOC} C-2: ValuationInput carries no depreciation fields",
    )
    def test_valuation_input_carries_the_depreciation_contract_fields(self) -> None:
        view = build_valuation_view(_payload(asset_in_service_date="2021-03-03"))
        serialized = view.to_dict()
        missing = [name for name in CONTRACT_FIELDS if name not in serialized]
        assert not missing, f"ValuationInput.to_dict() omits {missing}; see {DOC} section C-2"
        assert serialized["useful_life_months"] == 84
        assert serialized["residual_value_ratio"] == 0.10
        assert serialized["asset_in_service_date"].startswith("2021-03-03")
        assert serialized["feature_version"] == "valuation-view-v2", (
            "ValuationInput changed shape, so the feature version must move with it"
        )

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=f"{DOC} C-4: the asset lens publishes no depreciation evidence",
    )
    def test_the_asset_lens_publishes_its_depreciation_evidence(self) -> None:
        """Every intermediate value in C-3, on the card.

        A number a buyer cannot take apart is a number they have to trust.
        """
        report, error = _try_report(_payload(asset_in_service_date="2021-03-03"))
        assert error is None, f"valuation refused a fully specified input: {error}"
        assert report is not None

        evidence = _lens(report, "asset").evidence
        assert "depreciation" in evidence, f"asset lens carries no depreciation block; see {DOC}"
        block = evidence["depreciation"]
        expected = {
            "basis",
            "in_service_date",
            "effective_date",
            "elapsed_months",
            "useful_life_months",
            "residual_value_ratio",
            "residual",
            "accumulated_depreciation",
            "equipment_value_after_depreciation",
            "method",
            "version",
        }
        assert expected <= set(block), f"depreciation evidence omits {sorted(expected - set(block))}"
        assert block["elapsed_months"] == 66
        assert block["method"] == "straight_line"
        assert report.depreciation_applied is True
        assert report.depreciation_version == _domain_constant("AVM_DEPRECIATION_VERSION")

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=f"{DOC} C-5: missing depreciation inputs are not refused",
    )
    def test_missing_depreciation_inputs_do_not_yield_a_complete_card(self) -> None:
        """Fail closed, one required field at a time.

        `or 0.0` and "treat it as not depreciated" both produce a card. The
        second is worse than the first, because it is numerically identical to
        the appraised-basis case and means the opposite.
        """
        for omitted in ("equipment_original_cost", "useful_life_months", "asset_in_service_date"):
            payload = _payload(asset_in_service_date="2021-03-03")
            payload.pop(omitted, None)
            report, error = _try_report(payload)
            assert report is None, (
                f"a card was produced with {omitted} missing; see {DOC} section C-5"
            )
            assert error is not None and omitted in str(error), (
                f"the refusal for a missing {omitted} does not name the field: {error}"
            )

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=f"{DOC} C-1: the depreciation basis is not distinguished",
    )
    def test_an_appraised_basis_is_not_depreciated_twice(self) -> None:
        """An independently appraised value is already net of age.

        Numerically this looks like "no depreciation", which is why the card
        has to say which of the two it is.
        """
        payload = _payload(asset_in_service_date="2021-03-03")
        payload["equipment_depreciation_basis"] = "appraised_fair_value"
        payload.pop("equipment_original_cost", None)
        report, error = _try_report(payload)
        assert error is None, f"an appraised basis was refused: {error}"
        assert report is not None

        evidence = _lens(report, "asset").evidence
        assert "depreciation" in evidence, (
            f"asset lens carries no depreciation block; see {DOC} section C-1"
        )
        block = evidence["depreciation"]
        assert block.get("basis") == "appraised_fair_value"
        assert (
            block.get("equipment_value_after_depreciation") == BASE_INPUT["equipment_fair_value"]
        ), "an appraised fair value was depreciated a second time"
        assert report.depreciation_applied is False
        assert report.depreciation_version == "avm-depreciation-not-applicable-v1"

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=f"{DOC} L-1/L-2: no legacy depreciation version exists to tag old cards with",
    )
    def test_a_legacy_card_keeps_its_legacy_version_and_is_not_recomputed(self) -> None:
        """Cards already sent to buyers stay byte-comparable.

        The legacy tag is assigned at rehydration, never as a dataclass
        default -- a default would quietly relabel new cards as legacy too.
        """
        legacy_version = _domain_constant("AVM_DEPRECIATION_LEGACY_VERSION")
        assert legacy_version == "avm-depreciation-absent-v0"

        fields = {field.name: field for field in dataclasses.fields(ValuationReport)}
        assert "depreciation_version" in fields, f"ValuationReport has no version field; see {DOC}"
        assert (
            fields["depreciation_version"].default is dataclasses.MISSING
            and fields["depreciation_version"].default_factory is dataclasses.MISSING
        ), "depreciation_version has a default; every card must state its version on purpose"

        legacy_card = {
            "case_id": "avm-case-legacy",
            "store_id": BASE_INPUT["store_id"],
            "fair_price": {"p10": 100.0, "p50": 200.0, "p90": 300.0},
            "reserve_price": 97.0,
            "asking_price": 315.0,
            "model_version": "dealroom-avm-baseline-v1",
            "valuation_version": 1,
            "finance_approval": None,
        }
        rehydrate = getattr(
            __import__("modules.avm.domain.valuation", fromlist=["x"]),
            "rehydrate_legacy_valuation_card",
            None,
        )
        assert rehydrate is not None, (
            f"no rehydration path tags pre-cutover cards; see {DOC} section L-2"
        )
        tagged = rehydrate(legacy_card)
        assert tagged["depreciation_version"] == legacy_version
        assert tagged["depreciation_applied"] is False
        for key, value in legacy_card.items():
            assert tagged[key] == value, f"rehydration recomputed {key}; see {DOC} section L-1"

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=f"{DOC} R-1: there is no version pin to roll back to",
    )
    def test_a_v0_pin_reproduces_the_pre_cutover_numbers(self) -> None:
        """Rollback has to land on a known state, not an approximate one.

        The implementation must be additive: pinned to v0, the arithmetic is
        the arithmetic that shipped before the cutover.
        """
        legacy_version = _domain_constant("AVM_DEPRECIATION_LEGACY_VERSION")
        baseline, baseline_error = _try_report(dict(BASE_INPUT))
        assert baseline_error is None, f"the pre-cutover input path broke: {baseline_error}"
        assert baseline is not None

        pinned, pin_error = _try_report(
            _payload(asset_in_service_date="2021-03-03"), pin=legacy_version
        )
        assert pin_error is None, f"value_store accepts no depreciation version pin: {pin_error}"
        assert pinned is not None

        assert pinned.fair_price.to_dict() == baseline.fair_price.to_dict(), (
            "a v0 pin did not reproduce the pre-cutover fair price band"
        )
        assert pinned.reserve_price == baseline.reserve_price
        assert pinned.asking_price == baseline.asking_price
        assert pinned.depreciation_version == legacy_version
        assert pinned.depreciation_applied is False

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=f"{DOC} L-4: calibration pools every report regardless of version",
    )
    def test_calibration_does_not_silently_mix_depreciation_versions(self) -> None:
        """`AVMService.calibrate_deal_outcomes` collects every stored report.

        After the cutover that cohort holds both v0 cards (depreciation not in
        the number) and v1 cards (depreciation in it). Coverage and MAE will
        still compute, and still look ordinary.
        """
        fields = {field.name for field in dataclasses.fields(DealOutcomeCalibrationReport)}
        assert {"depreciation_version", "depreciation_version_breakdown"} & fields, (
            "DealOutcomeCalibrationReport records no depreciation version, so a mixed "
            f"cohort is indistinguishable from a clean one; see {DOC} section L-4"
        )
