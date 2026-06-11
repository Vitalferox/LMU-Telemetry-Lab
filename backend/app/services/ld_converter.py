"""
Convert native LMU .ld telemetry files to the app's .duckdb schema.

Binary layout (native LMU .ld, 245 channels):
  - File header: channel list ptr at offset 8 (uint32 LE)
  - Fixed header block: <I4xII20xI24xHHHI8sHHI4x16s16x16s16x64s64s64x64s64x>
      - vals[12] = date (16s)  vals[13] = time (16s)
      - vals[14] = driver (64s)  vals[16] = venue (64s)
  - Extended header (478 → ch_off): car class at 0x1f54, car name at 0x1f94
  - Channel descriptors: 124-byte linked list at ch_off
      struct: <IIIIHHHHhhhh32s8s12s40x>
      = (prev, next, data_ptr, n_data, counter, dtype_a, dtype_sz, freq,
         shift, mul, scale, dec, name[32], shortname[8], unit[12], pad[40])
  - Decoding formula: value = raw / scale * 10^(-dec) * mul + shift

DuckDB schema target:
  - metadata: (key TEXT, value TEXT)
  - channelsList: (channelName TEXT, frequency INT, unit TEXT)
  - GPS Time: continuous (value FLOAT) — master clock, from Session Elapsed Time
  - Most channels: continuous (value FLOAT) — no ts column
  - Multi-wheel channels: (value1..4 FLOAT) — FL, FR, RL, RR
  - Event channels: (ts FLOAT, value ...) — Lap, Lap Time, Last Sector1/2, In Pits, etc.
"""
from __future__ import annotations

import logging
import os
import re
import struct
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_CHAN_FMT = "<IIIIHHHHhhhh32s8s12s40x"
_CHAN_SIZE = struct.calcsize(_CHAN_FMT)  # 124

_HEAD_FMT = "<I4xII20xI24xHHHI8sHHI4x16s16x16s16x64s64s64x64s64x"
_CAR_CLASS_OFF = 0x1F54   # offset of car class string in extended header
_CAR_NAME_OFF  = 0x1F94   # offset of car name string in extended header


# ── Decode helpers ─────────────────────────────────────────────────────────────

def _s(b: bytes) -> str:
    return b.split(b"\x00")[0].decode("latin-1", "replace").strip()


def _decode(raw_arr: np.ndarray, shift: int, mul: int, scale: int, dec: int) -> np.ndarray:
    """Formula A: value = raw / scale * 10^(-dec) * mul + shift"""
    s = float(scale) if scale != 0 else 1.0
    return raw_arr.astype(np.float64) / s * (10.0 ** -dec) * float(mul) + float(shift)


def _read_channel_data(raw: bytes, dptr: int, n: int, dtype_a: int, dtype_sz: int) -> np.ndarray:
    if dtype_a == 0x07:
        np_t = {2: np.float16, 4: np.float32}.get(dtype_sz, np.float32)
    else:
        np_t = {1: np.int8, 2: np.int16, 4: np.int32}.get(dtype_sz, np.int16)
    return np.frombuffer(raw, dtype=np_t, count=n, offset=dptr).astype(np.float64)


# ── Parsing ────────────────────────────────────────────────────────────────────

def _parse_channels(raw: bytes, ch_off: int) -> dict:
    channels: dict[str, dict] = {}
    off = ch_off
    while off and off + _CHAN_SIZE <= len(raw):
        vals = struct.unpack_from(_CHAN_FMT, raw, off)
        prev_p, next_p, dptr, n, counter, dtype_a, dtype_sz, freq, shift, mul, scale, dec, name_b, sn_b, unit_b = vals
        name = _s(name_b)
        unit = _s(unit_b)
        if name and dtype_sz > 0 and dptr > 0 and n > 0:
            channels[name] = {
                "dtype_a": dtype_a, "dtype_sz": dtype_sz, "freq": freq,
                "shift": shift, "mul": mul, "scale": scale, "dec": dec,
                "dptr": dptr, "n": n, "unit": unit,
            }
        off = next_p
    return channels


def _parse_metadata(raw: bytes) -> dict:
    head_vals = struct.unpack_from(_HEAD_FMT, raw, 0)
    date_s = _s(head_vals[12])
    time_s = _s(head_vals[13])
    driver = _s(head_vals[14])
    venue  = _s(head_vals[16])

    car_class = ""
    car_name  = ""
    if _CAR_CLASS_OFF + 64 <= len(raw):
        car_class = _s(raw[_CAR_CLASS_OFF : _CAR_CLASS_OFF + 64])
    if _CAR_NAME_OFF + 64 <= len(raw):
        car_name = _s(raw[_CAR_NAME_OFF : _CAR_NAME_OFF + 64])

    return {
        "date_str": date_s,   # "DD/MM/YYYY"
        "time_str": time_s,   # "HH:MM:SS"
        "driver":   driver,
        "venue":    venue,
        "car_class": car_class,
        "car_name":  car_name,
    }


def _decode_all(raw: bytes, channels: dict) -> dict[str, np.ndarray]:
    decoded: dict[str, np.ndarray] = {}
    for name, ch in channels.items():
        try:
            arr = _read_channel_data(raw, ch["dptr"], ch["n"], ch["dtype_a"], ch["dtype_sz"])
            decoded[name] = _decode(arr, ch["shift"], ch["mul"], ch["scale"], ch["dec"])
        except Exception as e:
            log.warning("Skipping channel %s: %s", name, e)
    return decoded


# ── Session type from filename ─────────────────────────────────────────────────

def _session_type_from_filename(path: str) -> str:
    fname = os.path.basename(path).upper()
    if "- P" in fname or "_P" in fname:
        return "Practice"
    if "- Q" in fname or "_Q" in fname:
        return "Qualifying"
    if "- R" in fname or "_R" in fname:
        return "Race"
    return "Practice"


# ── Time axis builder ──────────────────────────────────────────────────────────

def _build_time_for_channel(session_time: np.ndarray, ch_n: int) -> np.ndarray:
    """Map ch_n samples onto the session_time array proportionally (same as fusion engine)."""
    indices = np.linspace(0, len(session_time) - 1, ch_n)
    return np.interp(indices, np.arange(len(session_time)), session_time)


# ── Lap detection ──────────────────────────────────────────────────────────────

def _build_lap_events(lap_numbers: np.ndarray, session_time: np.ndarray) -> list[tuple[float, int]]:
    """Detect lap crossings from Lap Number channel. Returns [(ts, lap_num)]."""
    events: list[tuple[float, int]] = [(float(session_time[0]), 0)]
    if len(lap_numbers) == 0:
        return events

    ln_int = lap_numbers.astype(int)
    changes = np.where(np.diff(ln_int) != 0)[0]
    for idx in changes:
        new_lap = ln_int[idx + 1]
        ts_idx = min(idx + 1, len(session_time) - 1)
        events.append((float(session_time[ts_idx]), int(new_lap)))

    return sorted(events, key=lambda x: x[0])


# ── Sector / laptime event detection ──────────────────────────────────────────

def _detect_state_changes(arr: np.ndarray, time_arr: np.ndarray, min_gap: float = 0.1) -> list[tuple[float, float]]:
    """Return [(ts, value)] for state changes in a sampled array."""
    if len(arr) == 0:
        return []
    events: list[tuple[float, float]] = [(float(time_arr[0]), float(arr[0]))]
    for i in range(1, len(arr)):
        if abs(arr[i] - arr[i - 1]) > 1e-6:
            ts = float(time_arr[i])
            if ts - events[-1][0] >= min_gap:
                events.append((ts, float(arr[i])))
    return events


def _build_laptime_events(last_laptime: np.ndarray, time_arr: np.ndarray) -> list[tuple[float, float]]:
    """Build Lap Time events: fired when last_laptime value increases."""
    events: list[tuple[float, float]] = []
    prev = float(last_laptime[0]) if len(last_laptime) > 0 else 0.0
    for i in range(1, len(last_laptime)):
        cur = float(last_laptime[i])
        if cur > 0.1 and abs(cur - prev) > 0.01:
            events.append((float(time_arr[i]), cur))
            prev = cur
    return events


