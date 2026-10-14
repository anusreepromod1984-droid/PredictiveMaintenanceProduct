"""
Dynamic PDM Threshold Provider & Upstream Fleet API Integration
================================================================
Fetches live alert thresholds from the upstream Fleet / PDM AI Query API:
  - GET  /api/ai/query/capabilities
  - POST /api/ai/query (with include: ["alertProfile", "thresholds"])

Fallback Principle:
  "Using this we need to get threshold values, and if it's not in API,
   then take what we are currently doing."

Thread-safe TTL caching prevents high-frequency REST calls on telemetry streams.
"""

import time
import threading
from typing import Dict, Any, List, Optional
import httpx

from src.config import settings
from src.schemas.asset import AssetDigitalTwin
from src.utils.logger import get_logger

logger = get_logger("Services.ThresholdProvider")


class UpstreamAPIError(RuntimeError):
    pass


class UpstreamFleetClient:
    """HTTP client for upstream PDM AI Query service."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout_s: Optional[float] = None):
        self.base_url = (base_url or settings.AI_SERVICE_BASE_URL).rstrip("/")
        self.api_key = (api_key or settings.AI_SERVICE_API_KEY).strip()
        self.timeout_s = timeout_s if timeout_s is not None else settings.AI_SERVICE_TIMEOUT_S

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key
        }

    def _build_url(self, path: str) -> str:
        clean_path = "/" + path.lstrip("/")
        # Avoid duplicate /api if base_url already ends with /api
        if self.base_url.endswith("/api") and clean_path.startswith("/api/"):
            return f"{self.base_url}{clean_path[4:]}"
        return f"{self.base_url}{clean_path}"

    def get_capabilities(self) -> Optional[Dict[str, Any]]:
        """GET /api/ai/query/capabilities (allowed includes, visible machines, limits)."""
        if not self.is_configured:
            return None

        urls = [self._build_url("/api/ai/query/capabilities"), self._build_url("/ai/query/capabilities")]
        for url in urls:
            try:
                with httpx.Client(timeout=self.timeout_s) as client:
                    resp = client.get(url, headers=self._headers())
                    if resp.status_code == 200:
                        return resp.json()
            except Exception as e:
                logger.debug(f"Capabilities call failed on {url}: {e}")
        return None

    def query(self, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """POST /api/ai/query."""
        if not self.is_configured:
            return None

        urls = [self._build_url("/api/ai/query"), self._build_url("/ai/query")]
        for url in urls:
            try:
                with httpx.Client(timeout=self.timeout_s) as client:
                    resp = client.post(url, headers=self._headers(), json=body)
                    if resp.status_code == 200:
                        return resp.json()
                    else:
                        logger.warning(f"Upstream query failed ({resp.status_code}): {resp.text[:200]}")
            except Exception as e:
                logger.warning(f"Upstream query network error on {url}: {e}")
        return None


def parse_alert_profile_thresholds(alert_profile: Dict[str, Any]) -> Dict[str, float]:
    """
    Parses upstream alertProfile structure into APMS standard threshold keys:
      - vibration_warning_mm_s, vibration_danger_mm_s
      - temp_warning_celsius, temp_danger_celsius
      - compressor_temp_warning_celsius, compressor_temp_danger_celsius
      - hydraulic_pressure_min_bar, pressure_danger_bar
      - sound_warning_db, sound_danger_db
      - rpm_min, rpm_max
    """
    extracted: Dict[str, float] = {}
    if not isinstance(alert_profile, dict):
        return extracted

    # alertProfile can have 'sensors' or 'parameters' list
    sensors = alert_profile.get("sensors") or alert_profile.get("parameters") or []
    if isinstance(sensors, dict):
        sensors = [sensors]

    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue
        sensor_name = (sensor.get("name") or "").lower()
        params = sensor.get("parameters") or []
        if isinstance(params, dict):
            params = [params]

        for p in params:
            if not isinstance(p, dict):
                continue
            param_name = (p.get("name") or sensor_name).lower()
            alert_th = p.get("alert_threshold")
            val_max = p.get("max")
            val_min = p.get("min")

            # Helper to get high/low from float or dict
            high_th = alert_th.get("high") if isinstance(alert_th, dict) else (alert_th if isinstance(alert_th, (int, float)) else None)
            low_th = alert_th.get("low") if isinstance(alert_th, dict) else None

            # 1. RPM (check before generic acceleration to avoid matching "IMU-Acceleration - RPM")
            if "rpm" in param_name or "rpm" in sensor_name:
                if high_th is not None:
                    extracted["rpm_max"] = float(high_th)
                if low_th is not None:
                    extracted["rpm_min"] = float(low_th)
                elif isinstance(val_min, (int, float)):
                    extracted["rpm_min"] = float(val_min)
                if isinstance(val_max, (int, float)) and "rpm_max" not in extracted:
                    extracted["rpm_max"] = float(val_max)

            # 2. Vibration / IMU Acceleration / Amplitude
            elif any(k in param_name or k in sensor_name for k in ("acceleration", "vibration", "amplitude")):
                if high_th is not None:
                    extracted["vibration_danger_mm_s"] = float(high_th)
                if isinstance(val_max, (int, float)):
                    extracted["vibration_warning_mm_s"] = float(val_max)

            # 3. Motor Temperature
            elif any(k in param_name for k in ("motor temperature", "temp_motor", "motor_temp")):
                if high_th is not None:
                    extracted["temp_danger_celsius"] = float(high_th)
                if isinstance(val_max, (int, float)):
                    extracted["temp_warning_celsius"] = float(val_max)

            # 4. Compressor Temperature
            elif any(k in param_name for k in ("compressor temperature", "temp_compressor")):
                if high_th is not None:
                    extracted["compressor_temp_danger_celsius"] = float(high_th)
                if isinstance(val_max, (int, float)):
                    extracted["compressor_temp_warning_celsius"] = float(val_max)

            # Generic Temperature fallback
            elif "temperature" in sensor_name or "ntc" in sensor_name:
                if high_th is not None and "temp_danger_celsius" not in extracted:
                    extracted["temp_danger_celsius"] = float(high_th)
                if isinstance(val_max, (int, float)) and "temp_warning_celsius" not in extracted:
                    extracted["temp_warning_celsius"] = float(val_max)

            # 5. Pressure / Hydraulic
            elif "pressure" in param_name or "pressure" in sensor_name:
                if high_th is not None:
                    extracted["pressure_danger_bar"] = float(high_th)
                    extracted["hydraulic_pressure_min_bar"] = float(high_th)

            # 6. Sound / Acoustics
            elif "sound" in param_name or "microphone" in sensor_name:
                if high_th is not None:
                    extracted["sound_danger_db"] = float(high_th)
                if isinstance(val_max, (int, float)):
                    extracted["sound_warning_db"] = float(val_max)

            # 7. Machine Load / Idle
            elif "machine load" in param_name or "load" in param_name:
                if isinstance(val_min, (int, float)):
                    extracted["load_idle_threshold_pct"] = float(val_min)
                elif high_th is not None:
                    extracted["load_idle_threshold_pct"] = float(high_th)

    return extracted


class DynamicThresholdProvider:
    """
    Central threshold provider.
    Tries the upstream API first; if unavailable, missing, or erroring,
    falls back seamlessly to local asset twin thresholds.
    """

    def __init__(self, client: Optional[UpstreamFleetClient] = None, cache_ttl_sec: Optional[float] = None):
        self.client = client or UpstreamFleetClient()
        self.cache_ttl_sec = cache_ttl_sec if cache_ttl_sec is not None else settings.THRESHOLD_CACHE_TTL_SEC
        self._cache: Dict[str, Dict[str, Any]] = {}  # {machine_id: {"ts": float, "thresholds": dict, "source": str}}
        self._lock = threading.RLock()

    def get_effective_thresholds(self, twin: AssetDigitalTwin) -> Dict[str, float]:
        """
        Returns merged threshold dictionary:
          Baseline = twin.thresholds (current code)
          Override = values fetched from upstream /api/ai/query (if found)
        """
        # 1. Baseline: what we are currently doing
        effective = dict(twin.thresholds)

        if not self.client.is_configured:
            return effective

        # Determine machine ID to query (asset_id or metadata alias)
        machine_id = twin.asset_id
        if hasattr(twin, "metadata") and isinstance(twin.metadata, dict):
            machine_id = twin.metadata.get("upstream_machine_id") or machine_id

        # 2. Check in-memory TTL cache
        now = time.time()
        with self._lock:
            cached = self._cache.get(machine_id)
            if cached and (now - cached["ts"]) < self.cache_ttl_sec:
                # Merge cached API thresholds over baseline
                effective.update(cached["thresholds"])
                return effective

        # 3. Query Upstream API: include ["alertProfile"]
        try:
            body = {
                "machineIds": [machine_id],
                "include": ["alertProfile"]
            }
            res = self.client.query(body)
            if res and isinstance(res.get("machines"), list):
                for m in res["machines"]:
                    mid = str(m.get("machineId") or "")
                    if mid.lower() == machine_id.lower() or mid.lower() == twin.asset_id.lower():
                        profile = m.get("alertProfile") or {}
                        api_thresholds = parse_alert_profile_thresholds(profile)

                        # Also support direct machine.thresholds if present
                        if isinstance(m.get("thresholds"), dict):
                            for k, v in m["thresholds"].items():
                                if isinstance(v, (int, float)):
                                    api_thresholds[k] = float(v)

                        if api_thresholds:
                            with self._lock:
                                self._cache[machine_id] = {
                                    "ts": now,
                                    "thresholds": api_thresholds,
                                    "source": "upstream_api"
                                }
                            effective.update(api_thresholds)
                            logger.info(
                                f"Applied {len(api_thresholds)} dynamic thresholds from upstream API for asset '{twin.asset_id}'"
                            )
                            return effective
        except Exception as e:
            logger.warning(f"Error fetching dynamic thresholds for asset '{twin.asset_id}': {e}. Using local baseline.")

        # 4. If not in API, record baseline in cache for negative caching to avoid hammering API
        with self._lock:
            self._cache[machine_id] = {
                "ts": now,
                "thresholds": {},
                "source": "local_fallback"
            }

        return effective

    def sync_twin_thresholds(self, twin: AssetDigitalTwin) -> AssetDigitalTwin:
        """Enriches the twin's thresholds in-place with effective dynamic values."""
        twin.thresholds = self.get_effective_thresholds(twin)
        return twin

    def get_cache_status(self, machine_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._cache.get(machine_id)


# Global singleton provider
threshold_provider = DynamicThresholdProvider()
