"""
Spectral Vibration Analysis Engine
====================================
Production-grade rotating machinery diagnostics via signal processing.

Pipeline per waveform packet:
  1. Anti-alias filter (Butterworth low-pass)
  2. Fast Fourier Transform → Power Spectrum
  3. Hilbert Transform → Envelope Signal
  4. Envelope FFT → High-frequency bearing defect detection
  5. Harmonic peak matching against kinematic BPFI / BPFO / BSF / FTF frequencies
  6. Spectral health scoring

This is the equivalent of what AVEVA PRiSM and Emerson AMS machinery health do
with raw acceleration waveforms from 10 kHz accelerometers.
"""

import math
from typing import Dict, Any, List, Optional
import numpy as np
from scipy import signal as scipy_signal
from scipy.fft import fft, fftfreq

from src.utils.logger import get_logger

logger = get_logger("Engine.SpectralAnalysis")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_HARMONIC_TOLERANCE_HZ = 0.5   # Hz tolerance for harmonic peak matching
_MIN_WAVEFORM_SAMPLES = 512    # Minimum samples for reliable FFT
_ENVELOPE_DEMOD_BAND = (2000.0, 10000.0)  # Hz – classic bearing defect energy band


def compute_power_spectrum(
    waveform: List[float],
    sample_rate_hz: float
) -> Dict[str, Any]:
    """
    Compute broadband power spectrum from raw waveform.
    Returns frequency bins + magnitude spectrum in mm/s RMS.
    """
    arr = np.array(waveform, dtype=np.float64)
    n = len(arr)
    if n < _MIN_WAVEFORM_SAMPLES:
        return {"valid": False, "reason": f"Waveform too short ({n} < {_MIN_WAVEFORM_SAMPLES} samples)"}

    # DC removal
    arr -= np.mean(arr)

    # Anti-alias Butterworth low-pass at Nyquist/2
    nyquist = sample_rate_hz / 2.0
    cutoff = min(nyquist * 0.9, 25000.0)
    b, a = scipy_signal.butter(4, cutoff / nyquist, btype="low")
    arr = scipy_signal.filtfilt(b, a, arr)

    # FFT
    n_fft = int(2 ** math.ceil(math.log2(n)))  # next power-of-2 for speed
    Y = fft(arr, n=n_fft)
    freqs = fftfreq(n_fft, d=1.0 / sample_rate_hz)

    # Single-sided spectrum (positive frequencies only)
    pos_mask = freqs > 0
    freqs_pos = freqs[pos_mask]
    magnitudes = (2.0 / n_fft) * np.abs(Y[pos_mask])

    # Overall vibration RMS from spectrum (Parseval's theorem)
    rms_spectral = float(np.sqrt(np.sum(magnitudes ** 2) / 2.0))

    return {
        "valid": True,
        "frequencies_hz": freqs_pos.tolist(),
        "magnitudes": magnitudes.tolist(),
        "rms_mm_s": round(rms_spectral, 4),
        "n_samples": n,
        "n_fft": n_fft,
        "freq_resolution_hz": round(sample_rate_hz / n_fft, 4),
    }


def compute_envelope_spectrum(
    waveform: List[float],
    sample_rate_hz: float,
    band_hz: tuple = _ENVELOPE_DEMOD_BAND
) -> Dict[str, Any]:
    """
    Hilbert Transform Envelope Demodulation.
    Extracts bearing defect signals hidden in the high-frequency energy band.

    Procedure:
    1. Band-pass filter waveform into the structural resonance band (2–10 kHz)
    2. Hilbert Transform → analytic signal → |amplitude envelope|
    3. FFT of envelope signal → envelope spectrum
    """
    arr = np.array(waveform, dtype=np.float64)
    n = len(arr)
    if n < _MIN_WAVEFORM_SAMPLES:
        return {"valid": False, "reason": "Waveform too short for envelope demodulation"}

    arr -= np.mean(arr)
    nyquist = sample_rate_hz / 2.0

    lo, hi = band_hz
    lo = max(lo, 100.0)
    hi = min(hi, nyquist * 0.95)

    if lo >= hi:
        return {"valid": False, "reason": f"Band {band_hz} invalid for sample rate {sample_rate_hz} Hz"}

    # Band-pass Butterworth (4th order)
    sos = scipy_signal.butter(4, [lo / nyquist, hi / nyquist], btype="bandpass", output="sos")
    filtered = scipy_signal.sosfiltfilt(sos, arr)

    # Hilbert envelope
    analytic = scipy_signal.hilbert(filtered)
    envelope = np.abs(analytic) - np.mean(np.abs(analytic))  # AC-coupled envelope

    # Envelope FFT
    n_fft = int(2 ** math.ceil(math.log2(n)))
    Y_env = fft(envelope, n=n_fft)
    freqs_env = fftfreq(n_fft, d=1.0 / sample_rate_hz)

    pos_mask = freqs_env > 0
    freqs_pos = freqs_env[pos_mask]
    env_magnitudes = (2.0 / n_fft) * np.abs(Y_env[pos_mask])

    return {
        "valid": True,
        "frequencies_hz": freqs_pos.tolist(),
        "magnitudes": env_magnitudes.tolist(),
        "band_hz": band_hz,
        "envelope_rms": round(float(np.sqrt(np.mean(envelope ** 2))), 6),
    }


