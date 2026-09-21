# Statistical routines adapted from Evidently 0.7.21 (Apache-2.0).
# Upstream: https://github.com/evidentlyai/evidently
# Retained license and adaptation details:
# docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/EVIDENTLY-LICENSE.txt
# docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/remediation.md
"""Native drift statistics core for released-model monitoring.

This module is a first-party re-implementation of the drift statistics the
LearningHub monitor needs. It deliberately does **not** import, call or load
Evidently or NLTK, and it has no runtime fallback to either engine; everything
is computed from the numerical stack the project already depends on (numpy,
pandas, scipy and scikit-learn).

The behaviour reproduced here was derived by inspecting and executing the
pinned ``evidently==0.7.21`` distribution that production currently runs
(``DataDriftPreset`` -> ``DriftedColumnsCount`` + ``ValueDrift``). The captured
reference and the case-by-case comparison live in
``docs/evidence/completion/ODP-DRIFT-NATIVE-MIGRATION-001/``.

Two independent evaluation passes are reproduced because the reference engine
runs two of them and they do not always agree:

* the **value-drift pass** produces one ``ValueDrift`` metric per
  numerical/categorical/text column and always strips ``+/-inf`` and ``NaN``
  before testing;
* the **counted pass** produces ``DriftedColumnsCount`` and only strips a
  column when that column actually contains ``NaN`` -- a column holding
  ``inf`` but no ``NaN`` reaches the statistical test uncleaned.

Provenance is reported honestly: the emitted report is marked as produced by
this native engine and never claims to have been computed by Evidently.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial import distance
from scipy.stats import chisquare, ks_2samp, norm
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

ENGINE_NAME = "native_drift"
ENGINE_VERSION = "1"
METRIC_TYPE_PREFIX = "native_drift:metric_v2:"

#: An ``int`` column with at most this many distinct values is treated as
#: categorical rather than numerical.
INTEGER_CARDINALITY_LIMIT = 10
#: A string column whose distinct-value count exceeds this fraction of its
#: non-null count is treated as free text rather than categorical.
TEXT_CARDINALITY_RATIO = 0.5
#: Reference samples of at most this many (cleaned) rows use the small-sample
#: test family; larger ones use the distance family.
SMALL_SAMPLE_ROW_LIMIT = 1000
#: Numerical columns with more distinct reference values than this are binned
#: by histogram instead of by value for the Jensen-Shannon distance.
BINNING_UNIQUE_LIMIT = 20
#: Bin count requested for Jensen-Shannon binning (kept for parity; the
#: histogram path derives its own edges with the Sturges rule).
JENSEN_SHANNON_BINS = 30


class NativeDriftError(ValueError):
    """Base class for native drift configuration and input errors."""


class StatTestNotFoundError(NativeDriftError):
    def __init__(self, name: str) -> None:
        super().__init__(f"No drift statistical test named {name!r} is registered")
        self.name = name


class StatTestInvalidColumnTypeError(NativeDriftError):
    def __init__(self, name: str, column_type: NativeColumnType) -> None:
        super().__init__(
            f"Drift statistical test {name!r} does not support column type {column_type.value!r}"
        )
        self.name = name
        self.column_type = column_type


class NativeColumnType(StrEnum):
    """Column kinds recognised by the native engine."""

    NUMERICAL = "num"
    CATEGORICAL = "cat"
    TEXT = "text"
    DATETIME = "datetime"
    UNKNOWN = "unknown"
    LIST = "list"


#: Column kinds that take part in drift evaluation at all.
DRIFT_COLUMN_TYPES = (
    NativeColumnType.NUMERICAL,
    NativeColumnType.CATEGORICAL,
    NativeColumnType.TEXT,
)


def infer_column_type(column_data: pd.Series) -> NativeColumnType:
    """Classify a column the way the released monitor's engine classifies it."""

    dtype_name = column_data.dtype.name
    if dtype_name.startswith("float"):
        return NativeColumnType.NUMERICAL
    if dtype_name.startswith("int"):
        if column_data.nunique() <= INTEGER_CARDINALITY_LIMIT:
            return NativeColumnType.CATEGORICAL
        return NativeColumnType.NUMERICAL
    if dtype_name in ("string", "str"):
        if column_data.nunique() > (column_data.count() * TEXT_CARDINALITY_RATIO):
            return NativeColumnType.TEXT
        return NativeColumnType.CATEGORICAL
    if dtype_name == "object":
        without_na = column_data.dropna()
        if without_na.count() == 0:
            return NativeColumnType.UNKNOWN
        if isinstance(without_na.iloc[0], str) and isinstance(without_na.iloc[-1], str):
            if column_data.nunique() > (column_data.count() * TEXT_CARDINALITY_RATIO):
                return NativeColumnType.TEXT
            return NativeColumnType.CATEGORICAL
        if isinstance(without_na.iloc[0], (list, tuple)) and isinstance(
            without_na.iloc[-1], (list, tuple)
        ):
            return NativeColumnType.LIST
        return NativeColumnType.UNKNOWN
    if dtype_name in ("bool", "category"):
        return NativeColumnType.CATEGORICAL
    if dtype_name.startswith("datetime"):
        return NativeColumnType.DATETIME
    return NativeColumnType.UNKNOWN


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def unique_not_nan_values(reference_data: pd.Series, current_data: pd.Series) -> list:
    """Distinct non-null values observed across both samples."""

    return list(set(reference_data.dropna().unique()) | set(current_data.dropna().unique()))


