"""State-level GIS API lookups for utility providers.

Queries state PUC/PSC ArcGIS REST APIs for authoritative point-in-polygon
utility territory data. These are more accurate than HIFLD national shapefiles
because state boundaries are maintained by the regulatory commissions themselves.

Usage:
    gis = StateGISLookup()
    result = gis.query(lat, lon, "TX", "electric")
    # Returns: {"name": "Oncor Electric Delivery", "source": "state_gis_TX", "confidence": 0.95, "state": "TX"}
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Default timeout for state GIS API requests (seconds)
_DEFAULT_TIMEOUT = 5

# Circuit breaker: disable endpoint after this many consecutive failures
_CIRCUIT_BREAKER_THRESHOLD = 3


class StateGISLookup:
    """Query state-level GIS APIs for utility provider at a point."""

    def __init__(self, endpoints_file: str = None):
        if endpoints_file is None:
            endpoints_file = str(Path(__file__).parent.parent / "data" / "state_gis_endpoints.json")
        with open(endpoints_file) as f:
            self.endpoints = json.load(f)

        # Circuit breaker state: {(state, utility_type): consecutive_failure_count}
        self._failures: dict = {}
        self._disabled: set = set()

        # Simple in-memory cache: {(lat_round, lon_round, state, utility_type): result}
        self._cache: dict = {}

    def query(self, lat: float, lon: float, state: str, utility_type: str) -> Optional[dict]:
        """
        Query the state GIS API for this state/utility_type.

        Returns:
            dict with keys: name, source, confidence, state
            None if no state GIS available or query fails
        """
        state = (state or "").upper()
        if not state:
            return None

        type_endpoints = self.endpoints.get(utility_type, {})
        state_config = type_endpoints.get(state)

        if not state_config:
            return None  # No state GIS for this state/type combo

        # Circuit breaker check
        key = (state, utility_type)
        if key in self._disabled:
            return None

        # Cache check (round to ~100m precision)
        cache_key = (round(lat, 3), round(lon, 3), state, utility_type)
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = None
        try:
            result = self._dispatch_query(lat, lon, state, state_config, utility_type)
        except Exception as e:
            logger.warning(f"State GIS query failed for {state}/{utility_type}: {e}")
            self._record_failure(key)
            return None

        # Cache the result (even None, to avoid re-querying)
        self._cache[cache_key] = result

        if result:
            self._failures.pop(key, None)  # Reset failure count on success
            logger.debug(f"State GIS hit: {state}/{utility_type} → {result.get('name')}")

        return result

    def _dispatch_query(self, lat: float, lon: float, state: str,
                        config: dict, utility_type: str) -> Optional[dict]:
        """Route to the appropriate query method based on config type."""
        config_type = config.get("type", "arcgis")

        if config_type == "single_utility":
            return {
                "name": config["default_name"],
                "source": f"state_gis_{state.lower()}",
                "confidence": config.get("confidence", 0.95),
                "state": state,
            }

        if config_type == "coordinate_mapping":
            return self._query_coordinate_mapping(lat, lon, state, config)

        # Per-endpoint timeout override
        timeout = config.get("timeout", _DEFAULT_TIMEOUT)

        # Multi-layer endpoints (TX electric, NC electric, MN electric, WI electric)
        if "layers" in config:
            return self._query_multi_layer(lat, lon, state, config, timeout=timeout)

        # Standard single-URL ArcGIS endpoint
        url = config.get("url")
        if not url:
            return None

        name = self._query_arcgis(
            url=url,
            lat=lat,
            lon=lon,
            name_field=config["name_field"],
            out_fields=config.get("out_fields", "*"),
            filter_field=config.get("filter_field"),
            filter_value=config.get("filter_value"),
            timeout=timeout,
        )

        if name:
            return {
                "name": name,
                "source": f"state_gis_{state.lower()}",
                "confidence": config.get("confidence", 0.90),
                "state": state,
            }

        # Try fallback URL if primary returned nothing
        fallback_url = config.get("fallback_url")
        if fallback_url:
            fallback_name_field = config.get("fallback_name_field", config["name_field"])
            name = self._query_arcgis(
                url=fallback_url,
                lat=lat,
                lon=lon,
                name_field=fallback_name_field,
                out_fields=config.get("out_fields", "*"),
                timeout=timeout,
            )
            if name:
                return {
                    "name": name,
                    "source": f"state_gis_{state.lower()}_fallback",
                    "confidence": config.get("fallback_confidence", config.get("confidence", 0.85)),
                    "state": state,
                }

        return None

    def _query_multi_layer(self, lat: float, lon: float, state: str,
                           config: dict, timeout: int = _DEFAULT_TIMEOUT) -> Optional[dict]:
        """Query multiple ArcGIS layers (e.g., IOU + Municipal + Coop)."""
        name_field = config["name_field"]
        out_fields = config.get("out_fields", "*")

        for layer in config["layers"]:
            if isinstance(layer, dict):
                url = layer["url"]
            else:
                # Layer ID template: url contains {layer}
                url = config["url"].replace("{layer}", str(layer))

            name = self._query_arcgis(
                url=url,
                lat=lat,
                lon=lon,
                name_field=name_field,
                out_fields=out_fields,
                timeout=timeout,
            )
            if name:
                return {
                    "name": name,
                    "source": f"state_gis_{state.lower()}",
                    "confidence": config.get("confidence", 0.92),
                    "state": state,
                }

        return None

    def _query_coordinate_mapping(self, lat: float, lon: float, state: str,
                                  config: dict) -> Optional[dict]:
        """Handle coordinate-based mappings (e.g., Hawaii islands)."""
        mappings = config.get("mappings", {})
        for region_name, region in mappings.items():
            lon_range = region.get("lon_range", [])
            if len(lon_range) != 2:
                continue
            if lon_range[0] <= lon <= lon_range[1]:
                lat_min = region.get("lat_min")
                if lat_min and lat < lat_min:
                    continue
                return {
                    "name": region["name"],
                    "source": f"state_gis_{state.lower()}",
                    "confidence": config.get("confidence", 0.95),
                    "state": state,
                }
        return None

    def _query_arcgis(self, url: str, lat: float, lon: float,
                      name_field: str, out_fields: str = "*",
                      filter_field: str = None, filter_value=None,
                      timeout: int = _DEFAULT_TIMEOUT) -> Optional[str]:
        """Execute an ArcGIS REST API point-in-polygon query."""
        params = {
            "where": "1=1",
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
        }

        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        features = data.get("features", [])
        if not features:
            return None

        # Apply filter if specified (e.g., Oregon gas: NG_or_Electric must contain "gas")
        if filter_field and filter_value is not None:
            if isinstance(filter_value, str):
                features = [
                    f for f in features
                    if filter_value.lower() in str(f.get("attributes", {}).get(filter_field, "")).lower()
                ]
            else:
                features = [
                    f for f in features
                    if f.get("attributes", {}).get(filter_field) == filter_value
                ]
            if not features:
                return None

        attributes = features[0].get("attributes", {})
        name = attributes.get(name_field)
        if name and isinstance(name, str):
            return name.strip()
        return None

    def _record_failure(self, key: tuple):
        """Track consecutive failures and disable endpoint if threshold reached."""
        count = self._failures.get(key, 0) + 1
        self._failures[key] = count
        if count >= _CIRCUIT_BREAKER_THRESHOLD:
            self._disabled.add(key)
            logger.warning(
                f"State GIS circuit breaker: disabled {key[0]}/{key[1]} "
                f"after {count} consecutive failures"
            )

    def has_state_source(self, state: str, utility_type: str) -> bool:
        """Check if a state GIS source exists for this state/type."""
        state = (state or "").upper()
        return state in self.endpoints.get(utility_type, {})

    def clear_cache(self):
        """Clear the in-memory result cache."""
        self._cache.clear()

    def reset_circuit_breakers(self):
        """Reset all circuit breakers (e.g., for a new batch run)."""
        self._failures.clear()
        self._disabled.clear()
