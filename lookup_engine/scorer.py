"""Ensemble confidence scoring for utility provider lookups."""

import importlib.util
import json
import logging
import re
from pathlib import Path
from typing import Optional

from .config import Config
from .models import ProviderResult

# Load provider_normalizer from project root (not a package member of lookup_engine)
_pn_path = Path(__file__).parent.parent / "provider_normalizer.py"
_pn_spec = importlib.util.spec_from_file_location("provider_normalizer", _pn_path)
_pn_mod = importlib.util.module_from_spec(_pn_spec)
_pn_spec.loader.exec_module(_pn_mod)
normalize_provider_verbose = _pn_mod.normalize_provider_verbose
get_canonical_id = _pn_mod.get_canonical_id
get_parent_company = _pn_mod.get_parent_company
is_deregulated_rep = _pn_mod.is_deregulated_rep
_PROVIDER_DATA = _pn_mod._PROVIDER_DATA
_CANONICAL_TO_DISPLAY = _pn_mod._CANONICAL_TO_DISPLAY

logger = logging.getLogger(__name__)

# Base confidence by match method
_BASE_CONFIDENCE = {
    "tenant_verified": 0.95,
    "eia_id": 0.90,
    "exact": 0.85,
    "fuzzy": 0.75,
    "passthrough": 0.60,
    "none": 0.0,
}

# Suffixes to strip for passthrough display names
_STRIP_SUFFIXES = [
    ", INC.", ", INC", " INC.", " INC",
    ", CORP.", ", CORP", " CORP.", " CORP",
    ", LLC", " LLC", ", L.L.C.", " L.L.C.",
    ", L.P.", " L.P.",
    " COMPANY", " CO.", " CO",
]


class EnsembleScorer:
    """Scores provider lookup results using multiple evidence sources."""

    def __init__(self, config: Config):
        self.config = config
        # Build EIA ID -> canonical_id index
        self._eia_to_canonical = {}
        for canon_key, entry in _PROVIDER_DATA.items():
            if isinstance(entry, dict) and "eia_id" in entry:
                eia = entry["eia_id"]
                if isinstance(eia, (int, float)):
                    self._eia_to_canonical[int(eia)] = canon_key
                elif isinstance(eia, str) and eia.isdigit():
                    self._eia_to_canonical[int(eia)] = canon_key
        logger.info(f"Scorer: {len(self._eia_to_canonical)} EIA ID mappings loaded")

        # Load provider contact info (phone, website)
        self._provider_contacts = {}
        _contacts_path = Path(__file__).parent.parent / "data" / "provider_contacts.json"
        if _contacts_path.exists():
            with open(_contacts_path) as _f:
                self._provider_contacts = json.load(_f)
            logger.info(f"Scorer: {len(self._provider_contacts)} provider contact records loaded")

        # Build lowercase index for fallback name matching
        self._contacts_by_lower = {k.lower(): v for k, v in self._provider_contacts.items()}

        # Build state-detection index for canonical entries (used to reject cross-state fuzzy matches)
        self._canonical_states = {}  # canon_key -> set of state abbrevs detected in name/aliases
        _STATE_ABBREVS = {
            'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY',
            'LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND',
            'OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC',
        }
        for canon_key, entry in _PROVIDER_DATA.items():
            if not isinstance(entry, dict):
                continue
            text = canon_key + ' ' + entry.get('display_name', '') + ' ' + ' '.join(entry.get('aliases', []))
            found = set()
            for st in _STATE_ABBREVS:
                # Match state abbrev as a word boundary (avoid matching 'IN' inside 'INDIANA' etc.)
                if re.search(r'(?:^|[\s,\-(])' + st + r'(?:$|[\s,\-).)])', text.upper()):
                    found.add(st)
            if found:
                self._canonical_states[canon_key] = found

    def resolve_provider(
        self,
        shapefile_name: str,
        eia_id=None,
        state: str = "",
        utility_type: str = "electric",
        polygon_source: str = "",
        area_km2: float = 0.0,
        cntrl_area: str = "",
        shp_type: str = "",
    ) -> ProviderResult:
        """
        Resolve a shapefile provider name to a canonical ProviderResult.

        Resolution order:
        1. EIA ID match
        2. Exact/fuzzy name match via normalize_provider_verbose
        3. Passthrough (clean up name)
        """
        # Water providers: skip normalization — canonical_providers.json only has
        # electric/gas utilities. Fuzzy matching water system names like
        # "MANHATTAN, CITY OF" against electric utilities produces false matches.
        # Confidence 0.82: CWS shapefile polygon intersection is reliable (44K records),
        # but no canonical normalization means name quality varies.
        if utility_type == "water":
            clean_name = self._clean_passthrough(shapefile_name)
            pr = ProviderResult(
                provider_name=clean_name,
                canonical_id=None,
                eia_id=None,
                utility_type=utility_type,
                confidence=min(0.82, self.config.max_confidence),
                match_method="passthrough",
                is_deregulated=False,
                polygon_source=polygon_source,
            )
            self._attach_contact_info(pr)
            return pr

        # 1. Try EIA ID match
        if eia_id is not None:
            try:
                eia_int = int(str(eia_id).split(".")[0]) if eia_id else None
            except (ValueError, TypeError):
                eia_int = None

            if eia_int and eia_int in self._eia_to_canonical:
                canon_key = self._eia_to_canonical[eia_int]
                display = _CANONICAL_TO_DISPLAY.get(canon_key, canon_key)
                is_dereg = self._is_deregulated(shapefile_name, cntrl_area, shp_type)
                pr = ProviderResult(
                    provider_name=display,
                    canonical_id=canon_key,
                    eia_id=eia_int,
                    utility_type=utility_type,
                    confidence=min(_BASE_CONFIDENCE["eia_id"], self.config.max_confidence),
                    match_method="eia_id",
                    is_deregulated=is_dereg,
                    deregulated_note=self._dereg_note(shapefile_name) if is_dereg else None,
                    polygon_source=polygon_source,
                )
                self._attach_contact_info(pr, canon_key)
                return pr

        # 2. Name match via normalizer
        result = normalize_provider_verbose(shapefile_name)
        if result["matched"]:
            match_type = result["match_type"]  # "exact", "fuzzy", or "substring"
            similarity = result.get("similarity", 0)

            # Require high similarity for fuzzy matches on shapefile names
            # (shapefile names are formal/legal, prone to false fuzzy matches)
            if match_type == "fuzzy" and similarity < 90:
                pass  # Fall through to passthrough
            elif match_type == "fuzzy" and similarity < 95 and state:
                # State-aware rejection: if the polygon has a known state and the
                # canonical entry is clearly from a different state, reject the fuzzy match.
                # Prevents cross-state false matches like "PUBLIC SERVICE CO OF NH" → PNM (NM)
                # or "CITY OF MONROE CITY - (MO)" → "City of Monroe - NC".
                canon_key = result["canonical_id"]
                canon_states = self._canonical_states.get(canon_key, set())
                poly_state = state.upper().strip()
                if canon_states and poly_state and poly_state not in canon_states:
                    pass  # Cross-state mismatch — fall through to passthrough
                elif not canon_states:
                    pass  # No state info on canonical entry — too risky for 90-95% fuzzy
                else:
                    base_conf = _BASE_CONFIDENCE.get(match_type, 0.75)
                    canon_key = result["canonical_id"]
                    display = result["display_name"]
                    entry = _PROVIDER_DATA.get(canon_key, {})
                    matched_eia = entry.get("eia_id") if isinstance(entry, dict) else None
                    if isinstance(matched_eia, float):
                        matched_eia = int(matched_eia)
                    is_dereg = self._is_deregulated(shapefile_name, cntrl_area, shp_type)
                    pr = ProviderResult(
                        provider_name=display,
                        canonical_id=canon_key,
                        eia_id=matched_eia,
                        utility_type=utility_type,
                        confidence=min(base_conf, self.config.max_confidence),
                        match_method=match_type,
                        is_deregulated=is_dereg,
                        deregulated_note=self._dereg_note(shapefile_name) if is_dereg else None,
                        polygon_source=polygon_source,
                    )
                    self._attach_contact_info(pr, canon_key)
                    return pr
            else:
                base_conf = _BASE_CONFIDENCE.get(match_type, 0.75)
                canon_key = result["canonical_id"]
                display = result["display_name"]

                # Look up EIA ID from canonical data
                entry = _PROVIDER_DATA.get(canon_key, {})
                matched_eia = entry.get("eia_id") if isinstance(entry, dict) else None
                if isinstance(matched_eia, float):
                    matched_eia = int(matched_eia)

                is_dereg = self._is_deregulated(shapefile_name, cntrl_area, shp_type)
                pr = ProviderResult(
                    provider_name=display,
                    canonical_id=canon_key,
                    eia_id=matched_eia,
                    utility_type=utility_type,
                    confidence=min(base_conf, self.config.max_confidence),
                    match_method=match_type,
                    is_deregulated=is_dereg,
                    deregulated_note=self._dereg_note(shapefile_name) if is_dereg else None,
                    polygon_source=polygon_source,
                )
                self._attach_contact_info(pr, canon_key)
                return pr

        # 3. Passthrough — clean up the shapefile name
        clean_name = self._clean_passthrough(shapefile_name)
        is_dereg = self._is_deregulated(shapefile_name, cntrl_area, shp_type)
        pr = ProviderResult(
            provider_name=clean_name,
            canonical_id=None,
            eia_id=None,
            utility_type=utility_type,
            confidence=min(_BASE_CONFIDENCE["passthrough"], self.config.max_confidence),
            match_method="passthrough",
            is_deregulated=is_dereg,
            deregulated_note=self._dereg_note(shapefile_name) if is_dereg else None,
            polygon_source=polygon_source,
        )
        self._attach_contact_info(pr)
        return pr

    def _attach_contact_info(self, pr: ProviderResult, canon_key: str = None):
        """Attach phone and website from provider_contacts.json.

        Uses the 'label' field in contact entries to prefer type-matching
        contacts (e.g., electric contact for electric lookups) and avoid
        cross-contamination (e.g., water phone on an electric provider).
        """
        utype = (pr.utility_type or "").lower()

        def _is_type_match(entry):
            label = (entry.get("label") or "").lower()
            return not label or label == utype

        def _find_contact(key):
            """Find best contact for key, preferring type-matched entries."""
            entry = self._provider_contacts.get(key)
            if entry and _is_type_match(entry):
                return entry
            return None

        contact = None
        # 1. Try canonical key
        if canon_key:
            contact = _find_contact(canon_key)
        # 2. Try provider display name
        if not contact:
            contact = _find_contact(pr.provider_name)
        # 3. Try lowercase fallback
        if not contact:
            entry = self._contacts_by_lower.get(pr.provider_name.lower())
            if entry and _is_type_match(entry):
                contact = entry
        # 4. Try type-suffixed variants (e.g., "Oncor Electric-TX" for electric)
        if not contact and utype:
            _TYPE_SUFFIXES = {
                "electric": ["Electric", "Electric-", "Power"],
                "gas": ["Gas", "Energy"],
                "water": ["Water", "Water Utility", "Water Utilities"],
                "sewer": ["Sewer", "Wastewater"],
            }
            for suffix in _TYPE_SUFFIXES.get(utype, []):
                for variant in [f"{pr.provider_name} {suffix}", f"{pr.provider_name}-{suffix}"]:
                    entry = self._provider_contacts.get(variant)
                    if not entry:
                        entry = self._contacts_by_lower.get(variant.lower())
                    if entry:
                        contact = entry
                        break
                if contact:
                    break
        # 5. Last resort: accept any entry even if type doesn't match (better than nothing)
        if not contact:
            if canon_key:
                contact = self._provider_contacts.get(canon_key)
            if not contact:
                contact = self._provider_contacts.get(pr.provider_name)
            if not contact:
                contact = self._contacts_by_lower.get(pr.provider_name.lower())
            # But reject entries matched via substring that are clearly wrong type
            if contact and contact.get("match_method") == "substring":
                label = (contact.get("label") or "").lower()
                if label and label != utype:
                    contact = None  # Reject cross-type substring match

        if contact:
            pr.phone = contact.get('phone') or None
            pr.website = contact.get('url') or None

    def boost_with_tenant(self, result: ProviderResult) -> ProviderResult:
        """Boost confidence if tenant-verified data agrees."""
        result.confidence = min(0.98, result.confidence + 0.08)
        result.match_method = "tenant_verified+" + result.match_method
        return result

    def _is_deregulated(self, shapefile_name: str, cntrl_area: str, shp_type: str) -> bool:
        """Check if this is an ERCOT deregulated territory.
        
        Co-ops and municipals in ERCOT are NOT deregulated (exempt).
        Only investor-owned TDUs are deregulated.
        """
        name_upper = (shapefile_name or "").upper()
        type_upper = (shp_type or "").upper()
        cntrl_upper = (cntrl_area or "").upper()

        # Co-ops and municipals are never deregulated
        if "COOPERATIVE" in type_upper or "MUNICIPAL" in type_upper:
            # Lubbock special case — municipal but deregulated since 2024
            if "LUBBOCK" in name_upper and self.config.lubbock_deregulated:
                return True
            return False

        # Check if this is a known TDU
        for tdu in self.config.ercot_tdu_names:
            if tdu.upper() in name_upper or name_upper in tdu.upper():
                return True

        # Check ERCOT control area for investor-owned utilities
        if cntrl_upper in ("ERCO", "ERCOT") and "INVESTOR" in type_upper:
            return True

        return False

    def _dereg_note(self, shapefile_name: str) -> str:
        clean = self._clean_passthrough(shapefile_name)
        return f"Address is in {clean} TDU territory. Tenant chooses their Retail Electric Provider (REP)."

    def _clean_passthrough(self, name: str) -> str:
        """Strip legal suffixes and title-case for display."""
        if not name:
            return ""
        clean = name.strip()
        upper = clean.upper()
        for suf in _STRIP_SUFFIXES:
            if upper.endswith(suf.upper()):
                clean = clean[: -len(suf)].strip()
                upper = clean.upper()
        # Title case if all-caps
        if clean == clean.upper() and len(clean) > 3:
            clean = clean.title()
        return clean
