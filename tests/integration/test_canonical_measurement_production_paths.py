"""Production-path coverage for the nullable canonical measurement cutover.

ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001

These cases deliberately do *not* assert on freshly constructed dataclasses.
A dataclass literal always carries the current class defaults, so it can never
observe the failure this cutover exists to prevent: a payload written *before*
the cutover reloading into the new class and silently adopting the new
``measurement_schema_version`` default, which relabels a substituted ``1.00``
as a genuine measurement.

Every case here therefore drives a real production path end to end:

* producer -> durable persistence -> process restart -> consumer/route, for
  each of the six canonical measured columns (or its documented live
  surrogate);
* a genuine pre-cutover serialized payload, built by stripping the
  post-cutover attributes from the instance *before* it is pickled, which is
  byte-identical to what the pre-cutover class produced;
* the PostgreSQL forward migration applied to a table holding pre-cutover
  rows, followed by execution of the model-ready geo view against it.
"""

from __future__ import annotations

import pickle
from datetime import UTC, datetime

import pytest

from apps.api.app.routes.listings import V1ListingRepositoryAdapter
from modules.heatzone.v3.contract import HeatZoneV3ScoreResult, HeatZoneV3State
from modules.listing.domain.models import ListingDedupKey
from modules.sitescore.application.reporting import SiteScoreReportService
from modules.sitescore.domain.scoring import SiteScoreFeatureInput
from shared.domain.models import (
    AddressLocation,
    CompetitorStore,
    DataSnapshot,
    HeatZoneScore,
    Listing,
    Poi,
    Prediction,
)
from shared.infrastructure.persistence import SqliteDocumentStore, SqliteEngine
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.model_ready import (
    DatasetSnapshotMaterializer,
    DocumentStoreLineageRecorder,
    LineageManifest,
)

# What each class gained in the cutover, and nothing else. ``snapshot_id`` is
# listed only for CompetitorStore: Poi and Listing have carried it since the
# baseline schema, so stripping it there would model a payload that never
# existed.
_MEASUREMENT = frozenset({"measurement_schema_version"})
POST_CUTOVER_FIELDS = {
    Poi: _MEASUREMENT | {"confidence_status"},
    CompetitorStore: _MEASUREMENT
    | {"confidence_status", "snapshot_id", "source_competitor_id"},
    Listing: _MEASUREMENT | {"confidence_status"},
    Prediction: _MEASUREMENT | {"confidence_status"},
    HeatZoneScore: _MEASUREMENT | {"confidence_status"},
    DataSnapshot: _MEASUREMENT | {"quality_score_status"},
    LineageManifest: _MEASUREMENT | {"quality_score_status"},
}


def as_pre_cutover(obj: object) -> object:
    """The aggregate as a pre-cutover process held it.

    Bypassing ``__init__`` and ``__setstate__`` on the way *in* is what makes
    this a genuine pre-cutover payload rather than a modern object with fields
    blanked out: the attributes are absent from ``__dict__`` entirely, so
    ``pickle.dumps`` omits them from the state exactly as the old class did.
    """
    dropped = POST_CUTOVER_FIELDS[type(obj)]
    shadow = type(obj).__new__(type(obj))
    shadow.__dict__.update(
        {k: v for k, v in obj.__dict__.items() if k not in dropped}
    )
    return shadow


def _reopened_store(tmp_path) -> tuple[SqliteEngine, SqliteDocumentStore]:
    engine = SqliteEngine(str(tmp_path / "documents.sqlite3"))
    return engine, SqliteDocumentStore(engine)


# ---------------------------------------------------------------------------
# 1. The durable path is pickle, not columns: the marker has to survive it.
# ---------------------------------------------------------------------------


