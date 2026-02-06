"""County-based gas utility lookup.

Uses gas_county_lookups.json for IL, PA, NY, TX county-to-gas-utility mappings.
Also supports city-level overrides (e.g., Chicago -> Peoples Gas, Evanston -> North Shore Gas).
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CountyGasLookup:
    """County-based gas utility lookup for states with detailed mappings."""

    def __init__(self, data_file: str = None):
        if data_file is None:
            data_file = str(Path(__file__).parent.parent / "data" / "gas_county_lookups.json")
        self._data: dict = {}
        self._states: set = set()
        self._load(data_file)

    def _load(self, data_file: str):
        path = Path(data_file)
        if not path.exists():
            logger.warning(f"County gas data not found: {data_file}")
            return
        try:
            with open(path) as f:
                raw = json.load(f)
            # Extract state entries (skip _metadata)
            for key, val in raw.items():
                if key.startswith("_"):
                    continue
                if isinstance(val, dict):
                    self._data[key.upper()] = val
                    self._states.add(key.upper())
            total_counties = sum(
                len(v.get("counties", {})) for v in self._data.values()
            )
            total_cities = sum(
                len(v.get("cities", {})) for v in self._data.values()
            )
            logger.info(
                f"County gas lookup: {len(self._states)} states, "
                f"{total_counties} counties, {total_cities} city overrides"
            )
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load county gas data: {e}")

    def lookup(self, state: str, county: str = "", city: str = "") -> Optional[dict]:
        """
        Look up gas utility by state + county or city.

        City overrides take priority over county mappings.

        Returns:
            dict with keys: name, source, confidence
            None if no data
        """
        state = (state or "").upper()
        if state not in self._data:
            return None

        state_data = self._data[state]

        # City override (highest priority within this source)
        if city:
            city_clean = city.strip()
            cities = state_data.get("cities", {})
            entry = cities.get(city_clean)
            if not entry:
                # Try case-insensitive
                for k, v in cities.items():
                    if k.lower() == city_clean.lower():
                        entry = v
                        break
            if entry:
                return {
                    "name": entry.get("utility", ""),
                    "source": f"county_gas_{state.lower()}_city",
                    "confidence": 0.88,
                    "state": state,
                }

        # County mapping
        if county:
            county_clean = county.replace(" County", "").replace(" county", "").strip()
            counties = state_data.get("counties", {})
            entry = counties.get(county_clean)
            if not entry:
                # Try case-insensitive
                for k, v in counties.items():
                    if k.lower() == county_clean.lower():
                        entry = v
                        break
            if entry:
                return {
                    "name": entry.get("utility", ""),
                    "source": f"county_gas_{state.lower()}",
                    "confidence": 0.85,
                    "state": state,
                }

        # State default (lowest priority within this source)
        default = state_data.get("_default")
        if default:
            return {
                "name": default,
                "source": f"county_gas_{state.lower()}_default",
                "confidence": 0.60,
                "state": state,
            }

        return None

    def has_state(self, state: str) -> bool:
        return (state or "").upper() in self._states

    @property
    def loaded(self) -> bool:
        return len(self._data) > 0
