"""
ISO Vibration Severity Standards Engine
Evaluates vibration RMS according to ISO 20816-3 (Rotary) and ISO 10816-6 (Vibrating/Reciprocating).
"""

from typing import Dict, Any


def evaluate_iso_20816_3(vib_velocity_rms: float, power_kw: float = 75.0, foundation: str = "flexible") -> Dict[str, Any]:
    """
    ISO 20816-3 standard for industrial rotating machines.
    Zone A: Newly commissioned (<2.3 mm/s)
    Zone B: Unrestricted continuous operation (2.3 - 4.5 mm/s)
    Zone C: Restricted operation / Warning (4.5 - 7.1 mm/s)
    Zone D: Damage occurs / Critical (>7.1 mm/s)
    """
    if vib_velocity_rms < 2.3:
        return {"zone": "A", "severity": "GOOD", "health_score": 100.0, "description": "Newly commissioned state"}
    elif vib_velocity_rms < 4.5:
        score = 100.0 - ((vib_velocity_rms - 2.3) / (4.5 - 2.3)) * 25.0
        return {"zone": "B", "severity": "ACCEPTABLE", "health_score": round(score, 1), "description": "Unrestricted continuous operation"}
    elif vib_velocity_rms < 7.1:
        score = 75.0 - ((vib_velocity_rms - 4.5) / (7.1 - 4.5)) * 35.0
        return {"zone": "C", "severity": "WARNING", "health_score": round(score, 1), "description": "Restricted operation - initiate maintenance planning"}
    else:
        score = max(5.0, 40.0 - min(35.0, (vib_velocity_rms - 7.1) * 5.0))
        return {"zone": "D", "severity": "CRITICAL", "health_score": round(score, 1), "description": "Dangerous vibration - immediate action required"}


def evaluate_iso_10816_6(vib_velocity_rms: float, machine_class: int = 5) -> Dict[str, Any]:
    """
    ISO 10816-6 for heavy vibrating machinery (e.g. Propel vibrating screens & crushers).
    Vibrating machines have naturally higher broadband baselines.
    """
    # For Class 5/6 (heavy vibrating screens/crushers), warning is typically 11.2 mm/s, danger 18.0 mm/s
    if vib_velocity_rms < 7.1:
        return {"zone": "A/B", "severity": "GOOD", "health_score": 95.0, "description": "Normal vibrating equipment operation"}
    elif vib_velocity_rms < 11.2:
        score = 85.0 - ((vib_velocity_rms - 7.1) / (11.2 - 7.1)) * 20.0
        return {"zone": "B/C", "severity": "ACCEPTABLE", "health_score": round(score, 1), "description": "Satisfactory continuous operation"}
    elif vib_velocity_rms < 18.0:
        score = 65.0 - ((vib_velocity_rms - 11.2) / (18.0 - 11.2)) * 30.0
        return {"zone": "C", "severity": "WARNING", "health_score": round(score, 1), "description": "Increased structural/bearing fatigue"}
    else:
        score = max(5.0, 35.0 - min(30.0, (vib_velocity_rms - 18.0) * 3.0))
        return {"zone": "D", "severity": "CRITICAL", "health_score": round(score, 1), "description": "Severe shock vibration - impending mechanical breakdown"}
