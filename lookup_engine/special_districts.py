"""Special districts water lookup.

ZIP-to-water-district mappings for AZ, CA, CO, FL, WA.
Pre-joined from ZIP-to-district-ID + district detail files.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SpecialDistrictsLookup:
    """ZIP-based water district lookup for special districts."""

    def __init__(self, data_file: str = None):
        if data_file is None:
            data_file = str(Path(__file__).parent.parent / "data" / "special_districts_water.json")
        self._data: dict = {}
        self._load(data_file)

    def _load(self, data_file: str):
        path = Path(data_file)
        if not path.exists():
            logger.warning(f"Special districts data not found: {data_file}")
            return
        try:
            with open(path) as f:
                self._data = json.load(f)
            logger.info(f"Special districts: {len(self._data)} ZIP entries")
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load special districts: {e}")

    def lookup(self, zip_code: str) -> Optional[dict]:
        """
        Look up water district by ZIP code.

        Returns:
            dict with keys: name, source, confidence, state
            None if no data
        """
        zip_code = (zip_code or "").strip()[:5]
        if not zip_code:
            return None

        entry = self._data.get(zip_code)
        if not entry:
            return None

        return {
            "name": entry.get("name", ""),
            "source": "special_district_water",
            "confidence": 0.82,
            "state": entry.get("state", ""),
            "district_type": entry.get("type", ""),
        }

    @property
    def loaded(self) -> bool:
        return len(self._data) > 0
