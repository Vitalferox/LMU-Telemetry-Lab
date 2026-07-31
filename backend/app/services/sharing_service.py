import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from .profiles_service import ProfilesService

logger = logging.getLogger("sharing_service")


class SharingService:
    """Registry of sessions their owner made visible to everyone on the server.

    Only a pointer is stored — the .duckdb stays in its owner's profile
    directory, which keeps sharing instant regardless of file size.
    """

    _registry_file: Optional[str] = None

    @classmethod
    def _get_registry_path(cls) -> str:
        if not cls._registry_file:
            cls._registry_file = os.path.join(ProfilesService.get_app_data_dir(), "shared_sessions.json")
        return cls._registry_file

    @classmethod
    def list_shared(cls) -> List[Dict]:
        path = cls._get_registry_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read shared_sessions.json: {e}")
            return []

        # Drop entries whose file no longer exists (deleted by its owner)
        alive = [e for e in entries if os.path.exists(cls._entry_path(e))]
        if len(alive) != len(entries):
            cls._save(alive)
        return alive

    @classmethod
    def _entry_path(cls, entry: Dict) -> str:
        return os.path.join(
            ProfilesService.get_profile_data_dir(entry["owner_profile_id"]),
            entry["session_id"],
        )

    @classmethod
    def share(cls, session_id: str, owner_profile_id: str) -> Dict:
        entries = cls.list_shared()
        for e in entries:
            if e["session_id"] == session_id and e["owner_profile_id"] == owner_profile_id:
                return e

        entry = {
            "session_id": session_id,
            "owner_profile_id": owner_profile_id,
            "shared_at": datetime.now().isoformat(),
        }
        entries.append(entry)
        cls._save(entries)
        logger.info(f"Shared session {session_id} from profile {owner_profile_id}")
        return entry

    @classmethod
    def unshare(cls, session_id: str, owner_profile_id: str) -> bool:
        entries = cls.list_shared()
        remaining = [
            e for e in entries
            if not (e["session_id"] == session_id and e["owner_profile_id"] == owner_profile_id)
        ]
        if len(remaining) == len(entries):
            return False
        cls._save(remaining)
        logger.info(f"Unshared session {session_id} from profile {owner_profile_id}")
        return True

    @classmethod
    def is_shared(cls, session_id: str, owner_profile_id: str) -> bool:
        return any(
            e["session_id"] == session_id and e["owner_profile_id"] == owner_profile_id
            for e in cls.list_shared()
        )

    @classmethod
    def find_owner(cls, session_id: str, exclude_profile_id: Optional[str] = None) -> Optional[str]:
        """Profile owning a shared session, if anyone shared one under that name."""
        for e in cls.list_shared():
            if e["session_id"] == session_id and e["owner_profile_id"] != exclude_profile_id:
                return e["owner_profile_id"]
        return None

    @classmethod
    def _save(cls, entries: List[Dict]):
        try:
            with open(cls._get_registry_path(), "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save shared_sessions.json: {e}")