# ── Main converter ─────────────────────────────────────────────────────────────

# Continuous single-value channel mapping: (ld_name, duckdb_name)
_SINGLE_MAP: list[tuple[str, str]] = [
    ("Ground Speed",           "Ground Speed"),
    ("Throttle Pos",           "Throttle Pos"),
    ("Throttle Pos Filtered",  "Throttle Pos Unfiltered"),   # Filtered → use as source for both
    ("Brake Pos",              "Brake Pos"),
    ("Brake Pos Filtered",     "Brake Pos Unfiltered"),
    ("Clutch Pos",             "Clutch Pos"),
    ("Clutch Pos Filtered",    "Clutch Pos Unfiltered"),
    ("Clutch RPM",             "Clutch RPM"),
    ("Engine RPM",             "Engine RPM"),
    ("Engine Max RPM",         "Engine Max RPM"),
    ("Steering Wheel Position","Steering Pos"),
    ("Steering",               "Steering Pos Unfiltered"),
    ("Steering Filtered",      "Steering Pos Unfiltered"),
    ("G Force Lat",            "G Force Lat"),
    ("G Force Long",           "G Force Long"),
    ("G Force Vert",           "G Force Vert"),
    ("Fuel Level",             "Fuel Level"),
    ("GPS Latitude",           "GPS Latitude"),
    ("GPS Longitude",          "GPS Longitude"),
    ("Lap Distance",           "Lap Dist"),
    ("Total Dist",             "Total Dist"),
    ("Path Lateral",           "Path Lateral"),
    ("Track Edge",             "Track Edge"),
    ("Front Ride Height",      "FrontRideHeight"),
    ("Rear Ride Height",       "RearRideHeight"),
    ("Front 3rd Pos",          "Front3rdDeflection"),
    ("Rear 3rd Pos",           "Rear3rdDeflection"),
    ("Eng Water Temp",         "Engine Water Temp"),
    ("Eng Oil Temp",           "Engine Oil Temp"),
    ("Track Temperature",      "Track Temperature"),
    ("Ambient Temperature",    "Ambient Temperature"),
    ("Turbo Boost Pressure",   "Turbo Boost Pressure"),
    ("Battery Charge Level",   "SoC"),
    ("Virtual Energy",         "Virtual Energy"),
    ("Regen Rate",             "Regen Rate"),
    ("Motor Temp",             "MotorTemp"),
    ("Motor RPM",              "MotorRPM"),
    ("Motor Torque",           "MotorTorque"),
    ("Motor State",            "MotorState"),
    ("Motor Water Temp",       "MotorWaterTemp"),
    ("Brake Bias Rear",        "Brake Bias Rear"),
    ("Drag",                   "Drag"),
    ("Cloud Darkness",         "CloudDarkness"),
    ("Yellow Flag State",      "Yellow Flag State"),
    ("Speed Limiter On",       "Speed Limiter"),
    ("Finish Status",          "Finish Status"),
    ("Front Flap Activated",   "FrontFlapActivated"),
    ("Rear Flap Activated",    "RearFlapActivated"),
    ("Rear Flap Legal Status", "RearFlapLegalStatus"),
    ("Anti Stall Activated",   "AntiStall Activated"),
    ("Headlights State",       "Headlights State"),
    ("Wind Heading",           "Wind Heading"),
    ("Wind Speed",             "Wind Speed"),
    ("Current Sector",         "Current Sector"),
    ("Cur Sector 1",           "Current Sector1"),
    ("Cur Sector 2",           "Current Sector2"),
    ("Last Impact Magnitude",  "LastImpactMagnitude"),
    ("Steering Shaft Torque",  "Steering Shaft Torque"),
    ("FFB Output",             "FFB Output"),
    ("Time Behind Next",       "Time Behind Next"),
    ("Time Behind Leader",     "Time Behind Next"),
    ("Min Path Wetness",       "Minimum Path Wetness"),
    ("Off Path Wetness",       "OffpathWetness"),
    ("Overheating State",      "OverheatingState"),
    ("Sector Flag 1",          "Sector1 Flag"),
    ("Sector Flag 2",          "Sector2 Flag"),
    ("Sector Flag 3",          "Sector3 Flag"),
    # DAMPlugin extra channels — single value
    ("Body Pitch",             "BodyPitch"),
    ("Body Roll",              "BodyRoll"),
    ("Front Downforce",        "DownforceFront"),
    ("Rear Downforce",         "DownforceRear"),
    ("Delta Best",             "DeltaBest"),
    ("Engine Torque",          "EngineTorque"),
    ("Front Wing Height",      "FrontWingHeight"),
    ("Local Rotation X",       "BodyRotX"),
    ("Local Rotation Y",       "BodyRotY"),
    ("Local Rotation Z",       "BodyRotZ"),
    ("Local Rot Accel X",      "BodyRotAccelX"),
    ("Local Rot Accel Y",      "BodyRotAccelY"),
    ("Local Rot Accel Z",      "BodyRotAccelZ"),
]

