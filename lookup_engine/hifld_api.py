"""HIFLD Electric Retail Service Territories — live ArcGIS API lookup.

Queries the HIFLD FeatureServer hosted on ArcGIS Online for electric utility
service territory data via point-in-polygon spatial queries.

This supplements the local HIFLD shapefiles with:
  - A live API fallback when local shapefiles miss
  - Phone and website contact info not in local shapefiles
  - TYPE field (MUNICIPAL, COOPERATIVE, INVESTOR OWNED, etc.)

Endpoint:
  https://services3.arcgis.com/OYP7N6mAJJCyH6hd/arcgis/rest/services/
  Electric_Retail_Service_Territories_HIFLD/FeatureServer/0

No API key required. Free public endpoint.
"""

import logging
import time
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = (
    "https://services3.arcgis.com/OYP7N6mAJJCyH6hd/arcgis/rest/services/"
    "Electric_Retail_Service_Territories_HIFLD/FeatureServer/0/query"
)

_OUT_FIELDS = "NAME,TYPE,TELEPHONE,WEBSITE,STATE,HOLDING_CO,CUSTOMERS,REGULATED"

# Timeout for API requests (seconds) — keep tight so lookups stay fast
_TIMEOUT = 2.0

# Circuit breaker: disable after N consecutive failures
_CIRCUIT_BREAKER_THRESHOLD = 3


class HIFLDApiLookup:
    """Query HIFLD electric service territories via live ArcGIS API."""

    def __init__(self):
        self._consecutive_failures = 0
        self._disabled = False
        self._last_failure_time = 0.0
        # Re-enable after 5 minutes
        self._disable_duration = 300

    @property
    def available(self) -> bool:
        """Check if the API is available (not circuit-broken)."""
        if not self._disabled:
            return True
        # Auto-recover after disable_duration
        if time.time() - self._last_failure_time > self._disable_duration:
            self._disabled = False
            self._consecutive_failures = 0
            logger.info("HIFLD API: circuit breaker reset, re-enabling")
            return True
        return False

    def query(self, lat: float, lon: float) -> List[dict]:
        """
        Query HIFLD for electric utilities at a point.

        Returns list of dicts, each with:
            name, type, telephone, website, state, holding_co, customers, regulated

        Returns empty list on failure or timeout.
        """
        if not self.available:
            return []

        params = {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": _OUT_FIELDS,
            "returnGeometry": "false",
            "f": "json",
        }

        try:
            t0 = time.time()
            resp = requests.get(_BASE_URL, params=params, timeout=_TIMEOUT)
            elapsed_ms = int((time.time() - t0) * 1000)

            if resp.status_code != 200:
                self._record_failure()
                logger.debug(f"HIFLD API: HTTP {resp.status_code} ({elapsed_ms}ms)")
                return []

            data = resp.json()
            features = data.get("features", [])

            # Reset circuit breaker on success
            self._consecutive_failures = 0

            results = []
            for f in features:
                attrs = f.get("attributes", {})
                name = (attrs.get("NAME") or "").strip()
                if not name:
                    continue
                results.append({
                    "name": name,
                    "type": (attrs.get("TYPE") or "").strip(),
                    "telephone": (attrs.get("TELEPHONE") or "").strip(),
                    "website": (attrs.get("WEBSITE") or "").strip(),
                    "state": (attrs.get("STATE") or "").strip(),
                    "holding_co": (attrs.get("HOLDING_CO") or "").strip(),
                    "customers": attrs.get("CUSTOMERS") or 0,
                    "regulated": (attrs.get("REGULATED") or "").strip(),
                })

            logger.debug(f"HIFLD API: {len(results)} results ({elapsed_ms}ms)")
            return results

        except requests.Timeout:
            self._record_failure()
            logger.debug("HIFLD API: timeout")
            return []
        except Exception as e:
            self._record_failure()
            logger.debug(f"HIFLD API: error: {e}")
            return []

    def _record_failure(self):
        """Track consecutive failures for circuit breaker."""
        self._consecutive_failures += 1
        self._last_failure_time = time.time()
        if self._consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
            self._disabled = True
            logger.warning(
                f"HIFLD API: circuit breaker tripped after "
                f"{self._consecutive_failures} consecutive failures"
            )
