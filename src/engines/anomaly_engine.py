"""
Multivariate Statistical & Anomaly Detection Engine
Works on ANY client parameter set (electronics SMT, cleanroom HVAC, hydraulics, quarry crushers).
"""

from typing import Dict, Any, List, Optional
import numpy as np


def detect_multivariate_anomalies(
    current_signals: Dict[str, float],
    historical_samples: List[Dict[str, float]],
    z_threshold: float = 3.0
) -> List[Dict[str, Any]]:
    """
    Calculates deviation across all available numeric signals.
    Identifies which specific parameters have departed from their operating baseline.
    """
    anomalies = []
    if len(historical_samples) < 10:
        return anomalies

    for key, current_val in current_signals.items():
        past_values = [s[key] for s in historical_samples if key in s and s[key] is not None]
        if len(past_values) < 8:
            continue
        
        arr = np.array(past_values, dtype=float)
        mean = float(np.mean(arr))
        std = float(np.std(arr))

        if std > 1e-4:
            z_score = abs(current_val - mean) / std
            if z_score >= z_threshold:
                direction = "ELEVATED" if current_val > mean else "DEPRESSED"
                anomalies.append({
                    "parameter": key,
                    "current_value": round(current_val, 3),
                    "baseline_mean": round(mean, 3),
                    "baseline_std": round(std, 3),
                    "z_score": round(z_score, 2),
                    "anomaly_type": f"{direction}_DEVIATION"
                })

    return anomalies