# Post-decode scale factors for specific multi-wheel channels (applied after Formula A).
# Key = duckdb_table name.
_MULTI_SCALE: dict[str, float] = {
    "Susp Pos": 1e-3,   # mm → m (reference DuckDB stores meters)
}

# Multi-wheel channels: (ld_prefix, duckdb_table, [suffixes_FL_FR_RL_RR])
_MULTI_MAP: list[tuple[str, str, list[str]]] = [
    ("Susp Pos",          "Susp Pos",          ["FL", "FR", "RL", "RR"]),
    ("Brake Temp",        "Brakes Temp",        ["FL", "FR", "RL", "RR"]),
    ("Tyre Pressure",     "TyresPressure",      ["FL", "FR", "RL", "RR"]),
    ("Tyre Temp {w} Centre", "TyresTempCentre", ["FL", "FR", "RL", "RR"]),
    ("Tyre Temp {w} Inner",  "TyresTempLeft",   ["FL", "FR", "RL", "RR"]),
    ("Tyre Temp {w} Outer",  "TyresTempRight",  ["FL", "FR", "RL", "RR"]),
    ("Tyre Carcass Temp", "TyresCarcassTemp",   ["FL", "FR", "RL", "RR"]),
    ("Tyre Rubber Temp {w} C", "TyresRubberTemp",       ["FL", "FR", "RL", "RR"]),
    ("Tyre Rubber Temp {w} I", "TyresRubberTempInner",  ["FL", "FR", "RL", "RR"]),
    ("Tyre Rubber Temp {w} O", "TyresRubberTempOuter",  ["FL", "FR", "RL", "RR"]),
    ("Tyre Wear",         "Tyres Wear",         ["FL", "FR", "RL", "RR"]),
    ("Wheel Rot Speed",   "Wheel Speed",        ["FL", "FR", "RL", "RR"]),
    ("Wheel Detached",    "WheelsDetached",     ["FL", "FR", "RL", "RR"]),
    ("Ride Height",       "RideHeights",        ["FL", "FR", "RL", "RR"]),
    ("Susp Force",        "Susp Force",         ["FL", "FR", "RL", "RR"]),
    ("Surface Type",      "SurfaceTypes",       ["FL", "FR", "RL", "RR"]),
    # DAMPlugin extra channels — multi-wheel
    ("Camber",                 "CamberDyn",           ["FL", "FR", "RL", "RR"]),
    ("Toe",                    "ToeDyn",              ["FL", "FR", "RL", "RR"]),
    ("Tyre Load",              "TyreLoad",            ["FL", "FR", "RL", "RR"]),
    ("Grip Fract",             "GripFract",           ["FL", "FR", "RL", "RR"]),
    ("Lat Force",              "TyreLatForce",        ["FL", "FR", "RL", "RR"]),
    ("Long Force",             "TyreLongForce",       ["FL", "FR", "RL", "RR"]),
    ("Vertical Tyre Deflection", "VertTyreDeflection", ["FL", "FR", "RL", "RR"]),
    ("Brake Pressure",         "BrakePressure",       ["FL", "FR", "RL", "RR"]),
]

