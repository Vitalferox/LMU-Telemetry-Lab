"""Persistent engineer memory — SQLite-backed observations that improve over time."""

from __future__ import annotations

import os
import sqlite3
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    circuit TEXT NOT NULL,
    car TEXT NOT NULL,
    car_class TEXT,
    category TEXT NOT NULL,
    observation TEXT NOT NULL,
    source_session TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class EngineerMemory:
    def __init__(self, data_dir: str):
        db_path = os.path.join(data_dir, "engineer_memory.sqlite")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _ensure_schema(self):
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def get_relevant(
        self, circuit: str, car: str, category: Optional[str] = None, limit: int = 15
    ) -> list[str]:
        sql = "SELECT observation FROM observations WHERE circuit = ? AND car = ?"
        params: list = [circuit, car]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [r[0] for r in rows]

    def save_observations(
        self,
        circuit: str,
        car: str,
        car_class: str,
        observations: list[dict],
        source_session: Optional[str] = None,
    ):
        if not observations:
            return
        with self._connect() as con:
            for obs in observations:
                cat = obs.get("category", "driving")
                text = obs.get("observation", "").strip()
                if not text:
                    continue
                # Dedup: skip if an identical observation already exists
                existing = con.execute(
                    "SELECT id FROM observations WHERE circuit=? AND car=? AND observation=?",
                    (circuit, car, text),
                ).fetchone()
                if existing:
                    continue
                con.execute(
                    "INSERT INTO observations (circuit, car, car_class, category, observation, source_session) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (circuit, car, car_class, cat, text, source_session),
                )
        logger.info(f"EngineerMemory: saved {len(observations)} observations for {circuit}/{car}")

    def list_all(
        self, circuit: Optional[str] = None, car: Optional[str] = None
    ) -> list[dict]:
        sql = "SELECT id, circuit, car, car_class, category, observation, source_session, created_at FROM observations WHERE 1=1"
        params: list = []
        if circuit:
            sql += " AND circuit = ?"
            params.append(circuit)
        if car:
            sql += " AND car = ?"
            params.append(car)
        sql += " ORDER BY created_at DESC"

        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [
            {
                "id": r[0],
                "circuit": r[1],
                "car": r[2],
                "car_class": r[3],
                "category": r[4],
                "observation": r[5],
                "source_session": r[6],
                "created_at": r[7],
            }
            for r in rows
        ]

    def delete(self, observation_id: int) -> bool:
        with self._connect() as con:
            cur = con.execute("DELETE FROM observations WHERE id = ?", (observation_id,))
            return cur.rowcount > 0
