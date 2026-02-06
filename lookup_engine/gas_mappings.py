"""Gas utility ZIP-prefix mapping lookup.

Uses state-specific JSON mapping files to resolve gas LDC by ZIP code.
This is particularly important for Texas where HIFLD gas boundaries are
overgeneralized and the ZIP-prefix mapping correctly distinguishes
CenterPoint (Houston) vs Texas Gas Service (Austin) vs Atmos (DFW).

Priority in the engine:
    State GIS API → Gas ZIP mapping → HIFLD shapefile
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# States with gas mapping JSON files
_SUPPORTED_STATES = {
    "AZ": "arizona",
    "CA": "california",
    "GA": "georgia",
    "IL": "illinois",
    "OH": "ohio",
    "TX": "texas",
}


class GasZIPMappingLookup:
    """Look up gas utility by ZIP code using state-specific mapping files."""

    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "data" / "gas_mappings")
        self._data_dir = Path(data_dir)
        self._cache: dict = {}  # state -> loaded mapping

    def query(self, zip_code: str, state: str) -> Optional[dict]:
        """
        Look up gas utility by ZIP code.

        Returns:
            dict with keys: name, source, confidence, state, phone, website
            None if no mapping available
        """
        state = (state or "").upper()
        zip_code = (zip_code or "").strip()[:5]

        if not state or not zip_code or state not in _SUPPORTED_STATES:
            return None

        mapping = self._load_mapping(state)
        if not mapping:
            return None

        utilities = mapping.get("utilities", {})
        zip_prefix = zip_code[:3] if len(zip_code) >= 3 else None

        # Priority 1: 5-digit ZIP overrides (highest confidence)
        if zip_code in mapping.get("zip_overrides", {}):
            utility_key = mapping["zip_overrides"][zip_code]
            utility = utilities.get(utility_key, {})
            if utility:
                return {
                    "name": utility.get("name", utility_key),
                    "source": f"gas_zip_mapping_{state.lower()}",
                    "confidence": 0.93,
                    "state": state,
                    "phone": utility.get("phone"),
                    "website": utility.get("website"),
                }

        # Priority 2: Check ambiguous ZIPs (boundary areas)
        if zip_code in mapping.get("ambiguous_zips", {}):
            ambiguous = mapping["ambiguous_zips"][zip_code]
            providers = ambiguous.get("providers", [])
            if providers:
                primary_key = providers[0]
                utility = utilities.get(primary_key, {})
                if utility:
                    return {
                        "name": utility.get("name", primary_key),
                        "source": f"gas_zip_mapping_{state.lower()}_ambiguous",
                        "confidence": 0.80,
                        "state": state,
                        "phone": utility.get("phone"),
                        "website": utility.get("website"),
                    }

        # Priority 3: 3-digit ZIP prefix mapping
        if zip_prefix and zip_prefix in mapping.get("zip_to_utility", {}):
            utility_key = mapping["zip_to_utility"][zip_prefix]
            utility = utilities.get(utility_key, {})
            if utility:
                return {
                    "name": utility.get("name", utility_key),
                    "source": f"gas_zip_mapping_{state.lower()}",
                    "confidence": 0.88,
                    "state": state,
                    "phone": utility.get("phone"),
                    "website": utility.get("website"),
                }

        return None

    def has_state(self, state: str) -> bool:
        """Check if a gas mapping exists for this state."""
        return (state or "").upper() in _SUPPORTED_STATES

    def _load_mapping(self, state: str) -> dict:
        """Load and cache a state gas mapping file."""
        if state in self._cache:
            return self._cache[state]

        filename = _SUPPORTED_STATES.get(state)
        if not filename:
            return {}

        filepath = self._data_dir / f"{filename}.json"
        if not filepath.exists():
            logger.warning(f"Gas mapping file not found: {filepath}")
            self._cache[state] = {}
            return {}

        try:
            with open(filepath) as f:
                data = json.load(f)
            self._cache[state] = data
            return data
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load gas mapping {filepath}: {e}")
            self._cache[state] = {}
            return {}
