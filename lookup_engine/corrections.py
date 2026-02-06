"""User corrections lookup — highest priority source.

Human-verified corrections override all other sources. Supports:
- Exact address match from corrections.db
- ZIP-level corrections from JSON files
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CorrectionsLookup:
    """Highest-priority source — human-verified corrections."""

    def __init__(self, db_path: str = None, corrections_dir: str = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "data" / "corrections.db")
        if corrections_dir is None:
            corrections_dir = str(Path(__file__).parent.parent / "data" / "corrections")

        self._db_path = db_path
        self._db_available = Path(db_path).exists()
        self._zip_corrections: dict = {}  # {utility_type: {zip: provider_name}}
        self._ensure_tables()
        self._load_zip_corrections(corrections_dir)

    def _ensure_tables(self):
        """Create mapper correction tables if they don't exist."""
        if not self._db_available:
            return
        try:
            conn = sqlite3.connect(self._db_path)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS address_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    address TEXT NOT NULL,
                    lat REAL,
                    lon REAL,
                    zip_code TEXT,
                    state TEXT,
                    utility_type TEXT NOT NULL,
                    corrected_provider TEXT NOT NULL,
                    corrected_catalog_id INTEGER,
                    original_provider TEXT,
                    original_source TEXT,
                    corrected_by TEXT DEFAULT 'mapper',
                    corrected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ac_zip ON address_corrections(zip_code, utility_type);
                CREATE INDEX IF NOT EXISTS idx_ac_state ON address_corrections(state, utility_type);
                CREATE INDEX IF NOT EXISTS idx_ac_latlon ON address_corrections(lat, lon);

                CREATE TABLE IF NOT EXISTS id_mapping_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine_provider_name TEXT NOT NULL,
                    utility_type TEXT NOT NULL,
                    correct_catalog_id INTEGER NOT NULL,
                    corrected_by TEXT DEFAULT 'mapper',
                    corrected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_imc ON id_mapping_corrections(engine_provider_name, utility_type);
            """)
            conn.close()
        except sqlite3.Error as e:
            logger.warning(f"Could not create correction tables: {e}")

    def lookup_by_latlon(self, lat: float, lon: float, utility_type: str) -> Optional[dict]:
        """Find mapper corrections within ~100m of this point."""
        if not self._db_available:
            return None
        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT corrected_provider, corrected_catalog_id, state, zip_code "
                "FROM address_corrections "
                "WHERE utility_type = ? AND ABS(lat - ?) < 0.001 AND ABS(lon - ?) < 0.001 "
                "ORDER BY corrected_at DESC LIMIT 1",
                (utility_type, lat, lon),
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return {
                    "name": row[0],
                    "catalog_id": row[1],
                    "state": row[2] or "",
                    "zip_code": row[3] or "",
                    "source": "mapper_correction",
                    "confidence": 0.99,
                }
        except sqlite3.Error as e:
            logger.debug(f"Mapper correction lookup error: {e}")
        return None

    def _load_zip_corrections(self, corrections_dir: str):
        """Load ZIP-level correction JSON files."""
        cdir = Path(corrections_dir)
        if not cdir.exists():
            return

        for utype in ("electric", "gas", "water"):
            fpath = cdir / f"{utype}_zip.json"
            if fpath.exists():
                try:
                    with open(fpath) as f:
                        data = json.load(f)
                    # Handle nested structure: {_metadata: ..., corrections: {zip: ...}}
                    if isinstance(data, dict) and "corrections" in data:
                        data = data["corrections"]
                    self._zip_corrections[utype] = data
                    logger.info(f"Corrections: loaded {len(data)} {utype} ZIP corrections")
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Failed to load {fpath}: {e}")

        total = sum(len(v) for v in self._zip_corrections.values())
        if total > 0:
            logger.info(f"Corrections: {total} total ZIP corrections loaded")

    def lookup_by_address(self, address: str, utility_type: str) -> Optional[dict]:
        """Exact address match in corrections DB."""
        if not self._db_available:
            return None

        try:
            conn = sqlite3.connect(self._db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT corrected_provider, state, zip_code FROM address_corrections "
                "WHERE address = ? AND utility_type = ? "
                "LIMIT 1",
                (address, utility_type),
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    "name": row[0],
                    "state": row[1] or "",
                    "zip_code": row[2] or "",
                    "source": "correction_address",
                    "confidence": 0.99,
                }
        except sqlite3.Error as e:
            logger.warning(f"Corrections DB error: {e}")

        return None

    def lookup_by_zip(self, zip_code: str, utility_type: str) -> Optional[dict]:
        """ZIP-level correction override."""
        corrections = self._zip_corrections.get(utility_type, {})
        entry = corrections.get(zip_code)
        if not entry:
            return None

        if isinstance(entry, str):
            return {
                "name": entry,
                "source": "correction_zip",
                "confidence": 0.98,
            }
        elif isinstance(entry, dict):
            return {
                "name": entry.get("provider", entry.get("name", "")),
                "source": "correction_zip",
                "confidence": entry.get("confidence", 0.98),
            }
        return None

    @property
    def loaded(self) -> bool:
        return self._db_available or len(self._zip_corrections) > 0
