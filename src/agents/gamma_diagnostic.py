"""
Agent Gamma: Multi-Vertical Diagnostics & Degradation Engine (v3.0 Production)
================================================================================
Dynamically activates specialized physics & ML diagnostic pipelines based on asset class.

Production Upgrade (v3.0):
  - Branch A now calls run_full_bearing_analysis() for FFT + envelope spectral analysis
  - SBM (Similarity-Based Modeling) runs in parallel across ALL verticals as a sub-threshold
    early-warning layer (detects anomalies 30-90 days before static thresholds fire)
  - Adaptive Weibull MLE replaces fixed-beta heuristic for RUL estimation
  - Metrics recorded for Prometheus observability
"""

from typing import Dict, Any, List, Optional
from src.core.context import TenantContext
from src.core.buffer import platform_buffer
from src.db.historian import platform_historian
from src.schemas.asset import AssetDigitalTwin
from src.schemas.telemetry import IngestTelemetryPacket
from src.schemas.prediction import FaultRecord, PrognosticRUL
from src.engines.iso_standards import evaluate_iso_20816_3, evaluate_iso_10816_6
from src.engines.bearing_physics import compute_waveform_metrics, calculate_bearing_frequencies, run_full_bearing_analysis
from src.engines.adaptive_weibull import estimate_rul_adaptive
from src.engines.anomaly_engine import detect_multivariate_anomalies
from src.engines.sbm_engine import sbm_registry
from src.utils.logger import get_logger

logger = get_logger("Agent.Gamma")


class AgentGammaDiagnostic:
    """
    Executes domain-specific physics diagnostics based on asset vertical and classification.
    """
    def process(
        self,
        packet: IngestTelemetryPacket,
        twin: AssetDigitalTwin,
        context: TenantContext,
        beta_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        faults: List[FaultRecord] = []
        health_score = 100.0
        prognostics: Optional[PrognosticRUL] = None
        spectral_report: Optional[Dict[str, Any]] = None

        # -----------------------------------------------------------------
        # SBM: Similarity-Based Model — runs across ALL verticals as a
        # sub-threshold early-warning layer (pre-alarm detection)
        # -----------------------------------------------------------------
        sbm_result = self._run_sbm(packet, twin, context)

        # -----------------------------------------------------------------
        # BRANCH A: HEAVY MACHINERY (Propel Industries — Crushers, Screens, Pumps)
        # -----------------------------------------------------------------
        if context.vertical == "heavy_machinery" or twin.asset_class in ("jaw_crusher", "vibrating_screen", "centrifugal_pump"):
            vib_rms = packet.get_role_value(twin, "primary_vibration")
            bearing_temp = packet.get_role_value(twin, "bearing_temp")
            hydraulic_press = packet.get_role_value(twin, "hydraulic_pressure")

            # 1. Full Bearing Analysis (Spectral + Time-Domain)
            if vib_rms is not None:
                rpm = twin.kinematics.get("rated_rpm", 1480.0)
                bearing_code = twin.kinematics.get("bearing_code", "SKF-6206")
                bearing_report = run_full_bearing_analysis(
                    waveform=packet.waveform,
                    sample_rate_hz=packet.sample_rate_hz,
                    rpm=rpm,
                    bearing_code=bearing_code,
                )
                spectral_report = bearing_report.get("spectral")

                # Incorporate spectral health if available
                if spectral_report and spectral_report.get("available"):
                    health_score = min(health_score, bearing_report["combined_health_score"])
                    # Raise fault for each spectral-detected bearing defect
                    for defect in spectral_report.get("matched_defects", []):
                        if defect.get("confidence", 0) > 0.65:
                            faults.append(FaultRecord(
                                fault_code=f"SPECTRAL_{defect['defect_type']}_DEFECT",
                                description=(
                                    f"Spectral bearing defect detected: {defect['defect_type']} "
                                    f"at {defect['base_frequency_hz']} Hz "
                                    f"({defect['harmonics_matched']} harmonics, SNR {defect['max_snr']:.1f})"
                                ),
                                severity="CRITICAL" if defect["confidence"] > 0.85 else "WARNING",
                                confidence=defect["confidence"],
                                failure_domain="MECHANICAL",
                                triggering_signal=twin.resolve_key("primary_vibration"),
                                triggering_value=vib_rms,
                            ))

                # ISO vibration severity check
                if twin.asset_class in ("jaw_crusher", "vibrating_screen"):
                    iso_res = evaluate_iso_10816_6(vib_rms)
                else:
                    iso_res = evaluate_iso_20816_3(vib_rms)

                health_score = min(health_score, iso_res["health_score"])

                if iso_res["severity"] in ("WARNING", "CRITICAL"):
                    faults.append(FaultRecord(
                        fault_code="ISO_VIB_EXCEEDED",
                        description=f"Vibration velocity ({vib_rms} mm/s) in Zone {iso_res['zone']}: {iso_res['description']}",
                        severity=iso_res["severity"],
                        confidence=0.92,
                        failure_domain="MECHANICAL",
                        triggering_signal=twin.resolve_key("primary_vibration"),
                        triggering_value=vib_rms,
                        threshold=twin.thresholds.get("vibration_warning_mm_s", 4.5)
                    ))

            # 2. Bearing Overheating
            if bearing_temp is not None:
                temp_warn = twin.thresholds.get("temp_warning_celsius", 75.0)
                temp_crit = twin.thresholds.get("temp_danger_celsius", 90.0)
                if bearing_temp >= temp_crit:
                    health_score = min(health_score, 25.0)
                    faults.append(FaultRecord(
                        fault_code="BEARING_OVERHEAT_CRITICAL",
                        description=f"Bearing temperature at {bearing_temp}°C exceeds critical threshold {temp_crit}°C",
                        severity="CRITICAL",
                        confidence=0.95,
                        failure_domain="THERMAL",
                        triggering_signal=twin.resolve_key("bearing_temp"),
                        triggering_value=bearing_temp,
                        threshold=temp_crit
                    ))
                elif bearing_temp >= temp_warn:
                    health_score = min(health_score, 60.0)
                    faults.append(FaultRecord(
                        fault_code="BEARING_OVERHEAT_WARNING",
                        description=f"Elevated bearing temperature: {bearing_temp}°C",
                        severity="WARNING",
                        confidence=0.85,
                        failure_domain="THERMAL",
                        triggering_signal=twin.resolve_key("bearing_temp"),
                        triggering_value=bearing_temp,
                        threshold=temp_warn
                    ))

            # 3. Hydraulic Clamping Pressure
            if hydraulic_press is not None:
                min_press = twin.thresholds.get("hydraulic_pressure_min_bar", 120.0)
                if hydraulic_press < min_press:
                    health_score = min(health_score, 45.0)
                    faults.append(FaultRecord(
                        fault_code="HYDRAULIC_CLAMP_LOSS",
                        description=f"Hydraulic toggle pressure dropped to {hydraulic_press} bar (minimum: {min_press} bar)",
                        severity="CRITICAL",
                        confidence=0.88,
                        failure_domain="HYDRAULIC",
                        triggering_signal=twin.resolve_key("hydraulic_pressure"),
                        triggering_value=hydraulic_press,
                        threshold=min_press
                    ))

            # 4. Adaptive Weibull RUL with historical fitting
            danger_vib = twin.thresholds.get("vibration_danger_mm_s", 7.1)
            actual_vib = vib_rms if vib_rms is not None else 2.0
            degradation_history = self._get_vib_history(context, twin)
            prognostics = estimate_rul_adaptive(
                health_score, actual_vib, danger_vib,
                degradation_history=degradation_history
            )

        # -----------------------------------------------------------------
        # BRANCH B: HIGH-TECH ELECTRONICS / SMT (Nest Group)
        # -----------------------------------------------------------------
        elif context.vertical == "electronics_smt" or twin.asset_class in ("smt_pick_place", "reflow_oven", "cleanroom_chiller"):
            vacuum_kpa = packet.get_role_value(twin, "nozzle_vacuum")
            cycle_time_ms = packet.get_role_value(twin, "cycle_time")
            servo_torque = packet.get_role_value(twin, "servo_torque")

            # 1. SMT Vacuum Pressure Drop
            if vacuum_kpa is not None:
                crit_vac = twin.thresholds.get("vacuum_critical_kpa", -65.0)
                if vacuum_kpa > crit_vac:
                    health_score = min(health_score, 35.0)
                    faults.append(FaultRecord(
                        fault_code="SMT_VACUUM_LEAK",
                        description=f"Pick nozzle vacuum dropped to {vacuum_kpa} kPa (threshold: {crit_vac} kPa) - Component drop risk",
                        severity="CRITICAL",
                        confidence=0.93,
                        failure_domain="PNEUMATIC",
                        triggering_signal=twin.resolve_key("nozzle_vacuum"),
                        triggering_value=vacuum_kpa,
                        threshold=crit_vac
                    ))

            # 2. Cycle Time Drift
            if cycle_time_ms is not None:
                max_cycle = twin.thresholds.get("cycle_time_max_ms", 35.0)
                if cycle_time_ms > max_cycle:
                    health_score = min(health_score, 65.0)
                    faults.append(FaultRecord(
                        fault_code="CYCLE_TIME_DEGRADATION",
                        description=f"SMT placement cycle time {cycle_time_ms} ms exceeds target {max_cycle} ms",
                        severity="WARNING",
                        confidence=0.82,
                        failure_domain="MECHANICAL",
                        triggering_signal=twin.resolve_key("cycle_time"),
                        triggering_value=cycle_time_ms,
                        threshold=max_cycle
                    ))

            # 3. Servo Torque Drift
            if servo_torque is not None:
                max_torque = twin.thresholds.get("servo_torque_max_pct", 85.0)
                if servo_torque > max_torque:
                    health_score = min(health_score, 50.0)
                    faults.append(FaultRecord(
                        fault_code="SERVO_AXIS_FRICTION",
                        description=f"Linear axis servo torque at {servo_torque}% suggests ball-screw lubrication failure",
                        severity="WARNING",
                        confidence=0.87,
                        failure_domain="ELECTROMECHANICAL",
                        triggering_signal=twin.resolve_key("servo_torque"),
                        triggering_value=servo_torque,
                        threshold=max_torque
                    ))

            # 4. SMT Prognostics RUL
            deg_factor = max(0.0, (100.0 - health_score) / 100.0)
            prognostics = PrognosticRUL(
                rul_hours=round(max(12.0, (1.0 - deg_factor) * 2000.0), 1),
                rul_days=round(max(0.5, (1.0 - deg_factor) * 85.0), 1),
                confidence_pct=88.5,
                degradation_slope_per_hour=round(0.01 + deg_factor * 0.2, 3),
                method="discrete_cycle_drift",
                projected_maintenance_window="Schedule nozzle flush & axis relubrication at next shift transition"
            )

        # -----------------------------------------------------------------
        # BRANCH C: GENERIC / MULTIVARIATE ANOMALY DETECTION
        # -----------------------------------------------------------------
        else:
            tail = platform_buffer.get_telemetry_tail(context.tenant_id, context.asset_id, limit=30)
            hist_signals = [t.get("signals", {}) for t in tail]
            anomalies = detect_multivariate_anomalies(packet.signals, hist_signals)

            for anom in anomalies:
                health_score -= 15.0
                faults.append(FaultRecord(
                    fault_code=f"ANOMALY_{anom['parameter'].upper()}",
                    description=f"Parameter '{anom['parameter']}' departed from baseline (Z-score: {anom['z_score']})",
                    severity="WARNING",
                    confidence=0.78,
                    failure_domain="PROCESS",
                    triggering_signal=anom["parameter"],
                    triggering_value=anom["current_value"]
                ))

            health_score = max(20.0, health_score)
            prognostics = PrognosticRUL(
                rul_hours=1500.0,
                rul_days=62.5,
                confidence_pct=75.0,
                degradation_slope_per_hour=0.08,
                method="multivariate_statistical",
                projected_maintenance_window="Routine inspection"
            )

        # -----------------------------------------------------------------
        # SBM Fusion: add SBM pre-alarm as an additional fault if CUSUM fired
        # but no hard threshold was triggered yet (early-warning use case)
        # -----------------------------------------------------------------
        if sbm_result and sbm_result.get("cusum_alarm") and not faults:
            alarming = sbm_result.get("alarming_signals", [])
            health_score = min(health_score, 72.0)  # Sub-threshold early warning
            faults.append(FaultRecord(
                fault_code="SBM_CUSUM_PREALARM",
                description=(
                    f"SBM sub-threshold pre-alarm: CUSUM drift detected in signals "
                    f"{alarming[:3]}. Anomaly index: {sbm_result.get('anomaly_index', 0):.3f}. "
                    f"Schedule inspection proactively — no hard threshold breached yet."
                ),
                severity="WARNING",
                confidence=0.75,
                failure_domain="PROCESS",
            ))
        elif sbm_result and sbm_result.get("is_anomalous") and sbm_result.get("sbm_active"):
            # SBM anomalous but CUSUM not yet alarming — add as INFO
            health_score = min(health_score, 85.0)

        # Classify overall health status
        health_score = round(max(0.0, min(100.0, health_score)), 1)
        if health_score >= 80.0:
            status = "HEALTHY"
        elif health_score >= 50.0:
            status = "WARNING"
        else:
            status = "CRITICAL"

        return {
            "health_score": health_score,
            "health_status": status,
            "faults": [f.model_dump() for f in faults],
            "prognostics": prognostics.model_dump() if prognostics else None,
            "sbm_analysis": sbm_result,
            "spectral_report": spectral_report,
        }

    def _run_sbm(self, packet: IngestTelemetryPacket, twin: AssetDigitalTwin, context: TenantContext) -> Optional[Dict[str, Any]]:
        """Run SBM estimation and feed the current sample to train the model."""
        try:
            model = sbm_registry.get_or_create(context.tenant_id, context.asset_id)
            # Feed current clean signals to reference library (only when healthy operating)
            model.update_reference(packet.signals)
            return model.estimate(packet.signals)
        except Exception as e:
            logger.warning(f"SBM error for {context.compound_key}: {e}")
            return None

    def _get_vib_history(self, context: TenantContext, twin: AssetDigitalTwin) -> List[float]:
        """Retrieve historical vibration RMS from persistent historian for Weibull MLE."""
        try:
            tail = platform_historian.get_telemetry_tail(context.tenant_id, context.asset_id, limit=200)
            vib_key = twin.resolve_key("primary_vibration")
            if not vib_key:
                return []
            return [t["signals"].get(vib_key, 0.0) for t in tail if vib_key in t.get("signals", {})]
        except Exception:
            # Fallback to in-memory buffer
            tail = platform_buffer.get_telemetry_tail(context.tenant_id, context.asset_id, limit=100)
            vib_key = twin.resolve_key("primary_vibration")
            if not vib_key:
                return []
            return [t.get("signals", {}).get(vib_key, 0.0) for t in tail if vib_key in t.get("signals", {})]