class TestPreCutoverPayloadsReloadAsLegacyUnknown:
    """All six canonical models, through the real durable document store.

    ``SqliteDocumentStore`` persists whole pickled aggregates; the SQL columns
    added by migration 000026 are the analytics mirror, not the path the
    application reads back. These cases therefore write through ``put`` and
    read back through ``get`` after reopening the database file.
    """

    @pytest.mark.parametrize(
        ("model", "value_field", "effective", "provenance"),
        [
            (Poi, "confidence", "effective_confidence", "effective_confidence_provenance"),
            (
                CompetitorStore,
                "confidence",
                "effective_confidence",
                "effective_confidence_provenance",
            ),
            (Listing, "confidence", "effective_confidence", "effective_confidence_provenance"),
            (
                Prediction,
                "confidence",
                "effective_confidence",
                "effective_confidence_provenance",
            ),
            (
                HeatZoneScore,
                "confidence",
                "effective_confidence",
                "effective_confidence_provenance",
            ),
            (
                DataSnapshot,
                "quality_score",
                "effective_quality_score",
                "effective_quality_score_provenance",
            ),
        ],
    )
    def test_substituted_one_reloads_as_absent(
        self, tmp_path, model, value_field, effective, provenance
    ) -> None:
        engine = SqliteEngine(str(tmp_path / "documents.sqlite3"))
        try:
            store = SqliteDocumentStore(engine)
            store.put("canonical", "doc-1", as_pre_cutover(model(**{value_field: 1.0})))
        finally:
            engine.close()

        engine, store = _reopened_store(tmp_path)
        try:
            restored = store.get("canonical", "doc-1")
        finally:
            engine.close()

        assert getattr(restored, value_field) == 1.0, "the stored column is untouched"
        assert getattr(restored, provenance) == "legacy_unknown"
        assert getattr(restored, effective) is None

    @pytest.mark.parametrize(
        ("model", "value_field", "effective", "provenance"),
        [
            (Poi, "confidence", "effective_confidence", "effective_confidence_provenance"),
            (
                CompetitorStore,
                "confidence",
                "effective_confidence",
                "effective_confidence_provenance",
            ),
            (Listing, "confidence", "effective_confidence", "effective_confidence_provenance"),
            (
                Prediction,
                "confidence",
                "effective_confidence",
                "effective_confidence_provenance",
            ),
            (
                HeatZoneScore,
                "confidence",
                "effective_confidence",
                "effective_confidence_provenance",
            ),
            (
                DataSnapshot,
                "quality_score",
                "effective_quality_score",
                "effective_quality_score_provenance",
            ),
        ],
    )
    def test_measured_extremes_and_omission_round_trip(
        self, tmp_path, model, value_field, effective, provenance
    ) -> None:
        """Measured 0, measured 1, and an omitted value stay distinguishable.

        A measured zero is the case a substituted default cannot be allowed to
        imitate in either direction: it must not become ``None``, and absence
        must not become ``0.0``.
        """
        engine = SqliteEngine(str(tmp_path / "documents.sqlite3"))
        try:
            store = SqliteDocumentStore(engine)
            store.put("canonical", "measured-zero", model(**{value_field: 0.0}))
            store.put("canonical", "measured-one", model(**{value_field: 1.0}))
            store.put("canonical", "omitted", model())
            # A pre-cutover row that is not the ambiguous 1.00 was genuinely
            # measured, and stays measured.
            store.put(
                "canonical",
                "legacy-measured",
                as_pre_cutover(model(**{value_field: 0.85})),
            )
        finally:
            engine.close()

        engine, store = _reopened_store(tmp_path)
        try:
            measured_zero = store.get("canonical", "measured-zero")
            measured_one = store.get("canonical", "measured-one")
            omitted = store.get("canonical", "omitted")
            legacy_measured = store.get("canonical", "legacy-measured")
        finally:
            engine.close()

        assert getattr(measured_zero, effective) == 0.0
        assert getattr(measured_zero, provenance) == "measured"

        assert getattr(measured_one, effective) == 1.0
        assert getattr(measured_one, provenance) == "measured"

        assert getattr(omitted, value_field) is None
        assert getattr(omitted, effective) is None
        assert getattr(omitted, provenance) == "unmeasured"

        assert getattr(legacy_measured, effective) == 0.85
        assert getattr(legacy_measured, provenance) == "measured"

    def test_marker_survives_mutation_and_a_second_write(self, tmp_path) -> None:
        """A promoted / re-saved legacy row must not be restamped as current.

        Re-persisting a reloaded aggregate is the ordinary case (status
        updates, promotion). If the marker did not survive, the second write
        would bake the substituted 1.00 in as measured.
        """
        from dataclasses import replace

        engine = SqliteEngine(str(tmp_path / "documents.sqlite3"))
        try:
            store = SqliteDocumentStore(engine)
            store.put(
                "canonical",
                "doc-1",
                as_pre_cutover(Listing(listing_id="L-1", confidence=1.0)),
            )
            reloaded = store.get("canonical", "doc-1")
            store.put("canonical", "doc-1", replace(reloaded, listing_status="candidate"))
        finally:
            engine.close()

        engine, store = _reopened_store(tmp_path)
        try:
            twice_stored = store.get("canonical", "doc-1")
        finally:
            engine.close()

        assert twice_stored.listing_status == "candidate"
        assert twice_stored.measurement_schema_version == "v1"
        assert twice_stored.effective_confidence_provenance == "legacy_unknown"
        assert twice_stored.effective_confidence is None


