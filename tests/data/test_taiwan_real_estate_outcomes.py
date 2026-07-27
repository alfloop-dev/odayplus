from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from modules.external_data.providers.taiwan_real_estate import (
    GOVERNMENT_OPEN_DATA_LICENSE_V1,
    MOI_HEADERS,
    NTPC_HEADERS,
    SOURCES,
    DownloadedArtifact,
    OfficialRealEstateBoundExceeded,
    OfficialRealEstateDownloader,
    OfficialRealEstateSchemaDrift,
    OfficialRealEstateSourceError,
    parse_official_real_estate,
)
from scripts.models.real_estate_outcomes import OfficialRealEstateOutcomeStore


def _csv_bytes(rows: list[list[str]]) -> bytes:
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode()


def _moi_row(**overrides: str) -> list[str]:
    values = {header: "" for header in MOI_HEADERS}
    values.update(
        {
            "鄉鎮市區": "板橋區",
            "交易標的": "房地(土地+建物)",
            "土地位置建物門牌": "新北市板橋區範例路１號",
            "土地移轉總面積平方公尺": "32.5",
            "交易年月日": "1150622",
            "交易筆棟數": "土地1建物1車位0",
            "建物型態": "店面(店鋪)",
            "主要用途": "商業用",
            "主要建材": "鋼筋混凝土造",
            "建築完成年月": "0540600",
            "建物移轉總面積平方公尺": "88.5",
            "建物現況格局-房": "1",
            "建物現況格局-廳": "1",
            "建物現況格局-衛": "1",
            "建物現況格局-隔間": "有",
            "有無管理組織": "無",
            "總價元": "20000000",
            "單價元平方公尺": "225989",
            "車位移轉總面積平方公尺": "0",
            "車位總價元": "0",
            "編號": "RPOFFICIAL001",
            "主建物面積": "80",
            "附屬建物面積": "2",
            "陽台面積": "6.5",
            "電梯": "無",
        }
    )
    values.update(overrides)
    return [values[header] for header in MOI_HEADERS]


def _moi_zip(
    *rows: list[str],
    headers: tuple[str, ...] = MOI_HEADERS,
    source_file: str = "f_lvr_land_a.csv",
    municipality: str = "新北市",
) -> bytes:
    manifest = _csv_bytes(
        [
            ["name", "schema", "description"],
            [source_file, "schema-main.csv", f"{municipality}不動產買賣"],
        ]
    )
    schema = _csv_bytes([["name", "title"], *[[header, header] for header in headers]])
    sale = _csv_bytes([list(headers), *rows])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.csv", manifest)
        archive.writestr("schema-main.csv", schema)
        archive.writestr(source_file, sale)
    return buffer.getvalue()


def _ntpc_row(**overrides: str) -> list[str]:
    values = {header: "" for header in NTPC_HEADERS}
    values.update(
        {
            "district": "板橋區",
            "rps01": "房地(土地+建物)",
            "rps02": "新北市板橋區範例路２號",
            "rps03_area": "20",
            "rps07_yyymmddroc": "1140516",
            "rps08": "土地1建物1車位0",
            "rps11": "店面(店鋪)",
            "rps12": "商業用",
            "rps13": "鋼筋混凝土造",
            "rps14_yyymmddroc": "0540631",
            "rps15_area": "70",
            "rps16_quantity": "1",
            "rps17_quantity": "1",
            "rps18_quantity": "1",
            "rps19": "有",
            "rps20": "無",
            "rps21_amountsunitdollars": "16000000",
            "rps22_amountsunitdollars": "228571",
            "rps24_area": "0",
            "rps25_amountsunitdollars": "0",
            "rps27": "RPOFFICIAL002",
            "rps28_area": "65",
            "rps29_area": "0",
            "rps30_area": "5",
            "rps31": "無",
        }
    )
    values.update(overrides)
    return [values[name] for name in NTPC_HEADERS]


def _ntpc_csv(**overrides: str) -> bytes:
    return _csv_bytes([list(NTPC_HEADERS), _ntpc_row(**overrides)])


def _artifact(source_key: str, content: bytes) -> DownloadedArtifact:
    source = SOURCES[source_key]
    return DownloadedArtifact(
        source=source,
        content=content,
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_type=source.media_type,
        fetched_at=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
        final_url=source.source_url,
        etag='"official-test"',
    )


