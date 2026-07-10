"""Transform raw DuckDB telemetry into compact statistical summaries for the LLM.

Each prepare_* function returns a plain string (max ~4000 chars) that gets injected
into the user message — the LLM never sees raw sample arrays.
"""

from __future__ import annotations

import json
import logging
import duckdb
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

NUM_ZONES = 10


def _get_metadata(db_path: str) -> dict:
    con = duckdb.connect(db_path, read_only=True)
    try:
        rows = con.execute("SELECT key, value FROM metadata").fetchall()
        return {k: v for k, v in rows}
    finally:
        con.close()


def _get_laps_info(db_path: str) -> list[dict]:
    from ..telemetry_service import TelemetryService
    return TelemetryService.get_laps_header(db_path)["laps"]


def _fuse_lap(db_path: str, lap: dict, freq: int = 20):
    """Fuse telemetry for a single lap at the given frequency."""
    from ..telemetry_service import TelemetryService
    df = TelemetryService.fuse_session_data(
        db_path,
        target_freq=freq,
        trim_start_time=lap["startTime"],
        trim_end_time=lap["endTime"],
    )
    return df


def _zone_stats(df, zone_idx: int, total_zones: int = NUM_ZONES) -> dict:
    """Compute stats for a single distance-based zone."""
    lap_dist = np.array(df["Lap Dist"]) if "Lap Dist" in df.columns else None
    if lap_dist is None or len(lap_dist) == 0:
        return {}

    max_dist = np.nanmax(lap_dist)
    if max_dist <= 0:
        return {}

    zone_start = (zone_idx / total_zones) * max_dist
    zone_end = ((zone_idx + 1) / total_zones) * max_dist
    mask = (lap_dist >= zone_start) & (lap_dist < zone_end)

    if not mask.any():
        return {}

    stats: dict = {"zone": f"{zone_idx * 10}-{(zone_idx + 1) * 10}%"}

    # Speed
    if "Ground Speed" in df.columns:
        speed = np.array(df["Ground Speed"])[mask]
        speed_vals = speed[~np.isnan(speed)]
        if len(speed_vals) > 0:
            max_speed = float(np.max(speed_vals))
            # Convert to km/h if in m/s
            if max_speed < 100:
                speed_vals = speed_vals * 3.6
            stats["speed_min"] = round(float(np.min(speed_vals)), 1)
            stats["speed_max"] = round(float(np.max(speed_vals)), 1)

    # Brake
    if "Brake Pos" in df.columns:
        brake = np.array(df["Brake Pos"])[mask]
        brake_vals = brake[~np.isnan(brake)]
        if len(brake_vals) > 0:
            stats["max_brake_pct"] = round(float(np.max(brake_vals)), 1)
            heavy_brake = brake_vals > 30
            if heavy_brake.any():
                first_heavy = np.argmax(heavy_brake)
                stats["brake_point_pct"] = round(first_heavy / len(brake_vals) * 100, 1)

    # Throttle
    if "Throttle Pos" in df.columns:
        throttle = np.array(df["Throttle Pos"])[mask]
        throttle_vals = throttle[~np.isnan(throttle)]
        if len(throttle_vals) > 0:
            throttle_on = throttle_vals > 20
            if throttle_on.any():
                first_on = np.argmax(throttle_on)
                stats["throttle_on_pct"] = round(first_on / len(throttle_vals) * 100, 1)

    # Lateral G
    if "G Force Lat" in df.columns:
        g_lat = np.array(df["G Force Lat"])[mask]
        g_vals = g_lat[~np.isnan(g_lat)]
        if len(g_vals) > 0:
            stats["max_lat_g"] = round(float(np.max(np.abs(g_vals))), 2)

    # Time spent in zone
    if "Time" in df.columns:
        time = np.array(df["Time"])[mask]
        if len(time) > 1:
            stats["time_s"] = round(float(time[-1] - time[0]), 2)

    return stats


def prepare_lap_analysis(
    db_path: str,
    lap_idx: int,
    reference_lap_idx: Optional[int] = None,
) -> str:
    """Build a compact zone-by-zone summary for a single lap."""
    meta = _get_metadata(db_path)
    laps = _get_laps_info(db_path)

    target_lap = next((l for l in laps if l["lap"] == lap_idx), None)
    if not target_lap:
        return f"Error: Lap {lap_idx} not found in session."

    df = _fuse_lap(db_path, target_lap)
    zones = [_zone_stats(df, i) for i in range(NUM_ZONES)]
    zones = [z for z in zones if z]

    # Tyre temps summary (average over the lap)
    tyre_info = _tyre_summary(df)

    result = {
        "circuit": meta.get("TrackName", "Unknown"),
        "car": meta.get("CarName", "Unknown"),
        "car_class": meta.get("CarClass", "Unknown"),
        "driver": meta.get("DriverName", "Unknown"),
        "lap_number": lap_idx,
        "lap_time": round(target_lap["duration"], 3),
        "sectors": {
            "s1": target_lap.get("s1"),
            "s2": target_lap.get("s2"),
            "s3": target_lap.get("s3"),
        },
        "zones": zones,
    }
    if tyre_info:
        result["tyres"] = tyre_info

    # Reference lap comparison
    if reference_lap_idx is not None:
        ref_lap = next((l for l in laps if l["lap"] == reference_lap_idx), None)
        if ref_lap:
            ref_df = _fuse_lap(db_path, ref_lap)
            ref_zones = [_zone_stats(ref_df, i) for i in range(NUM_ZONES)]
            ref_zones = [z for z in ref_zones if z]
            result["reference"] = {
                "lap_number": reference_lap_idx,
                "lap_time": round(ref_lap["duration"], 3),
                "sectors": {
                    "s1": ref_lap.get("s1"),
                    "s2": ref_lap.get("s2"),
                    "s3": ref_lap.get("s3"),
                },
                "zones": ref_zones,
            }
            result["delta_s"] = round(target_lap["duration"] - ref_lap["duration"], 3)

    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > 5000:
        text = text[:5000] + "...(truncated)"
    return text


def prepare_session_analysis(db_path: str) -> str:
    """Build a per-lap summary for the entire session."""
    meta = _get_metadata(db_path)
    laps = _get_laps_info(db_path)

    lap_summaries = []
    for lap in laps:
        entry = {
            "lap": lap["lap"],
            "time": round(lap["duration"], 3),
            "valid": lap["isValid"],
            "s1": lap.get("s1"),
            "s2": lap.get("s2"),
            "s3": lap.get("s3"),
            "stint": lap.get("stint"),
            "fuel_used": round(lap.get("fuelUsed", 0), 3),
            "out_lap": lap.get("isOutLap", False),
        }
        lap_summaries.append(entry)

    # Consistency metrics (valid laps only, excluding out laps)
    valid_times = [l["time"] for l in lap_summaries if l["valid"] and not l["out_lap"]]
    consistency = {}
    if len(valid_times) >= 2:
        consistency = {
            "best": round(min(valid_times), 3),
            "worst": round(max(valid_times), 3),
            "median": round(float(np.median(valid_times)), 3),
            "stdev": round(float(np.std(valid_times)), 3),
            "count": len(valid_times),
        }

    # Stint breakdown
    stints: dict[int, list] = {}
    for l in lap_summaries:
        s = l.get("stint", 1)
        stints.setdefault(s, []).append(l)

    stint_summaries = []
    for stint_id, stint_laps in sorted(stints.items()):
        valid_in_stint = [l["time"] for l in stint_laps if l["valid"] and not l["out_lap"]]
        summary = {
            "stint": stint_id,
            "laps": len(stint_laps),
            "fuel_total": round(sum(l.get("fuel_used", 0) for l in stint_laps), 2),
        }
        if len(valid_in_stint) >= 2:
            summary["degradation_s_per_lap"] = round(
                (valid_in_stint[-1] - valid_in_stint[0]) / (len(valid_in_stint) - 1), 3
            )
            summary["best"] = round(min(valid_in_stint), 3)
        stint_summaries.append(summary)

    result = {
        "circuit": meta.get("TrackName", "Unknown"),
        "car": meta.get("CarName", "Unknown"),
        "car_class": meta.get("CarClass", "Unknown"),
        "driver": meta.get("DriverName", "Unknown"),
        "weather": meta.get("WeatherConditions", "Unknown"),
        "laps": lap_summaries,
        "consistency": consistency,
        "stints": stint_summaries,
    }

    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > 5000:
        text = text[:5000] + "...(truncated)"
    return text


def prepare_setup_analysis(
    db_path: str,
    setup_data: Optional[dict] = None,
    lap_idx: Optional[int] = None,
) -> str:
    """Build a summary of DAMPlugin advanced channels + setup for the setup advisor."""
    meta = _get_metadata(db_path)
    laps = _get_laps_info(db_path)

    # Pick the best valid lap if no lap specified
    if lap_idx is None:
        valid = [l for l in laps if l["isValid"] and not l.get("isOutLap")]
        if valid:
            target = min(valid, key=lambda x: x["duration"])
            lap_idx = target["lap"]
        elif laps:
            lap_idx = laps[0]["lap"]
        else:
            return "Error: No laps in session."

    target_lap = next((l for l in laps if l["lap"] == lap_idx), None)
    if not target_lap:
        return f"Error: Lap {lap_idx} not found."

    df = _fuse_lap(db_path, target_lap)

    result: dict = {
        "circuit": meta.get("TrackName", "Unknown"),
        "car": meta.get("CarName", "Unknown"),
        "car_class": meta.get("CarClass", "Unknown"),
        "lap": lap_idx,
        "lap_time": round(target_lap["duration"], 3),
    }

    # Tyre temperatures (I/C/O)
    tyre_info = _tyre_summary(df)
    if tyre_info:
        result["tyres"] = tyre_info

    # Ride heights
    if "FrontRideHeight" in df.columns and "RearRideHeight" in df.columns:
        frh = np.array(df["FrontRideHeight"])
        rrh = np.array(df["RearRideHeight"])
        frh, rrh = frh[~np.isnan(frh)], rrh[~np.isnan(rrh)]
        if len(frh) > 0 and len(rrh) > 0:
            result["ride_heights_mm"] = {
                "front_avg": round(float(np.mean(frh)), 1),
                "front_min": round(float(np.min(frh)), 1),
                "rear_avg": round(float(np.mean(rrh)), 1),
                "rear_min": round(float(np.min(rrh)), 1),
            }

    # Aero forces
    for ch in ("DownforceFront", "DownforceRear", "Drag"):
        if ch in df.columns:
            vals = np.array(df[ch])
            vals = vals[~np.isnan(vals)]
            if len(vals) > 0:
                result.setdefault("aero", {})[ch] = {
                    "avg": round(float(np.mean(vals)), 1),
                    "max": round(float(np.max(vals)), 1),
                }

    # Tyre load
    if "TyreLoad" in df.columns:
        tl = np.array(df["TyreLoad"])
        if tl.ndim == 2 and tl.shape[1] == 4:
            result["tyre_load_avg_N"] = {
                "FL": round(float(np.nanmean(tl[:, 0])), 0),
                "FR": round(float(np.nanmean(tl[:, 1])), 0),
                "RL": round(float(np.nanmean(tl[:, 2])), 0),
                "RR": round(float(np.nanmean(tl[:, 3])), 0),
            }

    # Grip fraction
    if "GripFract" in df.columns:
        gf = np.array(df["GripFract"])
        if gf.ndim == 2 and gf.shape[1] == 4:
            result["grip_fraction_avg"] = {
                "FL": round(float(np.nanmean(gf[:, 0])), 3),
                "FR": round(float(np.nanmean(gf[:, 1])), 3),
                "RL": round(float(np.nanmean(gf[:, 2])), 3),
                "RR": round(float(np.nanmean(gf[:, 3])), 3),
            }

    # Current setup
    if setup_data:
        result["current_setup"] = setup_data

    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > 5000:
        text = text[:5000] + "...(truncated)"
    return text


def _tyre_summary(df) -> Optional[dict]:
    """Extract average tyre temps (centre, and I/C/O if available) for all 4 wheels."""
    wheels = ["FL", "FR", "RL", "RR"]
    info: dict = {}

    if "TyresTempCentre" in df.columns:
        tc = np.array(df["TyresTempCentre"])
        if tc.ndim == 2 and tc.shape[1] == 4:
            info["centre_avg"] = {w: round(float(np.nanmean(tc[:, i])), 1) for i, w in enumerate(wheels)}

    for label, col in [("inside_avg", "TyresTempLeft"), ("outside_avg", "TyresTempRight")]:
        if col in df.columns:
            arr = np.array(df[col])
            if arr.ndim == 2 and arr.shape[1] == 4:
                info[label] = {w: round(float(np.nanmean(arr[:, i])), 1) for i, w in enumerate(wheels)}

    if "TyresPressure" in df.columns:
        tp = np.array(df["TyresPressure"])
        if tp.ndim == 2 and tp.shape[1] == 4:
            info["pressure_avg_kpa"] = {w: round(float(np.nanmean(tp[:, i])), 1) for i, w in enumerate(wheels)}

    return info if info else None