def clean_column(column: pd.Series) -> pd.Series:
    """Replace infinities with nulls and drop every null value."""

    return column.replace([-np.inf, np.inf], np.nan).dropna()


def binned_distributions(
    reference_data: pd.Series,
    current_data: pd.Series,
    column_type: NativeColumnType,
    n_bins: int = JENSEN_SHANNON_BINS,
) -> tuple[np.ndarray, np.ndarray]:
    """Bucket both samples into aligned distributions expressed as fractions.

    High-cardinality numerical columns are binned on Sturges histogram edges
    derived from the concatenated sample; everything else is bucketed by
    distinct value. Empty buckets are left at zero -- the drift tests that use
    this helper do not fill them.
    """

    n_values = reference_data.nunique()
    if column_type == NativeColumnType.NUMERICAL and n_values > BINNING_UNIQUE_LIMIT:
        combined = np.asarray(pd.concat([reference_data, current_data], axis=0).values)
        bins = np.histogram_bin_edges(combined, bins="sturges")
        reference_percents = np.histogram(reference_data, bins)[0] / len(reference_data)
        current_percents = np.histogram(current_data, bins)[0] / len(current_data)
        return reference_percents, current_percents

    keys = unique_not_nan_values(reference_data, current_data)
    reference_counts = {**dict.fromkeys(keys, 0), **dict(reference_data.value_counts())}
    current_counts = {**dict.fromkeys(keys, 0), **dict(current_data.value_counts())}
    reference_percents = np.array([reference_counts[key] / len(reference_data) for key in keys])
    current_percents = np.array([current_counts[key] / len(current_data) for key in keys])
    return reference_percents, current_percents


# --------------------------------------------------------------------------
# Statistical tests
# --------------------------------------------------------------------------


def kolmogorov_smirnov(
    reference_data: pd.Series,
    current_data: pd.Series,
    column_type: NativeColumnType,
    threshold: float,
) -> tuple[float, bool]:
    """Two-sample two-sided Kolmogorov-Smirnov test; returns its p-value.

    Drift is reported when the p-value is *at or below* the threshold. The
    inclusive bound is deliberate and differs from the chi-square and Z tests.
    """

    p_value = float(ks_2samp(reference_data, current_data)[1])
    return p_value, bool(p_value <= threshold)


def chi_square(
    reference_data: pd.Series,
    current_data: pd.Series,
    column_type: NativeColumnType,
    threshold: float,
) -> tuple[float, bool]:
    """Chi-square goodness-of-fit test of current counts against scaled
    reference counts; returns its p-value.

    The reference counts are rescaled by ``len(current) / len(reference)`` so
    that both vectors sum to the current sample size. Categories seen only in
    the current sample keep an expected frequency of zero, which drives the
    p-value to zero.
    """

    keys = unique_not_nan_values(reference_data, current_data)
    reference_counts = {**dict.fromkeys(keys, 0), **dict(reference_data.value_counts())}
    current_counts = {**dict.fromkeys(keys, 0), **dict(current_data.value_counts())}
    k_norm = current_data.shape[0] / reference_data.shape[0]
    f_exp = [reference_counts[key] * k_norm for key in keys]
    f_obs = [current_counts[key] for key in keys]
    p_value = float(chisquare(f_obs, f_exp)[1])
    return p_value, bool(p_value < threshold)


def _proportions_diff_z_stat_ind(reference_data: pd.Series, current_data: pd.Series) -> float:
    n1 = len(reference_data)
    n2 = len(current_data)
    p1 = float(sum(reference_data)) / n1
    p2 = float(sum(current_data)) / n2
    pooled = float(p1 * n1 + p2 * n2) / (n1 + n2)
    return (p1 - p2) / np.sqrt(pooled * (1 - pooled) * (1.0 / n1 + 1.0 / n2))


