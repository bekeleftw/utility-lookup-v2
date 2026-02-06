"""Geocoding wrapper — pluggable: Census (free) or Google (API key required)."""

import csv
import io
import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

import requests

from .models import GeocodedAddress

logger = logging.getLogger(__name__)


class Geocoder(ABC):
    @abstractmethod
    def geocode(self, address: str) -> Optional[GeocodedAddress]:
        """Geocode an address string to lat/lon + components."""
        ...


class CensusGeocoder(Geocoder):
    """Free US Census Bureau geocoder. No API key needed. ~200-500ms per call."""

    BASE_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
    BATCH_URL = "https://geocoding.geo.census.gov/geocoder/geographies/addressbatch"
    BATCH_CHUNK_SIZE = 10000
    BATCH_TIMEOUT = 300  # 5 minutes per chunk
    BATCH_MAX_RETRIES = 3

    def geocode(self, address: str) -> Optional[GeocodedAddress]:
        params = {
            "address": address,
            "benchmark": "Public_AR_Current",
            "vintage": "Current_Current",
            "format": "json",
        }
        try:
            t0 = time.time()
            resp = requests.get(self.BASE_URL, params=params, timeout=10)
            elapsed_ms = int((time.time() - t0) * 1000)
            resp.raise_for_status()
            data = resp.json()

            matches = data.get("result", {}).get("addressMatches", [])
            if not matches:
                logger.debug(f"Census geocoder: no match for '{address}' ({elapsed_ms}ms)")
                return None

            best = matches[0]
            coords = best.get("coordinates", {})
            addr_components = best.get("addressComponents", {})
            geographies = best.get("geographies", {})

            # Extract county from geographies
            county = ""
            counties = geographies.get("Counties", [])
            if counties:
                county = counties[0].get("NAME", "")

            # Extract 15-digit Census block GEOID
            block_geoid = ""
            blocks = geographies.get("2020 Census Blocks", [])
            if blocks:
                block_geoid = blocks[0].get("GEOID", "")

            result = GeocodedAddress(
                lat=float(coords.get("y", 0)),
                lon=float(coords.get("x", 0)),
                confidence=0.95 if best.get("tigerLine") else 0.80,
                formatted_address=best.get("matchedAddress", address),
                city=addr_components.get("city", ""),
                state=addr_components.get("state", ""),
                zip_code=addr_components.get("zip", ""),
                county=county,
                block_geoid=block_geoid,
            )
            logger.debug(f"Census geocoder: {address} -> ({result.lat}, {result.lon}) ({elapsed_ms}ms)")
            return result

        except requests.RequestException as e:
            logger.error(f"Census geocoder error for '{address}': {e}")
            return None
        except (KeyError, ValueError, IndexError) as e:
            logger.error(f"Census geocoder parse error for '{address}': {e}")
            return None

    def geocode_batch(
        self, addresses: List[Tuple[str, str]]
    ) -> Dict[str, Optional[GeocodedAddress]]:
        """
        Geocode up to N addresses using the Census batch endpoint.

        Args:
            addresses: list of (unique_id, full_address) tuples

        Returns:
            dict mapping unique_id -> GeocodedAddress or None if no match
        """
        all_results: Dict[str, Optional[GeocodedAddress]] = {}
        total = len(addresses)

        for chunk_start in range(0, total, self.BATCH_CHUNK_SIZE):
            chunk = addresses[chunk_start : chunk_start + self.BATCH_CHUNK_SIZE]
            chunk_end = chunk_start + len(chunk)
            logger.info(f"Batch geocoding: {chunk_start + 1}-{chunk_end} of {total}...")

            # Build CSV — split one-line addresses into street, city, state, zip
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for uid, addr in chunk:
                street, city, state, zipcode = self._split_address(addr)
                writer.writerow([uid, street, city, state, zipcode])

            chunk_results = self._send_batch(buffer.getvalue(), attempt=1)
            all_results.update(chunk_results)

        matched = sum(1 for v in all_results.values() if v is not None)
        logger.info(f"Batch geocoding complete: {matched}/{total} matched")
        return all_results

    @staticmethod
    def _split_address(address: str) -> Tuple[str, str, str, str]:
        """Split a one-line address into (street, city, state, zip) for the batch CSV.

        Handles formats like:
          "233 S Wacker Dr, Chicago, IL 60606"
          "100 Military Plaza, San Antonio, TX 78205"
        """
        import re

        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 3:
            street = parts[0]
            city = parts[1]
            # Last part is usually "ST ZIP" or "ST" or just zip
            state_zip = parts[-1].strip()
            match = re.match(r"([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)", state_zip)
            if match:
                state, zipcode = match.group(1), match.group(2)
            else:
                state = state_zip
                zipcode = ""
        elif len(parts) == 2:
            street = parts[0]
            state_zip = parts[1].strip()
            match = re.match(r"(.+?)\s+([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)", state_zip)
            if match:
                city, state, zipcode = match.group(1), match.group(2), match.group(3)
            else:
                city, state, zipcode = state_zip, "", ""
        else:
            # Can't parse — send as street only, let Census try
            street, city, state, zipcode = address, "", "", ""

        return street, city, state, zipcode

    def _send_batch(self, csv_payload: str, attempt: int) -> Dict[str, Optional[GeocodedAddress]]:
        """Send a single batch request with retry logic."""
        files = {"addressFile": ("addresses.csv", csv_payload, "text/csv")}
        data = {
            "benchmark": "Public_AR_Current",
            "vintage": "Current_Current",
        }

        try:
            t0 = time.time()
            resp = requests.post(
                self.BATCH_URL, files=files, data=data, timeout=self.BATCH_TIMEOUT
            )
            elapsed = time.time() - t0
            resp.raise_for_status()
            logger.info(f"Batch response received in {elapsed:.1f}s")
            return self._parse_batch_response(resp.text)

        except requests.RequestException as e:
            if attempt < self.BATCH_MAX_RETRIES:
                wait = 2 ** attempt
                logger.warning(
                    f"Batch geocode attempt {attempt} failed: {e}. "
                    f"Retrying in {wait}s..."
                )
                time.sleep(wait)
                return self._send_batch(csv_payload, attempt + 1)
            logger.error(f"Batch geocode failed after {attempt} attempts: {e}")
            return {}

    def _parse_batch_response(self, response_text: str) -> Dict[str, Optional[GeocodedAddress]]:
        """Parse the Census batch CSV response."""
        results: Dict[str, Optional[GeocodedAddress]] = {}
        reader = csv.reader(io.StringIO(response_text))

        for row in reader:
            if len(row) < 3:
                continue
            uid = row[0].strip('"')
            match_status = row[2].strip('"') if len(row) > 2 else ""

            if match_status != "Match":
                results[uid] = None
                continue

            try:
                matched_address = row[4].strip('"') if len(row) > 4 else ""
                # lon_lat field is "lon,lat" — longitude first!
                lon_lat = row[5].strip('"') if len(row) > 5 else ""
                if "," in lon_lat:
                    lon_str, lat_str = lon_lat.split(",", 1)
                    lat = float(lat_str)
                    lon = float(lon_str)
                else:
                    results[uid] = None
                    continue

                # Parse city/state/zip from matched address
                # Format: "123 MAIN ST, DALLAS, TX, 75201"
                parts = [p.strip() for p in matched_address.split(",")]
                city = parts[1] if len(parts) > 1 else ""
                state = parts[2] if len(parts) > 2 else ""
                zip_code = parts[3] if len(parts) > 3 else ""

                # Parse FIPS fields into 15-digit block GEOID
                # Geographies batch: [8]=state, [9]=county, [10]=tract, [11]=block
                block_geoid = ""
                if len(row) >= 12:
                    fips_state = row[8].strip('"')
                    fips_county = row[9].strip('"')
                    fips_tract = row[10].strip('"')
                    fips_block = row[11].strip('"')
                    if fips_state and fips_county and fips_tract and fips_block:
                        block_geoid = f"{fips_state}{fips_county}{fips_tract}{fips_block}"

                results[uid] = GeocodedAddress(
                    lat=lat,
                    lon=lon,
                    confidence=0.95,
                    formatted_address=matched_address,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    block_geoid=block_geoid,
                )
            except (ValueError, IndexError) as e:
                logger.warning(f"Failed to parse batch row for uid={uid}: {e}")
                results[uid] = None

        return results


