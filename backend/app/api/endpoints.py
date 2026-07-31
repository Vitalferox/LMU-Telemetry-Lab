from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from typing import List, Optional
from pydantic import BaseModel
import os
import glob
import logging
import shutil
from datetime import datetime
from ..services.telemetry_service import TelemetryService
from ..services.profiles_service import ProfilesService
from ..services.sharing_service import SharingService
from ..services.elevation_service import get_3d_track_data
from ..security import safe_session_id, get_authenticated_profile
from ..config import get_settings
import duckdb
import numpy as np
from ..utils.track_db import find_track_in_registry, find_layout_in_track

router = APIRouter()

import sys

def resolve_layout_and_length(con, track_data, matched_key: str, track_layout: str, track_name: str):
    """
    Resolves the standardized layout key and official track length,
    incorporating distance-based fingerprint fallback logic.
    Returns (layout_key, official_track_length)
    """
    if not matched_key:
        return track_layout, None
        
    layouts_dict = track_data.get("layouts", {})
    matched_layout_key, layout_data = find_layout_in_track(track_data, track_layout, matched_key)
    
    layout_key = matched_layout_key if matched_layout_key else track_layout
    official_len = None
    if layout_data and "ref_points" in layout_data:
        official_len = float(layout_data["ref_points"][-1]["dist"])
        
    # Smart Fallback: Distance-based fingerprinting
    try:
        max_dist_row = con.execute('SELECT MAX(value) FROM "Lap Dist"').fetchone()
        if max_dist_row and max_dist_row[0] is not None:
            actual_max_dist = float(max_dist_row[0])
            if actual_max_dist > 500:
                cur_len = official_len if official_len else actual_max_dist
                ratio = actual_max_dist / cur_len
                if ratio < 0.8 or ratio > 1.2 or not official_len:
                    logger.info(f"Layout mismatch in endpoint (Ratio: {ratio:.2f}). Finding closest match for {actual_max_dist:.1f}m...")
                    best_layout_key = layout_key
                    best_len = official_len if official_len else actual_max_dist
                    min_diff = abs(actual_max_dist - best_len) if official_len else float('inf')
                    
                    for k, v in layouts_dict.items():
                        if "ref_points" in v:
                            l_dist = float(v["ref_points"][-1]["dist"])
                            diff = abs(actual_max_dist - l_dist)
                            if diff < min_diff:
                                min_diff = diff
                                best_len = l_dist
                                best_layout_key = k
                                
                    layout_key = best_layout_key
                    official_len = best_len
    except Exception as e:
        logger.warning(f"Failed resolve_layout_and_length fingerprinting: {e}")
        
    return layout_key, official_len

@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "antigravity-backend", "version": "1.4.3"}

@router.get("/sessions/{session_id}/3d-track")
async def get_3d_track(
    session_id: str, 
    lap: int = Query(..., ge=0),
    stint: Optional[int] = Query(None),
    profile_id: Optional[str] = Query("guest")
):
    """Get 3D track path (X, Y, Z) for visualization."""
    db_path, _, _ = resolve_readable_session(session_id, profile_id)
    session_id = safe_session_id(session_id)

    logger.info(f"API: GET /track3d - Session: {session_id}, Lap: {lap}, Profile: {profile_id}")


    try:
        # Extract track metadata for scaling and layout-specific mapping
        track_name = None
        track_layout = None
        with duckdb.connect(db_path, read_only=True) as con:
            meta = con.execute("SELECT key, value FROM metadata WHERE key IN ('TrackName', 'TrackLayout')").fetchall()
            meta_dict = {k: v for k, v in meta}
            track_name = meta_dict.get('TrackName')
            track_layout = meta_dict.get('TrackLayout')
            
            # Standardize track layout name with smart distance fallback
            layout_key = track_layout
            matched_key, track_data = find_track_in_registry(track_name)
            if matched_key:
                layout_key, _ = resolve_layout_and_length(con, track_data, matched_key, track_layout, track_name)
            
            # 1. Resolve Times for the Selected Lap (Racing Line)
            laps_header = TelemetryService.get_laps_header(db_path)
            laps_list = laps_header.get("laps", [])

            selected_lap = next((
                l for l in laps_list 
                if l.get('lap') == lap and (stint is None or l.get('stint') == stint)
            ), None)
            
            if not selected_lap and 0 <= lap < len(laps_list):
                selected_lap = laps_list[lap]
                
            if not selected_lap: 
                logger.error(f"3D Track: Lap {lap} (Stint: {stint}) NOT FOUND in session {session_id}")
                raise HTTPException(status_code=404, detail="Lap not found")
                
            lap_times = [selected_lap['startTime'], selected_lap['endTime']]

            # Resolve stint boundaries for "Stint-Anchor" logic
            cur_stint_id = selected_lap.get('stint')
            stint_laps = [l for l in laps_list if l.get('stint') == cur_stint_id]
            stint_start = min(l['startTime'] for l in stint_laps) if stint_laps else lap_times[0]
            stint_end = max(l['endTime'] for l in stint_laps) if stint_laps else lap_times[1]
            
            # RELAXED DETECTION: 5.0s tolerance to handle large metadata/telemetry offsets at stint junctions
            is_first = (lap_times[0] <= stint_start + 5.0)
            is_last = (lap_times[1] >= stint_end - 5.0)

            # 2. Resolve Times for the Base Map (Fastest Lap)
            valid_laps = [l for l in stint_laps if l.get('isValid') and not l.get('isOutLap')]
            best_lap = min(valid_laps, key=lambda x: x['duration']) if valid_laps else (max(stint_laps, key=lambda x: x['duration']) if stint_laps else selected_lap)
            
            # FALLBACK LOGIC: If best_lap is strangely empty or too short, use selected_lap
            if not best_lap or (best_lap.get('duration', 0) < 5.0):
                best_lap = selected_lap

            base_times = [best_lap['startTime'], best_lap['endTime']]
            logger.info(f"3D: Using Lap {selected_lap['lap']} for RacingLine, Lap {best_lap['lap']} for BaseMap")

            # 3. Use Refactored Elevation Service (Time-Driven + 2D Fusion Engine)
            # Pass stint boundaries and junction flags for robust "Stint-Anchor" extraction
            data_dict = get_3d_track_data(
                db_path=db_path, 
                lap_times=lap_times, 
                base_times=base_times, 
                track_name=track_name, 
                track_layout=layout_key,
                session_id=session_id,
                stint=stint,
                stint_range=[stint_start, stint_end],
                is_first=is_first,
                is_last=is_last,
                base_lap_info=best_lap
            )

            return {
                "baseMap": data_dict["baseMap"], "racingLine": data_dict["racingLine"],
                "trackName": track_name, "trackLayout": track_layout,
                "layoutKey": layout_key,
                "fastestLap": best_lap['lap'], "selectedLapInfo": selected_lap,
                "trackSectors": data_dict.get("trackSectors", []),
                "center": data_dict.get("center"),
                "zBase": data_dict.get("zBase", 0)
            }
            
    except Exception as e:
        logger.error(f"Error generating 3D track for {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def get_base_path():
    """Get the base path for resources, handles PyInstaller environment."""
    if getattr(sys, 'frozen', False):
        # If running as a bundled executable (PyInstaller)
        # sys._MEIPASS is the internal resource folder
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        return os.path.dirname(sys.executable)
    # If running in a normal Python environment (Development)
    # This assumes endpoints.py is at backend/app/api/endpoints.py
    # To reach root: api -> app -> backend -> root (4 levels)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

BASE_DIR = get_base_path()

# --- Profile Aware Path Resolution ---
# We initialize the persistent app data dir and migrate legacy project-root data if needed
APP_DATA_ROOT = ProfilesService.get_app_data_dir()
LEGACY_DATA_DIR = os.path.join(BASE_DIR, "DuckDB_data")

# Initial migration
ProfilesService.ensure_guest_profile()
ProfilesService.migrate_legacy_data(LEGACY_DATA_DIR)

def resolve_profile_id(profile_id: Optional[str] = "guest") -> str:
    """Profile whose data the request may touch.

    In server mode a token bound to a profile wins over the client-supplied
    profile_id, so a user cannot read another user's sessions by editing the
    query string.
    """
    bound = get_authenticated_profile()
    p_id = os.path.basename(bound or profile_id or "guest")
    if not p_id or ".." in p_id:
        raise HTTPException(status_code=400, detail="Invalid profile ID")
    return p_id


def get_contextual_dirs(profile_id: Optional[str] = "guest"):
    """Get dynamic data and cache dirs based on profile."""
    p_id = resolve_profile_id(profile_id)
    return (
        ProfilesService.get_profile_data_dir(p_id),
        ProfilesService.get_profile_cache_dir(p_id)
    )


def resolve_readable_session(session_id: str, profile_id: Optional[str] = "guest"):
    """Locate a session the caller is allowed to read.

    Falls back to sessions another profile has shared, so a friend's lap can be
    used as a reference. Returns (db_path, cache_dir, owner) where owner is None
    for your own sessions and the owning profile id for a shared one — callers
    building cache filenames must include it to avoid collisions between
    identically named sessions belonging to different people.
    """
    session_id = safe_session_id(session_id)
    p_id = resolve_profile_id(profile_id)
    cache_dir = ProfilesService.get_profile_cache_dir(p_id)

    own_path = os.path.join(ProfilesService.get_profile_data_dir(p_id), session_id)
    if os.path.exists(own_path):
        return own_path, cache_dir, None

    owner = SharingService.find_owner(session_id, exclude_profile_id=p_id)
    if owner:
        owner = safe_session_id(owner)
        shared_path = os.path.join(ProfilesService.get_profile_data_dir(owner), session_id)
        if os.path.exists(shared_path):
            return shared_path, cache_dir, owner

    raise HTTPException(status_code=404, detail="Session not found")

logger = logging.getLogger(__name__)

# --- Profile Endpoints ---

class ProfileCreate(BaseModel):
    name: str

class ProfileUpdate(BaseModel):
    name: str

@router.get("/profiles")
async def list_profiles():
    profiles = ProfilesService.list_profiles()
    if not profiles:
        ProfilesService.ensure_guest_profile()
        profiles = ProfilesService.list_profiles()
    return {"profiles": profiles}

def _reject_in_server_mode(action: str):
    """Profiles are provisioned from AUTH_TOKENS when hosted, not from the UI."""
    if get_settings().is_server:
        raise HTTPException(status_code=403, detail=f"{action} is disabled in server mode")

@router.post("/profiles")
async def create_profile(req: ProfileCreate):
    _reject_in_server_mode("Creating a profile")
    return ProfilesService.create_profile(req.name)

@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str):
    _reject_in_server_mode("Deleting a profile")
    ProfilesService.delete_profile(profile_id)
    return {"status": "success"}