def _proportions_diff_z_test(z_stat: float, alternative: str = "two-sided") -> float:
    if alternative == "two-sided":
        return 2 * (1 - norm.cdf(np.abs(z_stat)))
    if alternative == "less":
        return norm.cdf(z_stat)
    if alternative == "greater":
        return 1 - norm.cdf(z_stat)
    raise NativeDriftError("alternative should be 'two-sided', 'less' or 'greater'")


def z_test(
    reference_data: pd.Series,
    current_data: pd.Series,
    column_type: NativeColumnType,
    threshold: float,
) -> tuple[float, bool]:
    """Two-proportion Z test over the binarised column; returns its p-value.

    Both samples are mapped to ``0`` for the lowest observed value and ``1``
    for everything else. Two constant samples holding the same value
    short-circuit to a p-value of exactly ``1``.
    """

    if (
        reference_data.nunique() == 1
        and current_data.nunique() == 1
        and reference_data.unique()[0] == current_data.unique()[0]
    ):
        p_value = 1
    else:
        keys = sorted(unique_not_nan_values(reference_data, current_data))
        p_value = _proportions_diff_z_test(
            _proportions_diff_z_stat_ind(
                reference_data.apply(lambda x, key=keys[0]: 0 if x == key else 1),
                current_data.apply(lambda x, key=keys[0]: 0 if x == key else 1),
            )
        )
    return float(p_value), bool(p_value < threshold)


def wasserstein_distance_normed(
    reference_data: pd.Series,
    current_data: pd.Series,
    column_type: NativeColumnType,
    threshold: float,
) -> tuple[float, bool]:
    """First Wasserstein distance normalised by the reference standard
    deviation; returns a distance.

    The normaliser is floored at ``0.001`` so a constant reference sample
    cannot divide by zero. Drift is reported when the distance is at or above
    the threshold.
    """

    norm_value = max(float(np.std(reference_data)), 0.001)
    value = float(stats.wasserstein_distance(reference_data, current_data) / norm_value)
    return value, bool(value >= threshold)


def jensen_shannon(
    reference_data: pd.Series,
    current_data: pd.Series,
    column_type: NativeColumnType,
    threshold: float,
    n_bins: int = JENSEN_SHANNON_BINS,
    base: float | None = None,
) -> tuple[float, bool]:
    """Jensen-Shannon distance between the two bucketed distributions.

    Drift is reported when the distance is at or above the threshold.
    """

    reference_percents, current_percents = binned_distributions(
        reference_data, current_data, column_type, n_bins
    )
    value = float(distance.jensenshannon(reference_percents, current_percents, base))
    return value, bool(value >= threshold)


def _domain_classifier_roc_auc(x_train, x_test, y_train, y_test) -> float:
    pipeline = Pipeline(
        [
            ("vectorization", TfidfVectorizer(sublinear_tf=True, max_df=0.5, stop_words="english")),
            (
                "classification",
                SGDClassifier(
                    alpha=0.0001,
                    max_iter=50,
                    penalty="l1",
                    loss="modified_huber",
                    random_state=42,
                ),
            ),
        ]
    )
    pipeline.fit(x_train, y_train)
    y_pred_proba = pipeline.predict_proba(x_test)[:, 1]
    return roc_auc_score(y_test, y_pred_proba)


def _random_classifier_roc_auc_percentile(
    y_test: np.ndarray, p_value: float = 0.05, iter_num: int = 1000, seed: int = 42
) -> float:
    def roc_auc_for_seed(sample_seed: int) -> float:
        np.random.seed(sample_seed)
        return roc_auc_score(y_test, np.random.rand(len(y_test)))

    np.random.seed(seed)
    seeds = np.random.randint(0, iter_num * 10, size=iter_num)
    return np.percentile([roc_auc_for_seed(s) for s in seeds], 100 * (1 - p_value))


def text_domain_classifier_score(
    reference_data: pd.Series,
    current_data: pd.Series,
    *,
    bootstrap: bool,
    p_value: float = 0.05,
    threshold: float = 0.55,
) -> tuple[float, bool]:
    """Domain-classifier drift score for free-text columns.

    A TF-IDF + SGD classifier is trained to tell reference rows from current
    rows; its held-out ROC AUC is the drift score. With ``bootstrap`` the score
    is compared against the percentile a random classifier would reach on the
    same labels, otherwise against ``threshold`` directly.

    The vectoriser uses scikit-learn's built-in English stop-word list; no
    NLTK corpus, tokeniser or download is involved.
    """

    domain_data = pd.concat(
        [
            pd.DataFrame({"text": reference_data, "target": 0}),
            pd.DataFrame({"text": current_data, "target": 1}),
        ]
    )
    x_train, x_test, y_train, y_test = train_test_split(
        domain_data["text"],
        domain_data["target"],
        test_size=0.5,
        random_state=42,
        shuffle=True,
    )
    roc_auc = float(_domain_classifier_roc_auc(x_train, x_test, y_train, y_test))
    if not bootstrap:
        return roc_auc, bool(roc_auc > threshold)
    percentile = _random_classifier_roc_auc_percentile(y_test, p_value=p_value)
    return roc_auc, bool(roc_auc > percentile)


