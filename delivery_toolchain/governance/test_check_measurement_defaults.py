"""The measurement-default check must be able to fail, in every layer it claims.

A gate that has never been shown to refuse anything is indistinguishable from
one that cannot. That is not an abstract worry here: the assisted-listing schema
validator sat with a broken path from commit 549ce261 onwards and nobody knew,
because the only test that would have reported it was excluded from CI by a
marker expression. This file exists so the same thing cannot be said of this
check.

Each case plants one specific input and asserts the outcome, including the
cases where the check must stay quiet -- a rule that fires on legitimate code
gets a blanket exemption and then guards nothing.

The check now reads six layers, so the same two questions are asked of each of
them separately: what input makes this layer fail, and what nearby legitimate
input must it leave alone. A layer with no red case is a layer that has only
been claimed. The last two classes go further and ask the question the evidence
note says this repository keeps getting wrong -- whether the thing that can fail
is the thing CI actually runs.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from delivery_toolchain.governance.check_measurement_defaults import (
    Exemption,
    expired_exemptions,
    is_bounded_score,
    load_exemptions,
    main,
    scan,
    stale_exemptions,
)


def _tree(tmp_path: Path, source: str, *, package: str = "modules") -> Path:
    module_dir = tmp_path / package / "sample" / "domain"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "models.py").write_text(source, encoding="utf-8")
    return tmp_path


def _sql_tree(tmp_path: Path, sql: str, *, name: str = "view.sql") -> Path:
    sql_dir = tmp_path / "pipelines" / "dbt" / "models" / "model_ready"
    sql_dir.mkdir(parents=True, exist_ok=True)
    (sql_dir / name).write_text(sql, encoding="utf-8")
    return tmp_path


def _openapi_tree(tmp_path: Path, schemas: dict) -> Path:
    spec_dir = tmp_path / "packages" / "contract"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "openapi.json").write_text(
        json.dumps({"openapi": "3.1.0", "components": {"schemas": schemas}}, indent=2),
        encoding="utf-8",
    )
    return tmp_path


def _ts_tree(tmp_path: Path, source: str) -> Path:
    ts_dir = tmp_path / "apps" / "web" / "features"
    ts_dir.mkdir(parents=True, exist_ok=True)
    (ts_dir / "panel.ts").write_text(source, encoding="utf-8")
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
        assert violations[0].layer == "dataclass"

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

    def test_a_default_hidden_behind_field_is_still_a_default(self, tmp_path: Path) -> None:
        """`field(default=1.0)` says exactly what `= 1.0` says, one call deeper."""
        root = _tree(
            tmp_path,
            "from dataclasses import dataclass, field\n"
            "\n"
            "@dataclass\n"
            "class Feature:\n"
            "    quality_score: float = field(default=1.0)\n",
        )
        assert [v.field_name for v in scan(root)] == ["quality_score"]


class TestItReadsThePydanticLayer:
    """The API's own copy of the contract.

    A request model is the last place absence is still visible: once the model
    has filled in 1.0, no layer below it can tell the caller omitted the field.
    """

    def test_a_request_model_defaulting_to_perfect_is_reported(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            "from pydantic import BaseModel\n"
            "\n"
            "class ValuationPayload(BaseModel):\n"
            "    store_id: str\n"
            "    quality_score: float = 1.0\n",
            package="apps",
        )
        violations = scan(root)
        assert [(v.layer, v.class_name, v.field_name) for v in violations] == [
            ("pydantic", "ValuationPayload", "quality_score")
        ]

    def test_a_default_hidden_behind_Field_is_still_a_default(self, tmp_path: Path) -> None:
        """`Field(default=1.0)` is the spelling a reviewer is least likely to
        read as a default, because the line looks like validation config."""
        root = _tree(
            tmp_path,
            "from pydantic import BaseModel, Field\n"
            "\n"
            "class Payload(BaseModel):\n"
            "    confidence: float = Field(default=1.0, ge=0.0, le=1.0)\n"
            "    coverage_ratio: float = Field(1.0)\n",
            package="apps",
        )
        assert {v.field_name for v in scan(root)} == {"confidence", "coverage_ratio"}

    def test_a_project_base_model_is_still_a_model(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            "from shared.api import CamelBaseModel\n"
            "\n"
            "class Payload(CamelBaseModel):\n"
            "    reliability: float = 1.0\n",
            package="apps",
        )
        assert [v.layer for v in scan(root)] == ["pydantic"]

    def test_a_nullable_request_field_is_accepted(self, tmp_path: Path) -> None:
        """`float | None = None` is the fix, so it must not be flagged."""
        root = _tree(
            tmp_path,
            "from pydantic import BaseModel, Field\n"
            "\n"
            "class Payload(BaseModel):\n"
            "    quality_score: float | None = Field(default=None)\n"
            "    confidence: float | None = None\n",
            package="apps",
        )
        assert scan(root) == []

    def test_request_knobs_are_not_flagged(self, tmp_path: Path) -> None:
        """A page size of 1 and a floor of 1.0 are configuration, not evidence."""
        root = _tree(
            tmp_path,
            "from pydantic import BaseModel, Field\n"
            "\n"
            "class Query(BaseModel):\n"
            "    limit: int = Field(default=1, ge=1)\n"
            "    srid: int = 4326\n"
            "    risk_per_flag: float = 1.0\n"
            "    min_training_records: int = Field(default=1)\n",
            package="apps",
        )
        assert scan(root) == []


class TestItReadsTheMapperLayer:
    """Where a fixed annotation gets quietly undone.

    Making the dataclass field optional changes nothing if the row mapper one
    function below still substitutes 1.0, and the diff that leaves it behind
    looks complete.
    """

    def test_a_get_with_a_perfect_fallback_is_reported(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            "def from_row(row):\n"
            "    return Record(confidence=float(row.get('confidence', 1.0)))\n",
        )
        violations = scan(root)
        assert [(v.layer, v.field_name) for v in violations] == [("mapper", "confidence")]
        assert "row.get('confidence', 1.0)" in violations[0].detail

    def test_a_helper_hiding_the_fallback_behind_a_keyword_is_reported(
        self, tmp_path: Path
    ) -> None:
        """`_first_present(data, 'a', 'b', default=1.0)` is the sitescore shape:
        neither key present still yields a perfect score."""
        root = _tree(
            tmp_path,
            "def from_mapping(data):\n"
            "    return _first_present(data, 'average_confidence', 'confidence', default=1.0)\n",
        )
        assert [(v.layer, v.field_name) for v in scan(root)] == [("mapper", "average_confidence")]

    def test_two_mappers_in_one_file_are_two_pieces_of_debt(self, tmp_path: Path) -> None:
        """external.py holds the identical call in two connectors. They are two
        source contracts and two decisions, so they need two exemption keys."""
        root = _tree(
            tmp_path,
            "class PoiConnector:\n"
            "    def canonicalize(self, record):\n"
            "        return record.get('confidence', 1.0)\n"
            "\n"
            "class CompetitorConnector:\n"
            "    def canonicalize(self, record):\n"
            "        return record.get('confidence', 1.0)\n",
        )
        assert len({v.key for v in scan(root)}) == 2

    def test_a_get_without_a_fallback_is_accepted(self, tmp_path: Path) -> None:
        """No second argument means absence stays absent, which is the point."""
        root = _tree(
            tmp_path,
            "def from_row(row):\n    return row.get('confidence')\n",
        )
        assert scan(root) == []

    def test_a_fallback_to_the_bottom_of_the_range_is_accepted(self, tmp_path: Path) -> None:
        root = _tree(
            tmp_path,
            "def from_row(row):\n    return row.get('confidence', 0.0)\n",
        )
        assert scan(root) == []

    def test_configuration_fallbacks_are_not_flagged(self, tmp_path: Path) -> None:
        """Mappers are full of `.get(key, 1)` for counts and multipliers."""
        root = _tree(
            tmp_path,
            "def from_row(row):\n"
            "    return (\n"
            "        row.get('limit', 1),\n"
            "        row.get('srid', 4326),\n"
            "        row.get('weight', 1.0),\n"
            "        row.get('horizon_days', 1),\n"
            "    )\n",
        )
        assert scan(root) == []


class TestItReadsTheSqlLayer:
    """The layer that can put the value back before Python ever runs."""

    def test_a_column_default_of_perfect_is_reported(self, tmp_path: Path) -> None:
        root = tmp_path
        migrations = root / "infra" / "db" / "migrations"
        migrations.mkdir(parents=True)
        (migrations / "000001_init.sql").write_text(
            "CREATE TABLE IF NOT EXISTS pois (\n"
            "    poi_id TEXT PRIMARY KEY,\n"
            "    confidence REAL NOT NULL DEFAULT 1.00,\n"
            "    geocode_confidence REAL\n"
            ");\n",
            encoding="utf-8",
        )
        violations = scan(root)
        assert [(v.layer, v.class_name, v.field_name) for v in violations] == [
            ("sql", "pois", "confidence")
        ]

    def test_a_nullable_score_column_is_accepted(self, tmp_path: Path) -> None:
        """A column that can hold NULL is a column that can say 'not measured'."""
        root = tmp_path
        migrations = root / "infra" / "db" / "migrations"
        migrations.mkdir(parents=True)
        (migrations / "000001_init.sql").write_text(
            "CREATE TABLE pois (\n    confidence REAL,\n    quality_score NUMERIC(5, 4)\n);\n",
            encoding="utf-8",
        )
        assert scan(root) == []

    def test_a_coalesce_fallback_is_reported(self, tmp_path: Path) -> None:
        root = _sql_tree(
            tmp_path,
            "select\n"
            "    least(coalesce(listings.confidence, 1.0),\n"
            "          coalesce(address_locations.geocode_confidence, 1.0)) as confidence\n"
            "from expansion.listings\n",
        )
        violations = scan(root)
        assert {v.key.split("::")[1] for v in violations} == {
            "listings.confidence",
            "address_locations.geocode_confidence",
        }

    def test_a_constant_projected_as_a_score_is_reported(self, tmp_path: Path) -> None:
        """`1.0 as data_quality_score` is not a measurement and not a rule:
        there is no input that could make it report anything else."""
        root = _sql_tree(
            tmp_path,
            "select\n    1.0 as data_quality_score,\n    1.0 as confidence\nfrom core.stores\n",
        )
        assert {v.field_name for v in scan(root)} == {"data_quality_score", "confidence"}

    def test_a_projection_that_discriminates_is_accepted(self, tmp_path: Path) -> None:
        """This is the distinction the whole SQL layer turns on. A case
        expression on observation recency can return 0.0, so it is a rule that
        reports a bad row. Flagging it would make the layer unenforceable, since
        every model-ready view contains one."""
        root = _sql_tree(
            tmp_path,
            "select\n"
            "    case when latest_observation_time <= current_timestamp then 1.0 else 0.0 end\n"
            "        as data_quality_score,\n"
            "    case when h3_index is not null then 1.0 else 0.0 end as confidence\n"
            "from geo.h3_cells\n",
        )
        assert scan(root) == []

    def test_a_coalesce_to_the_bottom_of_the_range_is_accepted(self, tmp_path: Path) -> None:
        root = _sql_tree(tmp_path, "select coalesce(l.confidence, 0.0) as confidence from l\n")
        assert scan(root) == []

    def test_constants_projected_as_knobs_are_not_flagged(self, tmp_path: Path) -> None:
        root = _sql_tree(
            tmp_path,
            "select\n"
            "    1.0 as treatment_intensity,\n"
            "    4326 as srid,\n"
            "    1 as batch_size,\n"
            "    coalesce(x.rent_multiplier, 1.0) as rent_multiplier\n"
            "from core.stores\n",
        )
        assert scan(root) == []

    def test_a_commented_out_default_is_not_a_default(self, tmp_path: Path) -> None:
        """Comments are blanked before matching; a rule that reads dead SQL
        produces exemptions for lines nobody can fix."""
        root = _sql_tree(
            tmp_path,
            "select\n"
            "    -- 1.0 as data_quality_score,\n"
            "    /* coalesce(l.confidence, 1.0) as confidence, */\n"
            "    l.confidence as confidence\n"
            "from l\n",
        )
        assert scan(root) == []


class TestItReadsThePublishedContract:
    """OpenAPI and TypeScript: the layer the repository ships to other people."""

    def test_a_schema_default_of_perfect_is_reported(self, tmp_path: Path) -> None:
        root = _openapi_tree(
            tmp_path,
            {
                "ValuationPayload": {
                    "type": "object",
                    "properties": {
                        "store_id": {"type": "string"},
                        "quality_score": {"type": "number", "default": 1.0},
                    },
                }
            },
        )
        violations = scan(root)
        assert [(v.layer, v.class_name, v.field_name) for v in violations] == [
            ("openapi", "components.schemas.ValuationPayload", "quality_score")
        ]

    def test_an_integer_one_default_counts_as_perfect(self, tmp_path: Path) -> None:
        """JSON has one number type; `1` and `1.0` are the same promise."""
        root = _openapi_tree(
            tmp_path,
            {"Zone": {"properties": {"coverage_ratio": {"type": "number", "default": 1}}}},
        )
        assert len(scan(root)) == 1

    def test_a_schema_without_a_default_is_accepted(self, tmp_path: Path) -> None:
        root = _openapi_tree(
            tmp_path,
            {
                "Zone": {
                    "properties": {
                        "confidence": {"type": "number"},
                        "coverage_ratio": {"type": ["number", "null"], "default": None},
                        "limit": {"type": "integer", "default": 1},
                        "srid": {"type": "integer", "default": 4326},
                    }
                }
            },
        )
        assert scan(root) == []

    def test_a_json_file_that_is_not_a_spec_is_not_read_as_one(self, tmp_path: Path) -> None:
        """Selection is by an `openapi` key, not by filename or by shape."""
        config_dir = tmp_path / "packages" / "contract"
        config_dir.mkdir(parents=True)
        (config_dir / "openapi.json").write_text(
            json.dumps({"properties": {"confidence": {"default": 1.0}}}), encoding="utf-8"
        )
        assert scan(tmp_path) == []

    def test_a_typescript_fallback_to_perfect_is_reported(self, tmp_path: Path) -> None:
        root = _ts_tree(tmp_path, "const confidence = zone.confidence ?? 1;\n")
        violations = scan(root)
        assert [(v.layer, v.field_name) for v in violations] == [("typescript", "confidence")]

    def test_camel_case_is_the_same_field(self, tmp_path: Path) -> None:
        """`coverageRatio` and `coverage_ratio` are one field with two
        spellings; a predicate that only knows snake_case leaves the consumer
        unguarded exactly where the API contract stops."""
        assert is_bounded_score("coverageRatio")
        assert is_bounded_score("dataQualityScore")
        root = _ts_tree(tmp_path, "const coverageRatio = zone.coverageRatio || 1;\n")
        assert [v.field_name for v in scan(root)] == ["coverageRatio"]

    def test_a_comparison_against_one_is_not_a_fallback(self, tmp_path: Path) -> None:
        """A threshold test is the opposite of substituting a value: it is code
        that can still tell the difference."""
        root = _ts_tree(
            tmp_path,
            "if (zone.confidence >= 1) return;\n"
            "if (zone.confidence === 1) return;\n"
            "if (zone.confidence !== 1) return;\n"
            "if (zone.confidence <= 1) return;\n",
        )
        assert scan(root) == []

    def test_typescript_knobs_and_zero_fallbacks_are_not_flagged(self, tmp_path: Path) -> None:
        root = _ts_tree(
            tmp_path,
            "const limit = params.limit ?? 1;\n"
            "const page = 1;\n"
            "const confidence = zone.confidence ?? 0;\n"
            "const qualityThreshold = 1;\n",
        )
        assert scan(root) == []


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

    def test_typescript_tests_are_not_scanned_either(self, tmp_path: Path) -> None:
        ts_dir = tmp_path / "apps" / "web" / "features" / "__tests__"
        ts_dir.mkdir(parents=True)
        (ts_dir / "panel.test.ts").write_text(
            "const confidence = zone.confidence ?? 1;\n", encoding="utf-8"
        )
        assert scan(tmp_path) == []


class TestExemptionsStayAttributable:
    def test_an_exemption_without_an_owner_is_refused(self, tmp_path: Path) -> None:
        """An unattributed exemption is how the debt goes back to being
        invisible, which is the state this check was written to leave."""
        path = tmp_path / "exemptions.json"
        path.write_text(
            json.dumps(
                {"exemptions": [{"field": "a.py::C.f", "reason": "later", "expires": "2027-01-01"}]}
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as excinfo:
            load_exemptions(path)
        assert "owner" in str(excinfo.value)

    def test_an_exemption_without_a_reason_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "exemptions.json"
        path.write_text(
            json.dumps(
                {"exemptions": [{"field": "a.py::C.f", "owner": "Ops", "expires": "2027-01-01"}]}
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as excinfo:
            load_exemptions(path)
        assert "reason" in str(excinfo.value)

    def test_an_exemption_without_an_expiry_is_refused(self, tmp_path: Path) -> None:
        """An exemption with no date is a permanent decision wearing the clothes
        of a temporary one."""
        path = tmp_path / "exemptions.json"
        path.write_text(
            json.dumps(
                {"exemptions": [{"field": "a.py::C.f", "owner": "Ops", "reason": "documented"}]}
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as excinfo:
            load_exemptions(path)
        assert "expires" in str(excinfo.value)

    def test_an_unreadable_expiry_is_refused_rather_than_ignored(self, tmp_path: Path) -> None:
        """Silently treating an unparseable date as "no expiry" would make the
        weakest entry in the file the one nobody can see."""
        path = tmp_path / "exemptions.json"
        path.write_text(
            json.dumps(
                {
                    "exemptions": [
                        {
                            "field": "a.py::C.f",
                            "owner": "Ops",
                            "reason": "documented",
                            "expires": "next quarter",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SystemExit) as excinfo:
            load_exemptions(path)
        assert "expires" in str(excinfo.value)

    def test_a_complete_exemption_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "exemptions.json"
        path.write_text(
            json.dumps(
                {
                    "exemptions": [
                        {
                            "field": "a.py::C.f",
                            "owner": "Ops",
                            "reason": "documented",
                            "expires": "2027-01-31",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        loaded = load_exemptions(path)
        assert loaded["a.py::C.f"].owner == "Ops"
        assert loaded["a.py::C.f"].expires == date(2027, 1, 31)


class TestExemptionsRunOut:
    """The date has to do something, or it is documentation."""

    def _exemption(self, expires: date) -> dict[str, Exemption]:
        return {
            "a.py::C.f": Exemption(
                field="a.py::C.f", owner="Ops", reason="documented", expires=expires
            )
        }

    def test_an_exemption_past_its_date_is_reported(self) -> None:
        today = date(2026, 12, 1)
        expired = expired_exemptions(self._exemption(date(2026, 11, 30)), today)
        assert [e.field for e in expired] == ["a.py::C.f"]

    def test_an_exemption_on_its_last_day_is_still_live(self) -> None:
        """Expiry is inclusive: the owner has the whole day named in the file."""
        today = date(2026, 11, 30)
        assert expired_exemptions(self._exemption(date(2026, 11, 30)), today) == []

    def test_an_expired_exemption_fails_the_check_itself(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not just reported by a helper -- the command CI runs must return 1,
        while the repository is otherwise clean."""
        module = __import__(
            "delivery_toolchain.governance.check_measurement_defaults",
            fromlist=["main"],
        )
        root = _tree(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Observation:\n"
            "    data_quality_score: float = 1.0\n",
        )
        exemptions = tmp_path / "exemptions.json"
        entry = {
            "field": "modules/sample/domain/models.py::Observation.data_quality_score",
            "owner": "Ops",
            "reason": "documented",
        }
        monkeypatch.setattr(module, "REPO_ROOT", root)
        monkeypatch.setattr(module, "EXEMPTIONS_PATH", exemptions)

        exemptions.write_text(
            json.dumps({"exemptions": [dict(entry, expires="2999-01-01")]}), encoding="utf-8"
        )
        assert main([]) == 0

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        exemptions.write_text(
            json.dumps({"exemptions": [dict(entry, expires=yesterday)]}), encoding="utf-8"
        )
        assert main([]) == 1


