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


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_alert_profile_thresholds(alert_profile: Dict[str, Any]) -> Dict[str, float]:
    """
    Parses upstream alertProfile into APMS threshold keys using EXACT sensor/parameter
    names (substring matching previously leaked unrelated channels into these keys).

    Only parameters that are enabled AND carry a real alert_threshold are used, so
    informational channels (frequencies, energy, yaw, runtime) never become limits.
    Anything not found is simply omitted -> caller keeps the local baseline.

    Keys produced:
      vibration_warning_mm_s / vibration_danger_mm_s   <- IMU-Acceleration / Acceleration
      temp_warning_celsius / temp_danger_celsius       <- Motor temperature
      compressor_temp_warning_celsius / ..._danger_..  <- Compressor temperature
      pressure_warning_bar / pressure_danger_bar       <- Pressure (max-pressure limit)
      sound_warning_db / sound_danger_db               <- Microphone / Sound level
      rpm_min / rpm_max                                <- IMU-Acceleration - RPM / Current RPM
    """
    extracted: Dict[str, float] = {}
    if not isinstance(alert_profile, dict):
        return extracted

    sensors = alert_profile.get("sensors") or alert_profile.get("parameters") or []
    if isinstance(sensors, dict):
        sensors = [sensors]

    def put(key: str, value: Optional[float]):
        # First match wins so duplicate channels (e.g. "Pressure sensor x 2") can't overwrite.
        if value is not None and key not in extracted:
            extracted[key] = value

    for sensor in sensors:
        if not isinstance(sensor, dict):
            continue
        sensor_name = (sensor.get("name") or "").strip().lower()
        params = sensor.get("parameters") or []
        if isinstance(params, dict):
            params = [params]

        for p in params:
            if not isinstance(p, dict) or p.get("enabled") is False:
                continue
            param_name = (p.get("name") or "").strip().lower()
            alert_th = p.get("alert_threshold")
            high = _num(alert_th.get("high")) if isinstance(alert_th, dict) else _num(alert_th)
            low = _num(alert_th.get("low")) if isinstance(alert_th, dict) else None
            if high is None and low is None:
                continue  # informational channel, no alert limit defined
            val_max = _num(p.get("max"))

            if param_name == "current rpm":
                put("rpm_max", high)
                put("rpm_min", low)
            elif sensor_name == "imu-acceleration" and param_name == "acceleration":
                put("vibration_danger_mm_s", high)
                put("vibration_warning_mm_s", val_max)
                # The platform raises its red alert when the reading leaves this band
                # ("vibration.overall: 0.1 - outside 0.0-0.1"), so keep it explicitly.
                put("vibration_band_min", _num(p.get("min")))
                put("vibration_band_max", val_max)
            elif param_name == "motor temperature":
                put("temp_danger_celsius", high)
                put("temp_warning_celsius", val_max)
            elif param_name == "compressor temperature":
                put("compressor_temp_danger_celsius", high)
                put("compressor_temp_warning_celsius", val_max)
            elif param_name == "pressure":
                put("pressure_danger_bar", high)
                put("pressure_warning_bar", val_max)
            elif param_name == "sound level":
                put("sound_danger_db", high)
                put("sound_warning_db", val_max)

    # 'max' in the upstream profile is the healthy-band/sensor ceiling, not always a warning level.
    # Drop any warning that is not strictly below its danger limit (keeps local baseline instead).
    for warn_key, danger_key in (
        ("vibration_warning_mm_s", "vibration_danger_mm_s"),
        ("temp_warning_celsius", "temp_danger_celsius"),
        ("compressor_temp_warning_celsius", "compressor_temp_danger_celsius"),
        ("pressure_warning_bar", "pressure_danger_bar"),
        ("sound_warning_db", "sound_danger_db"),
    ):
        if warn_key in extracted and danger_key in extracted and extracted[warn_key] >= extracted[danger_key]:
            del extracted[warn_key]

    return extracted


DEFAULT_API_VIBRATION_BANDS: Dict[str, tuple[float, float]] = {
    "g-sensor-01": (0.0, 0.1),
    "gbz-rx-1": (0.0, 0.1),
    "compressor_unit_01": (0.0, 0.1),
    "compressor": (0.0, 0.1),
}


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

    def get_api_thresholds(self, twin: AssetDigitalTwin) -> Dict[str, float]:
        """Only the limits that came from the upstream API ({} if none / API unavailable)."""
        self.get_effective_thresholds(twin)  # refreshes the TTL cache if stale
        machine_id = twin.asset_id
        if hasattr(twin, "metadata") and isinstance(twin.metadata, dict):
            machine_id = twin.metadata.get("upstream_machine_id") or machine_id
        with self._lock:
            cached = self._cache.get(machine_id) or {}
        api_t = dict(cached.get("thresholds") or {})
        if "vibration_band_max" not in api_t:
            norm = machine_id.strip().lower()
            if norm in DEFAULT_API_VIBRATION_BANDS:
                lo, hi = DEFAULT_API_VIBRATION_BANDS[norm]
                api_t["vibration_band_min"] = lo
                api_t["vibration_band_max"] = hi
        return api_t

    def sync_twin_thresholds(self, twin: AssetDigitalTwin) -> AssetDigitalTwin:
        """Enriches the twin's thresholds in-place with effective dynamic values."""
        twin.thresholds = self.get_effective_thresholds(twin)
        return twin

    def get_cache_status(self, machine_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._cache.get(machine_id)


# Global singleton provider
threshold_provider = DynamicThresholdProvider()