@router.put("/profiles/{profile_id}")
async def update_profile(profile_id: str, req: ProfileUpdate):
    # Renaming stays available when hosted, but only for your own profile.
    profile_id = resolve_profile_id(profile_id)
    success = ProfilesService.update_profile(profile_id, req.name)
    if not success:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "success"}

class OpenPathRequest(BaseModel):
    path: str
    x: Optional[int] = None
    y: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None

@router.get("/system/validate-path")
async def validate_system_path(path: str = Query(...)):
    """Check if a local path exists."""
    return {"exists": os.path.exists(os.path.normpath(path))}


def _find_lmu_root() -> Optional[str]:
    """Auto-detect the LMU install root by scanning Steam library locations."""
    import string

    LMU_SUBPATH = os.path.join("steamapps", "common", "Le Mans Ultimate")

    steam_roots: list[str] = []
    default_steam = r"C:\Program Files (x86)\Steam"
    if os.path.isdir(default_steam):
        steam_roots.append(default_steam)

    vdf_path = os.path.join(default_steam, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf_path):
        try:
            content = open(vdf_path, encoding="utf-8", errors="replace").read()
            import re as _re
            for m in _re.finditer(r'"path"\s+"([^"]+)"', content):
                p = m.group(1).replace("\\\\", "\\")
                if os.path.isdir(p):
                    steam_roots.append(p)
        except Exception:
            pass

    for drive in string.ascii_uppercase:
        for lib_name in ("SteamLibrary", "Steam", "Games", "SteamGames"):
            p = f"{drive}:\\{lib_name}"
            if os.path.isdir(p):
                steam_roots.append(p)

    for root in steam_roots:
        p = os.path.normpath(os.path.join(root, LMU_SUBPATH))
        if os.path.isdir(p):
            return p

    return None


def _find_lmu_telemetry_dir() -> Optional[str]:
    """Auto-detect the LMU UserData/Telemetry folder."""
    lmu = _find_lmu_root()
    if lmu:
        p = os.path.join(lmu, "UserData", "Telemetry")
        if os.path.isdir(p):
            return os.path.normpath(p)
    return None


@router.get("/system/detect-lmu-path")
async def detect_lmu_path():
    """
    Auto-detect the LMU UserData/Telemetry folder by scanning common Steam library locations.
    Returns the first found path, or null if none found.
    """
    p = _find_lmu_telemetry_dir()
    return {"path": p, "found": p is not None}

@router.get("/system/damplugin/status")
async def damplugin_status():
    """Check whether the DAMPlugin is installed in the LMU game folder."""
    from ..services import dam_plugin
    from pathlib import Path

    lmu = _find_lmu_root()
    if not lmu:
        return {"error": "LMU installation not found", "installed": False, "assets_available": False}
    return dam_plugin.status(Path(lmu))


@router.post("/system/damplugin/activate")
async def damplugin_activate():
    """Install DAMPlugin into the LMU game folder."""
    from ..services import dam_plugin
    from pathlib import Path

    lmu = _find_lmu_root()
    if not lmu:
        raise HTTPException(status_code=404, detail="LMU installation not found")
    try:
        dam_plugin.activate(Path(lmu))
        return dam_plugin.status(Path(lmu))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"DAMPlugin activation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/damplugin/deactivate")
async def damplugin_deactivate():
    """Remove DAMPlugin from the LMU game folder."""
    from ..services import dam_plugin
    from pathlib import Path

    lmu = _find_lmu_root()
    if not lmu:
        raise HTTPException(status_code=404, detail="LMU installation not found")
    try:
        dam_plugin.deactivate(Path(lmu))
        return dam_plugin.status(Path(lmu))
    except Exception as e:
        logger.error(f"DAMPlugin deactivation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/open-path")
