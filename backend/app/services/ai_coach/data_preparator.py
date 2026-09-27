"""Transform raw DuckDB telemetry into compact statistical summaries for the LLM.

Each prepare_* function returns a plain string that gets injected
into the user message — the LLM never sees raw sample arrays.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
import duckdb
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)

NUM_ZONES = 10
_MAX_LAPS_FOR_BEST = 30  # caps the fusing work when computing per-segment bests
# Hand-tuned per-circuit corner segments shipped with the frontend (upstream data)
_SEGMENTS_FILE = Path(__file__).resolve().parents[4] / "frontend" / "src" / "assets" / "track_segments.json"


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


def _wheel_array(df, col: str) -> Optional[np.ndarray]:
    """Per-wheel channels are stored as one [FL, FR, RL, RR] list per sample -> (n, 4) array."""
    if col not in df.columns:
        return None
    rows = [x if isinstance(x, (list, tuple, np.ndarray)) and len(x) == 4 else [np.nan] * 4 for x in df[col]]
    arr = np.array(rows, dtype=float)
    return arr if arr.size and not np.all(np.isnan(arr)) else None


def _find_track_key(data: dict, track_name: str) -> Optional[str]:
    """Same matching as the frontend's mini-sectors: exact, accent/punctuation-insensitive, then word overlap."""
    def clean(name: str) -> str:
        name = unicodedata.normalize("NFD", name)
        name = "".join(c for c in name if unicodedata.category(c) != "Mn").lower()
        return re.sub(r"[^a-z0-9]", " ", name).strip()

    target = clean(track_name)
    for key in data:
        if key.lower() == track_name.lower() or clean(key) == target:
            return key
    target_words = [w for w in target.split() if len(w) > 2]
    for key in data:
        key_words = [w for w in clean(key).split() if len(w) > 2]
        overlap = [w for w in key_words if w in target_words]
        if key_words and target_words and len(overlap) >= min(len(key_words), len(target_words)) * 0.7:
            return key
    return None


def _load_segments(meta: dict, lap_length: float) -> tuple[list[tuple[str, float, float]], str]:
    """Split the lap into the track's hand-tuned corner segments (upstream track_segments.json).

    Falls back to 10 equal distance zones when the circuit isn't known. Returns
    ([(name, start_m, end_m), ...], source description).
    """
    try:
        data = json.loads(_SEGMENTS_FILE.read_text(encoding="utf-8"))
        key = _find_track_key(data, meta.get("TrackName", ""))
        if key:
            layouts = data[key]
            chosen = layouts.get(meta.get("TrackLayout", "")) or layouts.get("Default") or next(iter(layouts.values()))
            bounds, prev = [], 0.0
            for seg in chosen.get("segments", []):
                bounds.append((seg["name"], prev, float(seg["end"])))
                prev = float(seg["end"])
            if bounds:
                # The last segment runs to the finish line whatever this car's measured lap length
                bounds[-1] = (bounds[-1][0], bounds[-1][1], max(bounds[-1][2], lap_length))
                return bounds, "segments du circuit (complexes de virages)"
    except Exception as e:
        logger.warning(f"Track segments unavailable, using equal zones: {e}")

    step = lap_length / NUM_ZONES
    return [(f"Zone {i + 1}", i * step, (i + 1) * step) for i in range(NUM_ZONES)], "10 zones de distance égales"


def _lap_arrays(df) -> Optional[dict]:
    """Extract the scalar channels used by the segment metrics, as numpy arrays."""
    if "Lap Dist" not in df.columns or "Time" not in df.columns:
        return None
    a = {"dist": np.maximum.accumulate(np.nan_to_num(np.array(df["Lap Dist"], dtype=float))),
         "time": np.array(df["Time"], dtype=float)}
    for key, col in (("speed", "Ground Speed"), ("brake", "Brake Pos"), ("throttle", "Throttle Pos"),
                     ("lat_g", "G Force Lat")):
        a[key] = np.array(df[col], dtype=float) if col in df.columns else None
    if a["speed"] is not None and np.nanmax(a["speed"]) < 100:
        a["speed"] = a["speed"] * 3.6  # m/s -> km/h
    return a


def _time_at(a: dict, d: float) -> float:
    return float(np.interp(d, a["dist"], a["time"]))


