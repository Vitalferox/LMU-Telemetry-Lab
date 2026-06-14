"""DAMPlugin manager — install/remove the MoTeC telemetry plugin for LMU."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

_PLUGIN_DLL = "DAMPlugin.dll"
_PLUGINS_DIR = Path("Bin64") / "Plugins"
_PLUGIN_DATA_DIR = Path("PluginData")
_CONFIG_JSON = Path("UserData") / "Player" / "CustomPluginVariables.JSON"
_DAM_INI_DST = Path("UserData") / "Player" / "DAMPlugin.ini"


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "assets" / "damplugin"


def status(lmu_root: Path) -> dict:
    dll_installed = (lmu_root / _PLUGINS_DIR / _PLUGIN_DLL).exists()
    ini_present = (lmu_root / _DAM_INI_DST).exists()
    plugin_data_present = (lmu_root / _PLUGIN_DATA_DIR / "DAMPlugin").exists()

    enabled_in_json = False
    json_path = lmu_root / _CONFIG_JSON
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            for key in data:
                if "damplugin" in key.lower() and isinstance(data[key], dict):
                    enabled_in_json = data[key].get("Enabled", 0) == 1
                    break
        except Exception:
            pass

    assets = _assets_dir()
    assets_available = (assets / _PLUGIN_DLL).exists()

    return {
        "installed": dll_installed,
        "ini_present": ini_present,
        "plugin_data_present": plugin_data_present,
        "enabled_in_json": enabled_in_json,
        "assets_available": assets_available,
        "lmu_root": str(lmu_root),
    }


def activate(lmu_root: Path) -> None:
    assets = _assets_dir()
    dll_src = assets / _PLUGIN_DLL
    if not dll_src.exists():
        raise FileNotFoundError(f"DAMPlugin.dll not found in {assets}")

    dst_plugins = lmu_root / _PLUGINS_DIR
    dst_plugins.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dll_src, dst_plugins / _PLUGIN_DLL)

    src_pd = assets / "PluginData"
    dst_pd = lmu_root / _PLUGIN_DATA_DIR
    if src_pd.exists():
        if dst_pd.exists():
            shutil.rmtree(dst_pd)
        shutil.copytree(src_pd, dst_pd)

    src_ini = assets / "DAMPlugin.ini"
    if src_ini.exists():
        dst_ini = lmu_root / _DAM_INI_DST
        dst_ini.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_ini, dst_ini)

    _patch_json(lmu_root, enabled=1)


def deactivate(lmu_root: Path) -> None:
    dll_path = lmu_root / _PLUGINS_DIR / _PLUGIN_DLL
    if dll_path.exists():
        dll_path.unlink()

    dst_pd = lmu_root / _PLUGIN_DATA_DIR
    if dst_pd.exists():
        shutil.rmtree(dst_pd)

    dst_ini = lmu_root / _DAM_INI_DST
    if dst_ini.exists():
        dst_ini.unlink()

    _patch_json(lmu_root, enabled=0)


def _patch_json(lmu_root: Path, enabled: int) -> None:
    json_path = lmu_root / _CONFIG_JSON
    if not json_path.exists():
        return
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        for key in data:
            if "damplugin" in key.lower() and isinstance(data[key], dict):
                data[key]["Enabled"] = enabled
                break
        json_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass
