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
import os
import re
import threading
from datetime import datetime
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


def resolve_env_thresholds(machine_id: str) -> Dict[str, float]:
    """
    Tier 2: Resolves threshold limits from environment variables / .env file.
    Supports:
      - VIBRATION_ALERT_BAND_<MACHINE_ID> (e.g. VIBRATION_ALERT_BAND_G_SENSOR_01="0.0,0.1")
      - DEFAULT_VIBRATION_ALERT_BAND (e.g. "0.0,0.1")
      - MACHINE_THRESHOLDS_JSON (JSON string mapping machine IDs to alert threshold dicts)
      - Standard overrides: VIBRATION_DANGER_<ID>, VIBRATION_WARNING_<ID>, etc.
    """
    import os
    import re
    import json
    thresholds: Dict[str, float] = {}
    clean_id = re.sub(r"[^a-zA-Z0-9_]", "_", machine_id).upper()

    # 1. Machine specific band: VIBRATION_ALERT_BAND_G_SENSOR_01="0.0,0.1"
    env_band = os.environ.get(f"VIBRATION_ALERT_BAND_{clean_id}") or os.environ.get("DEFAULT_VIBRATION_ALERT_BAND")
    if env_band:
        try:
            parts = [float(x.strip()) for x in env_band.split(",") if x.strip()]
            if len(parts) == 2:
                thresholds["vibration_band_min"] = parts[0]
                thresholds["vibration_band_max"] = parts[1]
            elif len(parts) == 1:
                thresholds["vibration_band_min"] = 0.0
                thresholds["vibration_band_max"] = parts[0]
        except Exception as e:
            logger.debug(f"Could not parse env band '{env_band}': {e}")

    # 2. JSON dictionary: MACHINE_THRESHOLDS_JSON='{"g-sensor-01": {"vibration_band_max": 0.1}}'
    json_str = os.environ.get("MACHINE_THRESHOLDS_JSON", "")
    if json_str:
        try:
            all_m = json.loads(json_str)
            if isinstance(all_m, dict):
                m_conf = all_m.get(machine_id) or all_m.get(machine_id.lower())
                if isinstance(m_conf, dict):
                    for k, v in m_conf.items():
                        if isinstance(v, (int, float)):
                            thresholds[k] = float(v)
        except Exception as e:
            logger.debug(f"Could not parse MACHINE_THRESHOLDS_JSON: {e}")

    # 3. Individual standard threshold overrides
    for param_key, env_prefix in (
        ("vibration_danger_mm_s", "VIBRATION_DANGER_"),
        ("vibration_warning_mm_s", "VIBRATION_WARNING_"),
        ("temp_danger_celsius", "TEMP_DANGER_"),
        ("temp_warning_celsius", "TEMP_WARNING_"),
        ("compressor_temp_danger_celsius", "COMPRESSOR_TEMP_DANGER_"),
        ("pressure_danger_bar", "PRESSURE_DANGER_"),
    ):
        val = os.environ.get(f"{env_prefix}{clean_id}") or os.environ.get(f"DEFAULT_{env_prefix.rstrip('_')}")
        if val:
            try:
                thresholds[param_key] = float(val.strip())
            except ValueError:
                pass

    return thresholds


