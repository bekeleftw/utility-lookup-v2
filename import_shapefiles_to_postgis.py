#!/usr/bin/env python3
"""
Import shapefiles into PostGIS tables for fast spatial queries.

Usage:
    python import_shapefiles_to_postgis.py --db-url postgresql://user:pass@host:port/dbname

This creates three tables:
    - electric_territories (from HIFLD Electric Retail Service Territories)
    - gas_territories (from EIA Natural Gas Service Territories)
    - water_territories (from EPA CWS Boundaries)

Each table has a GIST spatial index on the geometry column for fast ST_Contains queries.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import geopandas as gpd
from shapely.validation import make_valid
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent

LAYERS = {
    "electric_territories": {
        "path": ROOT / "electric-retail-service-territories-shapefile" / "Electric_Retail_Service_Territories.shp",
        "columns": {
            "NAME": "name",
            "STATE": "state",
            "TYPE": "type",
            "HOLDING_CO": "holding_co",
            "CNTRL_AREA": "cntrl_area",
            "CUSTOMERS": "customers",
            "ID": "eia_id",
        },
    },
    "gas_territories": {
        "path": ROOT / "240245-V1" / "gas_shp" / "NG_Service_Terr.shp",
        "columns": {
            "NAME": "name",
            "STATE": "state",
            "TYPE": "type",
            "HOLDINGCO": "holding_co",
            "TOTAL_CUST": "customers",
            "SVCTERID": "eia_id",
        },
    },
    "water_territories": {
        "path": ROOT / "CWS_Boundaries_Latest" / "CWS_2_1.gpkg",
        "columns": {
            "PWS_Name": "name",
            "Primacy_Agency": "state",
            "PWSID": "pwsid",
            "Population_Served_Count": "population_served",
        },
    },
}


def import_layer(engine, table_name: str, layer_config: dict):
    """Import a single shapefile/gpkg into a PostGIS table."""
    path = layer_config["path"]
    col_map = layer_config["columns"]

    if not path.exists():
        logger.error(f"File not found: {path}")
        return

    logger.info(f"Reading {path}...")
    t0 = time.time()
    gdf = gpd.read_file(path)
    logger.info(f"  Read {len(gdf)} records in {time.time() - t0:.1f}s")

    # Fix invalid geometries
    invalid = ~gdf.geometry.is_valid
    if invalid.any():
        logger.info(f"  Fixing {invalid.sum()} invalid geometries")
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].apply(make_valid)

    # Reproject to WGS84 (EPSG:4326)
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        logger.info(f"  Reprojecting from {gdf.crs} to EPSG:4326")
        gdf = gdf.to_crs(epsg=4326)

    # Compute area in km² (using equal-area projection)
    gdf_area = gdf.to_crs(epsg=3083)
    gdf["area_km2"] = gdf_area.geometry.area / 1e6

    # Select and rename columns
    keep_cols = ["geometry", "area_km2"]
    for src, dst in col_map.items():
        if src in gdf.columns:
            gdf[dst] = gdf[src]
            keep_cols.append(dst)
        else:
            logger.warning(f"  Column {src} not found in {path}")
            gdf[dst] = None
            keep_cols.append(dst)

    gdf = gdf[keep_cols]

    # Write to PostGIS
    logger.info(f"  Writing {len(gdf)} records to {table_name}...")
    t0 = time.time()
    gdf.to_postgis(
        table_name,
        engine,
        if_exists="replace",
        index=False,
    )
    logger.info(f"  Written in {time.time() - t0:.1f}s")

    # Create spatial index
    logger.info(f"  Creating spatial index on {table_name}...")
    with engine.connect() as conn:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_geom ON {table_name} USING GIST (geometry)"))
        conn.execute(text(f"ANALYZE {table_name}"))
        conn.commit()
    logger.info(f"  Done with {table_name}")


def main():
    parser = argparse.ArgumentParser(description="Import shapefiles into PostGIS")
    parser.add_argument("--db-url", required=True, help="PostGIS database URL")
    parser.add_argument("--layer", choices=list(LAYERS.keys()), help="Import only this layer")
    args = parser.parse_args()

    engine = create_engine(args.db_url)

    # Verify PostGIS is available
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.commit()
        result = conn.execute(text("SELECT PostGIS_Version()"))
        version = result.scalar()
        logger.info(f"PostGIS version: {version}")

    if args.layer:
        import_layer(engine, args.layer, LAYERS[args.layer])
    else:
        for table_name, config in LAYERS.items():
            import_layer(engine, table_name, config)

    logger.info("All imports complete!")


if __name__ == "__main__":
    main()
