from __future__ import annotations

import math
from datetime import UTC, datetime

from models.priceops.elasticity import estimate_elasticity
from models.shared_ml.backtest import run_rolling_backtest
from models.shared_ml.drift import monitor_drift

MOMENT = datetime(2026, 6, 28, 9, 0, tzinfo=UTC)

# =====================================================================
# 1. Price Elasticity Estimator Tests
# =====================================================================

def test_elasticity_estimator_normal_regression() -> None:
    # y = log(q) = 5.0 - 1.5 * log(p)
    # elasticity should be -1.5
    data = []
    prices = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    for p in prices:
        # q = e^(5.0) * p^(-1.5)
        q = math.exp(5.0) * (p ** -1.5)
        data.append({"price": p, "demand": q})
        
    estimate = estimate_elasticity(data, current_price=4.0, prediction_origin_time=MOMENT)
    
    assert abs(estimate.elasticity_value - (-1.5)) < 1e-3
    assert estimate.confidence > 0.4  # Confidence is bound by sample size (n=6)

def test_elasticity_estimator_small_sample_falls_back() -> None:
    # Only 3 samples (min is 5)
    data = [
        {"price": 4.0, "demand": 100.0},
        {"price": 4.5, "demand": 90.0},
        {"price": 5.0, "demand": 80.0},
    ]
    estimate = estimate_elasticity(data, current_price=4.0, prediction_origin_time=MOMENT)
    
    assert estimate.elasticity_value == -1.2  # default fallback
    assert estimate.confidence == 0.3         # low confidence due to sample size

def test_elasticity_estimator_no_variance_falls_back() -> None:
    # 6 samples but all prices are identical
    data = [{"price": 4.0, "demand": 100.0} for _ in range(6)]
    estimate = estimate_elasticity(data, current_price=4.0, prediction_origin_time=MOMENT)
    
    assert estimate.elasticity_value == -1.2  # default fallback
    assert estimate.confidence == 0.1         # extremely low confidence

def test_elasticity_estimator_positive_truncated() -> None:
    # Positive elasticity: price up, demand up (e.g. Veblen goods, or noise)
    # y = log(q) = 1.0 + 1.0 * log(p)
    data = []
    prices = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    for p in prices:
        q = math.exp(1.0) * (p ** 1.0)
        data.append({"price": p, "demand": q})
        
    estimate = estimate_elasticity(data, current_price=4.0, prediction_origin_time=MOMENT)
    
    assert estimate.elasticity_value == -0.1  # truncated to -0.1
    assert estimate.confidence <= 0.2         # penalized confidence

def test_elasticity_estimator_extreme_negative_truncated() -> None:
    # Extremely negative elasticity: beta = -6.0
    data = []
    prices = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    for p in prices:
        q = math.exp(10.0) * (p ** -6.0)
        data.append({"price": p, "demand": q})
        
    estimate = estimate_elasticity(data, current_price=4.0, prediction_origin_time=MOMENT)
    
    assert estimate.elasticity_value == -3.0  # truncated to -3.0
    assert estimate.confidence <= 0.4         # penalized confidence

# =====================================================================
# 2. Backtest Engine Tests
# =====================================================================

def test_backtest_engine_calculates_correct_metrics() -> None:
    # A simple forecasting function: always predicts a flat trend equal to the mean of last 3 points
    def mock_predict_fn(history: list[float], horizon: int) -> list[float]:
        val = sum(history[-3:]) / min(len(history), 3)
        return [val] * horizon
        
    # Generate actual time series data: slightly noisy sine wave around 100
    series = [100.0 + 10.0 * math.sin(i * 0.5) for i in range(50)]
    
    horizons = [4, 8]
    results = run_rolling_backtest(
        model_predict_fn=mock_predict_fn,
        series=series,
        horizons=horizons,
        min_train_size=15,
        step_size=1,
    )
    
    # Results should contain both horizons
    assert 4 in results
    assert 8 in results
    
    # Verify metrics structure
    for h in horizons:
        metrics = results[h]
        assert "mape" in metrics
        assert "rmse" in metrics
        assert "mae" in metrics
        assert metrics["mape"] >= 0.0
        assert metrics["rmse"] >= 0.0
        assert metrics["mae"] >= 0.0
        
        # Reasonableness check: errors should be bounded
        assert metrics["mape"] < 0.20  # MAPE should be under 20% for this smooth series
        assert metrics["mae"] < 20.0

# =====================================================================
# 3. Drift Monitor Tests
# =====================================================================

def test_drift_monitor_detects_passed_status() -> None:
    # Baseline and target are from the same distribution
    import random
    random.seed(42)
    baseline = [random.normalvariate(100.0, 15.0) for _ in range(500)]
    target = [random.normalvariate(100.0, 15.0) for _ in range(500)]
    
    results = monitor_drift(baseline, target, num_bins=10)
    
    assert results["status"] == "PASSED"
    assert results["drift_score"] < 0.1

def test_drift_monitor_detects_failed_status() -> None:
    # Target distribution is significantly shifted (mean shifted from 100 to 115)
    import random
    random.seed(42)
    baseline = [random.normalvariate(100.0, 15.0) for _ in range(500)]
    target = [random.normalvariate(115.0, 15.0) for _ in range(500)]
    
    results = monitor_drift(baseline, target, num_bins=10)
    
    assert results["status"] == "FAILED"
    assert results["drift_score"] >= 0.2
