"""Configuration for the lookup engine."""

from dataclasses import dataclass, field
from pathlib import Path


_ROOT = Path(__file__).parent.parent


@dataclass
class Config:
    # Shapefile paths
    electric_shp: Path = _ROOT / "electric-retail-service-territories-shapefile" / "Electric_Retail_Service_Territories.shp"
    gas_shp: Path = _ROOT / "240245-V1" / "gas_shp" / "NG_Service_Terr.shp"
    water_gpkg: Path = _ROOT / "CWS_Boundaries_Latest" / "CWS_2_1.gpkg"

    # Data files
    canonical_file: Path = _ROOT / "data" / "canonical_providers.json"
    reps_file: Path = _ROOT / "data" / "deregulated_reps.json"

    # Cache
    cache_db: Path = _ROOT / "data" / "lookup_cache.db"
    cache_ttl_days: int = 90

    # Geocoder
    geocoder_type: str = "census"  # "census", "google", or "chained" (Census + Google fallback)
    google_api_key: str = ""

    # Scoring thresholds
    max_confidence: float = 0.98

    # Spatial
    target_crs: str = "EPSG:4326"  # WGS84 for lat/lon queries

    # ERCOT TDU names in the electric shapefile (for deregulated detection)
    ercot_tdu_names: list = field(default_factory=lambda: [
        "ONCOR ELECTRIC DELIVERY COMPANY LLC",
        "CENTERPOINT ENERGY",
        "AEP TEXAS CENTRAL COMPANY",
        "AEP TEXAS NORTH COMPANY",
        "TEXAS-NEW MEXICO POWER CO",
        "CITY OF LUBBOCK - (TX)",
    ])

    # Lubbock P&L is municipal but deregulated since 2024
    lubbock_deregulated: bool = True