class TestAFixedFieldTakesItsExemptionWithIt:
    """Fix and delete must be the same commit.

    An exemption that outlives its field reads as live debt that is already
    paid, and every entry around it becomes less believable.
    """

    @pytest.mark.parametrize(
        ("key", "source"),
        [
            (
                "modules/sample/domain/models.py::Observation.data_quality_score",
                "from dataclasses import dataclass\n"
                "\n"
                "@dataclass\n"
                "class Observation:\n"
                "    data_quality_score: float = 1.0\n",
            ),
            (
                "modules/sample/domain/models.py::from_row.confidence",
                "def from_row(row):\n    return row.get('confidence', 1.0)\n",
            ),
        ],
    )
    def test_the_exemption_is_live_while_the_field_is_and_stale_after(
        self, tmp_path: Path, key: str, source: str
    ) -> None:
        exemptions = {
            key: Exemption(field=key, owner="Ops", reason="documented", expires=date(2027, 1, 31))
        }

        root = _tree(tmp_path, source)
        violations = scan(root)
        assert [v.key for v in violations] == [key]
        assert stale_exemptions(exemptions, violations) == []

        # The fix, in the commit that would also delete the entry.
        _tree(tmp_path, source.replace("1.0", "None").replace(": float", ": float | None"))
        assert scan(root) == []
        assert stale_exemptions(exemptions, scan(root)) == [key]

    def test_an_unexempted_violation_fails_the_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point, exercised through the entry point CI invokes."""
        module = __import__(
            "delivery_toolchain.governance.check_measurement_defaults",
            fromlist=["main"],
        )
        root = _sql_tree(tmp_path, "select 1.0 as data_quality_score from core.stores\n")
        exemptions = tmp_path / "exemptions.json"
        exemptions.write_text(json.dumps({"exemptions": []}), encoding="utf-8")
        monkeypatch.setattr(module, "REPO_ROOT", root)
        monkeypatch.setattr(module, "EXEMPTIONS_PATH", exemptions)
        assert main([]) == 1


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

    def test_no_exemption_is_already_past_its_date(self) -> None:
        """This is the one test in the file that fails without anybody editing
        code. That is intended: the entries name owners and dates, and the date
        arriving is the event the file exists to produce."""
        from delivery_toolchain.governance.check_measurement_defaults import (
            EXEMPTIONS_PATH,
        )

        expired = expired_exemptions(load_exemptions(EXEMPTIONS_PATH), date.today())
        assert not expired, "expired: " + ", ".join(
            f"{e.field} (owner {e.owner}, {e.expires})" for e in expired
        )

    def test_the_ledger_still_covers_every_layer_it_claims(self) -> None:
        """The registered debt is the evidence that each layer runs against
        real code, not only against fixtures. If a layer drops to zero because
        its violations were fixed, delete the assertion for it -- do not let it
        drop to zero because the scanner stopped reading that file type."""
        from delivery_toolchain.governance.check_measurement_defaults import REPO_ROOT

        found = {violation.layer for violation in scan(REPO_ROOT)}
        assert {"dataclass", "mapper", "sql"} <= found


class TestTheLedgerIsItsOwnCanonicalForm:
    """`--write-exemptions` exists to bootstrap the ledger, which means somebody
    will run it on a ledger that is already written and commit the result."""

    def test_regenerating_the_checked_in_ledger_changes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Owners, reasons, dates, entry order and the prose all survive.

        Entry order carries an argument -- the file groups a field's layers by
        remediation batch, not by layer -- so a regeneration that re-sorted it
        would quietly discard that. Byte equality is the cheapest way to know.
        """
        module = __import__(
            "delivery_toolchain.governance.check_measurement_defaults",
            fromlist=["main"],
        )
        original = module.EXEMPTIONS_PATH.read_text(encoding="utf-8")
        scratch = tmp_path / "exemptions.json"
        scratch.write_text(original, encoding="utf-8")
        monkeypatch.setattr(module, "EXEMPTIONS_PATH", scratch)

        assert main(["--write-exemptions"]) == 0
        assert scratch.read_text(encoding="utf-8") == original


class TestTheGateIsWiredIntoCI:
    """Cause four of the structural review was a gate that was correct and
    structurally unreachable. A negative fixture does not answer that; only
    looking at what CI runs does."""

    def _workflow(self) -> str:
        from delivery_toolchain.governance.check_measurement_defaults import REPO_ROOT

        return (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    def test_ci_runs_the_checker(self) -> None:
        assert (
            "delivery_toolchain/governance/check_measurement_defaults.py" in self._workflow()
        ), "the check is not invoked by any CI step"

    def test_ci_collects_this_test_file(self) -> None:
        """`delivery_toolchain` has to be in the pytest step's paths, or the
        negative cases above prove only that they pass on a laptop."""
        workflow = self._workflow()
        pytest_steps = [line for line in workflow.splitlines() if "uv run pytest" in line]
        assert any(
            "delivery_toolchain" in line for line in pytest_steps
        ), f"no pytest step collects delivery_toolchain: {pytest_steps}"