# ---------------------------------------------------------------------------
# 2. Listing: producer -> durable repository -> restart -> route serializer
# ---------------------------------------------------------------------------


def _address() -> AddressLocation:
    return AddressLocation(
        address_id="ADDR-CUTOVER-1",
        raw_address="新北市板橋區府中路 26 號 1F",
        normalized_address="新北市板橋區府中路26號1樓",
        latitude=25.008,
        longitude=121.459,
        geocode_confidence=0.95,
        h3_res_9="8929a1d4d67ffff",
    )


def _dedup_key(listing: Listing, address: AddressLocation) -> ListingDedupKey:
    return ListingDedupKey(
        source_id=listing.source_id,
        source_listing_id=listing.source_listing_id,
        normalized_address=address.normalized_address,
        rent_amount=listing.rent_amount,
        area_ping=listing.area_ping,
    )


def _save_and_restart(db_path: str, listing: Listing) -> dict:
    """Persist through the durable repository, restart, serialize via the route."""
    address = _address()
    bundle = _durable_bundle(db_path)
    try:
        bundle.listing_repository.save_listing(
            listing, address, _dedup_key(listing, address)
        )
    finally:
        bundle.engine.close()

    reopened = _durable_bundle(db_path)
    try:
        return V1ListingRepositoryAdapter(reopened.listing_repository).get_listing(
            listing.listing_id
        )
    finally:
        reopened.engine.close()


class TestListingRouteSerializesAbsence:
    """The listings route adapter is called for real, not asserted around."""

    def _listing(self, **overrides) -> Listing:
        base = dict(
            listing_id="L-CUTOVER-1",
            source_listing_id="SRC-1",
            source_id="provider-a",
            address_id="ADDR-CUTOVER-1",
            rent_amount=54_000,
            area_ping=22,
            floor="1F",
            frontage_m=5,
        )
        base.update(overrides)
        return Listing(**base)

    def test_pre_cutover_listing_is_served_as_null_legacy_unknown(
        self, tmp_path
    ) -> None:
        db_path = str(tmp_path / "durable.sqlite3")
        address = _address()
        listing = self._listing(confidence=1.0)

        bundle = _durable_bundle(db_path)
        try:
            # Saved exactly as the pre-cutover process held it.
            bundle.listing_repository.save_listing(
                as_pre_cutover(listing), address, _dedup_key(listing, address)
            )
        finally:
            bundle.engine.close()

        reopened = _durable_bundle(db_path)
        try:
            served = V1ListingRepositoryAdapter(
                reopened.listing_repository
            ).get_listing(listing.listing_id)
        finally:
            reopened.engine.close()

        assert served["confidence"] is None
        assert served["confidenceProvenance"] == "legacy_unknown"

    def test_omitted_confidence_is_served_as_null_unmeasured(self, tmp_path) -> None:
        served = _save_and_restart(
            str(tmp_path / "durable.sqlite3"), self._listing()
        )
        assert served["confidence"] is None
        assert served["confidenceProvenance"] == "unmeasured"

    @pytest.mark.parametrize("measured", [0.0, 1.0])
    def test_measured_extremes_are_served_verbatim(self, tmp_path, measured) -> None:
        served = _save_and_restart(
            str(tmp_path / "durable.sqlite3"), self._listing(confidence=measured)
        )
        assert served["confidence"] == measured
        assert served["confidenceProvenance"] == "measured"


# ---------------------------------------------------------------------------
# 3. Prediction: SiteScore abstention must persist as NULL, not measured 0.0
# ---------------------------------------------------------------------------


def _feature(**overrides) -> SiteScoreFeatureInput:
    base = dict(
        candidate_site_id="CS-CUTOVER-1",
        target_format_code="ODAY_G2",
        feature_snapshot_time=datetime(2026, 9, 1, tzinfo=UTC),
        view_version="candidate-site-view-v2",
        monthly_rent=54_000.0,
        area_ping=22.0,
        comparable_store_count=3,
        average_confidence=0.9,
        data_quality_score=0.9,
        source_snapshot_ids=("snap-1",),
    )
    base.update(overrides)
    return SiteScoreFeatureInput(**base)