def _segment_stats(a: dict, name: str, start: float, end: float) -> dict:
    """Driver metrics for one track segment. Distances are metres from the start line."""
    mask = (a["dist"] >= start) & (a["dist"] < end)
    stats: dict = {"segment": name, "from_m": int(start), "to_m": int(end),
                   "time_s": round(_time_at(a, end) - _time_at(a, start), 3)}
    if mask.sum() < 3:
        return stats
    dist, t = a["dist"][mask], a["time"][mask]
    dt = np.gradient(t)

    i_min = 0
    if a["speed"] is not None:
        seg_speed = a["speed"][mask]
        i_min = int(np.nanargmin(seg_speed))
        stats.update({
            "entry_kmh": round(float(np.interp(start, a["dist"], a["speed"])), 1),
            "min_kmh": round(float(seg_speed[i_min]), 1),
            "min_at_m": int(dist[i_min]),
            "exit_kmh": round(float(np.interp(end, a["dist"], a["speed"])), 1),
        })

    brake = a["brake"][mask] if a["brake"] is not None else None
    throttle = a["throttle"][mask] if a["throttle"] is not None else None
    if brake is not None:
        on = brake > 10
        if on.any():
            stats["brake_start_m"] = int(dist[np.argmax(on)])
            stats["brake_peak_pct"] = round(float(np.nanmax(brake)), 1)
            stats["braking_s"] = round(float(dt[on].sum()), 2)
            # Trail braking: still on the brake while already turning hard
            if a["lat_g"] is not None:
                trail = on & (np.abs(a["lat_g"][mask]) > 1.0)
                stats["trail_braking_s"] = round(float(dt[trail].sum()), 2)
    if throttle is not None:
        stats["full_throttle_pct"] = round(float(dt[throttle > 95].sum() / dt.sum() * 100), 1)
        after_min = throttle[i_min:] > 90
        if after_min.any():
            stats["full_throttle_from_m"] = int(dist[i_min + int(np.argmax(after_min))])
        if brake is not None:
            coast = (throttle < 10) & (brake < 5)
            stats["coasting_s"] = round(float(dt[coast].sum()), 2)
    if a["lat_g"] is not None:
        stats["max_lat_g"] = round(float(np.nanmax(np.abs(a["lat_g"][mask]))), 2)
    return stats


# Fields repeated for the reference lap next to the analysed lap's own (keeps the prompt compact)
_REF_FIELDS = ("time_s", "entry_kmh", "min_kmh", "exit_kmh", "brake_start_m", "full_throttle_from_m")