def match_bearing_harmonics(
    freq_array: List[float],
    magnitude_array: List[float],
    kinematic_frequencies: Dict[str, float],
    noise_floor_factor: float = 3.0,
    n_harmonics: int = 3
) -> List[Dict[str, Any]]:
    """
    Match peaks in the envelope spectrum against known kinematic defect frequencies.
    Returns list of matched defect signatures.

    Args:
        freq_array:             Frequency bins from envelope FFT
        magnitude_array:        Corresponding magnitudes
        kinematic_frequencies:  Dict from calculate_bearing_frequencies() (bpfi_hz, bpfo_hz, etc.)
        noise_floor_factor:     Signal-to-Noise ratio threshold for a peak to count
        n_harmonics:            How many harmonics per fault frequency to search
    """
    freqs = np.array(freq_array)
    mags = np.array(magnitude_array)
    if len(freqs) == 0:
        return []

    noise_floor = np.median(mags) * noise_floor_factor

    defect_map = {
        "BPFI": kinematic_frequencies.get("bpfi_hz", 0.0),
        "BPFO": kinematic_frequencies.get("bpfo_hz", 0.0),
        "BSF":  kinematic_frequencies.get("bsf_hz",  0.0),
        "FTF":  kinematic_frequencies.get("ftf_hz",  0.0),
    }

    matched_faults: List[Dict[str, Any]] = []

    for defect_name, base_freq in defect_map.items():
        if base_freq <= 0:
            continue

        harmonic_matches = []
        for h in range(1, n_harmonics + 1):
            target_hz = base_freq * h
            # Find closest bin
            idx = int(np.argmin(np.abs(freqs - target_hz)))
            bin_freq = freqs[idx]
            if abs(bin_freq - target_hz) <= _HARMONIC_TOLERANCE_HZ:
                peak_mag = float(mags[idx])
                snr = peak_mag / noise_floor if noise_floor > 0 else 0.0
                if snr >= 1.0:
                    harmonic_matches.append({
                        "harmonic": h,
                        "target_hz": round(target_hz, 2),
                        "found_hz": round(float(bin_freq), 2),
                        "magnitude": round(peak_mag, 6),
                        "snr": round(snr, 2),
                    })

        if harmonic_matches:
            max_snr = max(m["snr"] for m in harmonic_matches)
            confidence = min(0.99, 0.55 + (len(harmonic_matches) / n_harmonics) * 0.35 + min(0.09, max_snr * 0.01))
            matched_faults.append({
                "defect_type": defect_name,
                "base_frequency_hz": round(base_freq, 2),
                "harmonics_matched": len(harmonic_matches),
                "max_snr": round(max_snr, 2),
                "confidence": round(confidence, 3),
                "details": harmonic_matches,
            })

    return matched_faults


def run_spectral_bearing_analysis(
    waveform: List[float],
    sample_rate_hz: float,
    kinematic_frequencies: Dict[str, float],
) -> Dict[str, Any]:
    """
    Full pipeline: broadband spectrum + envelope demodulation + harmonic matching.
    Returns a structured spectral health report.
    """
    if not waveform or len(waveform) < _MIN_WAVEFORM_SAMPLES:
        return {
            "available": False,
            "reason": f"Waveform not provided or too short (<{_MIN_WAVEFORM_SAMPLES} samples)"
        }

    # 1. Broadband spectrum
    spectrum = compute_power_spectrum(waveform, sample_rate_hz)
    if not spectrum.get("valid"):
        return {"available": False, "reason": spectrum.get("reason", "Spectrum computation failed")}

    # 2. Envelope spectrum (bearing defect band)
    envelope = compute_envelope_spectrum(waveform, sample_rate_hz)

    # 3. Harmonic peak matching on envelope spectrum
    matched_defects: List[Dict[str, Any]] = []
    if envelope.get("valid"):
        matched_defects = match_bearing_harmonics(
            envelope["frequencies_hz"],
            envelope["magnitudes"],
            kinematic_frequencies,
        )

    # 4. Compute spectral health score
    spectral_health = _compute_spectral_health(spectrum, envelope, matched_defects)

    return {
        "available": True,
        "broadband_rms_mm_s": spectrum["rms_mm_s"],
        "freq_resolution_hz": spectrum["freq_resolution_hz"],
        "envelope_rms": envelope.get("envelope_rms"),
        "matched_defects": matched_defects,
        "spectral_health_score": spectral_health,
        "defect_detected": len(matched_defects) > 0,
    }


def _compute_spectral_health(
    spectrum: Dict[str, Any],
    envelope: Dict[str, Any],
    matched_defects: List[Dict[str, Any]]
) -> float:
    """Composite spectral health score 0–100."""
    score = 100.0
    # Penalize for each matched bearing defect by confidence × severity weight
    for defect in matched_defects:
        confidence = defect.get("confidence", 0.5)
        harmonics = defect.get("harmonics_matched", 1)
        # More harmonics matched = worse
        deduction = confidence * (10.0 + harmonics * 8.0)
        score -= deduction

    # Penalize for high envelope RMS (broadband fatigue)
    env_rms = envelope.get("envelope_rms", 0.0)
    if env_rms and env_rms > 0.05:
        score -= min(20.0, env_rms * 100)

    return round(max(0.0, min(100.0, score)), 1)
