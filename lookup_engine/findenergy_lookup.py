"""FindEnergy city-based provider lookup.

Uses cached FindEnergy city-to-provider mappings as a fallback
when State GIS + HIFLD + EIA all return nothing.

Key format in the JSON: "STATE:city:utility_type"
e.g. "TX:austin:electric" -> {"providers": [{"name": "Austin Energy"}]}
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class FindEnergyLookup:
    """Look up utility provider by state + city from FindEnergy cache."""

    def __init__(self, data_file: str = None):
        if data_file is None:
            data_file = str(
                Path(__file__).parent.parent / "data" / "findenergy" / "city_providers.json"
            )
        self._data: dict = {}
        self._load(data_file)

    def _load(self, data_file: str):
        path = Path(data_file)
        if not path.exists():
            logger.warning(f"FindEnergy cache not found: {data_file}")
            return
        try:
            with open(path) as f:
                self._data = json.load(f)
            count = sum(1 for k in self._data if k != "_metadata")
            logger.info(f"FindEnergy lookup: loaded {count} city entries")
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load FindEnergy cache: {e}")

    def lookup(self, state: str, city: str, utility_type: str) -> Optional[dict]:
        """
        Look up provider by state + city + utility_type.

        Returns:
            dict with keys: name, source, confidence
            None if no data
        """
        state = (state or "").upper()
        city = (city or "").strip().lower()
        utility_type = (utility_type or "").lower()

        if not state or not city or not utility_type:
            return None

        key = f"{state}:{city}:{utility_type}"
        entry = self._data.get(key)
        if not entry:
            return None

        providers = entry.get("providers", [])
        if not providers:
            return None

        # Return first provider (primary)
        primary = providers[0]
        return {
            "name": primary.get("name", ""),
            "source": "findenergy_city",
            "confidence": 0.65,
            "state": state,
        }

    @property
    def loaded(self) -> bool:
        return len(self._data) > 0