# Event channels extracted directly from .ld (by monitoring state changes)
_EVENT_MAP: list[tuple[str, str, str]] = [
    ("In Pits",     "In Pits",     "float"),
    ("Last Laptime","Lap Time",    "float"),
    ("Last Sector 1","Last Sector1","float"),
    ("Last Sector 2","Last Sector2","float"),
    ("Best Laptime", "Best LapTime","float"),
    ("Best Sector 1","Best Sector1","float"),
    ("Best Sector 2","Best Sector2","float"),
]


def _build_multi_channel(decoded: dict, prefix: str, suffixes: list[str], n_gps: int, session_time: np.ndarray) -> Optional[np.ndarray]:
    """
    Build a (N, 4) array from per-wheel .ld channels.
    Channels can be named like 'Susp Pos FL' or 'Tyre Temp FL Centre'.
    """
    cols = []
    for w in suffixes:
        # Try "{prefix} {w}" or expand template like "Tyre Temp {w} Centre"
        if "{w}" in prefix:
            ld_name = prefix.replace("{w}", w)
        else:
            ld_name = f"{prefix} {w}"
        if ld_name in decoded:
            arr = decoded[ld_name]
            t_ch = _build_time_for_channel(session_time, len(arr))
            interp = np.interp(session_time, t_ch, arr)
            cols.append(interp)
        else:
            cols.append(None)

    if all(c is None for c in cols):
        return None

    n = n_gps
    result = np.zeros((n, 4), dtype=np.float64)
    for i, col in enumerate(cols):
        if col is not None:
            result[:, i] = col
    return result


