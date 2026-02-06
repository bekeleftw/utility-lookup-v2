"""Georgia EMC (Electric Membership Corporation) county-level lookup.

Georgia has 41 EMCs serving 4.7 million members across 65% of the state.
Uses county-to-EMC mapping since GA has no state GIS electric endpoint.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class GeorgiaEMCLookup:
    """County-based EMC lookup for Georgia electric."""

    def __init__(self, data_file: str = None):
        if data_file is None:
            data_file = str(Path(__file__).parent.parent / "data" / "georgia_emcs.json")
        self._data: dict = {}
        self._load(data_file)

    def _load(self, data_file: str):
        path = Path(data_file)
        if not path.exists():
            logger.warning(f"Georgia EMC data not found: {data_file}")
            return
        try:
            with open(path) as f:
                self._data = json.load(f)
            emcs = self._data.get("emcs", {})
            counties = self._data.get("county_to_emc", {})
            logger.info(f"Georgia EMC: {len(emcs)} EMCs, {len(counties)} counties")
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load Georgia EMC data: {e}")

    def lookup(self, county: str) -> Optional[dict]:
        """
        Look up EMC by county name.

        Args:
            county: County name (e.g., "Fulton", "Fulton County")

        Returns:
            dict with keys: name, source, confidence, alternatives
            None if not GA or no data
        """
        if not county or not self._data:
            return None

        # Normalize county name
        county = county.replace(" County", "").replace(" county", "").strip()

        county_to_emc = self._data.get("county_to_emc", {})
        emcs = county_to_emc.get(county, [])

        if not emcs:
            return None

        emc_name = emcs[0]
        emc_info = self._data.get("emcs", {}).get(emc_name, {})

        if len(emcs) == 1:
            confidence = 0.87  # Single EMC for county = high confidence
        else:
            confidence = 0.72  # Multiple EMCs = lower confidence

        return {
            "name": emc_name,
            "source": "georgia_emc",
            "confidence": confidence,
            "phone": emc_info.get("phone"),
            "website": emc_info.get("website"),
            "alternatives": emcs[1:] if len(emcs) > 1 else [],
        }

    def get_all_for_county(self, county: str) -> List[dict]:
        """Return all EMCs serving a county with metadata."""
        if not county or not self._data:
            return []

        county = county.replace(" County", "").replace(" county", "").strip()
        county_to_emc = self._data.get("county_to_emc", {})
        emcs = county_to_emc.get(county, [])

        results = []
        for i, emc_name in enumerate(emcs):
            emc_info = self._data.get("emcs", {}).get(emc_name, {})
            conf = 0.87 if len(emcs) == 1 else (0.72 - i * 0.05)
            results.append({
                "name": emc_name,
                "source": "georgia_emc",
                "confidence": max(conf, 0.50),
                "phone": emc_info.get("phone"),
                "website": emc_info.get("website"),
            })
        return results

    @property
    def loaded(self) -> bool:
        return len(self._data.get("county_to_emc", {})) > 0