def percentile_text_content_drift(
    reference_data: pd.Series,
    current_data: pd.Series,
    column_type: NativeColumnType,
    threshold: float,
) -> tuple[float, bool]:
    """Text drift judged against a bootstrapped random-classifier percentile."""

    return text_domain_classifier_score(
        reference_data, current_data, bootstrap=True, p_value=1 - threshold
    )


def absolute_text_content_drift(
    reference_data: pd.Series,
    current_data: pd.Series,
    column_type: NativeColumnType,
    threshold: float,
) -> tuple[float, bool]:
    """Text drift judged against an absolute ROC AUC threshold."""

    return text_domain_classifier_score(
        reference_data, current_data, bootstrap=False, threshold=threshold
    )


StatTestFunc = Callable[[pd.Series, pd.Series, "NativeColumnType", float], tuple[float, bool]]


@dataclass(frozen=True)
class NativeStatTest:
    """One registered drift statistical test."""

    name: str
    display_name: str
    allowed_column_types: tuple[NativeColumnType, ...]
    default_threshold: float
    #: How ``drift_score`` is compared with the threshold. ``bootstrap`` means
    #: the threshold is not the comparison bound at all.
    comparison: str
    func: StatTestFunc

    def evaluate(
        self,
        reference_data: pd.Series,
        current_data: pd.Series,
        column_type: NativeColumnType,
        threshold: float | None,
    ) -> tuple[float, bool, float]:
        actual_threshold = self.default_threshold if threshold is None else threshold
        score, drifted = self.func(reference_data, current_data, column_type, actual_threshold)
        return float(score), bool(drifted), actual_threshold


KS_STAT_TEST = NativeStatTest(
    name="ks",
    display_name="K-S p_value",
    allowed_column_types=(NativeColumnType.NUMERICAL,),
    default_threshold=0.05,
    comparison="p_value<=threshold",
    func=kolmogorov_smirnov,
)
CHI_SQUARE_STAT_TEST = NativeStatTest(
    name="chisquare",
    display_name="chi-square p_value",
    allowed_column_types=(NativeColumnType.CATEGORICAL,),
    default_threshold=0.05,
    comparison="p_value<threshold",
    func=chi_square,
)
Z_STAT_TEST = NativeStatTest(
    name="z",
    display_name="Z-test p_value",
    allowed_column_types=(NativeColumnType.CATEGORICAL,),
    default_threshold=0.05,
    comparison="p_value<threshold",
    func=z_test,
)
WASSERSTEIN_STAT_TEST = NativeStatTest(
    name="wasserstein",
    display_name="Wasserstein distance (normed)",
    allowed_column_types=(NativeColumnType.NUMERICAL,),
    default_threshold=0.1,
    comparison="distance>=threshold",
    func=wasserstein_distance_normed,
)
JENSEN_SHANNON_STAT_TEST = NativeStatTest(
    name="jensenshannon",
    display_name="Jensen-Shannon distance",
    allowed_column_types=(NativeColumnType.CATEGORICAL, NativeColumnType.NUMERICAL),
    default_threshold=0.1,
    comparison="distance>=threshold",
    func=jensen_shannon,
)
PERCENTILE_TEXT_STAT_TEST = NativeStatTest(
    name="perc_text_content_drift",
    display_name="Percentile text content drift",
    allowed_column_types=(NativeColumnType.TEXT,),
    default_threshold=0.95,
    comparison="bootstrap",
    func=percentile_text_content_drift,
)
ABSOLUTE_TEXT_STAT_TEST = NativeStatTest(
    name="abs_text_content_drift",
    display_name="Absolute text content drift",
    allowed_column_types=(NativeColumnType.TEXT,),
    default_threshold=0.55,
    comparison="distance>threshold",
    func=absolute_text_content_drift,
)

STAT_TESTS: dict[str, NativeStatTest] = {
    test.name: test
    for test in (
        KS_STAT_TEST,
        CHI_SQUARE_STAT_TEST,
        Z_STAT_TEST,
        WASSERSTEIN_STAT_TEST,
        JENSEN_SHANNON_STAT_TEST,
        PERCENTILE_TEXT_STAT_TEST,
        ABSOLUTE_TEXT_STAT_TEST,
    )
}


