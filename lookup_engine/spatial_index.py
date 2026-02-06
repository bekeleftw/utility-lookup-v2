"""Spatial index for point-in-polygon utility territory lookups."""

import logging
import time
from pathlib import Path
from typing import Optional

import geopandas as gpd
from shapely.geometry import Point
from shapely.validation import make_valid

from .config import Config

logger = logging.getLogger(__name__)


class SpatialIndex:
    """Loads shapefiles into memory and provides fast point-in-polygon queries."""

    def __init__(self, config: Config):
        self.config = config
        self._electric: Optional[gpd.GeoDataFrame] = None
        self._gas: Optional[gpd.GeoDataFrame] = None
        self._water: Optional[gpd.GeoDataFrame] = None

    def load_all(self):
        """Load all shapefiles. Call once at startup."""
        t0 = time.time()
        self._load_electric()
        self._load_gas()
        self._load_water()
        elapsed = time.time() - t0
        logger.info(f"All spatial layers loaded in {elapsed:.1f}s")

    def _load_layer(self, path, label):
        """Generic layer loader with geometry validation and reprojection."""
        if not path.exists():
            logger.warning(f"{label} file not found: {path}")
            return None
        t0 = time.time()
        gdf = gpd.read_file(path)
        # Fix invalid geometries
        invalid = ~gdf.geometry.is_valid
        if invalid.any():
            logger.info(f"{label}: fixing {invalid.sum()} invalid geometries")
            gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].apply(make_valid)
        # Reproject to WGS84
        if gdf.crs and str(gdf.crs) != self.config.target_crs:
            gdf = gdf.to_crs(self.config.target_crs)
        # Pre-compute area in km²
        gdf_area = gdf.to_crs(epsg=3083)
        gdf["_area_km2"] = gdf_area.geometry.area / 1e6
        _ = gdf.sindex
        elapsed = time.time() - t0
        logger.info(f"{label}: {len(gdf)} records loaded in {elapsed:.1f}s")
        return gdf

    def _load_electric(self):
        self._electric = self._load_layer(self.config.electric_shp, "Electric")

    def _load_gas(self):
        self._gas = self._load_layer(self.config.gas_shp, "Gas")

    def _load_water(self):
        try:
            self._water = self._load_layer(self.config.water_gpkg, "Water")
        except Exception as e:
            logger.error(f"Failed to load water layer: {e}")
            self._water = None

    def query_point(self, lat: float, lon: float, utility_type: str) -> list[dict]:
        """
        Find all polygons containing the point, sorted by area ascending
        (smallest polygon first = most specific match).

        Args:
            lat: Latitude (WGS84)
            lon: Longitude (WGS84)
            utility_type: "electric", "gas", or "water"

        Returns:
            List of dicts with polygon attributes, sorted smallest-first.
        """
        gdf = self._get_layer(utility_type)
        if gdf is None:
            return []

        point = Point(lon, lat)  # shapely uses (x=lon, y=lat)

        # Use spatial index for fast candidate lookup
        candidates_idx = list(gdf.sindex.intersection(point.bounds))
        if not candidates_idx:
            return []

        # Filter to actual containment
        results = []
        for idx in candidates_idx:
            row = gdf.iloc[idx]
            try:
                if row.geometry and row.geometry.contains(point):
                    attrs = self._extract_attributes(row, utility_type)
                    results.append(attrs)
            except Exception as e:
                logger.warning(f"Skipping geometry {idx}: {e}")
                continue

        # Sort by area ascending (smallest polygon = most specific)
        results.sort(key=lambda r: r.get("area_km2", float("inf")))
        return results

    def _get_layer(self, utility_type: str) -> Optional[gpd.GeoDataFrame]:
        if utility_type == "electric":
            return self._electric
        elif utility_type == "gas":
            return self._gas
        elif utility_type == "water":
            return self._water
        return None

    def _extract_attributes(self, row, utility_type: str) -> dict:
        """Extract relevant attributes from a GeoDataFrame row."""
        base = {
            "area_km2": row.get("_area_km2", 0),
        }

        if utility_type == "electric":
            base.update({
                "name": row.get("NAME", ""),
                "state": row.get("STATE", ""),
                "type": row.get("TYPE", ""),
                "holding_co": row.get("HOLDING_CO", ""),
                "cntrl_area": row.get("CNTRL_AREA", ""),
                "customers": row.get("CUSTOMERS", 0),
                "eia_id": row.get("ID", ""),
                "source": "HIFLD Electric Retail Service Territories",
            })
        elif utility_type == "gas":
            base.update({
                "name": row.get("NAME", ""),
                "state": row.get("STATE", ""),
                "type": row.get("TYPE", ""),
                "holding_co": row.get("HOLDINGCO", ""),
                "customers": row.get("TOTAL_CUST", 0),
                "eia_id": row.get("SVCTERID", ""),
                "source": "HIFLD Natural Gas Service Territories",
            })
        elif utility_type == "water":
            base.update({
                "name": row.get("PWS_Name", ""),
                "state": row.get("Primacy_Agency", ""),
                "type": "WATER",
                "pwsid": row.get("PWSID", ""),
                "population_served": row.get("Population_Served_Count", 0),
                "source": "EPA CWS Boundaries",
            })

        return base

    @property
    def is_loaded(self) -> bool:
        return self._electric is not None

    @property
    def layer_counts(self) -> dict:
        return {
            "electric": len(self._electric) if self._electric is not None else 0,
            "gas": len(self._gas) if self._gas is not None else 0,
            "water": len(self._water) if self._water is not None else 0,
        }
