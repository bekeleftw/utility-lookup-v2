"""Internet provider lookup via FCC BDC data in Postgres.

Queries the internet_providers table by Census block GEOID to return
all available ISPs with technology type, speeds, and latency info.
"""

import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

TECH_LABELS = {
    "10": "DSL",
    "40": "Cable",
    "50": "Fiber",
    "60": "Satellite (GSO)",
    "61": "Satellite (NGSO)",
    "70": "Fixed Wireless (Licensed)",
    "71": "Fixed Wireless (Unlicensed)",
    "72": "Fixed Wireless (CBRS)",
    "0": "Other",
}

TECH_PRIORITY = {
    "50": 0, "40": 1, "10": 2, "70": 3, "72": 4,
    "71": 5, "60": 6, "61": 7, "0": 8,
}


class InternetLookup:
    """Query FCC BDC data in Postgres for internet providers at a Census block."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.conn = None
        self._available = False
        try:
            self._get_conn()
            self._available = True
            logger.info("Internet lookup: Postgres connection established")
        except Exception as e:
            logger.warning(f"Internet lookup: Postgres unavailable: {e}")

    def _get_conn(self):
        import psycopg2
        if self.conn is None or self.conn.closed:
            self.conn = psycopg2.connect(self.db_url)
        return self.conn

    def lookup(self, block_geoid: str) -> Optional[Dict]:
        """
        Look up internet providers for a Census block.

        Returns dict with providers list, counts, fiber/cable flags, max speed.
        """
        if not block_geoid or not self._available:
            return None

        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT providers FROM internet_providers WHERE block_geoid = %s",
                (block_geoid,),
            )
            row = cursor.fetchone()
        except Exception as e:
            logger.debug(f"Internet lookup error for {block_geoid}: {e}")
            # Try to reconnect on next call
            self.conn = None
            return None

        if not row:
            return None

        # psycopg2 auto-deserializes JSONB columns; handle both cases
        if isinstance(row[0], list):
            raw_providers = row[0]
        elif isinstance(row[0], dict):
            raw_providers = [row[0]]
        else:
            raw_providers = json.loads(row[0])

        providers = []
        for p in raw_providers:
            tech_code = str(p.get("tech", "0"))
            providers.append({
                "name": p.get("name", ""),
                "technology": TECH_LABELS.get(tech_code, f"Unknown ({tech_code})"),
                "tech_code": tech_code,
                "max_down": p.get("down", 0),
                "max_up": p.get("up", 0),
                "low_latency": bool(p.get("low_lat", 0)),
            })

        # Sort: Fiber first, then Cable, then by download speed desc
        providers.sort(
            key=lambda p: (TECH_PRIORITY.get(p["tech_code"], 99), -p["max_down"])
        )

        return {
            "providers": providers,
            "provider_count": len(set(p["name"] for p in providers)),
            "has_fiber": any(p["tech_code"] == "50" for p in providers),
            "has_cable": any(p["tech_code"] == "40" for p in providers),
            "max_download_speed": max((p["max_down"] for p in providers), default=0),
            "source": "fcc_bdc",
            "confidence": 0.95,
        }

    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()

    @property
    def loaded(self) -> bool:
        return self._available