def convert_ld_to_duckdb(ld_path: str, output_path: str) -> dict:
    """
    Convert a native LMU .ld telemetry file to the app's .duckdb format.

    Returns a summary dict: {track, driver, car, laps, session_type, duration}
    """
    ld_path = str(ld_path)
    log.info("Converting %s → %s", ld_path, output_path)

    # ── 1. Read raw bytes ────────────────────────────────────────────────────
    raw = open(ld_path, "rb").read()
    ch_off = struct.unpack_from("<I", raw, 8)[0]

    # ── 2. Parse metadata & channels ────────────────────────────────────────
    meta  = _parse_metadata(raw)
    chans = _parse_channels(raw, ch_off)
    log.info("Parsed %d channels from %s", len(chans), os.path.basename(ld_path))

    # ── 3. Decode all channels ───────────────────────────────────────────────
    decoded = _decode_all(raw, chans)

    # ── 4. Build master time axis from Session Elapsed Time ─────────────────
    if "Session Elapsed Time" not in decoded:
        raise ValueError("Missing 'Session Elapsed Time' channel — not a native LMU .ld file?")

    session_time = decoded["Session Elapsed Time"]
    # Ensure monotonically increasing (handle edge cases)
    for i in range(1, len(session_time)):
        if session_time[i] <= session_time[i - 1]:
            session_time[i] = session_time[i - 1] + 0.02  # 50Hz step

    t_start = float(session_time[0])
    t_end   = float(session_time[-1])
    n_gps   = len(session_time)

    log.info("Session: %.1f s to %.1f s (%.1f min), %d time samples",
             t_start, t_end, (t_end - t_start) / 60, n_gps)

    # ── 5. Build Lap events ──────────────────────────────────────────────────
    lap_numbers_50hz = decoded.get("Lap Number", np.zeros(n_gps))
    lap_t_for_chan = _build_time_for_channel(session_time, len(lap_numbers_50hz))
    # Map lap number time onto master clock for correct timestamps
    lap_events = _build_lap_events(lap_numbers_50hz, lap_t_for_chan)
    n_laps = max((e[1] for e in lap_events), default=0) + 1
    log.info("Detected %d lap events", len(lap_events))

    # ── 6. Build event tables from sampled channels ─────────────────────────
    event_tables: dict[str, list[tuple]] = {}
    for ld_name, db_name, dtype in _EVENT_MAP:
        if ld_name not in decoded:
            continue
        arr = decoded[ld_name]
        t_ch = _build_time_for_channel(session_time, len(arr))
        events = _detect_state_changes(arr, t_ch)
        if events:
            event_tables[db_name] = events

    # ── 7. Lap Time events: from Last Laptime transitions ───────────────────
    if "Last Laptime" in decoded:
        arr = decoded["Last Laptime"]
        t_ch = _build_time_for_channel(session_time, len(arr))
        lt_events = _build_laptime_events(arr, t_ch)
        if lt_events:
            event_tables["Lap Time"] = lt_events

    # ── 8. Continuous single-value channels ─────────────────────────────────
    continuous_tables: dict[str, np.ndarray] = {}
    seen_db_names: set[str] = set()

    for ld_name, db_name in _SINGLE_MAP:
        if ld_name not in decoded:
            continue
        if db_name in seen_db_names:
            continue  # already filled by a higher-priority source
        seen_db_names.add(db_name)
        arr = decoded[ld_name]
        # Resample to master GPS Time grid
        t_ch = _build_time_for_channel(session_time, len(arr))
        continuous_tables[db_name] = np.interp(session_time, t_ch, arr)

    # GPS Speed (derive from Ground Speed)
    if "Ground Speed" in continuous_tables and "GPS Speed" not in continuous_tables:
        continuous_tables["GPS Speed"] = continuous_tables["Ground Speed"]

    # ── 9. Multi-wheel channels ──────────────────────────────────────────────
    multi_tables: dict[str, np.ndarray] = {}   # shape (N, 4)
    for prefix, db_name, suffixes in _MULTI_MAP:
        arr4 = _build_multi_channel(decoded, prefix, suffixes, n_gps, session_time)
        if arr4 is not None:
            scale_factor = _MULTI_SCALE.get(db_name, 1.0)
            if scale_factor != 1.0:
                arr4 = arr4 * scale_factor
            multi_tables[db_name] = arr4

    # ── 10. ABS / TC event channels ─────────────────────────────────────────
    # Detect ABS / TC level events
    for ld_src, db_evt in [("ABS", "ABSLevel"), ("TC", "TCLevel"), ("Brake Bias Rear", "Brake Migration")]:
        # Brake Bias Rear → Brake Migration is not quite right, skip
        pass

    # ── 11. TyresCompound (from Front/Rear Tyre Compound) ───────────────────
    # Low-frequency categorical, store as event
    if "Front Tyre Compound" in decoded:
        arr = decoded["Front Tyre Compound"]
        t_ch = _build_time_for_channel(session_time, len(arr))
        events = _detect_state_changes(arr, t_ch)
        if events:
            event_tables["TyresCompound"] = [(ts, int(v), int(v), int(v), int(v)) for ts, v in events]

    # ── 12. Steering Pos normalization ────────────────────────────────────────
    # Keep degrees as-is (fusion engine will handle normalization)

    # ── 13. Build metadata dict ──────────────────────────────────────────────
    # Parse recording time from filename (pattern: YYYY-MM-DD - HH-MM-SS - ...)
    fname_base = os.path.basename(ld_path).replace(".ld", "")
    recording_time = fname_base[:19].replace(" - ", "T").replace(" ", "T")
    # Try to standardize to ISO: "2026-05-07 - 02-10-40 - ..."
    m = re.match(r"(\d{4}-\d{2}-\d{2}) - (\d{2}-\d{2}-\d{2})", fname_base)
    if m:
        date_iso = m.group(1)
        time_part = m.group(2).replace("-", "_")
        recording_time = f"{date_iso}T{time_part}Z"

    session_type = _session_type_from_filename(ld_path)

    db_metadata = {
        "Version":       "1",
        "DriverName":    meta["driver"],
        "RecordingTime": recording_time,
        "SessionTime":   meta["time_str"],
        "SessionType":   session_type,
        "TrackName":     meta["venue"],
        "TrackLayout":   meta["venue"],   # native .ld has no layout info
        "CarName":       meta["car_name"],
        "CarClass":      meta["car_class"],
        "WeatherConditions": "",
        "Source":        "ld_converter_v1",
    }

    # ── 14. Write DuckDB ─────────────────────────────────────────────────────
    if os.path.exists(output_path):
        os.remove(output_path)

    con = duckdb.connect(output_path)
    try:
        # metadata table
        con.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        con.executemany("INSERT INTO metadata VALUES (?, ?)", list(db_metadata.items()))

        # channelsList table
        con.execute("CREATE TABLE channelsList (channelName TEXT, frequency INT, unit TEXT)")
        cl_rows = []
        for ld_name, ch in chans.items():
            cl_rows.append((ld_name, int(ch["freq"]), ch["unit"]))
        con.executemany("INSERT INTO channelsList VALUES (?, ?, ?)", cl_rows)

        # GPS Time (master clock — continuous)
        gps_time_arr = session_time.astype(np.float64)
        con.execute("CREATE TABLE \"GPS Time\" (value FLOAT)")
        con.execute("INSERT INTO \"GPS Time\" SELECT * FROM (SELECT unnest(?) AS value)", [gps_time_arr.tolist()])

        # eventsList placeholder (empty, required by app)
        con.execute("CREATE TABLE eventsList (ts FLOAT, event TEXT)")

        # Lap event table (ts FLOAT, value UINT16)
        import pandas as pd
        lap_df = pd.DataFrame(lap_events, columns=["ts", "value"])
        lap_df["ts"] = lap_df["ts"].astype("float64")
        lap_df["value"] = lap_df["value"].astype("uint16")
        con.execute("CREATE TABLE \"Lap\" AS SELECT * FROM lap_df")

        # Standard event tables
        _EVENT_SCHEMA = {
            "Lap Time":     ("ts FLOAT, value FLOAT", "float64"),
            "Last Sector1": ("ts FLOAT, value FLOAT", "float64"),
            "Last Sector2": ("ts FLOAT, value FLOAT", "float64"),
            "Best LapTime": ("ts FLOAT, value FLOAT", "float64"),
            "Best Sector1": ("ts FLOAT, value FLOAT", "float64"),
            "Best Sector2": ("ts FLOAT, value FLOAT", "float64"),
            "In Pits":      ("ts FLOAT, value FLOAT", "float64"),
        }

        for db_name, (schema_def, _) in _EVENT_SCHEMA.items():
            rows = event_tables.get(db_name, [])
            df_evt = pd.DataFrame(rows, columns=["ts", "value"]) if rows else pd.DataFrame(columns=["ts", "value"])
            df_evt["ts"] = pd.to_numeric(df_evt["ts"], errors="coerce").astype("float64")
            df_evt["value"] = pd.to_numeric(df_evt["value"], errors="coerce").astype("float64")
            con.execute(f"CREATE TABLE \"{db_name}\" AS SELECT * FROM df_evt")

        # TyresCompound event table (special: ts + value1..4)
        if "TyresCompound" in event_tables:
            rows = event_tables["TyresCompound"]
            df_tc = pd.DataFrame(rows, columns=["ts", "value1", "value2", "value3", "value4"])
            con.execute("CREATE TABLE \"TyresCompound\" AS SELECT * FROM df_tc")
        else:
            con.execute("CREATE TABLE \"TyresCompound\" (ts FLOAT, value1 INT, value2 INT, value3 INT, value4 INT)")

        # Continuous single-value tables
        for db_name, arr in continuous_tables.items():
            arr_f = arr.astype(np.float64)
            df_c = pd.DataFrame({"value": arr_f})
            con.execute(f"CREATE TABLE \"{db_name}\" AS SELECT * FROM df_c")

        # Multi-wheel tables (value1..value4)
        for db_name, arr4 in multi_tables.items():
            df_m = pd.DataFrame({
                "value1": arr4[:, 0],
                "value2": arr4[:, 1],
                "value3": arr4[:, 2],
                "value4": arr4[:, 3],
            })
            con.execute(f"CREATE TABLE \"{db_name}\" AS SELECT * FROM df_m")

        # Gear — event table (ts + value), sampled every 10Hz
        if "Gear" in decoded:
            gear_arr = decoded["Gear"].astype(int)
            t_gear = _build_time_for_channel(session_time, len(gear_arr))
            # Store as event: detect changes
            gear_events = _detect_state_changes(decoded["Gear"], t_gear)
            df_gear = pd.DataFrame(gear_events, columns=["ts", "value"])
            df_gear["ts"] = df_gear["ts"].astype("float64")
            df_gear["value"] = df_gear["value"].astype("int16")
            if df_gear.empty:
                df_gear = pd.DataFrame({"ts": [t_start], "value": [0]})
            con.execute("CREATE TABLE \"Gear\" AS SELECT * FROM df_gear")

        # ABS / TC events
        for ld_src, db_name in [
            ("Front 3rd Pos", None),  # skip non-event
        ]:
            pass

        # Simple boolean event channels
        for ld_src, db_name in [
            ("Anti Stall Activated", "AntiStall Activated"),
            ("Front Flap Activated", "FrontFlapActivated"),
            ("Rear Flap Activated",  "RearFlapActivated"),
        ]:
            if ld_src in decoded:
                arr = decoded[ld_src]
                t_ch = _build_time_for_channel(session_time, len(arr))
                events = _detect_state_changes(arr, t_ch)
                df_e = pd.DataFrame(events, columns=["ts", "value"]) if events else pd.DataFrame(columns=["ts", "value"])
                df_e["ts"] = pd.to_numeric(df_e["ts"], errors="coerce").astype("float64")
                df_e["value"] = pd.to_numeric(df_e["value"], errors="coerce").astype(bool)
                if db_name not in seen_db_names:
                    con.execute(f"CREATE TABLE \"{db_name}\" AS SELECT * FROM df_e")

        # ABS and TC activation (bool event)
        # In .ld these are 0/1 continuous channels; store as events
        for ld_src, db_name in [("ABS", "ABS"), ("TC", "TC")]:
            arr_src = None
            if ld_src in decoded:
                arr_src = decoded[ld_src]
            # Wheel-level ABS from Wheel Detached (wrong source, skip)
            if arr_src is None:
                continue
            t_ch = _build_time_for_channel(session_time, len(arr_src))
            events = _detect_state_changes(arr_src, t_ch)
            df_e = pd.DataFrame(events, columns=["ts", "value"]) if events else pd.DataFrame(columns=["ts", "value"])
            df_e["ts"] = pd.to_numeric(df_e["ts"], errors="coerce").astype("float64")
            df_e["value"] = pd.to_numeric(df_e["value"], errors="coerce").astype(bool)
            con.execute(f"CREATE TABLE \"{db_name}\" AS SELECT * FROM df_e")

        # ABSLevel and TCLevel (int events)
        for ld_src, db_name in [("ABSLevel", "ABSLevel"), ("TCLevel", "TCLevel")]:
            # These don't exist in native .ld; create empty tables
            con.execute(f"CREATE TABLE \"{db_name}\" (ts FLOAT, value INT)")

        # TCCut, TCSlipAngle (not in native .ld)
        con.execute("CREATE TABLE \"TCCut\" (ts FLOAT, value BOOL)")
        con.execute("CREATE TABLE \"TCSlipAngle\" (ts FLOAT, value FLOAT)")

        con.execute("CHECKPOINT")
        log.info("DuckDB written to %s", output_path)

    finally:
        con.close()

    return {
        "track":       meta["venue"],
        "driver":      meta["driver"],
        "car":         meta["car_name"],
        "car_class":   meta["car_class"],
        "session_type": session_type,
        "laps":        n_laps,
        "duration":    t_end - t_start,
        "recording_time": recording_time,
        "output":      output_path,
    }


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ld_converter.py <file.ld> [output.duckdb]")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ld_in = sys.argv[1]
    out_default = os.path.splitext(ld_in)[0] + ".duckdb"
    ld_out = sys.argv[2] if len(sys.argv) > 2 else out_default

    result = convert_ld_to_duckdb(ld_in, ld_out)
    print(f"\nConverted: {result['track']} | {result['driver']} | {result['car_class']}")
    print(f"Session type: {result['session_type']}, laps: {result['laps']}, duration: {result['duration']:.0f}s")
    print(f"Output: {result['output']}")
