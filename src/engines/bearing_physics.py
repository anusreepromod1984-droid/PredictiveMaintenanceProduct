"""
Rotational Kinematics & Bearing Analysis Engine
Computes statistical vibration metrics and evaluates bearing health.

Production Upgrade (v3.0):
  - Integrates with spectral_analysis.py for FFT + Hilbert envelope demodulation
  - Returns a unified BearingHealthReport combining time-domain + frequency-domain results
"""

import math
from typing import Dict, Any, List, Optional
import numpy as np


def compute_waveform_metrics(waveform: List[float]) -> Dict[str, float]:
    """Calculates RMS, Peak-to-Peak, Crest Factor, and Kurtosis from raw time-series."""
    if not waveform or len(waveform) < 16:
        return {"rms": 0.0, "peak_to_peak": 0.0, "crest_factor": 0.0, "kurtosis": 3.0}

    arr = np.array(waveform, dtype=float)
    mean = np.mean(arr)
    centered = arr - mean

    rms = float(np.sqrt(np.mean(centered ** 2)))
    p2p = float(np.ptp(arr))
    peak = float(np.max(np.abs(centered)))
    crest_factor = float(peak / (rms + 1e-9))

    std = np.std(centered)
    if std > 1e-9:
        kurtosis = float(np.mean((centered / std) ** 4))
    else:
        kurtosis = 3.0

    return {
        "rms": round(rms, 4),
        "peak_to_peak": round(p2p, 4),
        "crest_factor": round(crest_factor, 2),
        "kurtosis": round(kurtosis, 2)
    }


def calculate_bearing_frequencies(rpm: float, bearing_code: str = "SKF-6206") -> Dict[str, float]:
    """
    Calculates characteristic defect frequencies:
    BPFI (Ball Pass Inner Ring), BPFO (Ball Pass Outer Ring), BSF (Ball Spin), FTF (Cage).
    """
    f_rot = max(0.1, rpm / 60.0)

    # Standard geometric approximations for 6200 series deep-groove ball bearings
    # Nb = 9 balls, contact angle = 0, Bd/Pd ~ 0.22
    bpfi_mult = 5.43
    bpfo_mult = 3.57
    bsf_mult = 2.32
    ftf_mult = 0.40

    return {
        "rotational_hz": round(f_rot, 2),
        "bpfi_hz": round(f_rot * bpfi_mult, 2),
        "bpfo_hz": round(f_rot * bpfo_mult, 2),
        "bsf_hz": round(f_rot * bsf_mult, 2),
        "ftf_hz": round(f_rot * ftf_mult, 2)
    }


def run_full_bearing_analysis(
    waveform: Optional[List[float]],
    sample_rate_hz: Optional[float],
    rpm: float,
    bearing_code: str = "SKF-6206",
) -> Dict[str, Any]:
    """
    Production-grade bearing analysis pipeline combining:
      1. Time-domain waveform statistics (RMS, Kurtosis, Crest Factor)
      2. Kinematic defect frequency calculation
      3. FFT Power Spectrum + Hilbert Envelope demodulation (if waveform supplied)
      4. Harmonic peak matching for BPFI/BPFO/BSF/FTF fault identification

    This is the full analysis equivalent to what vibration analysts do in SKF @ptitude,
    Emerson AMS Machinery Health Manager, and Fluke 810 vibration testers.

    Returns:
        Unified bearing health report with spectral fault signatures if available.
    """
    kinematic_freqs = calculate_bearing_frequencies(rpm, bearing_code)

    time_domain: Dict[str, Any] = {}
    if waveform and len(waveform) >= 16:
        time_domain = compute_waveform_metrics(waveform)

    spectral: Dict[str, Any] = {"available": False, "reason": "No waveform provided"}
    if waveform and sample_rate_hz and len(waveform) >= 512:
        try:
            from src.engines.spectral_analysis import run_spectral_bearing_analysis
            spectral = run_spectral_bearing_analysis(waveform, sample_rate_hz, kinematic_freqs)
        except Exception as e:
            spectral = {"available": False, "reason": f"Spectral analysis error: {e}"}

    # Combined health score: take the more conservative of time-domain and spectral
    time_health = 100.0
    if time_domain:
        kurtosis = time_domain.get("kurtosis", 3.0)
        crest_factor = time_domain.get("crest_factor", 1.0)
        # Kurtosis > 6 indicates impulsive bearing impacts (SKF guideline)
        if kurtosis > 10.0:
            time_health = min(time_health, 35.0)
        elif kurtosis > 6.0:
            time_health = min(time_health, 65.0)
        elif kurtosis > 4.5:
            time_health = min(time_health, 80.0)
        # Crest factor > 6 indicates periodic impact energy
        if crest_factor > 8.0:
            time_health = min(time_health, 40.0)
        elif crest_factor > 6.0:
            time_health = min(time_health, 70.0)

    spectral_health = spectral.get("spectral_health_score", 100.0) if spectral.get("available") else 100.0
    combined_health = round(min(time_health, spectral_health), 1)

    return {
        "kinematic_frequencies": kinematic_freqs,
        "time_domain": time_domain,
        "spectral": spectral,
        "combined_health_score": combined_health,
    }