class TestSiteScorePersistsAbstentionAsNullPrediction:
    """The canonical ``Prediction`` producer boundary preserves absence."""

    def _predictions(self, feature: SiteScoreFeatureInput, tmp_path) -> list[Prediction]:
        db_path = str(tmp_path / "durable.sqlite3")
        bundle = _durable_bundle(db_path)
        try:
            service = SiteScoreReportService(
                repository=bundle.sitescore_repository,
                require_production_model=False,
                runtime_mode="poc",
            )
            reports = service.score_candidates([feature])
            assert reports
        finally:
            bundle.engine.close()

        reopened = _durable_bundle(db_path)
        try:
            # The report only knows its sitescore_run_id; the prediction run it
            # fed is resolved through the persisted SiteScoreRun, which is the
            # same hop the API takes.
            run_ids = {
                reopened.sitescore_repository.get_sitescore_run(
                    report.sitescore_run_id
                ).prediction_run_id
                for report in reports
            }
            predictions: list[Prediction] = []
            for run_id in run_ids:
                predictions.extend(
                    reopened.sitescore_repository.get_predictions(run_id)
                )
            return predictions
        finally:
            reopened.engine.close()

    def test_missing_source_confidence_persists_null_and_unmeasured(
        self, tmp_path
    ) -> None:
        predictions = self._predictions(_feature(average_confidence=None), tmp_path)

        assert predictions
        for prediction in predictions:
            assert prediction.confidence is None, (
                "an abstaining score must not persist as a measured 0.0"
            )
            assert prediction.effective_confidence is None
            assert prediction.effective_confidence_provenance == "unmeasured"

    def test_missing_data_quality_score_persists_null_and_unmeasured(
        self, tmp_path
    ) -> None:
        predictions = self._predictions(_feature(data_quality_score=None), tmp_path)

        assert predictions
        for prediction in predictions:
            assert prediction.confidence is None
            assert prediction.effective_confidence_provenance == "unmeasured"

    def test_measured_confidence_persists_verbatim(self, tmp_path) -> None:
        predictions = self._predictions(_feature(), tmp_path)

        assert predictions
        for prediction in predictions:
            assert prediction.confidence is not None
            assert prediction.effective_confidence == prediction.confidence
            assert prediction.effective_confidence_provenance == "measured"


# ---------------------------------------------------------------------------
# 4. DataSnapshot: the model-ready materializer is its production writer
# ---------------------------------------------------------------------------


def _model_ready_row(
    quality: float | None, entity: str = "cell-1", *, confidence: float | None = 0.9
) -> dict:
    moment = datetime(2026, 9, 1, tzinfo=UTC)
    return {
        "view_name": "geo_grid_view",
        "view_version": "v1",
        "entity_id": entity,
        "feature_snapshot_time": moment,
        "prediction_origin_time": moment,
        "source_snapshot_ids": ("snap-geo-1",),
        "data_quality_score": quality,
        "confidence": confidence,
        "is_training_eligible": True,
        "is_scoring_eligible": True,
        "exclusion_reason": "",
    }


class TestModelReadyMaterializerWritesDataSnapshot:
    """The sixth column has a real writer, and it survives a restart."""

    def _audit_row(self, tmp_path, rows) -> dict:
        db_path = str(tmp_path / "lineage.sqlite3")
        engine = SqliteEngine(db_path)
        try:
            materializer = DatasetSnapshotMaterializer(
                _InMemorySnapshotSink(),
                DocumentStoreLineageRecorder(SqliteDocumentStore(engine)),
            )
            result = materializer.materialize(rows, run_id="run-cutover-1")
            snapshot_id = result.snapshot.dataset_snapshot_id
        finally:
            engine.close()

        engine = SqliteEngine(db_path)
        try:
            recorder = DocumentStoreLineageRecorder(SqliteDocumentStore(engine))
            return recorder.get(snapshot_id).to_audit_snapshot_row()
        finally:
            engine.close()

    def test_measured_perfect_score_is_not_discarded_as_legacy(self, tmp_path) -> None:
        """A genuinely measured 1.0 must survive, whatever the dataset version.

        ``data_quality_score`` is 1.0 for every row of the geo model-ready
        dataset, so treating "dataset schema v1 plus quality 1.0" as legacy
        would erase the default path rather than a corner case.
        """
        row = self._audit_row(tmp_path, [_model_ready_row(1.0)])

        assert row["quality_score"] == 1.0
        assert row["quality_score_status"] == "measured"
        assert row["schema_version"] == "v1", "the dataset layout version is unchanged"
        assert row["measurement_schema_version"] == "v2"

    @pytest.mark.parametrize(
        "row", [_model_ready_row(None), _model_ready_row(1.0, confidence=None)]
    )
    def test_an_unmeasured_input_is_rejected_rather_than_defaulted(
        self, tmp_path, row
    ) -> None:
        """Absent quality fails admission; it is never substituted with 1.0.

        This is the "reject" half of abstain / mark / reject: the materializer
        refuses the row instead of persisting a snapshot whose quality header
        claims a measurement nobody took.
        """
        from modules.learninghub.domain.dataset_snapshot import (
            DatasetQualityAdmissionError,
        )

        with pytest.raises(DatasetQualityAdmissionError):
            self._audit_row(tmp_path, [row])

    def test_measured_zero_is_preserved(self, tmp_path) -> None:
        row = self._audit_row(tmp_path, [_model_ready_row(0.0)])

        assert row["quality_score"] == 0.0
        assert row["quality_score_status"] == "measured"

    def test_pre_cutover_manifest_reloads_as_legacy_unknown(self, tmp_path) -> None:
        """A manifest pickled before the cutover carries no marker either."""
        db_path = str(tmp_path / "lineage.sqlite3")
        engine = SqliteEngine(db_path)
        try:
            store = SqliteDocumentStore(engine)
            materializer = DatasetSnapshotMaterializer(
                _InMemorySnapshotSink(), DocumentStoreLineageRecorder(store)
            )
            result = materializer.materialize(
                [_model_ready_row(1.0)], run_id="run-cutover-1"
            )
            snapshot_id = result.snapshot.dataset_snapshot_id
            store.put(
                "learninghub.dataset_lineage",
                snapshot_id,
                as_pre_cutover(result.lineage),
            )
        finally:
            engine.close()

        engine = SqliteEngine(db_path)
        try:
            recorder = DocumentStoreLineageRecorder(SqliteDocumentStore(engine))
            row = recorder.get(snapshot_id).to_audit_snapshot_row()
        finally:
            engine.close()

        assert row["measurement_schema_version"] == "v1"
        assert row["quality_score"] is None
        assert row["quality_score_status"] == "legacy_unknown"


