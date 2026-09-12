"""A snapshot must not record an object generation it never read back.

`object_generation` is the provenance anchor: downstream consumers resolve it
to fetch that exact object version, and a masked release snapshot binds to it.
Before ODP-SNAPSHOT-GENERATION-FAILCLOSED-001 the recovery path substituted a
literal `1` when the read-back failed:

    except Exception:
        raw_gen = 1

That value was then persisted as the snapshot's `object_generation` with
nothing marking it as invented, so the claim looked identical to a verified
one.

The branch it lived in is the Precondition-Failed recovery: an earlier attempt
already uploaded the object, so the upload is fine and only the read-back of
*which version* failed. That is exactly when a provenance record cannot be
honestly written, so the operation now refuses instead.
"""

from __future__ import annotations

import pytest

from modules.external_data.application.source_snapshots import (
    SnapshotProvenanceError,
    resolve_existing_object_generation,
)

TENANT = "tenant-a"
URI = "gs://bucket/tenants/tenant-a/snapshots/snap-1/raw"
SNAPSHOT = "snap-1"
REAL_GENERATION = 1739284400123456


class _UnreadableStore:
    """The object is there; reading its metadata fails."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def head_object(self, tenant_id: str, uri: str) -> dict[str, object]:
        self.calls += 1
        raise self.error


class _ReadableStore:
    def head_object(self, tenant_id: str, uri: str) -> dict[str, object]:
        return {"generation": REAL_GENERATION}


class _GenerationlessStore:
    """Metadata comes back, but without the field that matters."""

    def head_object(self, tenant_id: str, uri: str) -> dict[str, object]:
        return {"size": 1024, "contentType": "application/json"}


class TestGenerationIsNeverInvented:
    def test_unreadable_generation_refuses_rather_than_substituting(self) -> None:
        """The regression this module exists for.

        A substituted generation is worse than a failure: the failure is
        visible, the substitution is not.
        """
        store = _UnreadableStore(RuntimeError("transient metadata read failure"))
        with pytest.raises(SnapshotProvenanceError) as excinfo:
            resolve_existing_object_generation(store, TENANT, URI, SNAPSHOT)

        message = str(excinfo.value)
        assert "generation could not be read back" in message
        assert "provenance" in message
        assert store.calls == 1

    def test_missing_generation_field_is_also_refused(self) -> None:
        """A response without the field is as unusable as no response."""
        with pytest.raises(SnapshotProvenanceError):
            resolve_existing_object_generation(
                _GenerationlessStore(), TENANT, URI, SNAPSHOT
            )

    def test_the_failure_names_the_snapshot_and_the_object(self) -> None:
        """An operator has to know which object to go and inspect."""
        store = _UnreadableStore(RuntimeError("boom"))
        with pytest.raises(SnapshotProvenanceError) as excinfo:
            resolve_existing_object_generation(store, TENANT, URI, SNAPSHOT)
        message = str(excinfo.value)
        assert SNAPSHOT in message
        assert URI in message

    def test_the_original_failure_is_kept_as_the_cause(self) -> None:
        """Refusing must not discard why the read-back failed."""
        original = RuntimeError("transient metadata read failure")
        with pytest.raises(SnapshotProvenanceError) as excinfo:
            resolve_existing_object_generation(
                _UnreadableStore(original), TENANT, URI, SNAPSHOT
            )
        assert excinfo.value.__cause__ is original

    def test_a_readable_generation_is_returned_verbatim(self) -> None:
        """The change removes the substitute, not the recovery path."""
        assert (
            resolve_existing_object_generation(_ReadableStore(), TENANT, URI, SNAPSHOT)
            == REAL_GENERATION
        )


class TestNoLiteralFallbackRemains:
    def test_source_has_no_literal_generation_fallback(self) -> None:
        """`raw_gen = 1` read as ordinary defensive coding, which is how it
        survived. A future edit that restores any literal for this value should
        fail here rather than quietly reinstate an unverifiable claim."""
        from pathlib import Path

        import modules.external_data.application.source_snapshots as module

        source = "\n".join(
            line
            for line in Path(module.__file__).read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "raw_gen = 1" not in source
        assert "raw_gen = 0" not in source
