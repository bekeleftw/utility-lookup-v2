"""PostGIS-backed spatial index for point-in-polygon utility territory lookups.

Drop-in replacement for SpatialIndex that queries PostGIS instead of in-memory
geopandas DataFrames. Provides instant startup since no shapefiles need loading.
"""

import logging
import os
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# SQL template for point-in-polygon query, sorted by area ascending (smallest first)
_QUERY_ELECTRIC = """
    SELECT name, state, type, holding_co, cntrl_area, customers, eia_id, area_km2
    FROM electric_territories
    WHERE ST_Contains(geometry, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
    ORDER BY area_km2 ASC
"""

_QUERY_GAS = """
    SELECT name, state, type, holding_co, customers, eia_id, area_km2
    FROM gas_territories
    WHERE ST_Contains(geometry, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
    ORDER BY area_km2 ASC
"""

_QUERY_WATER = """
    SELECT name, state, pwsid, population_served, area_km2
    FROM water_territories
    WHERE ST_Contains(geometry, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
    ORDER BY area_km2 ASC
"""


class PostGISSpatialIndex:
    """PostGIS-backed spatial index. Same interface as SpatialIndex."""

    def __init__(self, db_url: str):
        self._db_url = db_url
        self._conn = None
        self._available = False
        self._table_counts = {"electric": 0, "gas": 0, "water": 0}
        self._connect()

    def _connect(self):
        try:
            self._conn = psycopg2.connect(self._db_url)
            self._conn.autocommit = True
            # Verify tables exist and get counts
            with self._conn.cursor() as cur:
                for table, utype in [
                    ("electric_territories", "electric"),
                    ("gas_territories", "gas"),
                    ("water_territories", "water"),
                ]:
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM {table}")
                        self._table_counts[utype] = cur.fetchone()[0]
                    except psycopg2.Error:
                        self._conn.rollback()
                        self._table_counts[utype] = 0

            total = sum(self._table_counts.values())
            if total > 0:
                self._available = True
                logger.info(
                    f"PostGIS spatial index: electric={self._table_counts['electric']}, "
                    f"gas={self._table_counts['gas']}, water={self._table_counts['water']}"
                )
            else:
                logger.warning("PostGIS spatial tables are empty")
        except Exception as e:
            logger.warning(f"PostGIS spatial index unavailable: {e}")
            self._available = False

    def _ensure_connection(self):
        """Reconnect if connection was lost."""
        if self._conn is None or self._conn.closed:
            self._connect()

    def query_point(self, lat: float, lon: float, utility_type: str) -> list[dict]:
        """
        Find all polygons containing the point, sorted by area ascending.
        Returns same format as SpatialIndex.query_point().
        """
        if not self._available:
            return []

        self._ensure_connection()

        query_map = {
            "electric": _QUERY_ELECTRIC,
            "gas": _QUERY_GAS,
            "water": _QUERY_WATER,
        }

        query = query_map.get(utility_type)
        if not query:
            return []

        try:
            with self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(query, (lon, lat))  # PostGIS uses (x=lon, y=lat)
                rows = cur.fetchall()
        except psycopg2.Error as e:
            logger.warning(f"PostGIS query error: {e}")
            self._conn = None  # Force reconnect next time
            return []

        results = []
        for row in rows:
            attrs = self._row_to_attrs(dict(row), utility_type)
            results.append(attrs)

        return results

    def _row_to_attrs(self, row: dict, utility_type: str) -> dict:
        """Convert a PostGIS row to the same dict format as SpatialIndex._extract_attributes."""
        base = {"area_km2": row.get("area_km2", 0)}

        if utility_type == "electric":
            base.update({
                "name": row.get("name", ""),
                "state": row.get("state", ""),
                "type": row.get("type", ""),
                "holding_co": row.get("holding_co", ""),
                "cntrl_area": row.get("cntrl_area", ""),
                "customers": row.get("customers", 0),
                "eia_id": row.get("eia_id", ""),
                "source": "HIFLD Electric Retail Service Territories",
            })
        elif utility_type == "gas":
            base.update({
                "name": row.get("name", ""),
                "state": row.get("state", ""),
                "type": row.get("type", ""),
                "holding_co": row.get("holding_co", ""),
                "customers": row.get("customers", 0),
                "eia_id": row.get("eia_id", ""),
                "source": "HIFLD Natural Gas Service Territories",
            })
        elif utility_type == "water":
            base.update({
                "name": row.get("name", ""),
                "state": row.get("state", ""),
                "type": "WATER",
                "pwsid": row.get("pwsid", ""),
                "population_served": row.get("population_served", 0),
                "source": "EPA CWS Boundaries",
            })

        return base

    @property
    def is_loaded(self) -> bool:
        return self._available

    @property
    def layer_counts(self) -> dict:
        return self._table_counts.copy()

    def load_all(self):
        """No-op — PostGIS tables are always available. Matches SpatialIndex interface."""
        pass