def test_moi_zip_parser_verifies_manifest_schema_and_preserves_partial_date() -> None:
    batch = parse_official_real_estate(_artifact("moi", _moi_zip(_moi_row())))

    assert len(batch.records) == 1
    record = batch.records[0]
    assert record.municipality == "新北市"
    assert record.authority_partition == "f_lvr_land_a.csv"
    assert record.district == "板橋區"
    assert record.total_price_twd == 20_000_000
    assert record.completion_date is None
    assert record.completion_year == 1965
    assert record.completion_month == 6
    assert record.raw_fields["建築完成年月"] == "0540600"
    assert len(record.raw_record_sha256) == 64
    assert batch.source_snapshot_id.startswith("tw_moi_lvr_land_a:sha256:")


def test_ntpc_csv_parser_keeps_impossible_legacy_day_without_inventing_one() -> None:
    batch = parse_official_real_estate(_artifact("ntpc", _ntpc_csv()))

    record = batch.records[0]
    assert record.municipality == "新北市"
    assert record.authority_partition == "ntpc-real-estate-sales.csv"
    assert record.source_variant_id == "authority-natural:v1"
    assert len(record.identity_fingerprint) == 64
    assert record.source_record_id == "RPOFFICIAL002"
    assert record.completion_date is None
    assert record.completion_year == 1965
    assert record.completion_month == 6
    assert record.raw_fields["rps14_yyymmddroc"] == "0540631"


def test_moi_identity_includes_authority_partition_for_reused_record_ids() -> None:
    serial = "AUTHORITY-LOCAL-001"
    taipei = parse_official_real_estate(
        _artifact(
            "moi",
            _moi_zip(
                _moi_row(編號=serial),
                source_file="a_lvr_land_a.csv",
                municipality="臺北市",
            ),
        )
    ).records[0]
    new_taipei = parse_official_real_estate(
        _artifact(
            "moi",
            _moi_zip(
                _moi_row(編號=serial),
                source_file="f_lvr_land_a.csv",
                municipality="新北市",
            ),
        )
    ).records[0]

    assert taipei.source_record_id == new_taipei.source_record_id
    assert taipei.authority_partition != new_taipei.authority_partition
    assert taipei.transaction_id != new_taipei.transaction_id


def test_optional_transfer_number_does_not_fork_authority_identity() -> None:
    payload = _csv_bytes(
        [
            list(NTPC_HEADERS),
            _ntpc_row(rps27="REUSED-001", rps32="0202"),
            _ntpc_row(rps27="REUSED-001", rps32="0210"),
        ]
    )
    batch = parse_official_real_estate(_artifact("ntpc", payload))

    assert {record.source_variant_id for record in batch.records} == {
        "authority-natural:v1",
    }
    assert len({record.transaction_id for record in batch.records}) == 1

    original = _ntpc_row(
        rps27="CORRECTED-001",
        rps32="",
        rps21_amountsunitdollars="16000000",
        rps26="initial publication",
    )
    corrected = _ntpc_row(
        rps27="CORRECTED-001",
        rps32="",
        rps21_amountsunitdollars="16500000",
        rps26="non-key correction",
    )
    corrected_batch = parse_official_real_estate(
        _artifact(
            "ntpc",
            _csv_bytes([list(NTPC_HEADERS), original, corrected]),
        )
    )
    assert len(corrected_batch.records) == 2
    assert len({record.transaction_id for record in corrected_batch.records}) == 1
    assert {
        record.source_variant_id for record in corrected_batch.records
    } == {"authority-natural:v1"}
    assert len(
        {record.identity_fingerprint for record in corrected_batch.records}
    ) == 1
    assert len(
        {record.raw_record_sha256 for record in corrected_batch.records}
    ) == 2

    conflicting = _ntpc_row(
        rps27="CORRECTED-001",
        rps32="",
        rps02="新北市板橋區另一條路９９號",
    )
    with pytest.raises(
        OfficialRealEstateSchemaDrift,
        match="authority natural identity collision",
    ):
        parse_official_real_estate(
            _artifact(
                "ntpc",
                _csv_bytes([list(NTPC_HEADERS), original, conflicting]),
            )
        )


