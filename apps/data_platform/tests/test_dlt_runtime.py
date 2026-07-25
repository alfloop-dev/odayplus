from __future__ import annotations

from types import SimpleNamespace

from apps.data_platform.config import DataPlaneConfig
from apps.data_platform.contracts import SourceKind
from apps.data_platform.dlt_runtime import DltRawLoader


def _config() -> DataPlaneConfig:
    return DataPlaneConfig(
        mongo_uri="mongodb+srv://service:secret@approved.example/data",
        postgres_dsn="postgresql://service:secret@sql.example/oday",
    )


def test_dlt_raw_resource_is_content_addressed_merge(
    envelope_factory, monkeypatch
) -> None:
    loader = DltRawLoader(_config())
    captured: dict[str, object] = {}

    class Pipeline:
        def run(self, resource):
            captured["resource"] = resource
            return SimpleNamespace(loads_ids=("package-1",))

    monkeypatch.setattr(
        loader._dlt.destinations,
        "postgres",
        lambda **kwargs: SimpleNamespace(credentials=kwargs["credentials"]),
    )
    monkeypatch.setattr(
        loader._dlt,
        "pipeline",
        lambda **kwargs: Pipeline(),
    )
    envelope = envelope_factory(
        SourceKind.MERCHANT,
        {
            "_id": "merchant-1",
            "companyName": "Merchant",
            "country": "TW",
            "currency": "TWD",
            "operation": "active",
            "createdAt": "2026-07-23T00:00:00Z",
        },
    )
    result = loader.load(SourceKind.MERCHANT, (envelope,))
    resource = captured["resource"]
    assert result.loaded_count == 1
    assert result.load_package_ids == ("package-1",)
    assert resource.name == "raw_merchant"
    assert resource.write_disposition == "merge"
    schema = resource.compute_table_schema()
    assert schema["columns"]["source_snapshot_id"]["primary_key"] is True
    assert resource.max_table_nesting == 0