def get_stat_test(name: str, column_type: NativeColumnType) -> NativeStatTest:
    """Look up a registered test by name and check it accepts the column type."""

    stat_test = STAT_TESTS.get(name)
    if stat_test is None:
        raise StatTestNotFoundError(name)
    if column_type not in stat_test.allowed_column_types:
        raise StatTestInvalidColumnTypeError(name, column_type)
    return stat_test


def default_stat_test(
    reference_data: pd.Series,
    current_data: pd.Series,
    column_type: NativeColumnType,
) -> NativeStatTest:
    """Pick the drift test for a column from its type, size and cardinality.

    ``reference_data`` and ``current_data`` must already be cleaned, because
    the row-count branch is taken on the cleaned reference length.
    """

    n_values = pd.concat([reference_data, current_data]).nunique()
    if column_type == NativeColumnType.TEXT:
        if reference_data.shape[0] > SMALL_SAMPLE_ROW_LIMIT:
            return ABSOLUTE_TEXT_STAT_TEST
        return PERCENTILE_TEXT_STAT_TEST
    if reference_data.shape[0] <= SMALL_SAMPLE_ROW_LIMIT:
        if column_type == NativeColumnType.NUMERICAL:
            if n_values <= 5:
                return CHI_SQUARE_STAT_TEST if n_values > 2 else Z_STAT_TEST
            return KS_STAT_TEST
        if column_type == NativeColumnType.CATEGORICAL:
            return CHI_SQUARE_STAT_TEST if n_values > 2 else Z_STAT_TEST
    else:
        if column_type == NativeColumnType.NUMERICAL:
            if n_values <= 5:
                return JENSEN_SHANNON_STAT_TEST
            return WASSERSTEIN_STAT_TEST
        if column_type == NativeColumnType.CATEGORICAL:
            return JENSEN_SHANNON_STAT_TEST
    raise NativeDriftError(f"Unexpected column type {column_type.value!r}")


# --------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NativeDriftOptions:
    """Method and threshold selection, resolved per column.

    Precedence is per-column override, then column-type override, then the
    all-column setting, then the chosen test's own default.
    """

    drift_share: float = 0.5
    columns: Sequence[str] | None = None
    method: str | None = None
    cat_method: str | None = None
    num_method: str | None = None
    text_method: str | None = None
    per_column_method: Mapping[str, str] | None = None
    threshold: float | None = None
    cat_threshold: float | None = None
    num_threshold: float | None = None
    text_threshold: float | None = None
    per_column_threshold: Mapping[str, float] | None = None

    def method_for(self, column_name: str, column_type: NativeColumnType) -> str | None:
        selected = self.method
        by_type = {
            NativeColumnType.CATEGORICAL: self.cat_method,
            NativeColumnType.NUMERICAL: self.num_method,
            NativeColumnType.TEXT: self.text_method,
        }.get(column_type)
        if by_type is None and column_type not in DRIFT_COLUMN_TYPES:
            raise NativeDriftError(f"Unexpected column type {column_type.value!r}")
        if by_type is not None:
            selected = by_type
        if self.per_column_method is None:
            return selected
        return self.per_column_method.get(column_name, selected)

    def threshold_for(self, column_name: str, column_type: NativeColumnType) -> float | None:
        if self.per_column_threshold is not None and column_name in self.per_column_threshold:
            return self.per_column_threshold[column_name]
        by_type = {
            NativeColumnType.CATEGORICAL: self.cat_threshold,
            NativeColumnType.NUMERICAL: self.num_threshold,
            NativeColumnType.TEXT: self.text_threshold,
        }.get(column_type)
        if by_type is not None:
            return by_type
        return self.threshold


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NativeColumnDrift:
    """Drift outcome for one column."""

    column_name: str
    column_type: NativeColumnType
    stattest_name: str
    stattest_display_name: str
    stattest_threshold: float
    drift_score: float
    drift_detected: bool
    #: ``True`` when the caller pinned the method instead of auto-resolving it.
    method_was_explicit: bool = False
    #: ``True`` when the caller pinned the threshold instead of taking the
    #: test default.
    threshold_was_explicit: bool = False

    @property
    def report_method(self) -> str:
        """Method token as it appears in the serialized report.

        A caller-pinned method is echoed verbatim; an auto-resolved one is
        rendered with the test's display name.
        """

        return self.stattest_name if self.method_was_explicit else self.stattest_display_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "column_name": self.column_name,
            "column_type": self.column_type.value,
            "stattest_name": self.stattest_name,
            "stattest_display_name": self.stattest_display_name,
            "stattest_threshold": self.stattest_threshold,
            "drift_score": self.drift_score,
            "drift_detected": self.drift_detected,
        }