async def open_system_path(req: OpenPathRequest):
    """Open a local path in the system file explorer."""
    path = req.path
    if not path:
        raise HTTPException(status_code=400, detail="Path is required")
    
    try:
        if sys.platform == 'win32':
            import subprocess
            # Normalize path for Windows
            norm_path = os.path.normpath(path)
            if os.path.exists(norm_path):
                # Using explorer.exe directly often helps bring the window to front
                subprocess.Popen(['explorer', norm_path])
                return {"status": "success", "message": f"Opening {norm_path}"}
            else:
                # If path doesn't exist, try opening the parent
                parent = os.path.dirname(norm_path)
                if os.path.exists(parent):
                    subprocess.Popen(['explorer', parent])
                    return {"status": "partial", "message": f"Path not found, opening parent: {parent}"}
                raise HTTPException(status_code=404, detail="Path not found")
        else:
            # Fallback for Linux/macOS
            import subprocess
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.run([opener, path])
            return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to open path {path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/system/pick-and-upload")
async def pick_and_upload(req: OpenPathRequest, profile_id: Optional[str] = Query("guest")):
    """Open a native file picker at the specified path and 'upload' the selected file."""
    import tkinter as tk
    from tkinter import filedialog
    import shutil
    
    path = req.path
    data_dir, _ = get_contextual_dirs(profile_id)
    
    try:
        # Enable High DPI awareness for Windows to make the dialog look sharp
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        # Initialize tkinter and hide the main window
        root = tk.Tk()
        root.withdraw()
        
        # Position the hidden root window in the center of the app window
        # so the dialog opens centered relative to the app
        if req.x is not None and req.y is not None and req.width is not None and req.height is not None:
            # Calculate center
            center_x = req.x + (req.width // 2)
            center_y = req.y + (req.height // 2)
            # Setting geometry for the hidden window affects where its children (dialogs) appear
            root.geometry(f"+{center_x}+{center_y}")

        root.attributes('-topmost', True) # Ensure dialog is on top
        
        initial_dir = path if os.path.exists(path) else None
        
        # Open the native file picker with multi-select support
        file_paths = filedialog.askopenfilenames(
            initialdir=initial_dir,
            title="Select LMU Telemetry File(s) (.duckdb or .ld)",
            filetypes=[
                ("LMU Telemetry files", "*.duckdb *.ld"),
                ("DuckDB files", "*.duckdb"),
                ("LMU native .ld", "*.ld"),
                ("All files", "*.*"),
            ]
        )

        root.destroy() # Cleanup tkinter

        if not file_paths:
            return {"status": "cancelled"}

        last_filename = None
        last_info = None
        imported_ids = []
        for path_item in file_paths:
            if not path_item:
                continue
            filename = os.path.basename(path_item)

            if filename.lower().endswith(".ld"):
                # Convert .ld → .duckdb with canonical filename
                from ..services.ld_converter import convert_ld_to_duckdb
                tmp_path = os.path.join(data_dir, "_tmp_ld_convert.duckdb")
                try:
                    info = convert_ld_to_duckdb(path_item, tmp_path)
                    duckdb_filename = _canonical_db_name(info)
                    dest_path = os.path.join(data_dir, duckdb_filename)
                    if os.path.exists(dest_path):
                        os.remove(tmp_path)
                    else:
                        os.rename(tmp_path, dest_path)
                    last_filename = duckdb_filename
                    last_info = info
                    imported_ids.append(duckdb_filename)
                except Exception as conv_err:
                    logger.error(f".ld conversion failed for {filename}: {conv_err}", exc_info=True)
                    if os.path.exists(tmp_path):
                        try: os.remove(tmp_path)
                        except: pass
                    continue
            else:
                # Regular .duckdb — copy as-is
                dest_path = os.path.join(data_dir, filename)
                shutil.copy2(path_item, dest_path)
                last_filename = filename
                last_info = None
                imported_ids.append(filename)

        if not last_filename:
            return {"status": "cancelled"}

        result = {
            "status": "success",
            "id": last_filename,
            "ids": imported_ids,
            "message": f"Successfully imported {len(imported_ids)} file(s)"
        }
        if last_info:
            result["track"] = last_info.get("track")
            result["driver"] = last_info.get("driver")
            result["laps"] = last_info.get("laps")
        return result

    except Exception as e:
        logger.error(f"Native file picker failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/profiles/{profile_id}/avatar")
async def upload_profile_avatar(profile_id: str, file: UploadFile = File(...)):
    profile_id = resolve_profile_id(profile_id)
    # Create profile-specific avatar dir
    avatar_dir = os.path.join(ProfilesService.get_app_data_dir(), "Data", profile_id, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    
    # Save file
    file_ext = os.path.splitext(file.filename)[1]
    filename = f"avatar_{int(datetime.now().timestamp())}{file_ext}"
    file_path = os.path.join(avatar_dir, filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Update profile metadata
    # The URL should match our static mount: /api/v1/profile-data/Data/{profile_id}/avatars/{filename}
    avatar_url = f"/api/v1/profile-data/Data/{profile_id}/avatars/{filename}"
    ProfilesService.update_profile_avatar(profile_id, avatar_url)
    
    return {"avatar_url": avatar_url}

@router.get("/steering-wheels")
async def list_steering_wheels():
    """List categorized steering wheel images from the public directory."""
    # In development, look in public/steering wheel
    # In production, look in frontend/dist/steering wheel (where Vite copies public content)
    if getattr(sys, 'frozen', False):
        wheels_dir = os.path.join(BASE_DIR, "frontend", "dist", "steering wheel")
    else:
        wheels_dir = os.path.join(BASE_DIR, "frontend", "public", "steering wheel")
    
    if not os.path.exists(wheels_dir):
        logger.warning(f"Steering wheels directory NOT FOUND at: {wheels_dir}")
        return {"categories": {}}
        
    categories = {}
    
    for root, dirs, files in os.walk(wheels_dir):
        # Calculate relative path from wheels_dir
        rel_path = os.path.relpath(root, wheels_dir)
        
        category_name = "Root" if rel_path == "." else rel_path
        
        wheels = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        if wheels:
            # We want to return the path relative to the public/ steering wheel folder
            clean_path = "" if rel_path == "." else rel_path.replace("\\", "/")
            categories[category_name] = [
                {"name": os.path.splitext(w)[0], "path": f"{clean_path}/{w}" if clean_path else w}
                for w in wheels
            ]
            
    return {"categories": categories}

@router.get("/ping")
async def ping():
    return {"status": "pong", "message": "API is reachable"}


def _canonical_db_name(info: dict) -> str:
    """
    Build a standardized DuckDB filename matching the app's naming convention:
    {TrackName}_{SessionLetter}_{RecordingTime}.duckdb

    Example: Circuit de Spa-Francorchamps_R_2026-05-07T02_10_40Z.duckdb
    """
    import re as _re
    track = info.get("track", "Unknown")
    # Sanitize: strip filesystem-unsafe characters (but keep spaces and hyphens)
    track = _re.sub(r'[/\\:*?"<>|]', '', track).strip()
    stype = (info.get("session_type") or "Practice")[0].upper()   # R, P, Q
    rec_time = info.get("recording_time", "")
    if not rec_time:
        from datetime import datetime as _dt
        rec_time = _dt.utcnow().strftime("%Y-%m-%dT%H_%M_%SZ")
    return f"{track}_{stype}_{rec_time}.duckdb"


class LdImportRequest(BaseModel):
    ld_dir: str
    profile_id: Optional[str] = "guest"


@router.post("/sessions/import-ld")
async def import_ld_directory(req: LdImportRequest):
    """
    Scan a directory for LMU telemetry sessions and import any that aren't in the
    data directory yet: native .duckdb files are copied as-is, .ld files are converted.

    Returns a list of imported files and any errors.
    """
    from ..services.ld_converter import convert_ld_to_duckdb

    ld_dir = os.path.normpath(req.ld_dir)
    if not os.path.isdir(ld_dir):
        raise HTTPException(status_code=400, detail=f"Directory not found: {ld_dir}")

    data_dir, _ = get_contextual_dirs(req.profile_id)

    src_files = os.listdir(ld_dir)
    ld_files = [f for f in src_files if f.lower().endswith(".ld")]
    duckdb_files = [f for f in src_files if f.lower().endswith(".duckdb")]
    if not ld_files and not duckdb_files:
        return {"converted": [], "skipped": [], "errors": [], "message": "No .ld or .duckdb files found"}

    converted, skipped, errors = [], [], []

    # Pre-build a set of existing duckdb names for fast dedup
    existing_db = {f.lower() for f in os.listdir(data_dir) if f.lower().endswith(".duckdb")}

    # Native .duckdb sessions: straight copy
    for fname in sorted(duckdb_files):
        if fname.lower() in existing_db:
            skipped.append({"file": fname, "reason": "already imported"})
            continue
        try:
            src = os.path.join(ld_dir, fname)
            dest = os.path.join(data_dir, fname)
            shutil.copy2(src, dest)
            existing_db.add(fname.lower())
            converted.append({"file": fname, "id": fname})
            logger.info(f"Imported native .duckdb: {fname}")
        except Exception as e:
            logger.error(f"Failed to copy {fname}: {e}", exc_info=True)
            errors.append({"file": fname, "error": str(e)})

    for fname in sorted(ld_files):
        # First pass: check legacy name (raw .ld → .duckdb) to avoid re-converting
        legacy_name = os.path.splitext(fname)[0] + ".duckdb"
        if legacy_name.lower() in existing_db:
            skipped.append({"file": fname, "reason": "already imported"})
            continue

        ld_path = os.path.join(ld_dir, fname)
        try:
            # Convert to a temp name first, then rename to canonical
            tmp_path = os.path.join(data_dir, "_tmp_ld_convert.duckdb")
            info = convert_ld_to_duckdb(ld_path, tmp_path)
            db_name = _canonical_db_name(info)
            dest_path = os.path.join(data_dir, db_name)

            if db_name.lower() in existing_db or os.path.exists(dest_path):
                os.remove(tmp_path)
                skipped.append({"file": fname, "reason": "already imported"})
                continue

            os.rename(tmp_path, dest_path)
            existing_db.add(db_name.lower())
            converted.append({
                "file": fname,
                "id": db_name,
                "track": info.get("track"),
                "driver": info.get("driver"),
                "laps": info.get("laps"),
                "duration": info.get("duration"),
            })
            logger.info(f"Imported .ld: {fname} → {db_name}")
        except Exception as e:
            logger.error(f"Failed to convert {fname}: {e}", exc_info=True)
            # Cleanup temp if it exists
            tmp_path = os.path.join(data_dir, "_tmp_ld_convert.duckdb")
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except: pass
            errors.append({"file": fname, "error": str(e)})

    return {
        "converted": converted,
        "skipped": skipped,
        "errors": errors,
        "message": f"{len(converted)} converted, {len(skipped)} skipped, {len(errors)} errors",
    }


def _parse_native_session_name(fname: str):
    """Parse '{Track}_{P|Q|R}_{ISO}.duckdb' → (track, letter, datetime) or None."""
    import re as _re
    m = _re.match(r"^(.+)_([PQR])_(\d{4}-\d{2}-\d{2})T(\d{2})_(\d{2})_(\d{2})Z?\.duckdb$", fname)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return m.group(1), m.group(2), dt


def _parse_ld_session_name(fname: str):
    """Parse 'YYYY-MM-DD - HH-MM-SS - {Track} - {P|Q|R}N.ld' → (track, letter, datetime) or None."""
    import re as _re
    m = _re.match(r"^(\d{4}-\d{2}-\d{2}) - (\d{2})-(\d{2})-(\d{2}) - (.+) - ([PQR])\d*\.ld$", fname, _re.IGNORECASE)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return m.group(5), m.group(6).upper(), dt


def _session_times_match(dt_a: datetime, dt_b: datetime, tol_s: float = 180.0) -> bool:
    """
    True if the two timestamps refer to the same instant modulo a whole-(half-)hour
    timezone offset (.ld filenames use local time, native .duckdb names use UTC).
    """
    delta = abs((dt_a - dt_b).total_seconds())
    if delta > 15 * 3600:   # beyond any real timezone offset
        return False
    return abs(delta - round(delta / 1800.0) * 1800.0) <= tol_s


def _clear_session_cache(cache_dir: str, session_id: str):
    if os.path.exists(cache_dir):
        for cache_file in glob.glob(os.path.join(cache_dir, f"{session_id}*.parquet")):
            try:
                os.remove(cache_file)
            except Exception as e:
                logger.warning(f"Failed to delete cache file {cache_file}: {e}")


class LmuSyncRequest(BaseModel):
    profile_id: Optional[str] = "guest"
    telemetry_dir: Optional[str] = None   # default: auto-detect


@router.post("/sessions/sync-lmu")
async def sync_lmu_sessions(req: LmuSyncRequest):
    """
    One-shot sync with the game folders:
      1. Import new native .duckdb sessions from <LMU>/UserData/Telemetry.
      2. For each DAMPlugin .ld in <LMU>/LOG, find the imported native session it
         belongs to (same track + recording time modulo timezone) and merge its
         extra channels into it. Unmatched .ld files are imported standalone.

    Idempotent: already-imported sessions and already-merged .ld files are skipped.
    """
    from ..services.ld_converter import convert_ld_to_duckdb, merge_extra_channels

    telemetry_dir = req.telemetry_dir or _find_lmu_telemetry_dir()
    if not telemetry_dir or not os.path.isdir(telemetry_dir):
        raise HTTPException(status_code=404, detail="LMU Telemetry folder not found")
    telemetry_dir = os.path.normpath(telemetry_dir)
    # <LMU>/UserData/Telemetry → <LMU>/LOG
    lmu_root = os.path.dirname(os.path.dirname(telemetry_dir))
    log_dir = os.path.join(lmu_root, "LOG")

    data_dir, cache_dir = get_contextual_dirs(req.profile_id)

    imported, merged, skipped, errors = [], [], [], []

    # ── 1. Import new native .duckdb sessions ───────────────────────────────
    existing_db = {f.lower() for f in os.listdir(data_dir) if f.lower().endswith(".duckdb")}
    for fname in sorted(f for f in os.listdir(telemetry_dir) if f.lower().endswith(".duckdb")):
        if fname.lower() in existing_db:
            skipped.append({"file": fname, "reason": "already imported"})
            continue
        try:
            shutil.copy2(os.path.join(telemetry_dir, fname), os.path.join(data_dir, fname))
            existing_db.add(fname.lower())
            imported.append({"file": fname, "id": fname})
            logger.info(f"Sync: imported native {fname}")
        except Exception as e:
            logger.error(f"Sync: failed to copy {fname}: {e}", exc_info=True)
            errors.append({"file": fname, "error": str(e)})

    # ── 2. Index imported sessions by (track, letter, time) ─────────────────
    native_index = []
    for fname in os.listdir(data_dir):
        if not fname.lower().endswith(".duckdb"):
            continue
        parsed = _parse_native_session_name(fname)
        if parsed:
            native_index.append((parsed[0], parsed[1], parsed[2], fname))

    def _already_merged(db_path: str) -> bool:
        try:
            con = duckdb.connect(db_path, read_only=True)
            try:
                rows = con.execute("SELECT value FROM metadata WHERE key = 'DAMPluginMerged'").fetchall()
                return bool(rows)
            finally:
                con.close()
        except Exception:
            return False

    # ── 3. Process DAMPlugin .ld files (game LOG dir + the given dir itself) ──
    ld_entries: dict[str, str] = {}
    for src_dir in (log_dir, telemetry_dir):
        if os.path.isdir(src_dir):
            for f in os.listdir(src_dir):
                if f.lower().endswith(".ld") and f not in ld_entries:
                    ld_entries[f] = os.path.join(src_dir, f)

    tmp_path = os.path.join(data_dir, "_tmp_ld_sync.duckdb")
    for fname in sorted(ld_entries):
        ld_path = ld_entries[fname]
        parsed = _parse_ld_session_name(fname)

        # Find the native session this .ld belongs to
        target = None
        if parsed:
            track, letter, dt = parsed
            t = track.lower()
            candidates = [
                (abs((dt - n_dt).total_seconds()), n_file)
                for n_track, n_letter, n_dt, n_file in native_index
                if (n_track.lower() == t or t in n_track.lower() or n_track.lower() in t)
                and n_letter == letter and _session_times_match(dt, n_dt)
            ]
            if candidates:
                target = min(candidates)[1]

        try:
            if target:
                target_path = os.path.join(data_dir, target)
                if _already_merged(target_path):
                    skipped.append({"file": fname, "reason": f"already merged into {target}"})
                    continue
                info = convert_ld_to_duckdb(ld_path, tmp_path)
                try:
                    res = merge_extra_channels(target_path, tmp_path, source_name=fname)
                    _clear_session_cache(cache_dir, target)
                    merged.append({"file": fname, "into": target, "channels": len(res["merged"])})
                    logger.info(f"Sync: merged {fname} → {target} ({len(res['merged'])} tables)")
                except ValueError as ve:
                    # Lap times don't line up: not the same session after all → standalone import
                    logger.warning(f"Sync: merge rejected for {fname} ({ve}), importing standalone")
                    db_name = _canonical_db_name(info)
                    dest = os.path.join(data_dir, db_name)
                    if db_name.lower() in existing_db or os.path.exists(dest):
                        skipped.append({"file": fname, "reason": "already imported"})
                    else:
                        os.replace(tmp_path, dest)
                        existing_db.add(db_name.lower())
                        imported.append({"file": fname, "id": db_name})
            else:
                # No matching native session: legacy standalone import (dedup by canonical name)
                legacy_name = os.path.splitext(fname)[0] + ".duckdb"
                if legacy_name.lower() in existing_db:
                    skipped.append({"file": fname, "reason": "already imported"})
                    continue
                info = convert_ld_to_duckdb(ld_path, tmp_path)
                db_name = _canonical_db_name(info)
                dest = os.path.join(data_dir, db_name)
                if db_name.lower() in existing_db or os.path.exists(dest):
                    skipped.append({"file": fname, "reason": "already imported"})
                else:
                    os.replace(tmp_path, dest)
                    existing_db.add(db_name.lower())
                    imported.append({"file": fname, "id": db_name})
                    logger.info(f"Sync: imported standalone .ld {fname} → {db_name}")
        except Exception as e:
            logger.error(f"Sync: failed on {fname}: {e}", exc_info=True)
            errors.append({"file": fname, "error": str(e)})
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    return {
        "imported": imported,
        "merged": merged,
        "skipped": skipped,
        "errors": errors,
        "message": f"{len(imported)} imported, {len(merged)} merged, {len(skipped)} skipped, {len(errors)} errors",
    }


@router.get("/sessions")
async def list_sessions(profile_id: Optional[str] = Query("guest")):
    """List available DuckDB sessions with metadata."""
    data_dir, _ = get_contextual_dirs(profile_id)
    logger.info(f"API: GET /sessions - Using Profile: {profile_id}, Dir: {data_dir}")
    
    if not os.path.exists(data_dir):
         return {"sessions": [], "error": "Data directory not found"}
         
    import duckdb
    from ..services.car_lookup import get_car_info
    
    files = glob.glob(os.path.join(data_dir, "*.duckdb"))
    files = sorted(files, key=os.path.getmtime, reverse=True)
    sessions = []
    
    for f in files:
        name = os.path.basename(f)
        session_info = {
            "id": name,
            "name": name.replace(".duckdb", ""),
            "created": os.path.getmtime(f),
            "size": os.path.getsize(f)
        }
        
        # Try to extract metadata
        try:
            with duckdb.connect(f, read_only=True) as con:
                meta_rows = con.execute("SELECT key, value FROM metadata").fetchall()
                meta_dict = {k: v for k, v in meta_rows}
                
                track_name = meta_dict.get('TrackName', '')
                track_layout = meta_dict.get('TrackLayout', '')
                raw_car = meta_dict.get('CarName', '')
                raw_class = meta_dict.get('CarClass', '')
                driver_name = meta_dict.get('DriverName', '')
                
                car_model, _ = get_car_info(raw_car, raw_class)
                
                if track_name: 
                    session_info["trackName"] = track_name
                    # Try to find aliases in registry
                    matched_key, track_data = find_track_in_registry(track_name)
                    if matched_key:
                        session_info["commonTrackName"] = matched_key
                        session_info["displayName"] = track_data.get("display_name", matched_key)
                        session_info["trackAliases"] = track_data.get("aliases", [])
                        session_info["country"] = track_data.get("country", "")
                        
                        # Extract official length and standardize layout name with smart distance fallback
                        layout_key, official_len = resolve_layout_and_length(con, track_data, matched_key, track_layout, track_name)
                        if layout_key:
                            session_info["layoutKey"] = layout_key
                        if official_len:
                            session_info["officialTrackLength"] = official_len
                
                if track_layout: session_info["trackLayout"] = track_layout
                if car_model: session_info["carModel"] = car_model
                if raw_class: session_info["carClass"] = raw_class
                if driver_name: session_info["driverName"] = driver_name
                if raw_car: session_info["rawCarName"] = raw_car
                
                # Extract Best Lap
                try:
                    laps_res = TelemetryService.get_laps_header(f)
                    laps = laps_res.get('laps', [])
                    if laps:
                        # Exclude out laps and zero duration
                        # For exported files with a single lap, allow it to be the best lap even if it's Lap 0
                        is_single_lap = len(laps) == 1
                        valid_laps = [l for l in laps if l.get('isValid', False) and (not l.get('isOutLap', False) or is_single_lap) and l.get('duration', 0) > 0]
                        
                        if valid_laps:
                            best_lap = min(valid_laps, key=lambda x: x.get('duration', float('inf')))
                            session_info["bestLapTime"] = best_lap.get('duration')
                            session_info["bestLapValid"] = True
                        else:
                            invalid_laps = [l for l in laps if (not l.get('isOutLap', False) or is_single_lap) and l.get('duration', 0) > 0]
                            if invalid_laps:
                                best_lap = min(invalid_laps, key=lambda x: x.get('duration', float('inf')))
                                session_info["bestLapTime"] = best_lap.get('duration')
                                session_info["bestLapValid"] = False
                except Exception as ex:
                    logger.warning(f"Failed to extract best lap for {name}: {ex}")

        except Exception as e:
            logger.warning(f"Failed to read metadata for {name}: {e}")
            
        sessions.append(session_info)
        
    logger.info(f"API: Returning {len(sessions)} sessions")
    return {"sessions": sessions}

@router.post("/sessions/upload")
async def upload_session(file: UploadFile = File(...), profile_id: Optional[str] = Query("guest")):
    """Upload a .duckdb or .ld session file. .ld files are auto-converted."""
    data_dir, _ = get_contextual_dirs(profile_id)
    fname = file.filename or ""
    is_ld = fname.lower().endswith(".ld")
    is_duckdb = fname.lower().endswith(".duckdb")
    if not is_duckdb and not is_ld:
        raise HTTPException(status_code=400, detail="Only .duckdb or .ld files are allowed")

    filename = os.path.basename(fname)
    file_path = os.path.join(data_dir, filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        if is_ld:
            # Convert .ld → .duckdb with canonical filename
            from ..services.ld_converter import convert_ld_to_duckdb
            tmp_path = os.path.join(data_dir, "_tmp_ld_upload.duckdb")
            try:
                info = convert_ld_to_duckdb(file_path, tmp_path)
                os.remove(file_path)   # remove the temp .ld
                duckdb_filename = _canonical_db_name(info)
                duckdb_path = os.path.join(data_dir, duckdb_filename)
                if os.path.exists(duckdb_path):
                    os.remove(tmp_path)
                else:
                    os.rename(tmp_path, duckdb_path)
                return {
                    "id": duckdb_filename,
                    "status": "converted",
                    "size": os.path.getsize(duckdb_path),
                    "track": info.get("track"),
                    "driver": info.get("driver"),
                    "laps": info.get("laps"),
                }
            except Exception as conv_err:
                logger.error(f".ld conversion failed for {filename}: {conv_err}", exc_info=True)
                for p in (file_path, tmp_path):
                    if os.path.exists(p):
                        try: os.remove(p)
                        except: pass
                raise HTTPException(status_code=500, detail=f".ld conversion failed: {str(conv_err)}")

        return {"id": filename, "status": "uploaded", "size": os.path.getsize(file_path)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

class RenameRequest(BaseModel):
    new_name: str

@router.post("/sessions/{session_id}/rename") # Using POST or PUT
async def rename_session(session_id: str, request: RenameRequest, profile_id: Optional[str] = Query("guest")):
    """Rename a session file."""
    session_id = safe_session_id(session_id)
    data_dir, cache_dir = get_contextual_dirs(profile_id)
    old_path = os.path.join(data_dir, session_id)
    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="Session not found")
        
    new_name = request.new_name
    if not new_name.endswith(".duckdb"):
        new_name += ".duckdb"
        
    # Prevent path traversal
    new_name = os.path.basename(new_name)
    new_path = os.path.join(data_dir, new_name)
    
    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="File with new name already exists")
        
    try:
        os.rename(old_path, new_path)

        # Follow the rename in the sharing registry so it stays shared
        owner = resolve_profile_id(profile_id)
        if SharingService.unshare(session_id, owner):
            SharingService.share(new_name, owner)

        # Clear Cache if exists
        if os.path.exists(cache_dir):
            for cache_file in glob.glob(os.path.join(cache_dir, f"{session_id}*.parquet")):
                try:
                    os.remove(cache_file)
                except Exception as e:
                    print(f"Warning: Failed to delete cache file {cache_file}: {e}")
            
        return {"id": new_name, "old_id": session_id, "status": "renamed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, profile_id: Optional[str] = Query("guest")):
    """Delete a session file."""
    session_id = safe_session_id(session_id)
    data_dir, cache_dir = get_contextual_dirs(profile_id)
    file_path = os.path.join(data_dir, session_id)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Session not found")
        
    try:
        os.remove(file_path)
        
        # Clear Cache
        if os.path.exists(cache_dir):
            for cache_file in glob.glob(os.path.join(cache_dir, f"{session_id}*.parquet")):
                try:
                    os.remove(cache_file)
                except Exception as e:
                    print(f"Warning: Failed to delete cache file {cache_file}: {e}")
            
        return {"id": session_id, "status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# Session sharing — make your own sessions readable by everyone on the server
# ──────────────────────────────────────────────────────────────────────────────

def _require_own_session(session_id: str, profile_id: Optional[str]) -> tuple[str, str]:
    """Reject sharing a session you don't own."""
    session_id = safe_session_id(session_id)
    owner = resolve_profile_id(profile_id)
    if not os.path.exists(os.path.join(ProfilesService.get_profile_data_dir(owner), session_id)):
        raise HTTPException(status_code=404, detail="Session not found")
    return session_id, owner


@router.post("/sessions/{session_id}/share")
async def share_session(session_id: str, profile_id: Optional[str] = Query("guest")):
    session_id, owner = _require_own_session(session_id, profile_id)
    SharingService.share(session_id, owner)
    return {"id": session_id, "shared": True}


@router.delete("/sessions/{session_id}/share")
async def unshare_session(session_id: str, profile_id: Optional[str] = Query("guest")):
    session_id, owner = _require_own_session(session_id, profile_id)
    SharingService.unshare(session_id, owner)
    return {"id": session_id, "shared": False}


@router.get("/sessions/shared")
async def list_shared_sessions(profile_id: Optional[str] = Query("guest")):
    """Sessions shared on this server, with who owns each one."""
    me = resolve_profile_id(profile_id)
    names = {p["id"]: p.get("name", p["id"]) for p in ProfilesService.list_profiles()}
    shared = []
    for entry in SharingService.list_shared():
        owner = entry["owner_profile_id"]
        shared.append({
            "id": entry["session_id"],
            "ownerProfile": owner,
            "ownerName": names.get(owner, owner),
            "sharedAt": entry.get("shared_at"),
            "isMine": owner == me,
        })
    return {"shared": shared}


from fastapi.responses import PlainTextResponse
from ..services.setup_exporter import generate_svm_from_duckdb

@router.get("/sessions/{session_id}/setup/export")
async def export_session_setup(session_id: str, request: Request, custom_car_model: Optional[str] = Query(None), profile_id: Optional[str] = Query("guest")):
    """Export car setup data to .svm format."""
    session_id = safe_session_id(session_id)
    from ..services.car_lookup import get_car_info

    q_custom = request.query_params.get("custom_car_model")
    if q_custom:
        custom_car_model = q_custom

    logger.info(f"API: GET /setup/export - Session: {session_id}, custom_car_model: {custom_car_model}, profile_id: {profile_id}")

    db_path, _, _ = resolve_readable_session(session_id, profile_id)

    try:
        svm_content = generate_svm_from_duckdb(db_path)

        # Build filename with same logic as lap export
        filename = f"{os.path.splitext(session_id)[0]}_setup.svm"  # safe fallback
        try:
            with duckdb.connect(db_path, read_only=True) as con:
                meta_rows = con.execute(
                    "SELECT key, value FROM metadata WHERE key IN "
                    "('TrackName', 'TrackLayout', 'CarName', 'CarClass', 'RecordingTime')"
                ).fetchall()
            meta = {k: v for k, v in meta_rows}

            import re
            layout_name = meta.get("TrackLayout", "Layout").replace(" ", "-")
            layout_name = re.sub(r'[\\/*?:"<>|]', '', layout_name)

            raw_car = meta.get("CarName", "")
            raw_class = meta.get("CarClass", "")
            if custom_car_model:
                car_model = custom_car_model
            else:
                car_model, _ = get_car_info(raw_car, raw_class)
            car_model = car_model.replace(" ", "-")

            recording_time = meta.get("RecordingTime", os.path.splitext(session_id)[0])

            filename = f"{layout_name}_{car_model}_{recording_time}_setup.svm"
        except Exception as name_err:
            logger.warning(f"SVM filename generation failed, falling back: {name_err}")

        return PlainTextResponse(
            content=svm_content,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error exporting setup for {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to export setup: {str(e)}")

@router.get("/sessions/{session_id}/setup")
async def get_session_setup(session_id: str, profile_id: Optional[str] = Query("guest")):
    """Get structured car setup data from a session's DuckDB metadata."""
    db_path, _, _ = resolve_readable_session(session_id, profile_id)

    try:
        import json
        with duckdb.connect(db_path, read_only=True) as con:
            row = con.execute("SELECT value FROM metadata WHERE key = 'CarSetup'").fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="CarSetup not found in metadata")

        raw = json.loads(row[0])

        def val(key):
            entry = raw.get(key)
            if not isinstance(entry, dict): return None
            if not entry.get('available', False): return None
            v = entry.get('stringValue', '')
            if v in ('N/A', '', None): return None
            return v

        setup = {
            "powertrain": {
                "engine": {
                    "Virtual Energy":     val("VM_VIRTUAL_ENERGY"),
                    "Fuel Ratio":         val("VM_FUEL_LEVEL"),
                    "Fuel Capacity":      val("VM_FUEL_CAPACITY"),
                    "Rev Limiter":        val("VM_REV_LIMITER"),
                    "Engine Mixture":     val("VM_ENGINE_MIXTURE"),
                    "Water Radiator":     val("VM_WATER_RADIATOR"),
                    "Oil Radiator":       val("VM_OIL_RADIATOR"),
                },
                "electronics": {
                    "Onboard TC":         val("VM_TRACTIONCONTROLMAP"),
                    "TC Power Cut":       val("VM_TRACTIONCONTROLPOWERCUTMAP"),
                    "TC Slip Angle":      val("VM_TRACTIONCONTROLSLIPANGLEMAP"),
                    "Regen Level":        val("VM_REGEN_LEVEL"),
                    "Electric Motor Map": val("VM_ELECTRIC_MOTOR_MAP"),
                },
                "differential": {
                    "Power":              val("VM_DIFF_POWER"),
                    "Coast":              val("VM_DIFF_COAST"),
                    "Preload":            val("VM_DIFF_PRELOAD"),
                    "Front Power":        val("VM_FRONT_DIFF_POWER"),
                    "Front Coast":        val("VM_FRONT_DIFF_COAST"),
                    "Front Preload":      val("VM_FRONT_DIFF_PRELOAD"),
                    "Torque Split":       val("VM_TORQUE_SPLIT"),
                },
                "gearing": {
                    "Ratio Set":  val("VM_RATIO_SET"),
                    "Gear 1":     val("VM_GEAR_1"),
                    "Gear 2":     val("VM_GEAR_2"),
                    "Gear 3":     val("VM_GEAR_3"),
                    "Gear 4":     val("VM_GEAR_4"),
                    "Gear 5":     val("VM_GEAR_5"),
                    "Gear 6":     val("VM_GEAR_6"),
                    "Gear 7":     val("VM_GEAR_7"),
                    "Final Drive":val("VM_GEAR_FINAL"),
                    "Reverse":    val("VM_GEAR_REVERSE"),
                },
            },
            "wheelsAndBrakes": {
                "frontWheels": {
                    "Compound":     { "L": val("WM_COMPOUND-W_FL"),  "R": val("WM_COMPOUND-W_FR") },
                    "Tyre Pressure":{ "L": val("WM_PRESSURE-W_FL"),  "R": val("WM_PRESSURE-W_FR") },
                    "Camber":       { "L": val("WM_CAMBER-W_FL"),    "R": val("WM_CAMBER-W_FR") },
                    "Brake Disc":   { "L": val("WM_BRAKEDISC-W_FL"), "R": val("WM_BRAKEDISC-W_FR") },
                    "Brake Pad":    { "L": val("WM_BRAKEPAD-W_FL"),  "R": val("WM_BRAKEPAD-W_FR") },
                },
                "rearWheels": {
                    "Compound":     { "L": val("WM_COMPOUND-W_RL"),  "R": val("WM_COMPOUND-W_RR") },
                    "Tyre Pressure":{ "L": val("WM_PRESSURE-W_RL"),  "R": val("WM_PRESSURE-W_RR") },
                    "Camber":       { "L": val("WM_CAMBER-W_RL"),    "R": val("WM_CAMBER-W_RR") },
                    "Brake Disc":   { "L": val("WM_BRAKEDISC-W_RL"), "R": val("WM_BRAKEDISC-W_RR") },
                    "Brake Pad":    { "L": val("WM_BRAKEPAD-W_RL"),  "R": val("WM_BRAKEPAD-W_RR") },
                },
                "brakes": {
                    "Brake Bias":         val("VM_BRAKE_BALANCE"),
                    "Brake Migration":    val("VM_BRAKE_MIGRATION"),
                    "Max Pedal Force":    val("VM_BRAKE_PRESSURE"),
                    "Front Brake Duct":   val("VM_BRAKE_DUCTS"),
                    "Rear Brake Duct":    val("VM_BRAKE_DUCTS_REAR"),
                    "ABS":               val("VM_ANTILOCKBRAKESYSTEMMAP"),
                },
            },
            "suspension": {
                "front": {
                    "Spring Rate":   { "L": val("WM_SPRING-W_FL"),      "3rd": val("VM_FRONT_3RD_SPRING"),         "R": val("WM_SPRING-W_FR") },
                    "Tender Spring": { "L": val("WM_TENDERSPRING-W_FL"), "3rd": val("VM_FRONT_3RD_TENDERSPRING"),   "R": val("WM_TENDERSPRING-W_FR") },
                    "Packers":       { "L": val("WM_PACKERS-W_FL"),      "3rd": val("VM_FRONT_3RD_PACKERS"),        "R": val("WM_PACKERS-W_FR") },
                    "Ride Height":   { "L": val("WM_RIDEHEIGHT-W_FL"),   "3rd": None,                               "R": val("WM_RIDEHEIGHT-W_FR") },
                    "Spring Rubber": { "L": val("WM_SRUBBER-W_FL"),      "3rd": None,                               "R": val("WM_SRUBBER-W_FR") },
                },
                "rear": {
                    "Spring Rate":   { "L": val("WM_SPRING-W_RL"),      "3rd": val("VM_REAR_3RD_SPRING"),          "R": val("WM_SPRING-W_RR") },
                    "Tender Spring": { "L": val("WM_TENDERSPRING-W_RL"), "3rd": val("VM_REAR_3RD_TENDERSPRING"),    "R": val("WM_TENDERSPRING-W_RR") },
                    "Packers":       { "L": val("WM_PACKERS-W_RL"),      "3rd": val("VM_REAR_3RD_PACKERS"),         "R": val("WM_PACKERS-W_RR") },
                    "Ride Height":   { "L": val("WM_RIDEHEIGHT-W_RL"),   "3rd": None,                               "R": val("WM_RIDEHEIGHT-W_RR") },
                    "Spring Rubber": { "L": val("WM_SRUBBER-W_RL"),      "3rd": None,                               "R": val("WM_SRUBBER-W_RR") },
                },
            },
            "dampers": {
                "front": {
                    "Slow Bump":    { "L": val("WM_SLOWBUMP-W_FL"),    "3rd": val("VM_FRONT_3RD_SLOWBUMP"),    "R": val("WM_SLOWBUMP-W_FR") },
                    "Slow Rebound": { "L": val("WM_SLOWREBOUND-W_FL"), "3rd": val("VM_FRONT_3RD_SLOWREBOUND"), "R": val("WM_SLOWREBOUND-W_FR") },
                    "Fast Bump":    { "L": val("WM_FASTBUMP-W_FL"),    "3rd": val("VM_FRONT_3RD_FASTBUMP"),    "R": val("WM_FASTBUMP-W_FR") },
                    "Fast Rebound": { "L": val("WM_FASTREBOUND-W_FL"), "3rd": val("VM_FRONT_3RD_FASTREBOUND"), "R": val("WM_FASTREBOUND-W_FR") },
                },
                "rear": {
                    "Slow Bump":    { "L": val("WM_SLOWBUMP-W_RL"),    "3rd": val("VM_REAR_3RD_SLOWBUMP"),    "R": val("WM_SLOWBUMP-W_RR") },
                    "Slow Rebound": { "L": val("WM_SLOWREBOUND-W_RL"), "3rd": val("VM_REAR_3RD_SLOWREBOUND"), "R": val("WM_SLOWREBOUND-W_RR") },
                    "Fast Bump":    { "L": val("WM_FASTBUMP-W_RL"),    "3rd": val("VM_REAR_3RD_FASTBUMP"),    "R": val("WM_FASTBUMP-W_RR") },
                    "Fast Rebound": { "L": val("WM_FASTREBOUND-W_RL"), "3rd": val("VM_REAR_3RD_FASTREBOUND"), "R": val("WM_FASTREBOUND-W_RR") },
                },
            },
            "chassisAndAero": {
                "frontChassis": {
                    "Caster L":       val("VM_LEFT_CASTER"),
                    "Caster R":       val("VM_RIGHT_CASTER"),
                    "Toe-in":         val("VM_FRONT_TOEIN"),
                    "Anti-roll Bar":  val("VM_FRONT_ANTISWAY"),
                    "Wheel Track":    val("VM_FRONT_WHEEL_TRACK"),
                    "Wheel Range":    val("VM_STEER_LOCK"),
                    "Front Wing":     val("VM_FRONT_WING"),
                },
                "rearChassis": {
                    "Toe-in":         val("VM_REAR_TOEIN"),
                    "Anti-roll Bar":  val("VM_REAR_ANTISWAY"),
                    "Wheel Track":    val("VM_REAR_WHEEL_TRACK"),
                    "Rear Wing":      val("VM_REAR_WING"),
                },
                "weight": {
                    "Vertical":            val("VM_WEIGHT_VERTICAL"),
                    "Lateral":             val("VM_WEIGHT_LATERAL"),
                    "Weight Distribution": val("VM_WEIGHT_DISTRIB"),
                    "Wedge":               val("VM_WEIGHT_WEDGE"),
                },
                "advancedChassis": {
                    "Chassis Adj 0": val("VM_CHASSIS_ADJ_00"),
                    "Chassis Adj 1": val("VM_CHASSIS_ADJ_01"),
                    "Chassis Adj 2": val("VM_CHASSIS_ADJ_02"),
                    "Chassis Adj 3": val("VM_CHASSIS_ADJ_03"),
                    "Chassis Adj 4": val("VM_CHASSIS_ADJ_04"),
                    "Chassis Adj 5": val("VM_CHASSIS_ADJ_05"),
                },
            },
        }
        return {"setup": setup}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting setup for {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/{session_id}/laps")
async def get_session_laps(session_id: str, profile_id: Optional[str] = Query("guest")):
    """Get summary of laps for a session with robust logging."""
    logger.info(f"API: GET /laps - Profile: {profile_id}, Session: {session_id}")
    db_path, _, _ = resolve_readable_session(session_id, profile_id)


    try:
        data = TelemetryService.get_laps_header(db_path)
        logger.info(f"API: Successfully retrieved {len(data.get('laps', []))} laps for {session_id}")
        return data
    except Exception as e:
        logger.error(f"Error getting laps for {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/reference-laps")
async def get_reference_laps(
    track_name: str = Query(...),
    track_layout: str = Query(""),
    car_class: str = Query(...),
    profile_id: Optional[str] = Query("guest")
):
    """Find compatible laps for reference: own sessions plus those shared by others."""
    me = resolve_profile_id(profile_id)
    data_dir = ProfilesService.get_profile_data_dir(me)

    names = {p["id"]: p.get("name", p["id"]) for p in ProfilesService.list_profiles()}
    shared_sessions = []
    for entry in SharingService.list_shared():
        owner = entry["owner_profile_id"]
        if owner == me:
            continue   # already covered by the scan of the caller's own directory
        shared_sessions.append({
            "path": os.path.join(ProfilesService.get_profile_data_dir(owner), entry["session_id"]),
            "ownerProfile": owner,
            "ownerName": names.get(owner, owner),
        })

    try:
        laps = TelemetryService.find_compatible_laps(
            data_dir, track_name, track_layout, car_class, shared_sessions=shared_sessions
        )
        return {"laps": laps}
    except Exception as e:
        logger.error(f"Error finding compatible laps: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sessions/{session_id}/telemetry")
async def get_telemetry(
    session_id: str, 
    request: Request,
    freq: int = Query(60, ge=1, le=1000),
    stint_id: Optional[int] = Query(None),
    lap_id: Optional[int] = Query(None),
    profile_id: Optional[str] = Query("guest")
):
    """Get fused telemetry data with robust logging."""
    session_id = safe_session_id(session_id)
    logger.info(f"API: GET /telemetry - Profile: {profile_id}, Session: {session_id}, Freq: {freq}, Stint: {stint_id}")
    db_path, cache_dir, owner = resolve_readable_session(session_id, profile_id)

    try:
        # Ensure cache dir exists
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)

        stint_suffix = f"_stint{stint_id}" if stint_id is not None else ""
        lap_suffix = f"_lap{lap_id}" if lap_id is not None else ""
        owner_prefix = f"shared_{owner}_" if owner else ""
        parquet_path = os.path.join(cache_dir, f"{owner_prefix}{session_id}_{freq}Hz{stint_suffix}{lap_suffix}_elev_v12.parquet")
        
        if os.path.exists(parquet_path):
             import pandas as pd
             df = pd.read_parquet(parquet_path)
        else:
             logger.info(f"Fusing data for {session_id} at {freq}Hz...")
             trim_start = None
             trim_end = None
             
             header_data = TelemetryService.get_laps_header(db_path)
             if stint_id is not None:
                 stint_laps = [lap for lap in header_data['laps'] if lap.get('stint') == stint_id]
                 if stint_laps:
                     trim_start = min(lap['startTime'] for lap in stint_laps)
                     trim_end = max(lap['endTime'] for lap in stint_laps)
                 else:
                     logger.warning(f"Stint {stint_id} not found in session")
                     raise HTTPException(status_code=404, detail=f"Stint {stint_id} not found")
             
             # Map lap_id if provided (could be used together with stint or alone)
             # If lap_id is provided, it OVERRIDES stint boundaries for that specific lap
             lap_id = request.query_params.get("lap_id")
             if lap_id is not None:
                 try:
                     lap_idx = int(lap_id)
                     target_lap = next((l for l in header_data['laps'] if l['lap'] == lap_idx), None)
                     if target_lap:
                         trim_start = target_lap['startTime']
                         trim_end = target_lap['endTime']
                         logger.info(f"Slicing telemetry for specific lap {lap_idx}: {trim_start} to {trim_end}")
                 except: pass

             df = TelemetryService.fuse_session_data(
                 db_path, 
                 output_parquet=parquet_path, 
                 target_freq=freq,
                 trim_start_time=trim_start,
                 trim_end_time=trim_end
             )
        
        import numpy as np
        df = df.replace({np.nan: None})
        
        # Calculate suspension baselines if Susp Pos exists
        baselines = [0.0, 0.0, 0.0, 0.0]
        if 'Susp Pos' in df.columns:
            try:
                raw_col = df['Susp Pos'].tolist()
                # Log first sample to see what type/value we're dealing with
                first_sample = raw_col[0] if raw_col else None
                logger.info(f"[Baseline] Susp Pos sample type={type(first_sample).__name__}, value={first_sample}")

                # Build 2D array - handle list, ndarray, tuple, or scalar
                def to_row(x):
                    if x is None:
                        return [np.nan, np.nan, np.nan, np.nan]
                    try:
                        arr = np.asarray(x, dtype=np.float64).flatten()
                        if len(arr) == 4:
                            return arr.tolist()
                    except Exception:
                        pass
                    return [np.nan, np.nan, np.nan, np.nan]

                susp_vals = np.array([to_row(x) for x in raw_col], dtype=np.float64)
                logger.info(f"[Baseline] susp_vals shape={susp_vals.shape}, sample row={susp_vals[0] if len(susp_vals) else 'empty'}")

                if susp_vals.ndim == 2 and susp_vals.shape[1] == 4:
                    speed = None
                    if 'Ground Speed' in df.columns:
                        speed = np.array([s if s is not None else np.nan for s in df['Ground Speed'].tolist()], dtype=np.float64)
                    elif 'GPS Speed' in df.columns:
                        speed = np.array([s if s is not None else np.nan for s in df['GPS Speed'].tolist()], dtype=np.float64)

                    g_lat = np.array([g if g is not None else np.nan for g in df['G Force Lat'].tolist()], dtype=np.float64) if 'G Force Lat' in df.columns else np.zeros(len(df))
                    g_long = np.array([g if g is not None else np.nan for g in df['G Force Long'].tolist()], dtype=np.float64) if 'G Force Long' in df.columns else np.zeros(len(df))
                    logger.info(f"[Baseline] G Force Lat found={('G Force Lat' in df.columns)}, speed found={speed is not None}, speed_max={np.nanmax(speed) if speed is not None else 'N/A'}")

                    # Straight line and low speed mask
                    # g_lat < 0.3G to ensure we're on a straight, g_long < 0.5G to allow gentle pit braking
                    lat_ok = np.abs(np.nan_to_num(g_lat, nan=999.0)) < 0.3
                    long_ok = np.abs(np.nan_to_num(g_long, nan=999.0)) < 0.5
                    mask = lat_ok & long_ok

                    if speed is not None:
                        max_sp = np.nanmax(speed) if len(speed) > 0 else 0
                        # Detect unit: m/s if max < 80, km/h otherwise
                        # Use 15 m/s (~54 km/h) for m/s, 50 km/h for km/h
                        speed_limit = 15.0 if max_sp < 80 else 50.0
                        speed_ok = np.nan_to_num(speed, nan=999.0) < speed_limit
                        mask &= speed_ok

                    valid_points = susp_vals[mask]
                    if len(valid_points) > 0:
                        valid_points = valid_points[~np.isnan(valid_points).any(axis=1)]

                    logger.info(f"[Baseline] mask_count={mask.sum()}, valid_points_after_nan_filter={len(valid_points)}")

                    if len(valid_points) >= 10:
                        baselines = np.nanmean(valid_points, axis=0).tolist()
                    else:
                        baselines = np.nanmedian(susp_vals, axis=0).tolist()

                    # Keep raw sign (negative for LMU Susp Pos) – frontend uses Math.abs for Raw mode
                    # and -(val - baseline) for Relative/Standard, (val - baseline) for Inverted
                    baselines = [0.0 if np.isnan(x) or np.isinf(x) else float(x) for x in baselines]
                    logger.info(f"[Baseline] Final baselines (m, raw sign): {baselines}")
            except Exception as ex:
                logger.error(f"Failed to calculate suspension baselines: {ex}", exc_info=True)

        # Build JSON response manually for speed
        logger.info(f"Serializing {len(df)} rows...")
        from fastapi import Response
        import json
        json_parts = ['{']
        for i, col in enumerate(df.columns):
            if i > 0: json_parts.append(',')
            json_parts.append(f'"{col}":{df[col].to_json(orient="values")}')
        
        # Append suspension baselines list
        json_parts.append(f', "suspension_baselines": {json.dumps(baselines)}')
        json_parts.append('}')
        
        logger.info(f"API: Telemetry delivery complete")
        return Response(content="".join(json_parts), media_type="application/json")
        
    except Exception as e:
        logger.error(f"Error getting telemetry for {session_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/sessions/{session_id}/export/lap/{lap_number}")
async def export_session_lap(session_id: str, lap_number: int, request: Request, custom_car_model: Optional[str] = Query(None), profile_id: Optional[str] = Query("guest")):
    """Export a specific lap as a standalone .duckdb file."""
    session_id = safe_session_id(session_id)
    q_custom = request.query_params.get("custom_car_model")
    if q_custom:
        custom_car_model = q_custom

    logger.info(f"API: GET /export/lap - Session: {session_id}, Lap: {lap_number}, custom_car_model: {custom_car_model}, profile_id: {profile_id}")
    db_path, cache_dir, _ = resolve_readable_session(session_id, profile_id)


    try:
        # 1. Fetch metadata for naming
        from ..services.car_lookup import get_car_info
        
        track_name = "Unknown"
        car_model = "Unknown"
        driver_name = "Unknown"
        lap_time_str = "0m00s000"
        
        import duckdb
        with duckdb.connect(db_path, read_only=True) as con:
            meta = con.execute(
                "SELECT key, value FROM metadata WHERE key IN "
                "('TrackName', 'TrackLayout', 'CarName', 'CarClass', 'DriverName', 'RecordingTime')"
            ).fetchall()
            meta_dict = {k: v for k, v in meta}
            import re
            layout_name = meta_dict.get('TrackLayout', 'Layout').replace(" ", "-")
            layout_name = re.sub(r'[\\/*?:"<>|]', '', layout_name)
            raw_car = meta_dict.get('CarName', '')
            raw_class = meta_dict.get('CarClass', '')
            driver_name = meta_dict.get('DriverName', 'Driver').replace(" ", "-")
            if custom_car_model:
                car_model = custom_car_model
            else:
                car_model, _ = get_car_info(raw_car, raw_class)
            car_model = car_model.replace(" ", "-")
            recording_time = meta_dict.get('RecordingTime') or datetime.now().strftime("%Y-%m-%dT%H_%M_%SZ")

        # 2. Get Lap Time for naming
        laps_res = TelemetryService.get_laps_header(db_path)
        laps = laps_res.get("laps", [])
        target_lap = next((l for l in laps if l.get("lap") == lap_number), None)
        if target_lap:
            t = target_lap["duration"]
            m = int(t // 60)
            s = int(t % 60)
            ms = int((t * 1000) % 1000)
            lap_time_str = f"{m}m{s:02d}s{ms:03d}"

        # 3. Generate Filename — use original session's RecordingTime as timestamp
        export_filename = f"{layout_name}-{car_model}-L{lap_number}-{lap_time_str}_{recording_time}.duckdb"
        
        # Ensure cache dir exists
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
            
        export_path = os.path.join(cache_dir, export_filename)
        
        # 4. Execute Slicing
        TelemetryService.export_lap(db_path, lap_number, export_path)
        
        if not os.path.exists(export_path):
            raise HTTPException(status_code=500, detail="Failed to generate export file")
            
        # 5. Overwrite metadata in exported DuckDB if custom_car_model is supplied
        if custom_car_model:
            try:
                with duckdb.connect(export_path, read_only=False) as con_export:
                    con_export.execute("UPDATE metadata SET value = ? WHERE key = 'CarName'", (custom_car_model,))
            except Exception as update_err:
                logger.warning(f"Failed to update metadata CarName in sliced export DB: {update_err}")

        return FileResponse(
            path=export_path,
            filename=export_filename,
            media_type='application/octet-stream'
        )
        
    except Exception as e:
        logger.error(f"Lap export failed for {session_id} Lap {lap_number}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ──────────────────────────────────────────────────────────────────────────────
# AI Coach / Race Engineer
# ──────────────────────────────────────────────────────────────────────────────

class LapAnalysisRequest(BaseModel):
    lap_idx: int
    reference_lap_idx: Optional[int] = None

class SessionAnalysisRequest(BaseModel):
    pass

class SetupAdviceRequest(BaseModel):
    lap_idx: Optional[int] = None


def _get_coach_service():
    from ..services.ai_coach.coach_service import CoachService
    settings = get_settings()
    return CoachService(settings)


@router.get("/ai-coach/status")
async def ai_coach_status():
    """Check if the AI coach is configured (API key present)."""
    service = _get_coach_service()
    return {"configured": service.check_api_key()}


@router.post("/ai-coach/{session_id}/analyze-lap")
async def ai_coach_analyze_lap(
    session_id: str,
    req: LapAnalysisRequest,
    profile_id: Optional[str] = Query("guest"),
):
    session_id = safe_session_id(session_id)
    data_dir, _ = get_contextual_dirs(profile_id)   # engineer memory stays the reader's own
    db_path, _, _ = resolve_readable_session(session_id, profile_id)

    service = _get_coach_service()
    result = service.analyze_lap(
        db_path=db_path,
        lap_idx=req.lap_idx,
        reference_lap_idx=req.reference_lap_idx,
        data_dir=data_dir,
        session_id=session_id,
    )
    if result.error:
        raise HTTPException(status_code=500, detail=result.error)
    return result.to_dict()


@router.post("/ai-coach/{session_id}/analyze-session")
async def ai_coach_analyze_session(
    session_id: str,
    profile_id: Optional[str] = Query("guest"),
):
    session_id = safe_session_id(session_id)
    data_dir, _ = get_contextual_dirs(profile_id)   # engineer memory stays the reader's own
    db_path, _, _ = resolve_readable_session(session_id, profile_id)

    service = _get_coach_service()
    result = service.analyze_session(
        db_path=db_path,
        data_dir=data_dir,
        session_id=session_id,
    )
    if result.error:
        raise HTTPException(status_code=500, detail=result.error)
    return result.to_dict()


@router.post("/ai-coach/{session_id}/setup-advice")
async def ai_coach_setup_advice(
    session_id: str,
    req: SetupAdviceRequest,
    profile_id: Optional[str] = Query("guest"),
):
    session_id = safe_session_id(session_id)
    data_dir, _ = get_contextual_dirs(profile_id)   # engineer memory stays the reader's own
    db_path, _, _ = resolve_readable_session(session_id, profile_id)

    # Optionally fetch setup data
    setup_data = None
    try:
        import json as _json
        with duckdb.connect(db_path, read_only=True) as con:
            row = con.execute("SELECT value FROM metadata WHERE key = 'CarSetup'").fetchone()
        if row:
            setup_data = _json.loads(row[0])
    except Exception:
        pass

    service = _get_coach_service()
    result = service.advise_setup(
        db_path=db_path,
        setup_data=setup_data,
        lap_idx=req.lap_idx,
        data_dir=data_dir,
        session_id=session_id,
    )
    if result.error:
        raise HTTPException(status_code=500, detail=result.error)
    return result.to_dict()


@router.get("/ai-coach/memory")
async def ai_coach_list_memory(
    circuit: Optional[str] = Query(None),
    car: Optional[str] = Query(None),
    profile_id: Optional[str] = Query("guest"),
):
    from ..services.ai_coach.engineer_memory import EngineerMemory
    data_dir, _ = get_contextual_dirs(profile_id)
    memory = EngineerMemory(data_dir)
    return {"observations": memory.list_all(circuit=circuit, car=car)}


@router.delete("/ai-coach/memory/{observation_id}")
async def ai_coach_delete_memory(
    observation_id: int,
    profile_id: Optional[str] = Query("guest"),
):
    from ..services.ai_coach.engineer_memory import EngineerMemory
    data_dir, _ = get_contextual_dirs(profile_id)
    memory = EngineerMemory(data_dir)
    deleted = memory.delete(observation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Observation not found")
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────────
# Discord Telemetry Sharing
# ──────────────────────────────────────────────────────────────────────────────

class DiscordShareRequest(BaseModel):
    lap_number: int
    title: str
    content: str
    attach_setup: bool
    car_class: str
    custom_car_model: Optional[str] = None
    profile_id: Optional[str] = "guest"
    discord_handle: Optional[str] = None

@router.get("/discord/config")
async def get_discord_config():
    """Get public Discord configuration (configured state and invite URL)."""
    try:
        from ..services.discord_service import DiscordService
        return {
            "is_configured": DiscordService.is_configured(),
            "invite_url": DiscordService.get_invite_url()
        }
    except Exception as e:
        logger.error(f"Error in get_discord_config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sessions/{session_id}/discord/share")
async def discord_share_session_lap(session_id: str, request: DiscordShareRequest):
    """Export a lap (and optional setup) and upload to Discord Forum."""
    from ..services.discord_service import DiscordService
    from ..services.car_lookup import get_car_info
    import re
    
    if not DiscordService.is_configured():
        raise HTTPException(status_code=400, detail="Discord Bot is not configured. Please check discord_config.json")
        
    data_dir, cache_dir = get_contextual_dirs(request.profile_id)
    db_path = os.path.join(data_dir, session_id)
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail=f"Session file not found: {session_id}")
        
    # 1. Generate lap export (standalone sliced duckdb)
    try:
        with duckdb.connect(db_path, read_only=True) as con:
            meta_rows = con.execute(
                "SELECT key, value FROM metadata WHERE key IN "
                "('TrackName', 'TrackLayout', 'CarName', 'CarClass', 'RecordingTime')"
            ).fetchall()
        meta = {k: v for k, v in meta_rows}
        
        track_name = meta.get("TrackName", "Track")
        layout_name = meta.get("TrackLayout", "Layout").replace(" ", "-")
        layout_name = re.sub(r'[\\/*?:"<>|]', '', layout_name)
        
        raw_car = meta.get("CarName", "")
        raw_class = meta.get("CarClass", "")
        
        if request.custom_car_model:
            friendly_car = request.custom_car_model
        else:
            friendly_car, _ = get_car_info(raw_car, raw_class)
        car_model = friendly_car.replace(" ", "-")
        
        recording_time = meta.get("RecordingTime", os.path.splitext(session_id)[0])
        
        # Determine lap time string
        lap_time_str = "unknown"
        laps_res = TelemetryService.get_laps_header(db_path)
        laps = laps_res.get("laps", [])
        target_lap = next((l for l in laps if l.get("lap") == request.lap_number), None)
        if target_lap:
            t = target_lap["duration"]
            m = int(t // 60)
            s = int(t % 60)
            ms = int((t * 1000) % 1000)
            lap_time_str = f"{m}m{s:02d}s{ms:03d}"
            
        # Sliced lap filename
        export_filename = f"{layout_name}-{car_model}-L{request.lap_number}-{lap_time_str}_{recording_time}.duckdb"
        
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
            
        export_path = os.path.join(cache_dir, export_filename)
        TelemetryService.export_lap(db_path, request.lap_number, export_path)
        
        if not os.path.exists(export_path):
            raise HTTPException(status_code=500, detail="Failed to slice lap telemetry file")
            
        # Overwrite metadata in exported DuckDB if custom_car_model is supplied
        if request.custom_car_model:
            try:
                with duckdb.connect(export_path, read_only=False) as con_export:
                    con_export.execute("UPDATE metadata SET value = ? WHERE key = 'CarName'", (request.custom_car_model,))
            except Exception as update_err:
                logger.warning(f"Failed to update metadata CarName in sliced export DB for Discord: {update_err}")

        file_paths = [export_path]
        
        # 2. Generate Setup file (.svm) if requested
        setup_path = None
        if request.attach_setup:
            try:
                svm_content = generate_svm_from_duckdb(db_path)
                setup_filename = f"{layout_name}_{car_model}_{recording_time}_setup.svm"
                setup_path = os.path.join(cache_dir, setup_filename)
                with open(setup_path, "w", encoding="utf-8") as f_setup:
                    f_setup.write(svm_content)
                file_paths.append(setup_path)
            except Exception as setup_err:
                logger.error(f"Setup generation failed for Discord share: {setup_err}")

        # 3. Share to Discord (Match tag name by track name)
        track_tag_name = track_name if track_name else ""
        
        # Validate Discord membership (Scheme B)
        if not request.discord_handle:
            raise HTTPException(status_code=400, detail="Discord username is required for server membership verification.")
            
        member_data = DiscordService.search_guild_member(request.discord_handle)
        if not member_data:
            raise HTTPException(
                status_code=400, 
                detail="You are not a member of our Discord server yet! Please join our server to share telemetry."
            )
            
        user_id = member_data.get("user_id")
        driver_mention = f"<@{user_id}>"
        
        header_block = (
            f"### Telemetry Shared by {driver_mention}\n\n"
            f"* **Track:** {track_name} ({layout_name})\n"
            f"* **Car:** {friendly_car}\n"
            f"* **Lap Time:** {lap_time_str}\n\n"
            f"---\n\n"
        )
        full_content = header_block + request.content
        
        result = DiscordService.share_to_forum(
            car_class=request.car_class,
            title=request.title,
            content=full_content,
            track_tag_name=track_tag_name,
            file_paths=file_paths
        )
        
        # 4. Cleanup temp files
        for fp in file_paths:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception as cleanup_err:
                logger.warning(f"Failed to clean up temp file {fp}: {cleanup_err}")
                
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
            
        return result
        
    except Exception as e:
        logger.error(f"Discord sharing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/debug/env")
async def debug_env():
    """Diagnostic endpoint for packaged environment. Blocked in server mode by LocalOnlyGuard."""
    settings = get_settings()
    return {
        "is_frozen": getattr(sys, 'frozen', False),
        "APP_DATA_ROOT": APP_DATA_ROOT,
        "APP_DATA_ROOT_exists": os.path.exists(APP_DATA_ROOT),
        "mode": settings.APP_MODE,
    }
