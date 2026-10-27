"""An object version and its bytes must support the recorded provenance."""

from hashlib import sha256

import pytest

from modules.external_data.application.source_snapshots import (
    SnapshotProvenanceError,
    resolve_existing_object_generation,
)


@pytest.mark.parametrize("generation", [0, -1, True, "1", None])
def test_invalid_generation_never_becomes_a_provenance_anchor(generation):
    class Store:
        def head_object(self, *_args):
            return {"generation": generation}

    with pytest.raises(SnapshotProvenanceError):
        resolve_existing_object_generation(Store(), "synthetic-tenant", "gs://synthetic/raw", "synthetic-snapshot")


def test_pins_download_to_the_expected_generation_and_actual_content():
    calls = []

    class Store:
        def head_object(self, *_args):
            return {"generation": 2, "sha256": "untrusted metadata"}

        def download_object(self, tenant, uri, generation):
            calls.append((tenant, uri, generation))
            return b"actual supplied bytes"

    assert resolve_existing_object_generation(
        Store(), "tenant", "gs://synthetic/raw", "snapshot",
        expected_sha256=sha256(b"actual supplied bytes").hexdigest(), expected_generation=2,
    ) == 2
    assert calls == [("tenant", "gs://synthetic/raw", 2)]


def test_expected_generation_mismatch_never_reads_another_version():
    class Store:
        def head_object(self, *_args):
            return {"generation": 3}

        def download_object(self, *_args, **_kwargs):
            pytest.fail("A replacement generation is not the recorded evidence")

    with pytest.raises(SnapshotProvenanceError):
        resolve_existing_object_generation(Store(), "tenant", "gs://synthetic/raw", "snapshot", expected_generation=2)


def test_read_error_cannot_become_verified_content():
    error = OSError("synthetic download unavailable")

    class Store:
        def head_object(self, *_args):
            return {"generation": 2}

        def download_object(self, *_args, **_kwargs):
            raise error

    with pytest.raises(SnapshotProvenanceError) as raised:
        resolve_existing_object_generation(
            Store(), "tenant", "gs://synthetic/raw", "snapshot", expected_sha256=sha256(b"data").hexdigest(),
        )
    assert raised.value.__cause__ is error