class GoogleGeocoder(Geocoder):
    """Google Maps geocoder. Requires GOOGLE_MAPS_API_KEY."""

    BASE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Google geocoder requires an API key")
        self.api_key = api_key

    def geocode(self, address: str) -> Optional[GeocodedAddress]:
        params = {
            "address": address,
            "key": self.api_key,
        }
        try:
            t0 = time.time()
            resp = requests.get(self.BASE_URL, params=params, timeout=10)
            elapsed_ms = int((time.time() - t0) * 1000)
            resp.raise_for_status()
            data = resp.json()

            results = data.get("results", [])
            if not results:
                logger.debug(f"Google geocoder: no match for '{address}' ({elapsed_ms}ms)")
                return None

            best = results[0]
            loc = best.get("geometry", {}).get("location", {})
            loc_type = best.get("geometry", {}).get("location_type", "")

            # Map Google location_type to confidence
            confidence_map = {
                "ROOFTOP": 0.98,
                "RANGE_INTERPOLATED": 0.90,
                "GEOMETRIC_CENTER": 0.80,
                "APPROXIMATE": 0.60,
            }
            confidence = confidence_map.get(loc_type, 0.70)

            # Extract components
            components = {}
            for comp in best.get("address_components", []):
                types = comp.get("types", [])
                if "locality" in types:
                    components["city"] = comp["long_name"]
                elif "administrative_area_level_1" in types:
                    components["state"] = comp["short_name"]
                elif "postal_code" in types:
                    components["zip_code"] = comp["long_name"]
                elif "administrative_area_level_2" in types:
                    components["county"] = comp["long_name"]

            result = GeocodedAddress(
                lat=float(loc.get("lat", 0)),
                lon=float(loc.get("lng", 0)),
                confidence=confidence,
                formatted_address=best.get("formatted_address", address),
                **components,
            )
            logger.debug(f"Google geocoder: {address} -> ({result.lat}, {result.lon}) ({elapsed_ms}ms)")
            return result

        except requests.RequestException as e:
            logger.error(f"Google geocoder error for '{address}': {e}")
            return None


