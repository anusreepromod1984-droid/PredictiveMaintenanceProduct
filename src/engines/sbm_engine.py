"""
Similarity-Based Modeling (SBM) Baseline Engine
=================================================
This is the core algorithm behind GE SmartSignal (now GE Predix) and AVEVA PRiSM.

Principle:
  1. Learn a NORMAL operating correlation matrix from the first N "healthy" samples.
  2. For each incoming sample, reconstruct expected signal values using a
     weighted nearest-neighbor estimator from the historical reference library.
  3. Compute residuals between observed and estimated values.
  4. Apply CUSUM control chart on residuals to detect drift BEFORE any static
     threshold is breached (typically 30–90 days earlier detection).

This is fundamentally different from Z-score which assumes a stationary
Gaussian distribution. SBM naturally adapts to load, speed, and ambient
temperature changes because it learns the multivariate correlation structure.
"""

import math
import time
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

from src.utils.logger import get_logger

logger = get_logger("Engine.SBM")


class SimilarityBasedModel:
    """
    Weighted Nearest-Neighbor Similarity-Based Model.

    Per-asset, per-tenant model object:
      - Builds a reference matrix from healthy operating samples
      - Reconstructs expected values via kernel-weighted regression
      - Tracks CUSUM statistics per signal for drift detection
    """

    def __init__(
        self,
        asset_key: str,
        n_reference_samples: int = 100,
        k_nearest: int = 10,
        cusum_h: float = 5.0,     # CUSUM decision interval (sigmas)
        cusum_k: float = 0.5,     # CUSUM slack (allowance in sigmas)
        min_train_samples: int = 30
    ):
        self.asset_key = asset_key
        self.n_reference_samples = n_reference_samples
        self.k_nearest = k_nearest
        self.cusum_h = cusum_h
        self.cusum_k = cusum_k
        self.min_train_samples = min_train_samples

        self._reference_library: Optional[np.ndarray] = None  # (N, n_signals)
        self._signal_keys: List[str] = []
        self._signal_mean: Optional[np.ndarray] = None
        self._signal_std: Optional[np.ndarray] = None
        self._is_trained: bool = False

        # CUSUM state per signal key
        self._cusum_pos: Dict[str, float] = {}  # positive cumulative sum
        self._cusum_neg: Dict[str, float] = {}  # negative cumulative sum
        self._cusum_alarm: Dict[str, bool] = {}

        # Training samples buffer
        self._train_buffer: List[Dict[str, float]] = []
        self._trained_at: Optional[float] = None

    # -----------------------------------------------------------------------
    # Training
    # -----------------------------------------------------------------------
    def update_reference(self, signals: Dict[str, float]) -> bool:
        """
        Offer a sample to the reference library.
        Returns True once the model auto-trains.
        """
        # Filter to numeric finite values
        clean = {k: v for k, v in signals.items() if isinstance(v, (int, float)) and math.isfinite(v)}
        if not clean:
            return False

        self._train_buffer.append(clean)

        # Auto-train after collecting enough samples
        if len(self._train_buffer) >= self.min_train_samples and not self._is_trained:
            self._train()
            return True

        # Periodically refresh reference library
        if self._is_trained and len(self._train_buffer) >= self.n_reference_samples:
            self._train()
            return True

        return False

    def _train(self):
        """Build reference matrix from current training buffer."""
        if len(self._train_buffer) < self.min_train_samples:
            return

        # Intersect signal keys present in all samples
        all_keys = set(self._train_buffer[0].keys())
        for sample in self._train_buffer[1:]:
            all_keys &= set(sample.keys())
        self._signal_keys = sorted(list(all_keys))

        if not self._signal_keys:
            return

        matrix = np.array(
            [[s[k] for k in self._signal_keys] for s in self._train_buffer[-self.n_reference_samples:]],
            dtype=np.float64
        )

        self._signal_mean = np.mean(matrix, axis=0)
        self._signal_std = np.std(matrix, axis=0)
        # Avoid division by zero for constant signals
        self._signal_std = np.where(self._signal_std < 1e-6, 1.0, self._signal_std)

        # Normalize reference library
        self._reference_library = (matrix - self._signal_mean) / self._signal_std
        self._is_trained = True
        self._trained_at = time.time()

        # Reset CUSUM on retrain
        for k in self._signal_keys:
            self._cusum_pos[k] = 0.0
            self._cusum_neg[k] = 0.0
            self._cusum_alarm[k] = False

        logger.info(f"SBM trained for {self.asset_key}: {len(matrix)} samples, {len(self._signal_keys)} signals")

    # -----------------------------------------------------------------------
    # Inference
    # -----------------------------------------------------------------------
    def estimate(self, signals: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """
        Estimate expected values for the incoming sample using the
        k-nearest-neighbor weighted reconstruction.
        Returns residuals, anomaly scores, and CUSUM alarms.
        Returns None if model not yet trained.
        """
        if not self._is_trained or self._reference_library is None:
            return None

        # Extract only the signals in the model
        try:
            x = np.array([signals.get(k, self._signal_mean[i])
                           for i, k in enumerate(self._signal_keys)], dtype=np.float64)
        except Exception:
            return None

        x_norm = (x - self._signal_mean) / self._signal_std

        # Compute Euclidean distance to all reference rows
        diffs = self._reference_library - x_norm
        distances = np.sqrt(np.sum(diffs ** 2, axis=1))

        # k-Nearest Neighbors with kernel weights (Gaussian kernel)
        k = min(self.k_nearest, len(distances))
        knn_idx = np.argsort(distances)[:k]
        knn_dists = distances[knn_idx]

        # Gaussian kernel weights
        sigma = np.mean(knn_dists) + 1e-9
        weights = np.exp(-0.5 * (knn_dists / sigma) ** 2)
        weights /= weights.sum()

        # Weighted estimate (in normalized space, then de-normalize)
        ref_norm = self._reference_library[knn_idx]
        x_hat_norm = np.average(ref_norm, axis=0, weights=weights)
        x_hat = x_hat_norm * self._signal_std + self._signal_mean

        # Residuals (de-normalized)
        residuals = x - x_hat
        residuals_norm = (x - x_hat) / self._signal_std  # in sigma units

        # CUSUM update per signal
        cusum_alarms = {}
        cusum_magnitudes = {}
        for i, key in enumerate(self._signal_keys):
            r = float(residuals_norm[i])
            # Positive CUSUM (detect upward drift)
            self._cusum_pos[key] = max(0.0, self._cusum_pos[key] + r - self.cusum_k)
            # Negative CUSUM (detect downward drift)
            self._cusum_neg[key] = max(0.0, self._cusum_neg[key] - r - self.cusum_k)

            alarm = (self._cusum_pos[key] > self.cusum_h) or (self._cusum_neg[key] > self.cusum_h)
            self._cusum_alarm[key] = alarm
            cusum_alarms[key] = alarm
            cusum_magnitudes[key] = max(self._cusum_pos[key], self._cusum_neg[key])

        # Overall anomaly index (normalized Mahalanobis-like distance)
        anomaly_index = float(np.mean(np.abs(residuals_norm)))

        # Build per-signal report
        signal_residuals = {}
        for i, k_name in enumerate(self._signal_keys):
            signal_residuals[k_name] = {
                "observed": round(float(x[i]), 4),
                "estimated": round(float(x_hat[i]), 4),
                "residual": round(float(residuals[i]), 4),
                "sigma_deviation": round(float(residuals_norm[i]), 3),
                "cusum_alarm": cusum_alarms[k_name],
                "cusum_magnitude": round(cusum_magnitudes[k_name], 3),
            }

        # Collect alarming signals
        alarming_signals = [
            k_name for k_name in self._signal_keys
            if cusum_alarms.get(k_name, False)
        ]

        return {
            "sbm_active": True,
            "model_trained_at": self._trained_at,
            "n_reference_samples": len(self._reference_library),
            "k_nearest_used": k,
            "anomaly_index": round(anomaly_index, 4),
            "is_anomalous": anomaly_index > 1.5,   # > 1.5 sigma aggregate deviation
            "cusum_alarm": len(alarming_signals) > 0,
            "alarming_signals": alarming_signals,
            "signal_residuals": signal_residuals,
        }

    def reset_cusum(self, signal_key: Optional[str] = None):
        """Reset CUSUM state after maintenance intervention."""
        if signal_key:
            self._cusum_pos[signal_key] = 0.0
            self._cusum_neg[signal_key] = 0.0
            self._cusum_alarm[signal_key] = False
        else:
            for k_name in self._signal_keys:
                self._cusum_pos[k_name] = 0.0
                self._cusum_neg[k_name] = 0.0
                self._cusum_alarm[k_name] = False

    @property
    def is_trained(self) -> bool:
        return self._is_trained

    @property
    def n_training_samples_buffered(self) -> int:
        return len(self._train_buffer)


# ---------------------------------------------------------------------------
# Registry: one SBM model per (tenant_id, asset_id)
# ---------------------------------------------------------------------------
class SBMRegistry:
    """Thread-safe singleton registry of per-asset SBM models."""

    def __init__(self):
        self._models: Dict[str, SimilarityBasedModel] = {}
        import threading
        self._lock = threading.RLock()

    def get_or_create(self, tenant_id: str, asset_id: str) -> SimilarityBasedModel:
        key = f"{tenant_id.lower()}::{asset_id.lower()}"
        with self._lock:
            if key not in self._models:
                self._models[key] = SimilarityBasedModel(asset_key=key)
                logger.info(f"SBM model created for {key}")
            return self._models[key]

    def get(self, tenant_id: str, asset_id: str) -> Optional[SimilarityBasedModel]:
        key = f"{tenant_id.lower()}::{asset_id.lower()}"
        with self._lock:
            return self._models.get(key)

    def reset_cusum(self, tenant_id: str, asset_id: str):
        model = self.get(tenant_id, asset_id)
        if model:
            model.reset_cusum()

    def status(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "asset_key": key,
                    "is_trained": m.is_trained,
                    "n_samples_buffered": m.n_training_samples_buffered,
                }
                for key, m in self._models.items()
            ]


sbm_registry = SBMRegistry()
