from __future__ import annotations

import math
from datetime import UTC, datetime

from modules.priceops.domain.pricing import PriceElasticityEstimate

DEFAULT_ELASTICITY = -1.2
DEFAULT_CONFIDENCE = 0.5
MIN_SAMPLES = 5
MIN_PRICE_STD = 1e-4

def estimate_elasticity(
    data: list[dict[str, float]],
    current_price: float,
    prediction_origin_time: datetime | None = None,
) -> PriceElasticityEstimate:
    """Estimate price elasticity from historical (price, demand) data using log-log regression.
    
    Applies safety bounds on the estimated elasticity value and confidence scoring.
    """
    origin_time = prediction_origin_time or datetime.now(UTC)
    
    # Filter out invalid values (price and demand must be positive for log-log)
    valid_points = []
    for pt in data:
        p = pt.get("price", 0.0)
        q = pt.get("demand", 0.0)
        if p > 0 and q > 0:
            valid_points.append((p, q))
            
    n = len(valid_points)
    
    # Safety Check: insufficient data points
    if n < MIN_SAMPLES:
        return PriceElasticityEstimate(
            elasticity_value=DEFAULT_ELASTICITY,
            confidence=0.3,  # Low confidence due to small sample
            prediction_origin_time=origin_time,
        )
        
    log_prices = [math.log(p) for p, _ in valid_points]
    log_demands = [math.log(q) for _, q in valid_points]
    
    # Calculate means
    mean_x = sum(log_prices) / n
    mean_y = sum(log_demands) / n
    
    # Calculate variance and covariance
    var_x = sum((x - mean_x) ** 2 for x in log_prices) / n
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(log_prices, log_demands, strict=False)) / n
    
    # Safety Check: no price variation
    std_x = math.sqrt(var_x)
    if std_x < MIN_PRICE_STD:
        return PriceElasticityEstimate(
            elasticity_value=DEFAULT_ELASTICITY,
            confidence=0.1,  # extremely low confidence
            prediction_origin_time=origin_time,
        )
        
    # Calculate OLS slope (elasticity coefficient beta)
    beta = cov_xy / var_x
    
    # Calculate confidence based on sample size and fit (R-squared proxy)
    # R-squared proxy
    total_ss_y = sum((y - mean_y) ** 2 for y in log_demands)
    if total_ss_y > 1e-9:
        residual_ss = sum((log_demands[i] - (mean_y + beta * (log_prices[i] - mean_x))) ** 2 for i in range(n))
        r2 = max(0.0, 1.0 - (residual_ss / total_ss_y))
    else:
        r2 = 1.0
        
    # Confidence scales with sample size and R2
    sample_factor = min(1.0, n / 30.0)
    confidence = round(0.3 + 0.7 * r2 * sample_factor, 4)
    
    # Apply safety bounds on the elasticity coefficient
    # 1. Non-negative elasticity is physically invalid (demand increases with price)
    #    Cap at a slightly negative value and reduce confidence.
    if beta >= 0:
        return PriceElasticityEstimate(
            elasticity_value=-0.1,
            confidence=min(confidence, 0.2),  # penalize confidence
            prediction_origin_time=origin_time,
        )
        
    # 2. Extreme elasticity (extremely sensitive demand)
    #    If beta is less than -5.0, cap it at -3.0 and lower confidence.
    if beta < -5.0:
        return PriceElasticityEstimate(
            elasticity_value=-3.0,
            confidence=min(confidence, 0.4),  # penalize confidence due to extreme estimation
            prediction_origin_time=origin_time,
        )
        
    return PriceElasticityEstimate(
        elasticity_value=round(beta, 4),
        confidence=confidence,
        prediction_origin_time=origin_time,
    )
