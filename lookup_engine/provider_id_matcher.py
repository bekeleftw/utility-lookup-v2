"""Provider ID matching — fuzzy-matches engine provider names to internal catalog IDs."""

import csv
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


class ProviderIDMatcher:
    """Matches engine provider names to internal catalog IDs."""

    TYPE_MAP = {
        "electric": "2",
        "gas": "4",
        "water": "3",
        "sewer": "6",
        "trash": "5",
        "internet": "8",
    }

    def __init__(self, catalog_path: str = None):
        if catalog_path is None:
            catalog_path = str(Path(__file__).parent.parent / "data" / "provider_catalog.csv")
        self.catalog: List[Dict] = []
        self.by_type: Dict[str, List] = {}
        self._id_overrides: Dict[str, Dict] = {}  # (norm_name, type) -> entry
        self._load_catalog(catalog_path)
        self._build_index()
        self._load_id_overrides()

    def _load_catalog(self, path: str):
        p = Path(path)
        if not p.exists():
            logger.warning(f"Provider catalog not found: {path}")
            return
        with open(p, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    int(row["ID"])
                    if row.get("UtilityTypeId", "") not in ("2", "3", "4", "5", "6", "7", "8"):
                        continue
                except (ValueError, KeyError):
                    continue

                self.catalog.append({
                    "id": int(row["ID"]),
                    "type_id": row["UtilityTypeId"],
                    "title": row["Title"].strip(),
                    "url": row.get("URL", ""),
                    "phone": row.get("Phone", ""),
                    "source": row.get("Source", ""),
                    "normalized": self._normalize(row["Title"].strip()),
                })
        logger.info(f"Provider catalog: {len(self.catalog)} entries loaded")

    # Common abbreviation -> full name aliases
    ALIASES = {
        "sce": "southern california edison",
        "socalgaz": "southern california gas",
        "socalgas": "southern california gas",
        "sdg&e": "san diego gas electric",
        "sdge": "san diego gas electric",
        "pg&e": "pg e",
        "pge": "pg e",
        "pse&g": "pse g",
        "pseg": "pse g",
        "cemc": "cumberland electric membership",
        "comed": "comed",
        "lge": "louisville gas electric",
        "lg&e": "louisville gas electric",
        "bge": "baltimore gas electric",
        "dte": "dte energy",
        "aps": "arizona public service",
        "tep": "tucson electric power",
        "nstar": "eversource",
        "rge": "rochester gas electric",
        "rg&e": "rochester gas electric",
        "nyseg": "new york state electric gas",
        "jcp&l": "jersey central power light",
        "jcpl": "jersey central power light",
        "pepco": "potomac electric power",
        "eastohiogas": "enbridge gas ohio",
        "eastohiogascodominion": "enbridge gas ohio",
        "dominioneastohio": "enbridge gas ohio",
        "sceg": "dominion energy south carolina",
        "sce&g": "dominion energy south carolina",
        "srp": "salt river project",
        "ladwp": "los angeles department of water power",
        "tnmp": "texas new mexico power",
        "lge ku": "louisville gas electric",
        "lgeku": "louisville gas electric",
        "lg&e/ku": "louisville gas electric",
        "chelco": "choctawhatchee electric cooperative",
    }

    @staticmethod
    def _normalize(title: str) -> str:
        """Normalize a provider title for matching."""
        t = title.lower().strip()
        # Expand common HIFLD truncations
        t = t.replace(" elec ", " electric ")
        if t.endswith(" elec"):
            t = t[:-5] + " electric"
        t = t.replace("elec member", "electric membership")
        t = t.replace(" coop", " cooperative")
        t = t.replace(" pwr ", " power ")
        if t.endswith(" pwr"):
            t = t[:-4] + " power"
        t = t.replace(" svc ", " service ").replace(" svcs ", " services ")
        t = t.replace(" util ", " utilities ").replace(" utils ", " utilities ")
        # Rebrands
        t = t.replace("east ohio gas", "enbridge gas ohio")
        t = t.replace("dominion east ohio", "enbridge gas ohio")
        # HIFLD shapefile territory names -> canonical names
        t = t.replace("little rock pine bluff", "entergy arkansas")
        t = t.replace("cheyenne light fuel power", "black hills energy")
        t = t.replace("cheyenne light fuel & power", "black hills energy")
        if "jones" in t and "onslow" in t and ("emc" in t or "electric" in t):
            t = "jones onslow electric membership"
        if "intermountain gas" in t:
            t = "intermountain gas"
        if "upper cumberland e m c" in t or "upper cumberland emc" in t:
            t = "upper cumberland electric membership"
        if "wisconsin rapids waterworks" in t:
            t = "wisconsin rapids water works lighting commission"
        # Water: EPA/SDWIS system names -> catalog names
        if "philadelphia water" in t:
            t = "city of philadelphia"
        if "citizens water" in t and "indianapolis" in t:
            t = "citizens energy"
        if "fort wayne" in t and "3 rivers" in t:
            t = "fort wayne city utilities"
        if "pittsburgh" in t and ("w and s" in t or "water" in t and "sewer" in t):
            t = "pittsburgh water sewer authority"
        if "sarasota" in t and "special" in t:
            t = "sarasota county water"
        if "augusta" in t and "richmond" in t:
            t = "augusta utility"
        if "north las vegas" in t and ("util" in t or "water" in t):
            t = "city of north las vegas"
        if "cal am water" in t or "cal american water" in t:
            t = "california american water"
        if "acsa" in t and "urban" in t:
            t = "albemarle county service authority"
        if "okaloosa" in t and ("wtr" in t or "water" in t):
            t = "okaloosa county water sewer"
        if "global water" in t and "santa cruz" in t:
            t = "global water resources"
        if "west view" in t and ("muni" in t or "auth" in t):
            t = "west view water authority"
        if "saws" in t:
            t = "san antonio water system"
        if "charles county" in t and "dpw" in t:
            t = "charles county department of public works"
        if "greer cpw" in t or ("greer" in t and "commission" in t):
            t = "greer commission of public works"
        if "pwcsa" in t:
            t = "prince william water"
        if "coachella" in t and ("vwd" in t or "valley" in t):
            t = "coachella valley water district"
        if "elsinore" in t and ("mwd" in t or "valley" in t):
            t = "elsinore valley municipal water district"
        if "skagit" in t and ("pud" in t or "county" in t):
            t = "skagit public utility district"
        if "goforth" in t and "sud" in t:
            t = "goforth special utility district"
        if "consolidated mutual" in t:
            t = "consolidated mutual water"
        if "smyrna" in t and "natural gas" in t:
            t = "smyrna utilities department"
        if "rio grande valley gas" in t:
            t = "rio grande valley gas"
        # Water: American Water state abbreviations
        # Matches both "Mo American Water Co" and "Mo American St Louis St Charles Counties"
        _aw_abbrevs = {
            "mo ": "missouri ", "pa ": "pennsylvania ", "in ": "indiana ",
            "wv ": "west virginia ", "tn ": "tennessee ", "il ": "illinois ",
            "ia ": "iowa ", "nj ": "new jersey ", "va ": "virginia ",
            "ca ": "california ", "ky ": "kentucky ", "md ": "maryland ",
        }
        if "amer" in t:
            # Expand "amer" → "american" first
            t = re.sub(r'\bamer\b', 'american', t)
            for abbrev, full in _aw_abbrevs.items():
                if t.startswith(abbrev) and "american" in t[len(abbrev):len(abbrev)+12]:
                    # Strip district/city suffixes first
                    t = re.sub(r'\s+(pittsburgh|st louis|st charles|chattanooga|southeast|northwest|monterey)[\w\s]*$', '', t)
                    # Normalize to "[State] American Water"
                    t = full + "american water"
                    break
        # Water: Charlotte-Mecklenburg → Charlotte Water
        if "charlotte" in t and "mecklenburg" in t:
            t = "charlotte water"
        if "winston" in t and "salem" in t and ("water" in t or "city" in t):
            t = "city of winston salem"
        # Water: EPA system name patterns
        if "chaparral city water" in t:
            t = "epcor water arizona"
        if "az water co" in t or "arizona water co" in t:
            t = "epcor water arizona"
        # Check aliases first (exact match on lowered input)
        alias_key = t.replace("&", "").replace("-", "").replace(" ", "").strip()
        for ak, av in ProviderIDMatcher.ALIASES.items():
            if alias_key == ak.replace("&", "").replace(" ", ""):
                t = av
                break
        # Remove state suffixes: "- TX", "- OH", "(OH)", " - (TX)"
        t = re.sub(r"\s*[-–]\s*\(?[A-Z]{2}\)?\s*$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*\([A-Z]{2}\)\s*$", "", t, flags=re.IGNORECASE)
        # Remove common suffixes
        for suffix in [" corporation", " corp", " inc", " llc", " co-op", " co op",
                       " company", " electric delivery"]:
            if t.endswith(suffix):
                t = t[: -len(suffix)]
        # Normalize whitespace and punctuation
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _build_index(self):
        self.by_type = {}
        for entry in self.catalog:
            tid = entry["type_id"]
            if tid not in self.by_type:
                self.by_type[tid] = []
            self.by_type[tid].append((entry["normalized"], entry))
        for tid, entries in self.by_type.items():
            logger.debug(f"  Catalog type {tid}: {len(entries)} entries")

    def _load_id_overrides(self):
        """Load mapper ID corrections from corrections.db if available."""
        import sqlite3
        db_path = Path(__file__).parent.parent / "data" / "corrections.db"
        if not db_path.exists():
            return
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='id_mapping_corrections'"
            )
            if not cursor.fetchone():
                conn.close()
                return
            rows = conn.execute(
                "SELECT engine_provider_name, utility_type, correct_catalog_id FROM id_mapping_corrections"
            ).fetchall()
            conn.close()
            for row in rows:
                key = (self._normalize(row["engine_provider_name"]), row["utility_type"])
                # Find the catalog entry for this ID
                for entry in self.catalog:
                    if entry["id"] == row["correct_catalog_id"]:
                        self._id_overrides[key] = entry
                        break
            if self._id_overrides:
                logger.info(f"ID overrides: {len(self._id_overrides)} mapper corrections loaded")
        except Exception as e:
            logger.debug(f"Could not load ID overrides: {e}")

    def match(self, provider_name: str, utility_type: str, state: str = None) -> Optional[Dict]:
        """
        Match a provider name to a catalog entry.

        Returns dict with id, title, url, phone, match_score, match_method, confident
        or None if no match found.
        """
        if not provider_name:
            return None

        type_id = self.TYPE_MAP.get(utility_type)
        if not type_id or type_id not in self.by_type:
            return None

        normalized_input = self._normalize(provider_name)

        # Step 0: Check mapper ID overrides
        override_key = (normalized_input, utility_type)
        if override_key in self._id_overrides:
            entry = self._id_overrides[override_key]
            return self._result(entry, 100, "override")

        candidates = self.by_type[type_id]

        # Step 1: Exact match on normalized name
        for norm, entry in candidates:
            if norm == normalized_input:
                return self._result(entry, 100, "exact")

        # Step 2: State-specific match (prefer entries with matching state suffix)
        if state:
            state_matches = []
            for norm, entry in candidates:
                title_upper = entry["title"].upper()
                title_words = re.findall(r'[A-Z]+', title_upper)
                if state.upper() in title_words:
                    score = fuzz.token_sort_ratio(normalized_input, norm)
                    if score >= 70:
                        state_matches.append((score, entry))
            if state_matches:
                state_matches.sort(key=lambda x: x[0], reverse=True)
                best_score, best_entry = state_matches[0]
                return self._result(best_entry, best_score, "state_specific")

        # Step 3: Fuzzy match across all entries of this type
        names = [c[0] for c in candidates]

        # Try token_sort_ratio first (stricter)
        result = process.extractOne(
            normalized_input, names,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=82,
        )
        if result:
            matched_name, score, idx = result
            entry = candidates[idx][1]
            return self._result(entry, int(score), "fuzzy")

        # Fallback: token_set_ratio (handles "Duke Energy Carolinas" -> "Duke Energy")
        result = process.extractOne(
            normalized_input, names,
            scorer=fuzz.token_set_ratio,
            score_cutoff=90,
        )
        if result:
            matched_name, score, idx = result
            entry = candidates[idx][1]
            return self._result(entry, int(score), "fuzzy_set")

        return None

    @staticmethod
    def _result(entry, score, method):
        return {
            "id": entry["id"],
            "title": entry["title"],
            "url": entry["url"],
            "phone": entry["phone"],
            "match_score": score,
            "match_method": method,
            "confident": score >= 85,
        }

    def match_all_candidates(self, candidates: list, utility_type: str, state: str = None) -> List[Dict]:
        """Match all candidates, return list with IDs attached."""
        results = []
        for c in candidates:
            match = self.match(c.get("provider", ""), utility_type, state)
            result = {**c}
            if match:
                result["catalog_id"] = match["id"]
                result["catalog_title"] = match["title"]
                result["catalog_url"] = match["url"]
                result["catalog_phone"] = match["phone"]
                result["id_match_score"] = match["match_score"]
                result["id_match_method"] = match["match_method"]
                result["id_confident"] = match["confident"]
            else:
                result["catalog_id"] = None
                result["catalog_title"] = None
                result["id_match_score"] = 0
                result["id_match_method"] = "none"
                result["id_confident"] = False
            results.append(result)
        return results

    @property
    def loaded(self) -> bool:
        return len(self.catalog) > 0