def prepare_lap_analysis(
    db_path: str,
    lap_idx: int,
    reference_lap_idx: Optional[int] = None,
) -> str:
    """Build a segment-by-segment summary of one lap, compared to a reference lap.

    Without an explicit reference, the session's best valid lap is used. Each segment
    also carries the driver's best time for it across the session, which gives the
    ideal lap and shows where time is left on the table.
    """
    meta = _get_metadata(db_path)
    laps = _get_laps_info(db_path)

    target_lap = next((l for l in laps if l["lap"] == lap_idx), None)
    if not target_lap:
        return f"Error: Lap {lap_idx} not found in session."

    df = _fuse_lap(db_path, target_lap)
    a = _lap_arrays(df)
    if a is None:
        return "Error: Lap has no distance/time channels."
    bounds, seg_source = _load_segments(meta, float(a["dist"][-1]))
    segments = [_segment_stats(a, *b) for b in bounds]

    valid = [l for l in laps if l["isValid"] and not l.get("isOutLap")][:_MAX_LAPS_FOR_BEST]
    ref_source = "choisi par le pilote"
    if reference_lap_idx is None:
        others = [l for l in valid if l["lap"] != lap_idx]
        if others:
            reference_lap_idx = min(others, key=lambda l: l["duration"])["lap"]
            ref_source = "meilleur des autres tours valides de la session (le tour analysé est exclu)"

    # Every valid lap's segment times -> best per segment (ideal lap)
    lap_arrays = {lap_idx: a}
    for l in valid:
        if l["lap"] not in lap_arrays:
            la = _lap_arrays(_fuse_lap(db_path, l))
            if la is not None:
                lap_arrays[l["lap"]] = la
    best_seg = [min((_time_at(la, e) - _time_at(la, s), n) for n, la in lap_arrays.items())
                for _, s, e in bounds]

    ref_lap = next((l for l in laps if l["lap"] == reference_lap_idx), None) if reference_lap_idx is not None else None
    ref_a = None
    if ref_lap:
        ref_a = lap_arrays.get(reference_lap_idx) or _lap_arrays(_fuse_lap(db_path, ref_lap))

    for i, seg in enumerate(segments):
        best_t, best_lap = best_seg[i]
        seg["best_in_session_s"] = round(best_t, 3)
        seg["best_on_lap"] = best_lap
        seg["loss_vs_best_s"] = round(seg["time_s"] - best_t, 3)
        if ref_a is not None:
            ref = _segment_stats(ref_a, *bounds[i])
            seg["reference"] = {k: ref[k] for k in _REF_FIELDS if k in ref}
            seg["delta_vs_ref_s"] = round(seg["time_s"] - ref["time_s"], 3)

    result: dict = {
        "circuit": meta.get("TrackName", "Unknown"),
        "layout": meta.get("TrackLayout", ""),
        "car": meta.get("CarName", "Unknown"),
        "car_class": meta.get("CarClass", "Unknown"),
        "driver": meta.get("DriverName", "Unknown"),
        "lap_number": lap_idx,
        "lap_time": round(target_lap["duration"], 3),
        "sectors": {k: target_lap.get(k) for k in ("s1", "s2", "s3")},
        "ideal_lap_s": round(sum(t for t, _ in best_seg), 3),
        "segmentation": seg_source,
        "units": "distances en m depuis la ligne, vitesses en km/h, temps en s",
        "segments": segments,
    }
    if ref_a is not None:
        result["reference_lap"] = {"lap_number": reference_lap_idx,
                                   "lap_time": round(ref_lap["duration"], 3), "source": ref_source}
        result["delta_vs_ref_s"] = round(target_lap["duration"] - ref_lap["duration"], 3)

    tyre_info = _tyre_summary(df)
    if tyre_info:
        result["tyres"] = tyre_info

    return json.dumps(result, ensure_ascii=False, default=str)


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
        tl = _wheel_array(df, "TyreLoad")
        if tl is not None:
            result["tyre_load_avg_N"] = {
                "FL": round(float(np.nanmean(tl[:, 0])), 0),
                "FR": round(float(np.nanmean(tl[:, 1])), 0),
                "RL": round(float(np.nanmean(tl[:, 2])), 0),
                "RR": round(float(np.nanmean(tl[:, 3])), 0),
            }

    # Grip fraction
    if "GripFract" in df.columns:
        gf = _wheel_array(df, "GripFract")
        if gf is not None:
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
    """Per-wheel tyre/brake state over the lap: temps I/C/O, carcass, pressures, wear, brakes."""
    wheels = ["FL", "FR", "RL", "RR"]
    info: dict = {}

    def avg(arr):
        return {w: round(float(np.nanmean(arr[:, i])), 1) for i, w in enumerate(wheels)}

    # DAMPlugin rubber temps first (steadier), native I/C/O channels otherwise
    for cols in (("TyresRubberTempInner", "TyresRubberTemp", "TyresRubberTempOuter"),
                 ("TyresTempInside", "TyresTempCentre", "TyresTempOutside")):
        arrs = [_wheel_array(df, c) for c in cols]
        if all(x is not None for x in arrs):
            info["temp_inner_avg_c"], info["temp_centre_avg_c"], info["temp_outer_avg_c"] = (avg(x) for x in arrs)
            break

    for label, col in (("carcass_avg_c", "TyresCarcassTemp"), ("pressure_avg_kpa", "TyresPressure"),
                       ("brake_temp_avg_c", "Brakes Temp")):
        arr = _wheel_array(df, col)
        if arr is not None:
            info[label] = avg(arr)

    wear = _wheel_array(df, "Tyres Wear")
    if wear is not None:
        rows = wear[~np.isnan(wear).any(axis=1)]
        if len(rows) > 1:
            info["wear_this_lap_pct"] = {w: round(float(rows[-1, i] - rows[0, i]), 2) for i, w in enumerate(wheels)}

    return info if info else None
