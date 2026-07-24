from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared.infrastructure.persistence import factory
from shared.infrastructure.persistence.factory import build_persistence


@pytest.mark.parametrize("mode", ["memory", "durable", "sqlite"])
def test_live_data_runtime_rejects_surrogate_persistence(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")

    with pytest.raises(RuntimeError, match="production PostgreSQL persistence"):
        build_persistence(mode=mode)


def test_postgres_mode_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)
    monkeypatch.delenv("ODAY_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="ODAY_DATABASE_URL is required"):
        build_persistence(mode="postgres")


def test_unknown_persistence_mode_never_falls_back_to_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)

    with pytest.raises(ValueError, match="Unsupported ODP_PERSISTENCE mode"):
        build_persistence(mode="postgresqlx")


def test_local_memory_mode_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ODP_REQUIRE_LIVE_DATA", raising=False)

    bundle = build_persistence(mode="memory")

    assert bundle.mode == "memory"
    assert bundle.is_durable is False


@pytest.mark.parametrize("mode", ["postgres", "postgresql"])
def test_live_data_gate_accepts_only_verified_postgresql_bundle(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    engine = SimpleNamespace(is_production=True, close=lambda: None)
    expected = SimpleNamespace(
        mode="postgresql",
        engine=engine,
        is_production=True,
    )
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv(
        "ODAY_DATABASE_URL",
        "postgresql://app:secret@db.example.test/oday",
    )
    captured_urls: list[str] = []

    def build(database_url: str, worm_sink=None):
        del worm_sink
        captured_urls.append(database_url)
        return expected

    monkeypatch.setattr(factory, "_postgres_bundle", build)

    bundle = build_persistence(mode=mode)

    assert bundle is expected
    assert captured_urls == ["postgresql://app:secret@db.example.test/oday"]


def test_postgresql_mode_never_falls_back_when_bundle_is_not_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []
    engine = SimpleNamespace(
        is_production=False,
        close=lambda: closed.append(True),
    )
    surrogate = SimpleNamespace(
        mode="postgresql",
        engine=engine,
        is_production=False,
    )
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv(
        "ODAY_DATABASE_URL",
        "postgresql://app:secret@db.example.test/oday",
    )
    monkeypatch.setattr(
        factory,
        "_postgres_bundle",
        lambda database_url, worm_sink=None: surrogate,
    )

    with pytest.raises(RuntimeError, match="verified production PostgreSQL"):
        build_persistence(mode="postgresql")

    assert closed == [True]