def test_parser_fails_closed_on_schema_drift_and_row_bound() -> None:
    changed_headers = (*MOI_HEADERS[:-1], "未知欄位")
    with pytest.raises(OfficialRealEstateSchemaDrift, match="schema-main"):
        parse_official_real_estate(_artifact("moi", _moi_zip(_moi_row(), headers=changed_headers)))

    with pytest.raises(OfficialRealEstateBoundExceeded, match="max_rows"):
        parse_official_real_estate(
            _artifact("moi", _moi_zip(_moi_row(), _moi_row(編號="RPOFFICIAL003"))),
            max_rows=1,
        )


def test_source_binding_and_checksum_fail_closed() -> None:
    source = SOURCES["moi"]
    with pytest.raises(OfficialRealEstateSourceError, match="dataset mismatch"):
        source.validate_binding(
            dataset_id="not-25119",
            license_id=GOVERNMENT_OPEN_DATA_LICENSE_V1,
        )
    with pytest.raises(OfficialRealEstateSourceError, match="license mismatch"):
        source.validate_binding(dataset_id="25119", license_id="unknown-license")

    content = _moi_zip(_moi_row())
    changed = replace(_artifact("moi", content), content_sha256="0" * 64)
    with pytest.raises(OfficialRealEstateSourceError, match="content_sha256"):
        parse_official_real_estate(changed)

    wrong_media = replace(_artifact("moi", content), content_type="text/csv")
    with pytest.raises(OfficialRealEstateSourceError, match="media type"):
        parse_official_real_estate(wrong_media)

    unapproved_source = replace(source, license_id="changed-license")
    unapproved = replace(_artifact("moi", content), source=unapproved_source)
    with pytest.raises(OfficialRealEstateSourceError, match="approved registry"):
        parse_official_real_estate(unapproved)


class _Response(io.BytesIO):
    def __init__(self, content: bytes, *, url: str, content_type: str) -> None:
        super().__init__(content)
        self._url = url
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(content)),
            "ETag": '"fixture"',
            "Last-Modified": "Tue, 21 Jul 2026 07:54:00 GMT",
        }

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def test_downloader_checks_url_media_type_size_and_checksum() -> None:
    content = _moi_zip(_moi_row())
    source = SOURCES["moi"]

    def opener(_request: Any, _timeout: float) -> _Response:
        return _Response(content, url=source.source_url, content_type=source.media_type)

    downloader = OfficialRealEstateDownloader(opener)
    artifact = downloader.download(
        source,
        max_bytes=len(content),
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )
    assert artifact.content_sha256 == hashlib.sha256(content).hexdigest()
    assert artifact.source_published_at == datetime(2026, 7, 21, 7, 54, tzinfo=UTC)

    with pytest.raises(OfficialRealEstateBoundExceeded):
        downloader.download(source, max_bytes=len(content) - 1)
    with pytest.raises(OfficialRealEstateSourceError, match="checksum mismatch"):
        downloader.download(source, expected_sha256="1" * 64)


class _OutcomeClient:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[Any, ...]]] = []

    @contextmanager
    def transaction(self) -> Any:
        yield self

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if params:
            assert sql.count("?") == len(params)
        self.executions.append((sql, params))

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        if "to_regclass" in sql:
            return {"relation": params[0]}
        return None

    def query(
        self,
        _sql: str,
        _params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        return []


def test_store_applies_atomic_upsert_with_raw_observation_provenance() -> None:
    batch = parse_official_real_estate(_artifact("moi", _moi_zip(_moi_row())))
    client = _OutcomeClient()

    result = OfficialRealEstateOutcomeStore(client).upsert(batch)

    assert result["status"] == "succeeded"
    assert result["parsed_row_count"] == 1
    assert result["projection_row_count"] == 1
    assert result["observation_row_count"] == 1
    assert result["inserted_row_count"] == 1
    assert result["updated_row_count"] == 0
    assert result["unchanged_row_count"] == 0
    assert result["stale_row_count"] == 0
    assert sum(
        "pg_advisory_xact_lock" in sql
        for sql, _params in client.executions
    ) == 2
    assert any(
        "real_estate_transaction_observations" in sql and "CAST(? AS JSONB)" in sql
        for sql, _params in client.executions
    )
