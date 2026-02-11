"""SQLite-based address lookup cache."""

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .models import LookupResult, ProviderResult

logger = logging.getLogger(__name__)


def _normalize_address_key(address: str) -> str:
    """Normalize address string for cache key (lowercase, collapse whitespace, standard abbrevs)."""
    if not address:
        return ""
    key = address.lower().strip()
    key = re.sub(r"\s+", " ", key)
    # Standard abbreviations
    for full, abbr in [("street", "st"), ("avenue", "ave"), ("boulevard", "blvd"),
                        ("drive", "dr"), ("road", "rd"), ("lane", "ln"),
                        ("court", "ct"), ("place", "pl"), ("apartment", "apt"),
                        ("suite", "ste"), ("north", "n"), ("south", "s"),
                        ("east", "e"), ("west", "w")]:
        key = re.sub(rf"\b{full}\b", abbr, key)
    return key


class LookupCache:
    """SQLite cache for address lookup results."""

    def __init__(self, db_path: Path, ttl_days: int = 90):
        self.db_path = db_path
        self.ttl_days = ttl_days
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS lookup_cache (
                address_key TEXT PRIMARY KEY,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_expires ON lookup_cache(expires_at)
        """)
        self._conn.commit()

    def get(self, address: str) -> Optional[LookupResult]:
        """Get cached result for address, or None if not cached / expired."""
        key = _normalize_address_key(address)
        if not key:
            return None
        row = self._conn.execute(
            "SELECT result_json FROM lookup_cache WHERE address_key = ? AND expires_at > ?",
            (key, time.time()),
        ).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row[0])
            return self._dict_to_result(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def put(self, address: str, result: LookupResult):
        """Cache a lookup result."""
        key = _normalize_address_key(address)
        if not key:
            return
        now = time.time()
        expires = now + (self.ttl_days * 86400)
        result_json = json.dumps(result.to_dict())
        self._conn.execute(
            "INSERT OR REPLACE INTO lookup_cache (address_key, result_json, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (key, result_json, now, expires),
        )
        self._conn.commit()

    def invalidate(self, address: str):
        """Remove a cached result."""
        key = _normalize_address_key(address)
        self._conn.execute("DELETE FROM lookup_cache WHERE address_key = ?", (key,))
        self._conn.commit()

    def clear_expired(self):
        """Remove all expired entries."""
        deleted = self._conn.execute(
            "DELETE FROM lookup_cache WHERE expires_at <= ?", (time.time(),)
        ).rowcount
        self._conn.commit()
        if deleted:
            logger.info(f"Cache: cleared {deleted} expired entries")

    @property
    def size(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM lookup_cache").fetchone()
        return row[0] if row else 0

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @staticmethod
    def _dict_to_result(data: dict) -> LookupResult:
        """Reconstruct LookupResult from cached dict."""
        def _pr(d):
            if d is None:
                return None
            pr = ProviderResult(
                provider_name=d.get("provider_name", ""),
                canonical_id=d.get("canonical_id"),
                eia_id=d.get("eia_id"),
                utility_type=d.get("utility_type", ""),
                confidence=d.get("confidence", 0.0),
                match_method=d.get("match_method", "none"),
                is_deregulated=d.get("is_deregulated", False),
                deregulated_note=d.get("deregulated_note"),
                polygon_source=d.get("polygon_source"),
            )
            pr.catalog_id = d.get("catalog_id")
            pr.catalog_title = d.get("catalog_title")
            pr.id_match_score = d.get("id_match_score")
            pr.id_confident = d.get("id_confident")
            pr.needs_review = d.get("needs_review", False)
            pr.alternatives = d.get("alternatives", [])
            pr.phone = d.get("phone")
            pr.website = d.get("website")
            return pr
        return LookupResult(
            address=data.get("address", ""),
            lat=data.get("lat", 0.0),
            lon=data.get("lon", 0.0),
            geocode_confidence=data.get("geocode_confidence", 0.0),
            electric=_pr(data.get("electric")),
            gas=_pr(data.get("gas")),
            water=_pr(data.get("water")),
            sewer=_pr(data.get("sewer")),
            trash=_pr(data.get("trash")),
            internet=data.get("internet"),
            lookup_time_ms=data.get("lookup_time_ms", 0),
            timestamp=data.get("timestamp", ""),
        )
