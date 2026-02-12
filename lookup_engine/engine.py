"""Main LookupEngine — orchestrates geocoding, spatial lookup, normalization, and scoring."""

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from .cache import LookupCache
from .config import Config
from .geocoder import Geocoder, create_geocoder, get_census_block_geoid
from .models import GeocodedAddress, LookupResult, ProviderResult
from .scorer import EnsembleScorer, get_canonical_id
from .spatial_index import SpatialIndex
from .postgis_spatial import PostGISSpatialIndex
from .corrections import CorrectionsLookup
from .county_gas import CountyGasLookup
from .eia_verification import EIAVerification
from .findenergy_lookup import FindEnergyLookup
from .gas_mappings import GasZIPMappingLookup
from .georgia_emc import GeorgiaEMCLookup
from .remaining_states import RemainingStatesLookup
from .provider_id_matcher import ProviderIDMatcher
from .special_districts import SpecialDistrictsLookup
from .state_gis import StateGISLookup

logger = logging.getLogger(__name__)


class LookupEngine:
    """
    Utility provider lookup engine.

    Takes a street address, geocodes it, runs point-in-polygon against
    local shapefiles, normalizes results to canonical providers, and
    returns scored results. No external APIs except geocoding.
    """

    def __init__(self, config: Optional[Config] = None, skip_water: bool = False):
        self.config = config or Config()
        self._skip_water = skip_water

        logger.info("Initializing LookupEngine...")
        t0 = time.time()

        # Spatial index — use PostGIS if available, else in-memory geopandas
        postgis_url = os.environ.get("POSTGIS_URL", "")
        if postgis_url:
            self.spatial = PostGISSpatialIndex(postgis_url)
            if not self.spatial.is_loaded:
                logger.warning("PostGIS unavailable, falling back to in-memory spatial index")
                self.spatial = SpatialIndex(self.config)
                self.spatial.load_all()
        else:
            self.spatial = SpatialIndex(self.config)
            if skip_water:
                self.spatial._load_electric()
                self.spatial._load_gas()
                logger.info("Skipped water layer (skip_water=True)")
            else:
                self.spatial.load_all()

        # Scorer / normalization bridge
        self.scorer = EnsembleScorer(self.config)

        # Geocoder
        self.geocoder: Geocoder = create_geocoder(
            self.config.geocoder_type, google_api_key=self.config.google_api_key
        )

        # Priority 0: User corrections (highest priority)
        self.corrections = CorrectionsLookup()

        # Priority 1: State GIS API
        self.state_gis = StateGISLookup()
        self.state_gis.prewarm()

        # Priority 2: Gas ZIP mapping (gas only)
        self.gas_mappings = GasZIPMappingLookup()

        # Priority 2.5: Georgia EMC (GA electric only)
        self.georgia_emc = GeorgiaEMCLookup()

        # Priority 2.7: County gas lookup (IL, PA, NY, TX county/city -> gas utility)
        self.county_gas = CountyGasLookup()

        # Priority 3: HIFLD shapefile (handled by self.spatial)

        # Priority 3.5: Remaining states ZIP data
        self.remaining_states = RemainingStatesLookup()

        # Priority 3.7: Special districts water (AZ, CA, CO, FL, WA)
        self.special_districts = SpecialDistrictsLookup()

        # Priority 4: EIA verification + fallback for electric
        self.eia_verify = EIAVerification()

        # Priority 5: FindEnergy city-based fallback
        self.findenergy = FindEnergyLookup()

        # Priority 6: State gas defaults (last resort for gas)
        self._state_gas_defaults = {}
        _gas_defaults_path = Path(__file__).parent.parent / "data" / "state_gas_defaults.json"
        if _gas_defaults_path.exists():
            import json as _json
            with open(_gas_defaults_path) as _f:
                self._state_gas_defaults = _json.load(_f)
            logger.info(f"State gas defaults: {len(self._state_gas_defaults)} states")

        # Provider ID matching (catalog name -> internal ID)
        self.id_matcher = ProviderIDMatcher()

        # Internet lookup (FCC BDC via Postgres)
        self.internet = None
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            try:
                from .internet_lookup import InternetLookup
                self.internet = InternetLookup(db_url)
            except Exception as e:
                logger.warning(f"Internet lookup init failed: {e}")

        # Cache
        self.cache = LookupCache(self.config.cache_db, self.config.cache_ttl_days)

        elapsed = time.time() - t0
        counts = self.spatial.layer_counts
        logger.info(
            f"LookupEngine ready in {elapsed:.1f}s — "
            f"electric={counts['electric']}, gas={counts['gas']}, water={counts['water']}, "
            f"cache={self.cache.size} entries"
        )

    def lookup(self, address: str, use_cache: bool = True) -> LookupResult:
        """
        Look up utility providers for an address.

        1. Check cache
        2. Geocode address -> (lat, lon)
        3. Spatial query for electric, gas, water
        4. Normalize + score each result
        5. Handle deregulated market logic
        6. Cache and return
        """
        t0 = time.time()

        # 1. Cache check
        if use_cache:
            cached = self.cache.get(address)
            if cached:
                cached.lookup_time_ms = int((time.time() - t0) * 1000)
                logger.debug(f"Cache hit for '{address}' ({cached.lookup_time_ms}ms)")
                return cached

        # 2. Geocode
        geo = self.geocoder.geocode(address)
        if not geo:
            result = LookupResult(
                address=address,
                lookup_time_ms=int((time.time() - t0) * 1000),
            )
            return result

        # 3 + 4. Spatial query + normalize for each utility type
        # Extract state and ZIP from geocoder result or address string
        addr_state = geo.state or ""
        addr_zip = geo.zip_code or ""
        if not addr_state:
            _st_match = re.search(r",\s*([A-Z]{2})\s+(\d{5})", address)
            if _st_match:
                addr_state = _st_match.group(1)
                if not addr_zip:
                    addr_zip = _st_match.group(2)
            else:
                _st_match = re.search(r",\s*([A-Z]{2})\s*$", address)
                if _st_match:
                    addr_state = _st_match.group(1)
        if not addr_zip:
            _zip_match = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", address)
            if _zip_match:
                addr_zip = _zip_match.group(1)

        # Extract city from geocoder result or address string
        addr_city = geo.city or ""
        if not addr_city:
            _city_match = re.search(r",\s*([^,]+?)\s*,\s*[A-Z]{2}", address)
            if _city_match:
                addr_city = _city_match.group(1).strip()

        # Extract county from geocoder result
        addr_county = geo.county or ""

        _lkw = dict(zip_code=addr_zip, city=addr_city, county=addr_county, address=address)
        electric = self._lookup_with_state_gis(geo.lat, geo.lon, addr_state, "electric", **_lkw)
        gas = self._lookup_with_state_gis(geo.lat, geo.lon, addr_state, "gas", **_lkw)
        water = self._lookup_with_state_gis(geo.lat, geo.lon, addr_state, "water",
                                             **_lkw) if not self._skip_water else None

        # Sewer: inherits from water, checks sewer catalog
        sewer = self._lookup_sewer(
            geo.lat, geo.lon, addr_state, addr_zip, addr_city, addr_county, water
        )

        # 5. Internet lookup (FCC BDC via Census block GEOID)
        internet_data = None
        if self.internet and geo.block_geoid:
            try:
                internet_data = self.internet.lookup(geo.block_geoid)
            except Exception as e:
                logger.debug(f"Internet lookup failed for {geo.block_geoid}: {e}")

        # 6. Build result
        result = LookupResult(
            address=address,
            lat=geo.lat,
            lon=geo.lon,
            geocode_confidence=geo.confidence,
            electric=electric,
            gas=gas,
            water=water,
            sewer=sewer,
            trash=None,   # No federal shapefile for trash
            internet=internet_data,
            lookup_time_ms=int((time.time() - t0) * 1000),
        )

        # 6. Cache (skip geocode failures — they're transient and may succeed on retry)
        if use_cache and result.lat != 0.0:
            self.cache.put(address, result)

        logger.info(
            f"Lookup '{address}' -> "
            f"electric={electric.provider_name if electric else 'None'}, "
            f"gas={gas.provider_name if gas else 'None'}, "
            f"water={water.provider_name if water else 'None'} "
            f"({result.lookup_time_ms}ms)"
        )
        return result

    def lookup_batch(self, addresses: list, use_cache: bool = True, delay_ms: int = 200) -> list:
        """
        Batch lookup with progress logging.

        Args:
            addresses: List of address strings
            use_cache: Whether to use cache
            delay_ms: Delay between geocoding calls (Census rate limiting)
        """
        results = []
        total = len(addresses)
        for i, addr in enumerate(addresses, 1):
            result = self.lookup(addr, use_cache=use_cache)
            results.append(result)
            if i % 10 == 0 or i == total:
                logger.info(f"Batch progress: {i}/{total}")
            if delay_ms > 0 and i < total:
                time.sleep(delay_ms / 1000)
        return results

    def _lookup_sewer(self, lat, lon, state, zip_code, city, county, water_result):
        """
        Sewer lookup — inherits from water, then checks sewer-specific catalog entries.

        Args:
            water_result: The water ProviderResult (if any) — sewer often = water provider
        """
        candidates = []

        def _add(name, conf, source, catalog_id=None):
            candidates.append(ProviderResult(
                provider_name=name,
                utility_type="sewer",
                confidence=conf,
                polygon_source=source,
                catalog_id=catalog_id,
            ))

        # Priority 1: Check if water provider has a sewer catalog entry
        if water_result and water_result.provider_name:
            sewer_match = self.id_matcher.match(
                water_result.provider_name, "sewer", state
            )
            if sewer_match and sewer_match["match_score"] >= 80:
                _add(
                    sewer_match["title"],
                    min(water_result.confidence + 0.05, 0.88),
                    "water_inheritance",
                    sewer_match["id"],
                )

        # Priority 2: City/municipality match against sewer catalog
        if city:
            city_variants = [
                f"City of {city}",
                f"{city} Sewer",
                f"{city} Utilities",
                f"{city} Public Works",
                city,
            ]
            for variant in city_variants:
                match = self.id_matcher.match(variant, "sewer", state)
                if match and match["match_score"] >= 75:
                    _add(match["title"], 0.82, "sewer_city_match", match["id"])
                    break

        # Priority 3: County sanitary district match
        if county:
            county_clean = county.replace(" County", "").replace(" county", "").strip()
            for variant in [f"{county_clean} County Sanitary", f"{county_clean} Sanitary", county_clean]:
                match = self.id_matcher.match(variant, "sewer", state)
                if match and match["match_score"] >= 70:
                    _add(match["title"], 0.75, "sewer_county_match", match["id"])
                    break

        # Priority 4: Fall back to water provider name with lower confidence
        if not candidates and water_result and water_result.provider_name:
            _add(water_result.provider_name, 0.50, "water_fallback_no_sewer_id")

        if not candidates:
            return None

        # Deduplicate and pick best
        candidates = self._deduplicate_and_boost(candidates)
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        primary = candidates[0]
        primary.needs_review = primary.confidence < 0.70

        # Alternatives
        seen = {primary.provider_name.upper()}
        alts = []
        for c in candidates[1:5]:
            if c.provider_name.upper() not in seen:
                alts.append({
                    "provider": c.provider_name,
                    "confidence": round(c.confidence, 3),
                    "source": c.polygon_source or "",
                    "catalog_id": c.catalog_id,
                })
                seen.add(c.provider_name.upper())
        primary.alternatives = alts

        # ID matching (if not already set from catalog match)
        if not primary.catalog_id and self.id_matcher.loaded:
            id_match = self.id_matcher.match(primary.provider_name, "sewer", state)
            if id_match:
                primary.catalog_id = id_match["id"]
                primary.catalog_title = id_match["title"]
                primary.id_match_score = id_match["match_score"]
                primary.id_confident = id_match["confident"]

        # Attach contact info (phone/website)
        self.scorer._attach_contact_info(primary)

        return primary

    # Texas TDU priority for overlap resolution.
    # HIFLD polygons overlap significantly (TNMP overlaps Oncor by 58%).
    # Without subdivision-level data, we use empirical priority:
    #   - Co-ops/municipals always win (most specific, exempt from deregulation)
    #   - CenterPoint: well-defined Houston metro boundary, rarely overlaps incorrectly
    #   - AEP Texas: geographically distinct from Oncor (South/West TX)
    #   - Oncor: largest TDU, default for DFW metro — wins over TNMP
    #   - TNMP: polygon is overgeneralized, 58% overlap with Oncor is mostly Oncor territory
    #   - Lubbock P&L: tiny territory, wins by area anyway
    _TDU_PRIORITY = {
        "CENTERPOINT ENERGY": 1,
        "AEP TEXAS CENTRAL COMPANY": 2,
        "AEP TEXAS NORTH COMPANY": 2,
        "ONCOR ELECTRIC DELIVERY COMPANY LLC": 3,
        "TEXAS-NEW MEXICO POWER CO": 4,
        "CITY OF LUBBOCK - (TX)": 5,
    }

    def _lookup_with_state_gis(self, lat: float, lon: float, address_state: str,
                               utility_type: str,
                               zip_code: str = "",
                               city: str = "",
                               county: str = "",
                               address: str = "") -> Optional[ProviderResult]:
        """
        Multi-source lookup: collect candidates from all sources, deduplicate,
        return primary result with alternatives.

        Priority chain:
          0. User corrections (0.99)
          1. State GIS API (0.90-0.95)
          2. Gas ZIP mapping (gas only, 0.85-0.93)
          2.5 Georgia EMC (GA electric only, 0.72-0.87)
          3. HIFLD shapefile (0.75-0.85)
          3.5 Remaining states ZIP (0.65-0.85)
          4. EIA ZIP fallback (electric only, 0.70)
          5. FindEnergy city cache (electric + gas, 0.65)
          6. State default LDC (gas only, 0.40-0.65)
        """
        candidates = []  # list of ProviderResult

        def _add_candidate(name, eia_id, state, source, max_conf=None, set_conf=None):
            """Resolve a raw name through the scorer and add as candidate."""
            pr = self.scorer.resolve_provider(
                shapefile_name=name, eia_id=eia_id, state=state,
                utility_type=utility_type, polygon_source=source,
                area_km2=0, cntrl_area="", shp_type="",
            )
            if pr:
                if set_conf is not None:
                    pr.confidence = set_conf  # Override scorer confidence
                elif max_conf is not None:
                    pr.confidence = min(pr.confidence, max_conf)
                candidates.append(pr)

        # Priority 0: User corrections
        if address:
            corr = self.corrections.lookup_by_address(address, utility_type)
            if corr and corr.get("name"):
                _add_candidate(corr["name"], None, corr.get("state", address_state),
                               "correction_address", set_conf=0.99)
        if not candidates and zip_code:
            corr = self.corrections.lookup_by_zip(zip_code, utility_type)
            if corr and corr.get("name"):
                _add_candidate(corr["name"], None, address_state,
                               "correction_zip", set_conf=0.98)

        # If we have a correction, it wins — skip other sources for primary
        # but still collect alternatives for the response

        # Priority 1: State GIS API
        if address_state:
            gis_result = self.state_gis.query(lat, lon, address_state, utility_type)
            if gis_result and gis_result.get("name"):
                gis_name = gis_result["name"]
                # Water-specific: detect subdivision/street names from TWDB data
                # (e.g., "CROSSBOW COURT", "OAK HOLLOW ESTATES") and replace with
                # city water utility. Real water utilities contain keywords like
                # "water", "city of", "municipal", "utility", "district", "MUD", etc.
                if utility_type == "water" and not self._is_water_utility_name(gis_name):
                    if city:
                        gis_name = f"City of {city}"
                        gis_result = dict(gis_result, name=gis_name)
                        logger.info(f"Water name override: '{gis_result.get('name')}' -> '{gis_name}' (subdivision name detected)")
                    else:
                        gis_result = None  # Drop subdivision name with no city fallback
                        logger.info(f"Water name dropped: '{gis_name}' (subdivision name, no city)")
            if gis_result and gis_result.get("name"):
                gis_source = gis_result.get("source", "state_gis")
                n_before = len(candidates)
                _add_candidate(gis_result["name"], None,
                               gis_result.get("state", address_state),
                               gis_source)
                # State GIS is higher resolution than HIFLD/ZIP — boost to 0.90
                # so it outranks HIFLD (0.75-0.85), EIA ZIP (0.70), and other fallbacks.
                # Without this, passthrough-resolved names (0.60) lose to lower-priority sources.
                if len(candidates) > n_before:
                    gis_candidate = candidates[n_before]
                    if gis_candidate.confidence < 0.90:
                        gis_candidate.confidence = 0.90

        # Priority 2: Gas ZIP-prefix mapping (gas only)
        if utility_type == "gas" and zip_code and address_state:
            gas_result = self.gas_mappings.query(zip_code, address_state)
            if gas_result and gas_result.get("name"):
                _add_candidate(gas_result["name"], None,
                               gas_result.get("state", address_state),
                               gas_result.get("source", "gas_zip_mapping"))

        # Priority 2.5: Georgia EMC (GA electric only)
        if utility_type == "electric" and address_state == "GA" and county:
            ga_results = self.georgia_emc.get_all_for_county(county)
            for ga in ga_results:
                _add_candidate(ga["name"], None, "GA", ga["source"],
                               ga["confidence"])

        # Priority 2.7: County gas lookup (IL, PA, NY, TX)
        if utility_type == "gas" and address_state and self.county_gas.has_state(address_state):
            cg_result = self.county_gas.lookup(address_state, county=county, city=city)
            if cg_result and cg_result.get("name"):
                _add_candidate(cg_result["name"], None,
                               cg_result.get("state", address_state),
                               cg_result["source"],
                               cg_result["confidence"])

        # Priority 3: HIFLD shapefile
        hifld_result = self._lookup_type(lat, lon, utility_type, address_state=address_state)
        if hifld_result:
            candidates.append(hifld_result)

        # Priority 3.5: Remaining states ZIP data
        if zip_code and address_state:
            rs_result = self.remaining_states.lookup(zip_code, address_state, utility_type)
            if rs_result and rs_result.get("name"):
                _add_candidate(rs_result["name"], None,
                               rs_result.get("state", address_state),
                               rs_result["source"],
                               rs_result["confidence"])

        # Priority 3.7: Special districts water (AZ, CA, CO, FL, WA)
        if utility_type == "water" and zip_code and self.special_districts.loaded:
            sd_result = self.special_districts.lookup(zip_code)
            if sd_result and sd_result.get("name"):
                _add_candidate(sd_result["name"], None,
                               sd_result.get("state", address_state),
                               sd_result["source"],
                               sd_result["confidence"])

        # Priority 4: EIA ZIP fallback (electric only)
        if utility_type == "electric" and zip_code and self.eia_verify.loaded:
            eia_fb = self.eia_verify.lookup_by_zip(zip_code)
            if eia_fb and eia_fb.get("name"):
                _add_candidate(eia_fb["name"], eia_fb.get("eia_id"),
                               eia_fb.get("state", address_state),
                               "eia_zip", 0.70)

        # Priority 5: FindEnergy city cache (electric + gas)
        if city and address_state and utility_type in ("electric", "gas"):
            fe_result = self.findenergy.lookup(address_state, city, utility_type)
            if fe_result and fe_result.get("name"):
                _add_candidate(fe_result["name"], None,
                               fe_result.get("state", address_state),
                               "findenergy_city", 0.65)

        # Priority 6: State default gas LDC (gas only, last resort)
        if utility_type == "gas" and address_state:
            default = self._state_gas_defaults.get(address_state)
            if default and default.get("provider"):
                _add_candidate(default["provider"], None, address_state,
                               "state_gas_default",
                               default.get("confidence", 0.45))

        if not candidates:
            return None

        # Deduplicate and boost: if multiple sources agree, boost confidence
        candidates = self._deduplicate_and_boost(candidates)

        # Sort by confidence descending
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        # IOU demotion: if primary is a large IOU and a co-op/municipal exists, prefer the local utility.
        # Large IOUs (Duke, Dominion, etc.) have overgeneralized HIFLD polygons that overlap
        # smaller co-ops and municipals. The local utility is almost always correct.
        # Validated against 91K batch: 2,400+ cases where IOU is wrong primary and co-op/municipal
        # is correct in alternatives. Only ~1 false positive (Marion, IA) out of 2,400+.
        primary = candidates[0]
        if (utility_type == "electric" and len(candidates) > 1
                and self._is_large_iou(primary.provider_name)):
            for alt in candidates[1:]:
                alt_name_upper = alt.provider_name.upper()
                # Check if alternative looks like a co-op, municipal, or local utility
                is_local = (
                    any(kw in alt_name_upper for kw in (
                        "COOPERATIVE", "COOP", "ELECTRIC MEMBERSHIP",
                        "MUNICIPAL", "CITY OF", "TOWN OF",
                        "PUBLIC UTILITIES", "UTILITIES COMMISSION",
                        "PUD", "PUBLIC UTILITY DISTRICT",
                        "EMC", "CPW", "REA", "REC",
                    ))
                    or any(name in alt_name_upper for name in self._LOCAL_UTILITY_NAMES)
                )
                # Require reasonable confidence and exclude low-quality sources
                alt_source = (alt.polygon_source or "").lower()
                is_low_quality_source = any(s in alt_source for s in ("findenergy_city", "state_gas_default"))
                if is_local and alt.confidence >= 0.70 and not is_low_quality_source:
                    # Swap: local utility becomes primary
                    candidates.remove(alt)
                    candidates.insert(0, alt)
                    primary = alt
                    break

        # EIA verification for electric primary
        if utility_type == "electric" and zip_code and self.eia_verify.loaded:
            if primary.polygon_source not in ("eia_zip", "correction_address", "correction_zip"):
                eia_check = self.eia_verify.verify(zip_code, primary.provider_name)
                primary.confidence = max(0.0, min(1.0,
                    primary.confidence + eia_check["confidence_adjustment"]))
                if eia_check.get("eia_id") and not primary.eia_id:
                    primary.eia_id = eia_check["eia_id"]

        # Build alternatives list from remaining candidates
        alternatives = []
        seen = {primary.provider_name.upper()}
        for c in candidates[1:5]:
            if c.provider_name.upper() not in seen:
                alternatives.append({
                    "provider": c.provider_name,
                    "confidence": round(c.confidence, 3),
                    "source": c.polygon_source or "",
                    "eia_id": c.eia_id,
                })
                seen.add(c.provider_name.upper())

        primary.alternatives = alternatives
        primary.needs_review = primary.confidence < 0.70

        # ID matching: map provider name to catalog ID
        if self.id_matcher.loaded:
            id_match = self.id_matcher.match(
                primary.provider_name, utility_type, address_state
            )
            if id_match:
                primary.catalog_id = id_match["id"]
                primary.catalog_title = id_match["title"]
                primary.id_match_score = id_match["match_score"]
                primary.id_confident = id_match["confident"]

            # Also match alternatives
            for alt in primary.alternatives:
                alt_match = self.id_matcher.match(
                    alt.get("provider", ""), utility_type, address_state
                )
                if alt_match:
                    alt["catalog_id"] = alt_match["id"]
                    alt["catalog_title"] = alt_match["title"]

        return primary

    @staticmethod
    def _deduplicate_and_boost(candidates: list) -> list:
        """If multiple sources agree on the same provider, boost confidence.

        Groups by canonical provider ID (from canonical_providers.json) so that
        name variants like 'Duke Energy Carolinas' and 'Duke Energy' are
        recognized as the same provider and get the multi-source boost.
        Falls back to uppercased display name if no canonical ID exists.
        """
        from collections import defaultdict
        groups = defaultdict(list)

        for c in candidates:
            canon = get_canonical_id(c.provider_name)
            key = canon.upper() if canon else c.provider_name.upper().strip()
            groups[key].append(c)

        deduped = []
        for key, group in groups.items():
            best = max(group, key=lambda c: c.confidence)
            if len(group) > 1:
                # Multiple sources agree — boost confidence
                boost = min(0.10, 0.05 * (len(group) - 1))
                best.confidence = min(0.98, best.confidence + boost)
                # Track agreement in source
                sources = set(c.polygon_source or "" for c in group)
                if len(sources) > 1:
                    best.polygon_source = (best.polygon_source or "") + f" (+{len(group)-1} agree)"
            deduped.append(best)

        return deduped

    def _lookup_type(self, lat: float, lon: float, utility_type: str,
                     address_state: str = "") -> Optional[ProviderResult]:
        """Run spatial query and resolve the best provider for a utility type."""
        polygons = self.spatial.query_point(lat, lon, utility_type)
        if not polygons:
            return None

        if len(polygons) == 1:
            best = polygons[0]
        elif utility_type == "electric":
            # Check if any polygon is in Texas for TDU-specific logic
            has_texas = any(
                (p.get("state", "") or "").upper() == "TX"
                or (p.get("name", "") or "").upper() in self._TDU_PRIORITY
                for p in polygons
            )
            if has_texas:
                best = self._resolve_texas_overlap(polygons)
            else:
                best = self._resolve_overlap_by_customers(polygons)
        elif utility_type == "gas" and len(polygons) > 1 and address_state:
            # Gas: smallest-area-wins but penalize cross-state results
            best = self._resolve_gas_overlap(polygons, address_state)
        elif utility_type == "water" and len(polygons) > 1:
            # Water: prefer smallest-area (city > county > regional)
            best = self._resolve_water_overlap(polygons)
        else:
            best = polygons[0]

        return self.scorer.resolve_provider(
            shapefile_name=best.get("name", ""),
            eia_id=best.get("eia_id"),
            state=best.get("state", ""),
            utility_type=utility_type,
            polygon_source=best.get("source", ""),
            area_km2=best.get("area_km2", 0),
            cntrl_area=best.get("cntrl_area", ""),
            shp_type=best.get("type", ""),
        )

    # Large IOUs whose HIFLD polygons are known to overlap smaller utilities.
    # Co-ops and municipals carved out pockets decades ago that HIFLD draws
    # as one big polygon. When a co-op/municipal candidate exists alongside
    # one of these, the local utility should win.
    _LARGE_IOU_NAMES = {
        # Duke Energy (NC, SC, FL, IN, OH)
        "DUKE ENERGY", "DUKE ENERGY CAROLINAS", "DUKE ENERGY PROGRESS",
        "DUKE ENERGY FLORIDA", "DUKE ENERGY INDIANA", "DUKE ENERGY OHIO",
        # Dominion Energy (VA, SC)
        "DOMINION ENERGY", "DOMINION VIRGINIA POWER",
        "DOMINION ENERGY SOUTH CAROLINA",
        # AEP (OH, TX, WV, VA, IN, MI, KY, TN, OK)
        "AMERICAN ELECTRIC POWER", "AEP",
        "AEP OHIO", "AEP TEXAS", "APPALACHIAN POWER",
        "INDIANA MICHIGAN POWER", "KENTUCKY POWER",
        # Southern Company (GA, AL, MS)
        "SOUTHERN COMPANY", "GEORGIA POWER", "ALABAMA POWER",
        "MISSISSIPPI POWER",
        # Entergy (AR, LA, MS, TX)
        "ENTERGY", "ENTERGY ARKANSAS", "ENTERGY LOUISIANA",
        "ENTERGY MISSISSIPPI", "ENTERGY TEXAS",
        # NextEra / FPL (FL) — OUC, JEA, Gainesville, Lakeland inside
        "NEXTERA ENERGY", "FLORIDA POWER & LIGHT", "FPL",
        "FLORIDA POWER AND LIGHT", "GULF POWER",
        # Xcel Energy (MN, CO, WI, TX) — municipals throughout
        "XCEL ENERGY", "NORTHERN STATES POWER",
        "PUBLIC SERVICE COMPANY OF COLORADO",
        # PG&E (CA) — SMUD, Roseville, Silicon Valley Power inside
        "PACIFIC GAS & ELECTRIC", "PACIFIC GAS AND ELECTRIC", "PG&E",
        # Consumers Energy (MI) — Lansing BWL, other municipals
        "CONSUMERS ENERGY",
        # Eversource (CT, MA, NH) — municipal light depts throughout MA
        "EVERSOURCE", "EVERSOURCE ENERGY",
        # PPL / LG&E-KU (PA, KY) — co-ops in rural areas
        "PPL ELECTRIC", "PPL CORPORATION",
        "LOUISVILLE GAS AND ELECTRIC", "KENTUCKY UTILITIES",
        # PacifiCorp / Rocky Mountain Power (UT, WY, OR, WA)
        "PACIFICORP", "ROCKY MOUNTAIN POWER", "PACIFIC POWER",
        # Ameren (IL, MO)
        "AMEREN", "AMEREN ILLINOIS", "AMEREN MISSOURI",
        # APS / Arizona Public Service
        "APS", "ARIZONA PUBLIC SERVICE",
        # Idaho Power
        "IDAHO POWER",
        # Tampa Electric (TECO)
        "TAMPA ELECTRIC", "TECO ENERGY",
    }

    # Known local utilities that should be promoted over large IOUs.
    # These don't have standard co-op/municipal keywords in their names.
    _LOCAL_UTILITY_NAMES = {
        "ENERGY UNITED", "BRIGHTRIDGE", "JEA",
        "GREER CPW", "GREER COMMISSION OF PUBLIC WORKS",
        "SANTEE COOPER",
        "SECO ENERGY",
        "PEDERNALES ELECTRIC", "PEDERNALES ELECTRIC COOPERATIVE",
        "NEW BRAUNFELS UTILITIES",
        "BRYAN TEXAS UTILITIES",
        "CPS ENERGY",
        "AUSTIN ENERGY",
        "EPB", "EPB CHATTANOOGA",
        "GAINESVILLE REGIONAL UTILITIES",
        "KISSIMMEE UTILITY AUTHORITY",
        "TALQUIN ELECTRIC",
        "COWETA-FAYETTE EMC", "COWETA FAYETTE EMC",
        "CANOOCHEE EMC",
        "SNAPPING SHOALS EMC",
        "WAKE EMC",
        "PIEDMONT EMC", "PIEDMONT ELECTRIC MEMBERSHIP",
        "CENTRAL EMC", "CENTRAL ELECTRIC MEMBERSHIP",
        "LUMBEE RIVER EMC",
        "PEE DEE ELECTRIC",
        "BROAD RIVER ELECTRIC",
        "MID-CAROLINA ELECTRIC", "MID CAROLINA ELECTRIC",
        "NEWBERRY ELECTRIC",
    }

    @staticmethod
    def _is_water_utility_name(name: str) -> bool:
        """Check if a name looks like a real water utility vs a subdivision/street.

        TWDB and other state water GIS data sometimes return subdivision or
        HOA names (e.g., "CROSSBOW COURT", "OAK HOLLOW ESTATES") instead of
        the actual water utility. Real water utilities contain keywords like
        "water", "city of", "utility", "district", "MUD", "WSC", etc.
        """
        if not name:
            return False
        upper = name.upper()
        _WATER_KEYWORDS = (
            "WATER", "CITY OF", "TOWN OF", "VILLAGE OF", "COUNTY",
            "MUNICIPAL", "UTILITY", "UTILITIES", "DISTRICT",
            "MUD", "WSC", "SUD", "PUD", "WCID",
            "AUTHORITY", "COMMISSION", "DEPARTMENT", "DEPT",
            "SERVICE", "SUPPLY", "SYSTEM", "WORKS",
            "COOPERATIVE", "COOP", "CORP", "CORPORATION",
            "IMPROVEMENT", "SPECIAL", "RURAL",
        )
        return any(kw in upper for kw in _WATER_KEYWORDS)

    @classmethod
    def _is_large_iou(cls, name: str) -> bool:
        """Check if a provider name matches a known large IOU."""
        upper = (name or "").upper()
        return any(iou in upper for iou in cls._LARGE_IOU_NAMES)

    @classmethod
    def _resolve_overlap_by_customers(cls, polygons: list) -> dict:
        """
        Resolve overlapping polygons using customer-weighted hybrid scoring.

        Key rule: co-ops and municipals with small areas beat large IOUs
        (Duke, Dominion, etc.) whose HIFLD polygons are overgeneralized.
        """
        if not polygons:
            return polygons[0] if polygons else {}

        # First: check for co-op/municipal vs large IOU overlap
        coops_munis = []
        large_ious = []
        for p in polygons:
            ptype = (p.get("type", "") or "").upper()
            name = p.get("name", "") or ""
            if "COOPERATIVE" in ptype or "MUNICIPAL" in ptype:
                coops_munis.append(p)
            elif cls._is_large_iou(name):
                large_ious.append(p)

        # Co-op/municipal with reasonable area beats large IOU
        if coops_munis and large_ious:
            specific = [c for c in coops_munis if (c.get("area_km2", 0) or 0) < 5000]
            if specific:
                return specific[0]

        def _score(p):
            customers = p.get("customers", 0) or 0
            area = p.get("area_km2", 0) or 1
            ptype = (p.get("type", "") or "").upper()
            name = p.get("name", "") or ""

            if customers == 0:
                if area < 5000:
                    score = 10_000_000.0 / area
                else:
                    score = 100.0 / area
            else:
                score = float(customers)

            # Genuine local utility: small area + real customers
            if area < 1000 and customers > 1000:
                score *= 2.0
            elif area < 5000 and customers > 50000:
                score *= 1.5

            # Real city utility (Austin Energy 533K, CPS Energy 918K, OUC 269K)
            if "MUNICIPAL" in ptype and customers > 50000:
                score *= 1.5

            # Co-ops: boost small, penalize large
            if "COOPERATIVE" in ptype:
                if area < 3000:
                    score *= 1.5  # Genuine local co-op
                elif area > 10000:
                    score *= 0.3  # Overgeneralized

            elif "NOT AVAILABLE" in ptype and area > 10000:
                score *= 0.2

            # Large IOU penalty when competing with others
            if cls._is_large_iou(name) and len(polygons) > 1:
                score *= 0.5

            # Overgeneralized federal/regional (WAPA 1.5M km², BPA, etc.)
            if area > 50000:
                score *= 0.1
            elif area > 20000 and customers < 10000:
                score *= 0.3

            if "POLITICAL" in ptype and customers < 100 and customers > 0:
                score *= 0.1

            return score

        polygons.sort(key=_score, reverse=True)
        return polygons[0]

    @staticmethod
    def _resolve_gas_overlap(polygons: list, address_state: str) -> dict:
        """Resolve gas overlaps with state-match preference.

        Gas utilities almost never serve areas outside their listed state.
        Penalize cross-state candidates so same-state smallest-area wins.
        """
        if not polygons:
            return {}
        addr_st = (address_state or "").upper()
        if not addr_st:
            return polygons[0]  # No state info, fall back to smallest-area

        # Partition into same-state and cross-state
        same_state = [p for p in polygons if (p.get("state", "") or "").upper() == addr_st]
        if same_state:
            # Return smallest-area same-state polygon (already sorted by area)
            return same_state[0]
        # No same-state match — return smallest-area overall
        return polygons[0]

    @staticmethod
    def _resolve_water_overlap(polygons: list) -> dict:
        """Resolve overlapping water polygons — smallest area wins.

        Water systems are nested: city systems inside county systems inside
        regional authorities. The smallest polygon is almost always the
        actual provider for that address.
        """
        if not polygons:
            return {}
        # Sort by area ascending — smallest wins
        polygons.sort(key=lambda p: p.get("area_km2", 0) or float("inf"))
        return polygons[0]

    def _resolve_texas_overlap(self, polygons: list) -> dict:
        """
        Resolve overlapping electric polygons with Texas-specific TDU priority.

        Rules:
        1. Co-ops/municipals win ONLY if their polygon is smaller than the
           smallest TDU polygon at this point. Large rural co-op polygons
           (e.g. Hilco 12K km²) overlap urban TDU areas due to HIFLD
           generalization — they should NOT win over the actual TDU.
           True co-op/municipal service areas (e.g. CPS Energy 1.5K km²)
           are smaller than any TDU and will correctly win.
        2. Among TDUs, use _TDU_PRIORITY ranking (lower number = higher priority)
        3. For non-Texas or single-polygon results, fall back to smallest-area-wins
        """
        if len(polygons) == 1:
            return polygons[0]

        # Separate co-ops/municipals from investor-owned (TDUs)
        coops_munis = []
        tdus = []
        others = []

        for p in polygons:
            ptype = (p.get("type", "") or "").upper()
            if "COOPERATIVE" in ptype or "MUNICIPAL" in ptype:
                coops_munis.append(p)
            else:
                name_upper = (p.get("name", "") or "").upper()
                if name_upper in self._TDU_PRIORITY:
                    tdus.append(p)
                else:
                    others.append(p)

        # Rule 1: Co-ops/municipals win only if genuinely specific (small area).
        # Real municipal service areas: CPS Energy (San Antonio) = 1,557 km²,
        #   Austin Energy = 830 km², Lubbock P&L = 350 km².
        # Overgeneralized rural co-ops: Hilco = 12,020 km², Trinity Valley = 14,204 km².
        # Threshold: 5,000 km² separates real local boundaries from HIFLD artifacts.
        _COOP_AREA_THRESHOLD_KM2 = 5000

        if coops_munis and tdus:
            specific_coops = [c for c in coops_munis
                              if c.get("area_km2", float("inf")) < _COOP_AREA_THRESHOLD_KM2]
            if specific_coops:
                return specific_coops[0]
            # All co-op polygons are too large — fall through to TDU priority
        elif coops_munis and not tdus:
            # Only co-ops/municipals, no TDUs — co-op wins (smallest first)
            return coops_munis[0]

        # Rule 2: Among TDUs, use priority ranking
        if tdus:
            tdus.sort(key=lambda p: self._TDU_PRIORITY.get(
                (p.get("name", "") or "").upper(), 99
            ))
            return tdus[0]

        # Rule 3: Fall back to smallest area
        return polygons[0]
