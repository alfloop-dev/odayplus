from __future__ import annotations

import math
from collections.abc import Callable


def calculate_mape(actuals: list[float], predictions: list[float]) -> float:
    """Calculate Mean Absolute Percentage Error (MAPE)."""
    valid_count = 0
    total_abs_pct_error = 0.0
    for act, pred in zip(actuals, predictions, strict=False):
        if abs(act) > 1e-9:
            total_abs_pct_error += abs((act - pred) / act)
            valid_count += 1
    return total_abs_pct_error / valid_count if valid_count > 0 else 0.0

def calculate_rmse(actuals: list[float], predictions: list[float]) -> float:
    """Calculate Root Mean Squared Error (RMSE)."""
    n = len(actuals)
    if n == 0:
        return 0.0
    mse = sum((act - pred) ** 2 for act, pred in zip(actuals, predictions, strict=False)) / n
    return math.sqrt(mse)

def calculate_mae(actuals: list[float], predictions: list[float]) -> float:
    """Calculate Mean Absolute Error (MAE)."""
    n = len(actuals)
    if n == 0:
        return 0.0
    return sum(abs(act - pred) for act, pred in zip(actuals, predictions, strict=False)) / n

def run_rolling_backtest(
    model_predict_fn: Callable[[list[float], int], list[float]],
    series: list[float],
    horizons: list[int],
    min_train_size: int,
    step_size: int = 1,
) -> dict[int, dict[str, float]]:
    """Execute rolling-origin backtesting over a single time series.
    
    Args:
        model_predict_fn: A function that takes (history: list[float], horizon: int) 
                         and returns a list of H predictions.
        series: The full historical time series of float values.
        horizons: List of horizons (e.g. [4, 8, 12, 24]) to calculate metrics for.
        min_train_size: The minimum size of the history to start making predictions.
        step_size: How many steps to roll forward the origin.
        
    Returns:
        A dictionary mapping horizon (int) to a metrics dict containing 'mape', 'rmse', 'mae'.
    """
    if not series or not horizons:
        return {}
        
    max_h = max(horizons)
    n_points = len(series)
    
    # Store predictions and actuals per horizon
    # Key: horizon, Value: (actuals, predictions)
    horizon_data: dict[int, tuple[list[float], list[float]]] = {
        h: ([], []) for h in horizons
    }
    
    # Rolling origin loop
    # The origin is the last index of the available training data
    origin = min_train_size
    while origin + max_h <= n_points:
        history = series[:origin]
        
        # Predict up to max horizon
        predictions = model_predict_fn(history, max_h)
        
        # Collect predictions for each horizon
        for h in horizons:
            pred_val = predictions[h - 1]
            act_val = series[origin + h - 1]
            horizon_data[h][0].append(act_val)
            horizon_data[h][1].append(pred_val)
            
        origin += step_size
        
    # Calculate metrics for each horizon
    results = {}
    for h in horizons:
        actuals, predictions = horizon_data[h]
        if not actuals:
            continue
            
        results[h] = {
            "mape": round(calculate_mape(actuals, predictions), 6),
            "rmse": round(calculate_rmse(actuals, predictions), 6),
            "mae": round(calculate_mae(actuals, predictions), 6),
        }
        
    return results