class _InMemorySnapshotSink:
    def __init__(self) -> None:
        self._snapshots: dict = {}

    def save_dataset_snapshot(self, snapshot):
        self._snapshots[snapshot.dataset_snapshot_id] = snapshot
        return snapshot

    def get_dataset_snapshot(self, dataset_snapshot_id: str):
        return self._snapshots.get(dataset_snapshot_id)


# ---------------------------------------------------------------------------
# 5. HeatZoneScore: the live v3 surrogate serializes through the canonical model
# ---------------------------------------------------------------------------


def _v3_result(confidence: float | None, *, abstained: bool = False) -> HeatZoneV3ScoreResult:
    return HeatZoneV3ScoreResult(
        heat_zone_id="HZ-1",
        h3_index="8929a1d4d67ffff",
        h3_resolution=9,
        score=42.0,
        priority_rank=1,
        unmet_demand_score=0.5,
        format_fit_score=0.5,
        competitor_pressure_score=0.5,
        cannibalization_risk_score=0.5,
        rent_feasibility_score=0.5,
        listing_availability_score=0.5,
        housing_density_score=0.5,
        demographic_vitality_score=0.5,
        confidence=confidence,
        state=HeatZoneV3State.UNTOUCHED,
        abstained=abstained,
        abstain_reasons=("missing_inputs",) if abstained else (),
    )


class TestHeatZoneSerializesThroughCanonicalScore:
    def test_abstained_result_publishes_null_not_a_substituted_one(self) -> None:
        payload = _v3_result(None, abstained=True).to_dict()

        assert payload["confidence"] is None
        assert payload["confidence_provenance"] == "unmeasured"

    def test_measured_extremes_are_published_verbatim(self) -> None:
        for measured in (0.0, 1.0):
            payload = _v3_result(measured).to_dict()
            assert payload["confidence"] == measured
            assert payload["confidence_provenance"] == "measured"

    def test_pre_cutover_payload_round_trips_as_legacy_unknown(self) -> None:
        """The canonical score a reloaded legacy v3 result produces."""
        legacy_canonical = pickle.loads(
            pickle.dumps(as_pre_cutover(HeatZoneScore(confidence=1.0)))
        )

        assert legacy_canonical.effective_confidence is None
        assert legacy_canonical.effective_confidence_provenance == "legacy_unknown"

    def test_map_feature_carries_the_same_resolution(self) -> None:
        feature = _v3_result(None, abstained=True).to_map_feature()

        assert feature["properties"]["confidence"] is None
        assert feature["properties"]["confidence_provenance"] == "unmeasured"


# The Python half of the Poi / CompetitorStore aggregation contract lives in
# tests/domain/test_canonical_measurement_nullable.py
# (test_geo_pipeline_aggregation_emits_none_on_absent_and_mixed_buckets); its
# SQL counterpart, which is where PostgreSQL's NULL-skipping avg() had to be
# guarded, is in tests/integration/test_canonical_measurement_postgresql.py.