def _render_metric_params(params: Sequence[tuple[str, Any]]) -> str:
    rendered = []
    for name, value in params:
        if value is None or isinstance(value, Mapping):
            continue
        if isinstance(value, (list, tuple)):
            if len(value) > 0:
                rendered.append(f"{name}={','.join(str(item) for item in value)}")
            continue
        rendered.append(f"{name}={value}")
    return ",".join(rendered)


def _metric_config(metric_type: str, params: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    config: dict[str, Any] = {"type": f"{METRIC_TYPE_PREFIX}{metric_type}"}
    for name, value in params:
        if value is None:
            continue
        config[name] = dict(value) if isinstance(value, Mapping) else value
    return config


def _metric_id(metric_name: str) -> str:
    return sha256(metric_name.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class NativeDriftEvaluation:
    """Result of one native drift evaluation over a reference/current pair.

    ``value_drift`` holds the per-column outcomes; ``counted_columns`` holds
    the outcomes the dataset-level count and share are computed from. The two
    lists usually coincide, but they are produced by separate passes and can
    legitimately disagree -- see the module docstring.
    """

    drift_share_threshold: float
    number_of_columns: int
    number_of_drifted_columns: int
    share_of_drifted_columns: float
    dataset_drift: bool
    value_drift: tuple[NativeColumnDrift, ...]
    counted_columns: tuple[NativeColumnDrift, ...]
    options: NativeDriftOptions = field(default_factory=NativeDriftOptions)
    engine: str = ENGINE_NAME
    engine_version: str = ENGINE_VERSION

    @property
    def drifted_column_names(self) -> tuple[str, ...]:
        """Columns the per-column pass reported as drifting, in report order."""

        return tuple(result.column_name for result in self.value_drift if result.drift_detected)

    def column(self, column_name: str) -> NativeColumnDrift:
        for result in self.value_drift:
            if result.column_name == column_name:
                return result
        raise KeyError(column_name)

    def _dataset_metric(self) -> dict[str, Any]:
        options = self.options
        params: list[tuple[str, Any]] = [
            ("columns", list(options.columns) if options.columns is not None else None),
            ("drift_share", self.drift_share_threshold),
            ("method", options.method),
            ("cat_method", options.cat_method),
            ("num_method", options.num_method),
            ("text_method", options.text_method),
            ("per_column_method", options.per_column_method),
            ("threshold", options.threshold),
            ("cat_threshold", options.cat_threshold),
            ("num_threshold", options.num_threshold),
            ("text_threshold", options.text_threshold),
            ("per_column_threshold", options.per_column_threshold),
        ]
        metric_name = f"DriftedColumnsCount({_render_metric_params(params)})"
        return {
            "id": _metric_id(metric_name),
            "metric_name": metric_name,
            "config": _metric_config("DriftedColumnsCount", params),
            "value": {
                "count": float(self.number_of_drifted_columns),
                "share": float(self.share_of_drifted_columns),
            },
        }

    def _value_drift_metric(self, result: NativeColumnDrift) -> dict[str, Any]:
        explicit: list[tuple[str, Any]] = [("column", result.column_name)]
        resolved: list[tuple[str, Any]] = []
        (explicit if result.method_was_explicit else resolved).append(
            ("method", result.report_method)
        )
        (explicit if result.threshold_was_explicit else resolved).append(
            ("threshold", result.stattest_threshold)
        )
        params = explicit + resolved
        metric_name = f"ValueDrift({_render_metric_params(params)})"
        return {
            "id": _metric_id(metric_name),
            "metric_name": metric_name,
            "config": _metric_config("ValueDrift", params),
            "value": result.drift_score,
        }

    def dict(self) -> dict[str, Any]:
        """Serializable report, shaped like the released monitor's payload.

        ``metrics`` carries one ``DriftedColumnsCount`` entry followed by one
        ``ValueDrift`` entry per evaluated column, in the same order and with
        the same ``metric_name`` grammar the current consumers parse. The
        ``engine`` keys and the ``config.type`` prefix identify this native
        engine as the producer.
        """

        return {
            "engine": self.engine,
            "engine_version": self.engine_version,
            "metrics": [
                self._dataset_metric(),
                *(self._value_drift_metric(result) for result in self.value_drift),
            ],
            "tests": [],
        }

    def json(self) -> str:
        return json.dumps(self.dict())

    def to_dict(self) -> dict[str, Any]:
        """Typed summary of the evaluation for callers that want the numbers."""

        return {
            "engine": self.engine,
            "engine_version": self.engine_version,
            "drift_share_threshold": self.drift_share_threshold,
            "number_of_columns": self.number_of_columns,
            "number_of_drifted_columns": self.number_of_drifted_columns,
            "share_of_drifted_columns": self.share_of_drifted_columns,
            "dataset_drift": self.dataset_drift,
            "drifted_column_names": list(self.drifted_column_names),
            "value_drift": [result.to_dict() for result in self.value_drift],
            "counted_columns": [result.to_dict() for result in self.counted_columns],
        }


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


def _column_type_map(frame: pd.DataFrame) -> dict[str, NativeColumnType]:
    return {str(column): infer_column_type(frame[column]) for column in frame.columns}


def _columns_of_type(
    types: Mapping[str, NativeColumnType], wanted: NativeColumnType
) -> list[str]:
    return [column for column, column_type in types.items() if column_type == wanted]


class NativeDriftEngine:
    """Native replacement for the released ``DataDriftPreset`` evaluation.

    ``run(current, reference)`` mirrors the argument order of the engine it
    replaces so the call site does not have to be rewritten, and the returned
    object exposes ``dict()``/``json()`` with the same report shape.
    """

    def __init__(
        self,
        *,
        drift_share: float = 0.5,
        columns: Sequence[str] | None = None,
        method: str | None = None,
        cat_method: str | None = None,
        num_method: str | None = None,
        text_method: str | None = None,
        per_column_method: Mapping[str, str] | None = None,
        threshold: float | None = None,
        cat_threshold: float | None = None,
        num_threshold: float | None = None,
        text_threshold: float | None = None,
        per_column_threshold: Mapping[str, float] | None = None,
    ) -> None:
        self.options = NativeDriftOptions(
            drift_share=drift_share,
            columns=tuple(columns) if columns is not None else None,
            method=method,
            cat_method=cat_method,
            num_method=num_method,
            text_method=text_method,
            per_column_method=dict(per_column_method) if per_column_method else per_column_method,
            threshold=threshold,
            cat_threshold=cat_threshold,
            num_threshold=num_threshold,
            text_threshold=text_threshold,
            per_column_threshold=(
                dict(per_column_threshold) if per_column_threshold else per_column_threshold
            ),
        )

    # -- column-level evaluation -------------------------------------------

    def _evaluate_column(
        self,
        *,
        reference_column: pd.Series,
        current_column: pd.Series,
        column_name: str,
        column_type: NativeColumnType,
    ) -> NativeColumnDrift:
        if reference_column.empty:
            raise NativeDriftError(
                f"An empty column {column_name!r} was provided for drift calculation "
                "in the reference dataset."
            )
        if current_column.empty:
            raise NativeDriftError(
                f"An empty column {column_name!r} was provided for drift calculation "
                "in the current dataset."
            )
        if column_type == NativeColumnType.NUMERICAL:
            if not pd.api.types.is_numeric_dtype(reference_column):
                raise NativeDriftError(
                    f"Column {column_name!r} in reference dataset should contain numerical values only."
                )
            if not pd.api.types.is_numeric_dtype(current_column):
                raise NativeDriftError(
                    f"Column {column_name!r} in current dataset should contain numerical values only."
                )

        requested_method = self.options.method_for(column_name, column_type)
        requested_threshold = self.options.threshold_for(column_name, column_type)
        if requested_method is None:
            stat_test = default_stat_test(reference_column, current_column, column_type)
        else:
            stat_test = get_stat_test(requested_method, column_type)
        score, drifted, actual_threshold = stat_test.evaluate(
            reference_column, current_column, column_type, requested_threshold
        )
        return NativeColumnDrift(
            column_name=column_name,
            column_type=column_type,
            stattest_name=requested_method if requested_method is not None else stat_test.name,
            stattest_display_name=stat_test.display_name,
            stattest_threshold=actual_threshold,
            drift_score=score,
            drift_detected=drifted,
            method_was_explicit=requested_method is not None,
            threshold_was_explicit=requested_threshold is not None,
        )

    # -- column selection ---------------------------------------------------

    def _value_drift_columns(self, types: Mapping[str, NativeColumnType]) -> list[str]:
        """Columns that get a per-column metric, grouped numeric/cat/text."""

        if self.options.columns is not None:
            return list(self.options.columns)
        return [
            column
            for wanted in DRIFT_COLUMN_TYPES
            for column in _columns_of_type(types, wanted)
        ]

    def _counted_columns(
        self,
        types: Mapping[str, NativeColumnType],
        reference: pd.DataFrame,
    ) -> list[str]:
        """Columns the dataset count and share are computed over.

        The numerical group is re-filtered against the *reference* frame's
        dtypes (plus any column that is entirely null there), which is why a
        column that is numeric in the current frame but not in the reference
        frame drops out of the denominator entirely.
        """

        if self.options.columns is not None:
            return list(self.options.columns)

        numerical = [
            column
            for column in _columns_of_type(types, NativeColumnType.NUMERICAL)
            if column in reference.columns
        ]
        empty_columns = reference[numerical].isnull().mean()
        empty_columns = empty_columns[empty_columns == 1.0].index.to_series()
        numerical = sorted(
            set(reference[numerical].select_dtypes([np.number]).columns) | set(empty_columns)
        )
        categorical = reference[
            _columns_of_type(types, NativeColumnType.CATEGORICAL)
        ].columns.tolist()
        text = _columns_of_type(types, NativeColumnType.TEXT)
        return numerical + categorical + text

    # -- entry point --------------------------------------------------------

    def run(self, current: pd.DataFrame, reference: pd.DataFrame) -> NativeDriftEvaluation:
        """Evaluate drift of ``current`` against ``reference``."""

        if not isinstance(current, pd.DataFrame) or not isinstance(reference, pd.DataFrame):
            raise NativeDriftError("native drift evaluation requires pandas DataFrames")

        types = _column_type_map(current)
        for column in self._value_drift_columns(types):
            if column not in current.columns:
                raise NativeDriftError(f"Cannot find column {column!r} in current dataset")
            if column not in reference.columns:
                raise NativeDriftError(f"Cannot find column {column!r} in reference dataset")

        # The counted pass runs first because the released engine emits
        # DriftedColumnsCount as its first metric, so when both passes would
        # fail it is the counted pass whose error surfaces.
        counted_names = self._counted_columns(types, reference)
        if not counted_names:
            raise NativeDriftError(
                "no drift-eligible columns remain after column typing; "
                "the drifted-column share has no denominator"
            )
        reference_has_nans = reference.isna().any()
        current_has_nans = current.isna().any()
        counted = tuple(
            self._evaluate_column(
                reference_column=(
                    clean_column(reference[column])
                    if bool(reference_has_nans[column])
                    else reference[column]
                ),
                current_column=(
                    clean_column(current[column])
                    if bool(current_has_nans[column])
                    else current[column]
                ),
                column_name=column,
                column_type=types[column],
            )
            for column in counted_names
        )

        value_drift = tuple(
            self._evaluate_column(
                reference_column=clean_column(reference[column]),
                current_column=clean_column(current[column]),
                column_name=column,
                column_type=types[column],
            )
            for column in self._value_drift_columns(types)
        )

        drifted = sum(1 for result in counted if result.drift_detected)
        share = drifted / len(counted)
        return NativeDriftEvaluation(
            drift_share_threshold=self.options.drift_share,
            number_of_columns=len(counted),
            number_of_drifted_columns=drifted,
            share_of_drifted_columns=share,
            dataset_drift=bool(share >= self.options.drift_share),
            value_drift=value_drift,
            counted_columns=counted,
            options=self.options,
        )


def evaluate_drift(
    *,
    reference: pd.DataFrame,
    current: pd.DataFrame,
    drift_share_threshold: float = 0.5,
    **options: Any,
) -> NativeDriftEvaluation:
    """Evaluate drift between two DataFrames.

    Keyword-only convenience wrapper around :class:`NativeDriftEngine` that
    takes the reference frame first, matching how the monitor names its
    arguments.
    """

    return NativeDriftEngine(drift_share=drift_share_threshold, **options).run(current, reference)


__all__ = [
    "ABSOLUTE_TEXT_STAT_TEST",
    "BINNING_UNIQUE_LIMIT",
    "CHI_SQUARE_STAT_TEST",
    "ENGINE_NAME",
    "ENGINE_VERSION",
    "INTEGER_CARDINALITY_LIMIT",
    "JENSEN_SHANNON_STAT_TEST",
    "KS_STAT_TEST",
    "METRIC_TYPE_PREFIX",
    "PERCENTILE_TEXT_STAT_TEST",
    "SMALL_SAMPLE_ROW_LIMIT",
    "STAT_TESTS",
    "TEXT_CARDINALITY_RATIO",
    "WASSERSTEIN_STAT_TEST",
    "Z_STAT_TEST",
    "NativeColumnDrift",
    "NativeColumnType",
    "NativeDriftEngine",
    "NativeDriftError",
    "NativeDriftEvaluation",
    "NativeDriftOptions",
    "NativeStatTest",
    "StatTestInvalidColumnTypeError",
    "StatTestNotFoundError",
    "absolute_text_content_drift",
    "binned_distributions",
    "chi_square",
    "clean_column",
    "default_stat_test",
    "evaluate_drift",
    "get_stat_test",
    "infer_column_type",
    "jensen_shannon",
    "kolmogorov_smirnov",
    "percentile_text_content_drift",
    "text_domain_classifier_score",
    "unique_not_nan_values",
    "wasserstein_distance_normed",
    "z_test",
]
