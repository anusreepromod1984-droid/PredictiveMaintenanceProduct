"""
Weibull Survival & Prognostics RUL Engine
Calculates Remaining Useful Life in operating hours and days based on degradation trajectory.
"""

import math
from typing import Dict, Any, Optional
from src.schemas.prediction import PrognosticRUL


def estimate_rul(
    current_health_score: float,
    vibration_rms: float,
    threshold_danger: float,
    nominal_baseline: float = 2.0,
    base_lifespan_hours: float = 12000.0,
    operating_hours_per_day: float = 16.0
) -> PrognosticRUL:
    """
    Calculates Remaining Useful Life (RUL) using Weibull Hazard degradation rate.
    """
    # Normalized degradation ratio: 0.0 (baseline) to 1.0 (danger threshold)
    delta_total = max(0.1, threshold_danger - nominal_baseline)
    excess_vib = max(0.0, vibration_rms - nominal_baseline)
    deg_ratio = min(1.0, excess_vib / delta_total)

    # Weibull cumulative hazard shape (beta=2.2 for mechanical fatigue wearout)
    beta = 2.2
    survival_prob = math.exp(- (deg_ratio ** beta))
    
    # Calculate RUL
    remaining_fraction = max(0.01, (current_health_score / 100.0) * survival_prob)
    rul_hours = round(remaining_fraction * (base_lifespan_hours * 0.25), 1)
    rul_days = round(rul_hours / operating_hours_per_day, 1)

    confidence = round(max(50.0, min(95.0, 95.0 - (deg_ratio * 30.0))), 1)
    slope = round(0.05 + (deg_ratio * 0.45), 3)

    if rul_days < 7.0:
        window = "CRITICAL: Schedule maintenance within next 48-72 hours"
    elif rul_days < 30.0:
        window = "WARNING: Schedule inspection during next planned weekend shift"
    else:
        window = "NORMAL: Routine inspection on next planned PM interval"

    return PrognosticRUL(
        rul_hours=rul_hours,
        rul_days=rul_days,
        confidence_pct=confidence,
        degradation_slope_per_hour=slope,
        method="weibull_hazard",
        projected_maintenance_window=window
    )
