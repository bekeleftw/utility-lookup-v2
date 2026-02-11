"""Data models for the lookup engine."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class GeocodedAddress:
    lat: float
    lon: float
    confidence: float
    formatted_address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    county: str = ""
    block_geoid: str = ""


@dataclass
class ProviderResult:
    provider_name: str
    canonical_id: Optional[str] = None
    eia_id: Optional[int] = None
    utility_type: str = "electric"
    confidence: float = 0.0
    match_method: str = "none"
    is_deregulated: bool = False
    deregulated_note: Optional[str] = None
    polygon_source: Optional[str] = None
    needs_review: bool = False
    alternatives: List[Dict] = field(default_factory=list)
    catalog_id: Optional[int] = None
    catalog_title: Optional[str] = None
    id_match_score: int = 0
    id_confident: bool = False
    phone: Optional[str] = None
    website: Optional[str] = None


@dataclass
class LookupResult:
    address: str
    lat: float = 0.0
    lon: float = 0.0
    geocode_confidence: float = 0.0
    electric: Optional[ProviderResult] = None
    gas: Optional[ProviderResult] = None
    water: Optional[ProviderResult] = None
    sewer: Optional[ProviderResult] = None
    trash: Optional[ProviderResult] = None
    internet: Optional[Dict] = None
    lookup_time_ms: int = 0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        """Serialize to dict for JSON output."""
        def _pr(pr):
            if pr is None:
                return None
            return {
                "provider_name": pr.provider_name,
                "canonical_id": pr.canonical_id,
                "eia_id": pr.eia_id,
                "utility_type": pr.utility_type,
                "confidence": round(pr.confidence, 3),
                "match_method": pr.match_method,
                "is_deregulated": pr.is_deregulated,
                "deregulated_note": pr.deregulated_note,
                "polygon_source": pr.polygon_source,
                "needs_review": pr.needs_review,
                "alternatives": pr.alternatives,
                "catalog_id": pr.catalog_id,
                "catalog_title": pr.catalog_title,
                "id_match_score": pr.id_match_score,
                "id_confident": pr.id_confident,
                "phone": pr.phone,
                "website": pr.website,
            }
        return {
            "address": self.address,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "geocode_confidence": round(self.geocode_confidence, 3),
            "electric": _pr(self.electric),
            "gas": _pr(self.gas),
            "water": _pr(self.water),
            "sewer": _pr(self.sewer),
            "trash": _pr(self.trash),
            "internet": self.internet,
            "lookup_time_ms": self.lookup_time_ms,
            "timestamp": self.timestamp,
        }