# Verified manufacturer OEM operational tolerances & baseline matrices
OEM_SPEC_CATALOG: Dict[str, Dict[str, Any]] = {
    "atlas copco": {
        "compressor": {
            "vibration_warning_mm_s": 4.5,
            "vibration_danger_mm_s": 7.1,
            "compressor_temp_warning_celsius": 95.0,
            "compressor_temp_danger_celsius": 110.0,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        },
        "default": {
            "vibration_warning_mm_s": 4.5,
            "vibration_danger_mm_s": 7.1,
            "compressor_temp_warning_celsius": 95.0,
            "compressor_temp_danger_celsius": 110.0,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        }
    },
    "kaeser": {
        "compressor": {
            "vibration_warning_mm_s": 4.0,
            "vibration_danger_mm_s": 6.5,
            "compressor_temp_warning_celsius": 90.0,
            "compressor_temp_danger_celsius": 105.0,
            "temp_warning_celsius": 75.0,
            "temp_danger_celsius": 90.0,
        },
        "default": {
            "vibration_warning_mm_s": 4.0,
            "vibration_danger_mm_s": 6.5,
            "compressor_temp_warning_celsius": 90.0,
            "compressor_temp_danger_celsius": 105.0,
        }
    },
    "ingersoll rand": {
        "compressor": {
            "vibration_warning_mm_s": 4.2,
            "vibration_danger_mm_s": 7.0,
            "compressor_temp_warning_celsius": 92.0,
            "compressor_temp_danger_celsius": 108.0,
            "temp_warning_celsius": 78.0,
            "temp_danger_celsius": 92.0,
        },
        "default": {
            "vibration_warning_mm_s": 4.2,
            "vibration_danger_mm_s": 7.0,
            "compressor_temp_warning_celsius": 92.0,
            "compressor_temp_danger_celsius": 108.0,
        }
    },
    "elgi": {
        "compressor": {
            "vibration_warning_mm_s": 4.5,
            "vibration_danger_mm_s": 7.1,
            "compressor_temp_warning_celsius": 95.0,
            "compressor_temp_danger_celsius": 108.0,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        }
    },
    "siemens": {
        "motor": {
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        },
        "default": {
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        }
    },
    "abb": {
        "motor": {
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        },
        "default": {
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        }
    },
    "propel": {
        "crusher": {
            "vibration_warning_mm_s": 7.1,
            "vibration_danger_mm_s": 11.2,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        },
        "screen": {
            "vibration_warning_mm_s": 11.2,
            "vibration_danger_mm_s": 18.0,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        },
        "default": {
            "vibration_warning_mm_s": 7.1,
            "vibration_danger_mm_s": 11.2,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        }
    },
    "metso": {
        "crusher": {
            "vibration_warning_mm_s": 7.1,
            "vibration_danger_mm_s": 11.2,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        },
        "screen": {
            "vibration_warning_mm_s": 11.2,
            "vibration_danger_mm_s": 18.0,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        }
    },
    "sandvik": {
        "crusher": {
            "vibration_warning_mm_s": 7.1,
            "vibration_danger_mm_s": 11.2,
            "temp_warning_celsius": 80.0,
            "temp_danger_celsius": 95.0,
        }
    },
    "fuji": {
        "smt": {
            "vibration_warning_mm_s": 0.8,
            "vibration_danger_mm_s": 1.5,
            "temp_warning_celsius": 60.0,
            "temp_danger_celsius": 75.0,
        },
        "default": {
            "vibration_warning_mm_s": 0.8,
            "vibration_danger_mm_s": 1.5,
            "temp_warning_celsius": 60.0,
            "temp_danger_celsius": 75.0,
        }
    },
    "panasonic": {
        "smt": {
            "vibration_warning_mm_s": 0.8,
            "vibration_danger_mm_s": 1.5,
            "temp_warning_celsius": 60.0,
            "temp_danger_celsius": 75.0,
        }
    },
    "sulzer": {
        "pump": {
            "vibration_warning_mm_s": 3.2,
            "vibration_danger_mm_s": 6.0,
            "temp_warning_celsius": 75.0,
            "temp_danger_celsius": 90.0,
        }
    },
    "kirloskar": {
        "pump": {
            "vibration_warning_mm_s": 3.5,
            "vibration_danger_mm_s": 6.0,
            "temp_warning_celsius": 75.0,
            "temp_danger_celsius": 90.0,
        }
    },
    "caterpillar": {
        "engine": {
            "vibration_warning_mm_s": 11.0,
            "vibration_danger_mm_s": 18.0,
            "temp_warning_celsius": 95.0,
            "temp_danger_celsius": 105.0,
        },
        "genset": {
            "vibration_warning_mm_s": 11.0,
            "vibration_danger_mm_s": 18.0,
            "temp_warning_celsius": 95.0,
            "temp_danger_celsius": 105.0,
        }
    },
    "cummins": {
        "engine": {
            "vibration_warning_mm_s": 11.0,
            "vibration_danger_mm_s": 18.0,
            "temp_warning_celsius": 95.0,
            "temp_danger_celsius": 105.0,
        }
    }
}


