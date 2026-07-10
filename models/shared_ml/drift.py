from __future__ import annotations

import math
from typing import Any

def calculate_psi(
    baseline: list[float],
    target: list[float],
    num_bins: int = 10,
) -> float:
    """Calculate the Population Stability Index (PSI) between baseline and target distributions.
    
    If numerical bins contain 0 count, standard smoothing (1e-4) is applied to avoid division by zero.
    """
    if not baseline or not target:
        return 0.0
        
    # Find min and max over both distributions to define bins
    all_values = baseline + target
    min_val = min(all_values)
    max_val = max(all_values)
    
    # Avoid zero division if all values are identical
    if abs(max_val - min_val) < 1e-9:
        return 0.0
        
    # Generate bin edges
    bin_width = (max_val - min_val) / num_bins
    bin_edges = [min_val + i * bin_width for i in range(num_bins + 1)]
    bin_edges[-1] = max_val + 1e-9  # extend last edge slightly to include maximum
    
    # Count frequencies
    baseline_counts = [0] * num_bins
    target_counts = [0] * num_bins
    
    for val in baseline:
        for i in range(num_bins):
            if bin_edges[i] <= val < bin_edges[i+1]:
                baseline_counts[i] += 1
                break
                
    for val in target:
        for i in range(num_bins):
            if bin_edges[i] <= val < bin_edges[i+1]:
                target_counts[i] += 1
                break
                
    total_baseline = len(baseline)
    total_target = len(target)
    
    psi_value = 0.0
    for b_count, t_count in zip(baseline_counts, target_counts):
        # Calculate percentages
        b_pct = b_count / total_baseline
        t_pct = t_count / total_target
        
        # Apply smoothing to avoid log(0) or division by zero
        b_pct = max(b_pct, 1e-4)
        t_pct = max(t_pct, 1e-4)
        
        psi_value += (t_pct - b_pct) * math.log(t_pct / b_pct)
        
    return round(psi_value, 6)

def monitor_drift(
    baseline: list[float],
    target: list[float],
    num_bins: int = 10,
    warning_threshold: float = 0.1,
    failed_threshold: float = 0.2,
) -> dict[str, Any]:
    """Evaluate drift status and return validation results.
    
    Status values:
    - PASSED: PSI < warning_threshold (0.1)
    - WARNING: warning_threshold <= PSI < failed_threshold (0.2)
    - FAILED: PSI >= failed_threshold (0.2)
    """
    psi = calculate_psi(baseline, target, num_bins)
    
    if psi < warning_threshold:
        status = "PASSED"
        message = f"Distribution is stable (PSI = {psi:.4f} < {warning_threshold})"
    elif psi < failed_threshold:
        status = "WARNING"
        message = f"Moderate distribution drift detected (PSI = {psi:.4f})"
    else:
        status = "FAILED"
        message = f"Significant distribution drift detected! Action required (PSI = {psi:.4f} >= {failed_threshold})"
        
    return {
        "drift_score": psi,
        "status": status,
        "message": message,
    }
