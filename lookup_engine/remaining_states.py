"""Remaining states ZIP-to-provider mappings.

Tenant-verified ZIP mappings for states/ZIPs not well-covered by HIFLD.
Includes dominance percentage and confidence level from the 87K dataset.

Used as a fallback between HIFLD and EIA/FindEnergy.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class RemainingStatesLookup:
    """ZIP-based utility lookup from tenant-verified data."""

    def __init__(self, data_dir: str = None):
        if data_dir is None:
            data_dir = str(Path(__file__).parent.parent / "data")

        self._data: dict = {}  # {utility_type: {state: {zip: entry}}}
        self._load(data_dir)

    def _load(self, data_dir: str):
        dpath = Path(data_dir)
        for utype in ("electric", "gas", "water"):
            fpath = dpath / f"remaining_states_{utype}.json"
            if not fpath.exists():
                continue
            try:
                with open(fpath) as f:
                    raw = json.load(f)
                states = raw.get("states", {})
                self._data[utype] = states
                total_zips = sum(len(v) for v in states.values())
                logger.info(
                    f"Remaining states {utype}: {len(states)} states, {total_zips} ZIPs"
                )
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load {fpath}: {e}")

    def lookup(self, zip_code: str, state: str, utility_type: str) -> Optional[dict]:
        """
        Look up provider by ZIP code.

        Returns:
            dict with keys: name, source, confidence, dominance_pct
            None if no data
        """
        state = (state or "").upper()
        zip_code = (zip_code or "").strip()
        utility_type = (utility_type or "").lower()

        if not zip_code or not state or not utility_type:
            return None

        states = self._data.get(utility_type, {})
        zips = states.get(state, {})
        entry = zips.get(zip_code)
        if not entry:
            return None

        name = entry.get("name", "")
        if not name:
            return None

        # Map confidence_level to numeric confidence
        conf_level = entry.get("confidence_level", "medium")
        dominance = entry.get("dominance_pct", 0)
        sample_count = entry.get("sample_count", 0)

        if conf_level == "high" and dominance >= 90:
            confidence = 0.82
        elif conf_level == "high":
            confidence = 0.78
        elif conf_level == "medium" and dominance >= 80:
            confidence = 0.75
        elif conf_level == "medium":
            confidence = 0.72
        else:
            confidence = 0.65

        # Boost slightly for high sample counts
        if sample_count >= 20:
            confidence = min(confidence + 0.03, 0.85)

        return {
            "name": name,
            "source": f"remaining_states_{utility_type}",
            "confidence": confidence,
            "dominance_pct": dominance,
            "sample_count": sample_count,
            "state": state,
        }

    @property
    def loaded(self) -> bool:
        return len(self._data) > 0