class ChainedGeocoder(Geocoder):
    """Census → Google fallback chain. Tries Census first, falls back to Google on miss."""

    def __init__(self, primary: Geocoder, fallback: Geocoder):
        self.primary = primary
        self.fallback = fallback
        self.primary_hits = 0
        self.fallback_hits = 0
        self.total_misses = 0

    def geocode(self, address: str) -> Optional[GeocodedAddress]:
        result = self.primary.geocode(address)
        if result is not None:
            self.primary_hits += 1
            return result
        # Primary failed, try fallback
        result = self.fallback.geocode(address)
        if result is not None:
            self.fallback_hits += 1
            logger.debug(f"Fallback geocoder matched: '{address}'")
            return result
        self.total_misses += 1
        return None

    @property
    def stats(self) -> dict:
        total = self.primary_hits + self.fallback_hits + self.total_misses
        return {
            "total": total,
            "primary_hits": self.primary_hits,
            "fallback_hits": self.fallback_hits,
            "total_misses": self.total_misses,
            "primary_rate": f"{self.primary_hits / total * 100:.1f}%" if total else "N/A",
            "fallback_rate": f"{self.fallback_hits / total * 100:.1f}%" if total else "N/A",
        }


def create_geocoder(geocoder_type: str = "census", google_api_key: str = "") -> Geocoder:
    """Factory function to create a geocoder instance.

    Args:
        geocoder_type: "census" (default, free), "google", or "chained" (Census + Google fallback)
        google_api_key: Required for "google" and "chained" types
    """
    if geocoder_type == "chained" and google_api_key:
        return ChainedGeocoder(CensusGeocoder(), GoogleGeocoder(google_api_key))
    elif geocoder_type == "google" and google_api_key:
        return GoogleGeocoder(google_api_key)
    return CensusGeocoder()


def get_census_block_geoid(lat: float, lon: float) -> Optional[str]:
    """Get 15-digit Census block GEOID from coordinates via TIGERweb."""
    url = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
    params = {
        "x": lon,
        "y": lat,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        geographies = data.get("result", {}).get("geographies", {})
        blocks = geographies.get("2020 Census Blocks", [])
        if blocks:
            return blocks[0].get("GEOID")

        blocks_2010 = geographies.get("Census Blocks", [])
        if blocks_2010:
            return blocks_2010[0].get("GEOID")
    except Exception as e:
        logger.debug(f"Census block GEOID lookup error: {e}")

    return None
