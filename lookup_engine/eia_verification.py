"""EIA ZIP-to-utility verification layer for electric providers.

Uses EIA Form 861 data to verify/boost confidence of electric utility results.
This is NOT a primary source — it's a verification layer that can:
1. Confirm a state GIS or HIFLD result (boost confidence)
2. Flag potential mismatches (lower confidence)
3. Provide a fallback name when no other source has data

The EIA data covers most IOUs (Investor-Owned Utilities) across the US.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class EIAVerification:
    """Verify electric utility results against EIA ZIP-to-utility data."""

    def __init__(self, data_file: str = None):
        if data_file is None:
            data_file = str(Path(__file__).parent.parent / "data" / "eia_zip_utility_lookup.json")
        self._data: dict = {}
        self._load(data_file)

    def _load(self, data_file: str):
        path = Path(data_file)
        if not path.exists():
            logger.warning(f"EIA ZIP lookup file not found: {data_file}")
            return
        try:
            with open(path) as f:
                self._data = json.load(f)
            logger.info(f"EIA verification: loaded {len(self._data)} ZIP entries")
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load EIA data: {e}")

    def get_utilities(self, zip_code: str) -> list:
        """
        Get EIA utilities for a ZIP code.

        Returns:
            List of dicts with keys: name, eiaid, state, ownership
            Empty list if no data
        """
        zip_code = (zip_code or "").strip()[:5]
        utilities = self._data.get(zip_code, [])
        if not utilities:
            return []

        # Dedupe by name
        seen = set()
        unique = []
        for u in utilities:
            name = u.get("name", "")
            if name and name not in seen:
                seen.add(name)
                unique.append(u)
        return unique

    def verify(self, zip_code: str, provider_name: str) -> dict:
        """
        Verify a provider name against EIA data for a ZIP code.

        Returns:
            {
                "verified": bool,
                "eia_name": str or None,
                "confidence_adjustment": float (-0.1 to +0.1),
                "eia_id": int or None,
            }
        """
        utilities = self.get_utilities(zip_code)
        if not utilities:
            return {
                "verified": False,
                "eia_name": None,
                "confidence_adjustment": 0.0,
                "eia_id": None,
            }

        provider_upper = (provider_name or "").upper().strip()
        if not provider_upper:
            # No provider to verify — return EIA primary as suggestion
            primary = utilities[0]
            return {
                "verified": False,
                "eia_name": primary.get("name"),
                "confidence_adjustment": 0.0,
                "eia_id": primary.get("eiaid"),
            }

        # Check for match — use stop-word filtering to avoid false positives
        # from generic utility words like "ELECTRIC", "POWER", "ENERGY"
        _EIA_STOP_WORDS = {
            "ELECTRIC", "POWER", "ENERGY", "COMPANY", "CORPORATION", "CORP",
            "INC", "LLC", "CO", "OF", "THE", "AND", "UTILITY", "UTILITIES",
            "SERVICE", "SERVICES", "LIGHT", "GAS", "COOPERATIVE", "COOP",
            "ASSOCIATION", "AUTHORITY", "DEPARTMENT", "DEPT", "COMMISSION",
            "BOARD", "DISTRICT", "MUNICIPAL", "CITY", "COUNTY", "STATE",
            "PUBLIC", "RURAL",
        }
        provider_words = set(provider_upper.replace(",", "").replace(".", "").split())
        provider_meaningful = provider_words - _EIA_STOP_WORDS

        for eia_util in utilities:
            eia_name = (eia_util.get("name") or "").upper().strip()
            eia_words = set(eia_name.replace(",", "").replace(".", "").split())

            # Exact match
            if provider_upper == eia_name:
                return {
                    "verified": True,
                    "eia_name": eia_util.get("name"),
                    "confidence_adjustment": 0.05,
                    "eia_id": eia_util.get("eiaid"),
                }

            # Meaningful word overlap (after removing stop words)
            eia_meaningful = eia_words - _EIA_STOP_WORDS
            common = provider_meaningful & eia_meaningful
            shorter_len = min(len(provider_meaningful), len(eia_meaningful)) or 1
            if common and len(common) / shorter_len >= 0.50:
                return {
                    "verified": True,
                    "eia_name": eia_util.get("name"),
                    "confidence_adjustment": 0.03,
                    "eia_id": eia_util.get("eiaid"),
                }

            # Substring match
            if provider_upper in eia_name or eia_name in provider_upper:
                return {
                    "verified": True,
                    "eia_name": eia_util.get("name"),
                    "confidence_adjustment": 0.02,
                    "eia_id": eia_util.get("eiaid"),
                }

        # No match — EIA disagrees
        primary = utilities[0]
        return {
            "verified": False,
            "eia_name": primary.get("name"),
            "confidence_adjustment": -0.05,
            "eia_id": primary.get("eiaid"),
        }

    def lookup_by_zip(self, zip_code: str) -> Optional[dict]:
        """
        Look up the primary electric utility for a ZIP code.
        Used as a fallback when State GIS + HIFLD return nothing.

        Returns:
            dict with keys: name, eia_id, state, source, confidence
            None if no data
        """
        utilities = self.get_utilities(zip_code)
        if not utilities:
            return None

        # Use first entry — EIA data is ordered by relevance for the ZIP.
        # Do NOT prefer IOU over municipal/co-op: municipals (e.g. College Station
        # Utilities) and co-ops are the actual provider in their territory.
        best = utilities[0]

        return {
            "name": best.get("name", ""),
            "eia_id": best.get("eiaid"),
            "state": best.get("state", ""),
            "source": "eia_zip",
            "confidence": 0.70,
        }

    @property
    def loaded(self) -> bool:
        return len(self._data) > 0