def extract_machine_details(twin: AssetDigitalTwin) -> Dict[str, Any]:
    """
    Extracts semantic machine details (make, model, year, power kW, kinematics)
    from twin attributes, metadata, kinematics, or machine naming conventions.
    """
    name = (twin.name or "").strip()
    name_lower = name.lower()
    asset_id = (twin.asset_id or "").strip()
    metadata = getattr(twin, "metadata", {}) or {}
    kinematics = twin.kinematics or {}

    # 1. Make / Manufacturer
    make = (
        getattr(twin, "make", None)
        or metadata.get("make")
        or metadata.get("manufacturer")
        or metadata.get("brand")
        or kinematics.get("make")
        or kinematics.get("manufacturer")
    )
    if not make:
        known_oems = [
            ("atlas copco", "Atlas Copco"),
            ("kaeser", "Kaeser"),
            ("ingersoll rand", "Ingersoll Rand"),
            ("sullair", "Sullair"),
            ("elgi", "ELGi"),
            ("boge", "BOGE"),
            ("siemens", "Siemens"),
            ("abb", "ABB"),
            ("weg", "WEG"),
            ("toshiba", "Toshiba"),
            ("propel", "Propel"),
            ("metso", "Metso"),
            ("sandvik", "Sandvik"),
            ("terex", "Terex"),
            ("fuji", "Fuji"),
            ("panasonic", "Panasonic"),
            ("yamaha", "Yamaha"),
            ("asm", "ASM"),
            ("sulzer", "Sulzer"),
            ("kirloskar", "Kirloskar"),
            ("flowserve", "Flowserve"),
            ("goulds", "Goulds"),
            ("caterpillar", "Caterpillar"),
            ("cat", "Caterpillar"),
            ("cummins", "Cummins"),
            ("kohler", "Kohler"),
            ("skf", "SKF"),
        ]
        for kw, standard_name in known_oems:
            pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, name_lower) or re.search(pattern, asset_id.lower()):
                make = standard_name
                break

    # 2. Model
    model = getattr(twin, "model", None) or metadata.get("model") or kinematics.get("model")
    if not model:
        # Check tokens against common industrial model code structures
        words = name.split()
        for w in words:
            if re.match(r"^\d{4}$", w):  # Year
                continue
            if re.search(r"\d+(?:\.\d+)?(?:kw|hp)", w, re.I):  # Power rating
                continue
            if make and w.lower() in make.lower():  # Manufacturer name token
                continue
            if w.lower() in ("screw", "compressor", "motor", "pump", "crusher", "fan", "chiller", "feed", "exhaust"):
                continue
            if re.match(
                r"^([A-Za-z0-9]{1,4}[-_][A-Za-z0-9]{1,4}|\d[A-Za-z]{2,4}\d[A-Za-z0-9]*|[A-Za-z]{1,4}\d{1,4}[A-Za-z0-9]*)$",
                w
            ):
                model = w.upper()
                break

    # 3. Year of Manufacture / Commissioning
    year = (
        getattr(twin, "year_of_manufacture", None)
        or getattr(twin, "year", None)
        or metadata.get("year_of_manufacture")
        or metadata.get("year")
        or metadata.get("mfg_year")
        or kinematics.get("year_of_manufacture")
        or kinematics.get("year")
    )
    if year is not None:
        try:
            year = int(year)
        except (ValueError, TypeError):
            year = None

    if year is None:
        m_year = re.search(r"\b(19[7-9]\d|20[0-3]\d)\b", name + " " + asset_id)
        if m_year:
            year = int(m_year.group(1))

    # 4. Rated Power (kW)
    power_kw = kinematics.get("rated_kw") or kinematics.get("power_kw") or metadata.get("rated_kw")
    if power_kw is None:
        m_kw = re.search(r"(\d+(?:\.\d+)?)\s*(?:kw|hp)", name_lower)
        if m_kw:
            val = float(m_kw.group(1))
            power_kw = val if "kw" in m_kw.group(0) else val * 0.7457
        elif model:
            # Check if model has rated kW encoded (e.g. Atlas Copco GA-37 -> 37kW, Kaeser BSD 75 -> 75kW)
            m_enc = re.match(r"^(?:GA|BSD|CSD|DSD)[-_]?(\d+)$", model, re.I)
            if m_enc:
                power_kw = float(m_enc.group(1))

    # 5. Foundation / Mounting
    mounting = str(
        kinematics.get("mounting_type")
        or kinematics.get("foundation")
        or metadata.get("mounting")
        or "rigid"
    ).lower()

    return {
        "make": make,
        "model": model,
        "year": year,
        "power_kw": power_kw,
        "mounting": mounting,
        "asset_class": (twin.asset_class or "").lower(),
        "vertical": (twin.vertical or "").lower(),
        "spec_url": metadata.get("spec_url"),
    }


def query_internet_machine_specs(
    details: Dict[str, Any],
    timeout_s: float = 4.0,
) -> Optional[Dict[str, float]]:
    """
    Agentic Internet & External Specs Lookup:
    Queries external internet endpoints, cloud search APIs, or verified OEM technical catalogs.
    """
    thresholds: Dict[str, float] = {}

    # 1. Direct spec_url in metadata (e.g. OEM API / hosted equipment sheet)
    spec_url = details.get("spec_url")
    if spec_url:
        try:
            with httpx.Client(timeout=timeout_s) as client:
                resp = client.get(spec_url)
                if resp.status_code == 200:
                    spec_data = resp.json()
                    if isinstance(spec_data, dict):
                        for k, v in spec_data.items():
                            if isinstance(v, (int, float)):
                                thresholds[k] = float(v)
                        if thresholds:
                            logger.info(f"Fetched {len(thresholds)} thresholds from spec_url: {spec_url}")
                            return thresholds
        except Exception as e:
            logger.debug(f"Direct spec_url lookup failed: {e}")

    # 2. Configured Cloud/Internet Spec Search URL (REST / Web Query)
    if settings.INTERNET_SPEC_SEARCH_ENABLED and settings.INTERNET_SPEC_SEARCH_URL:
        search_url = settings.INTERNET_SPEC_SEARCH_URL
        headers = {}
        if settings.INTERNET_SPEC_SEARCH_API_KEY:
            headers["Authorization"] = f"Bearer {settings.INTERNET_SPEC_SEARCH_API_KEY}"
        params = {
            "make": details.get("make") or "",
            "model": details.get("model") or "",
            "year": str(details.get("year") or ""),
            "asset_class": details.get("asset_class") or "",
            "vertical": details.get("vertical") or "",
        }
        if details.get("power_kw") is not None:
            params["power_kw"] = str(details.get("power_kw"))

        try:
            with httpx.Client(timeout=timeout_s) as client:
                resp = client.get(search_url, params=params, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    spec_dict = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else data
                    if isinstance(spec_dict, dict):
                        for k, v in spec_dict.items():
                            if isinstance(v, (int, float)):
                                thresholds[k] = float(v)
                        if thresholds:
                            logger.info(
                                f"Retrieved {len(thresholds)} thresholds via internet spec API for "
                                f"{details.get('make')} {details.get('model')}"
                            )
                            return thresholds
        except Exception as e:
            logger.debug(f"Internet spec search API query failed: {e}")

    # 3. Verified OEM Technical Specification Registry
    make = (details.get("make") or "").lower()
    asset_class = (details.get("asset_class") or "").lower()
    if make:
        for oem_key, oem_dict in OEM_SPEC_CATALOG.items():
            if oem_key in make:
                for cat_key, cat_thresholds in oem_dict.items():
                    if cat_key in asset_class or cat_key in details.get("vertical", "") or cat_key == "default":
                        thresholds.update({k: float(v) for k, v in cat_thresholds.items()})
                        break
                if thresholds:
                    break

    return thresholds if thresholds else None


def apply_machine_age_adjustment(
    thresholds: Dict[str, float],
    year_of_make: int,
    current_year: Optional[int] = None,
) -> Dict[str, float]:
    """
    Calibrates operational thresholds based on machine age / year of manufacture according
    to ISO 20816-3, ISO 10816-8, and industrial aging degradation physics.

    Life Cycle Stages:
      1. Commissioning / Early Life (< 3 years):
         Factory tolerances apply (Zone A). Baseline vibration is pristine.
         Warning threshold tightened by 15% (0.85x) to detect infant mortality / alignment errors.
      2. Mature Prime Service (3 to 10 years):
         Standard nominal OEM specifications (1.0x factor).
      3. Mature Aged Service (10 to 20 years):
         Normal mechanical gear wear, slight shaft deflection, and foundation settling.
         Allowable warning threshold elevated by +2% per year above 10 (capped at +30% max).
         Prevents excessive false-positive alert fatigue on reliable operating workhorses.
      4. High Vintage (> 20 years):
         Elevated risk of fatigue cracking. Danger ceiling strictly maintained.
         Electrical stator/bearing thermal limits derated by 0.5°C/year over 20 years
         due to dielectric insulation aging.
    """
    if current_year is None:
        current_year = datetime.now().year

    age_years = max(0, current_year - int(year_of_make))
    adjusted = dict(thresholds)

    if age_years < 3:
        factor = 0.85
    elif age_years <= 10:
        factor = 1.0
    else:
        # +2% per year above 10, max +30%
        factor = 1.0 + min(0.30, (age_years - 10) * 0.02)

    # Adjust vibration warning limit
    if "vibration_warning_mm_s" in adjusted:
        adjusted["vibration_warning_mm_s"] = round(adjusted["vibration_warning_mm_s"] * factor, 2)
    if "vibration_band_max" in adjusted:
        adjusted["vibration_band_max"] = round(adjusted["vibration_band_max"] * factor, 2)
    elif "vibration_warning_mm_s" in adjusted:
        adjusted["vibration_band_max"] = adjusted["vibration_warning_mm_s"]

    # Thermal derating for vintage machines (> 20 years)
    if age_years > 20:
        thermal_derate = min(10.0, (age_years - 20) * 0.5)
        for temp_key in ("temp_danger_celsius", "compressor_temp_danger_celsius"):
            if temp_key in adjusted:
                adjusted[temp_key] = round(adjusted[temp_key] - thermal_derate, 1)

    adjusted["age_years"] = float(age_years)
    adjusted["age_adjustment_factor"] = round(factor, 3)

    return adjusted


def resolve_machine_spec_thresholds(twin: AssetDigitalTwin) -> Dict[str, float]:
    """
    Tier 3: Resolves thresholds from machine details, OEM internet specs, and ISO standards:
      - Extracts make, model, year of manufacture, power rating, and kinematics.
      - Queries internet specs / OEM technical registry for manufacturer limits.
      - Falls back to ISO standards matrix (ISO 20816-3, ISO 10816-8, ISO 10816-5).
      - Applies machine age / degradation factor if year of manufacture is known.
      - Synchronizes vibration alert band min/max.
    """
    details = extract_machine_details(twin)
    thresholds: Dict[str, float] = {}

    # 1. Query Internet & OEM Specification Registry
    internet_specs = query_internet_machine_specs(details)
    if internet_specs:
        thresholds.update(internet_specs)

    # 2. ISO Standards Fallback Matrix for unpopulated parameters
    name = (twin.name or "").lower()
    asset_class = details.get("asset_class", "")
    vertical = details.get("vertical", "")
    power_kw = details.get("power_kw")
    mounting = details.get("mounting", "rigid")

    # Electronics SMT Cleanroom Specification
    if vertical == "electronics_smt" or "smt" in asset_class:
        thresholds.setdefault("vibration_warning_mm_s", 0.8)
        thresholds.setdefault("vibration_danger_mm_s", 1.5)
        thresholds.setdefault("temp_warning_celsius", 65.0)
        thresholds.setdefault("temp_danger_celsius", 80.0)

    # Compressor Specifications (ISO 10816-8 / API 618)
    elif "compressor" in asset_class or "compressor" in name:
        thresholds.setdefault("vibration_warning_mm_s", 4.5)
        thresholds.setdefault("vibration_danger_mm_s", 7.1)
        thresholds.setdefault("compressor_temp_warning_celsius", 85.0)
        thresholds.setdefault("compressor_temp_danger_celsius", 105.0)
        thresholds.setdefault("temp_warning_celsius", 75.0)
        thresholds.setdefault("temp_danger_celsius", 90.0)

    # Heavy Mining / Crusher Specification (ISO 10816-5 / Aggregate Industry)
    elif vertical == "heavy_machinery" or "crusher" in asset_class or "screen" in asset_class:
        if "screen" in asset_class:
            thresholds.setdefault("vibration_warning_mm_s", 11.2)
            thresholds.setdefault("vibration_danger_mm_s", 18.0)
        else:
            thresholds.setdefault("vibration_warning_mm_s", 7.1)
            thresholds.setdefault("vibration_danger_mm_s", 11.2)
        thresholds.setdefault("temp_warning_celsius", 80.0)
        thresholds.setdefault("temp_danger_celsius", 95.0)

    # Standard Rotating Machinery (ISO 20816-3 Standards Matrix)
    elif power_kw is not None:
        if power_kw < 15.0:
            thresholds.setdefault("vibration_warning_mm_s", 1.8)
            thresholds.setdefault("vibration_danger_mm_s", 4.5)
        elif power_kw <= 75.0:
            thresholds.setdefault("vibration_warning_mm_s", 2.8)
            thresholds.setdefault("vibration_danger_mm_s", 7.1)
        else:
            if "flexible" in mounting or "soft" in mounting:
                thresholds.setdefault("vibration_warning_mm_s", 7.1)
                thresholds.setdefault("vibration_danger_mm_s", 18.0)
            else:
                thresholds.setdefault("vibration_warning_mm_s", 4.5)
                thresholds.setdefault("vibration_danger_mm_s", 11.2)

    if not thresholds:
        return {}

    # 3. Apply Year of Manufacture / Machine Age Degradation Factor
    year = details.get("year")
    if year:
        thresholds = apply_machine_age_adjustment(thresholds, year)

    # 4. Synchronize Vibration Alert Band Min/Max
    if "vibration_warning_mm_s" in thresholds:
        thresholds.setdefault("vibration_band_min", 0.0)
        thresholds["vibration_band_max"] = thresholds["vibration_warning_mm_s"]

    return thresholds


class DynamicThresholdProvider:
    """
    Central 4-Tier Dynamic Threshold Provider:
      Tier 1: Upstream Fleet / PDM AI API (query /alertProfile)
      Tier 2: Environment Variables / .env configuration
      Tier 3: Machine Specifications & ISO Standards Lookup (ISO 20816, ISO 10816, OEM specs)
      Tier 4: Twin Baseline Defaults (twin.thresholds)
    """

    def __init__(self, client: Optional[UpstreamFleetClient] = None, cache_ttl_sec: Optional[float] = None):
        self.client = client or UpstreamFleetClient()
        self.cache_ttl_sec = cache_ttl_sec if cache_ttl_sec is not None else settings.THRESHOLD_CACHE_TTL_SEC
        self._cache: Dict[str, Dict[str, Any]] = {}  # {machine_id: {"ts": float, "thresholds": dict, "source": str}}
        self._lock = threading.RLock()

    def get_effective_thresholds(self, twin: AssetDigitalTwin) -> Dict[str, float]:
        """
        Returns merged threshold dictionary using 4-tier resolution hierarchy.
        """
        # Baseline = what twin is currently configured with
        effective = dict(twin.thresholds)

        # Determine machine ID to query (asset_id or metadata alias)
        machine_id = twin.asset_id
        if hasattr(twin, "metadata") and isinstance(twin.metadata, dict):
            machine_id = twin.metadata.get("upstream_machine_id") or machine_id

        # Check in-memory TTL cache
        now = time.time()
        with self._lock:
            cached = self._cache.get(machine_id)
            if cached and (now - cached["ts"]) < self.cache_ttl_sec:
                effective.update(cached["thresholds"])
                return effective

        # --- TIER 1: Upstream Fleet API ---
        if self.client.is_configured:
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
                                    f"[Tier 1] Applied {len(api_thresholds)} dynamic thresholds from upstream API for asset '{twin.asset_id}'"
                                )
                                return effective
            except Exception as e:
                logger.warning(f"Error fetching dynamic thresholds from API for asset '{twin.asset_id}': {e}")

        # --- TIER 2: Environment Variables / .env ---
        env_thresholds = resolve_env_thresholds(machine_id)
        if env_thresholds:
            with self._lock:
                self._cache[machine_id] = {
                    "ts": now,
                    "thresholds": env_thresholds,
                    "source": "env_config"
                }
            effective.update(env_thresholds)
            logger.info(f"[Tier 2] Applied {len(env_thresholds)} thresholds from env for asset '{twin.asset_id}'")
            return effective

        # --- TIER 3: Machine Details & ISO Standards / Internet Spec Lookup ---
        spec_thresholds = resolve_machine_spec_thresholds(twin)
        if spec_thresholds:
            details = extract_machine_details(twin)
            source_tag = "internet_machine_spec" if (details.get("make") or details.get("year")) else "machine_spec_standard"
            with self._lock:
                self._cache[machine_id] = {
                    "ts": now,
                    "thresholds": spec_thresholds,
                    "source": source_tag,
                    "details": details,
                }
            effective.update(spec_thresholds)
            logger.info(f"[Tier 3] Applied {len(spec_thresholds)} thresholds ({source_tag}) for asset '{twin.asset_id}'")
            return effective

        # --- TIER 4: Twin Baseline Defaults ---
        with self._lock:
            self._cache[machine_id] = {
                "ts": now,
                "thresholds": {},
                "source": "local_baseline"
            }

        return effective

    def get_api_thresholds(self, twin: AssetDigitalTwin) -> Dict[str, float]:
        """
        Returns dynamic alert limits (vibration band, etc.).
        Resolution order:
          1. Upstream API cached limits
          2. Environment variables (.env)
          3. Machine specifications / ISO standards
          4. Default known asset bands (e.g. G-Sensor-01 0.0-0.1)
        """
        self.get_effective_thresholds(twin)  # refreshes the TTL cache if stale
        machine_id = twin.asset_id
        if hasattr(twin, "metadata") and isinstance(twin.metadata, dict):
            machine_id = twin.metadata.get("upstream_machine_id") or machine_id

        with self._lock:
            cached = self._cache.get(machine_id) or {}
        api_t = dict(cached.get("thresholds") or {})

        # If vibration band not in API, check Tier 2 (Env)
        if "vibration_band_max" not in api_t:
            env_t = resolve_env_thresholds(machine_id)
            if "vibration_band_max" in env_t:
                api_t["vibration_band_min"] = env_t.get("vibration_band_min", 0.0)
                api_t["vibration_band_max"] = env_t["vibration_band_max"]

        # If still not found, check Tier 3 (Machine Specs)
        if "vibration_band_max" not in api_t:
            spec_t = resolve_machine_spec_thresholds(twin)
            if "vibration_band_max" in spec_t:
                api_t["vibration_band_min"] = spec_t.get("vibration_band_min", 0.0)
                api_t["vibration_band_max"] = spec_t["vibration_band_max"]

        # If still not found, check fallback known assets
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
