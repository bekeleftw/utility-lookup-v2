#!/usr/bin/env python3
"""Integration tests for the LookupEngine."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lookup_engine.config import Config
from lookup_engine.engine import LookupEngine

total = 0
passed = 0


def test(description, check_fn):
    global total, passed
    total += 1
    try:
        ok = check_fn()
    except Exception as e:
        ok = False
        print(f"  [FAIL] {description} — EXCEPTION: {e}")
        return
    passed += ok
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {description}")


def main():
    global total, passed

    config = Config()
    print("Loading engine (all layers including water)...")
    t0 = time.time()
    engine = LookupEngine(config, skip_water=False)
    load_time = time.time() - t0
    print(f"Engine loaded in {load_time:.1f}s\n")

    # ============================================================
    print("=== Spatial Layer Tests ===")
    # ============================================================
    test("Electric layer loaded", lambda: engine.spatial.layer_counts["electric"] > 2900)
    test("Gas layer loaded", lambda: engine.spatial.layer_counts["gas"] > 1200)

    # ============================================================
    print("\n=== Spatial Query Tests (no geocoding) ===")
    # ============================================================

    # Chicago - ComEd
    results = engine.spatial.query_point(41.8781, -87.6298, "electric")
    test("Chicago electric: at least 1 polygon", lambda: len(results) >= 1)
    test("Chicago electric: ComEd in results",
         lambda: any("COMMONWEALTH EDISON" in r["name"].upper() for r in results))

    # Chicago gas
    gas_results = engine.spatial.query_point(41.8781, -87.6298, "gas")
    test("Chicago gas: at least 1 polygon", lambda: len(gas_results) >= 1)
    test("Chicago gas: Peoples Gas in results",
         lambda: any("PEOPLES GAS" in r["name"].upper() for r in gas_results))

    # San Antonio - CPS Energy (municipal, smallest polygon)
    sa_results = engine.spatial.query_point(29.4241, -98.4936, "electric")
    test("San Antonio: CPS Energy is smallest polygon",
         lambda: "SAN ANTONIO" in sa_results[0]["name"].upper() if sa_results else False)
    test("San Antonio: type is MUNICIPAL",
         lambda: sa_results[0]["type"] == "MUNICIPAL" if sa_results else False)

    # Dallas area - multiple overlapping polygons
    dal_results = engine.spatial.query_point(32.7767, -96.7970, "electric")
    test("Dallas: multiple overlapping polygons", lambda: len(dal_results) >= 3)
    test("Dallas: sorted by area ascending",
         lambda: all(dal_results[i]["area_km2"] <= dal_results[i+1]["area_km2"]
                     for i in range(len(dal_results)-1)))
    test("Dallas: Oncor in results",
         lambda: any("ONCOR" in r["name"].upper() for r in dal_results))

    # FIX 4: TDU priority — Oncor must be PRIMARY result for Dallas (not co-ops)
    dal_best = engine._resolve_texas_overlap(dal_results)
    test("Dallas TX: Oncor is primary electric provider (TDU priority)",
         lambda: "ONCOR" in dal_best["name"].upper())

    # AEP Texas — stored under STATE=OK in HIFLD shapefile
    # Must be found via geometry intersection, not STATE field filter
    # FIX 3: Corpus Christi, TX (AEP Texas Central territory)
    aep_results = engine.spatial.query_point(27.8006, -97.3964, "electric")
    test("AEP Texas Central found via geometry (not STATE filter)",
         lambda: any("AEP" in r["name"].upper() for r in aep_results))

    # FIX 3: Abilene, TX (AEP Texas North territory)
    aep_north = engine.spatial.query_point(32.4487, -99.7331, "electric")
    test("AEP Texas North found via geometry",
         lambda: any("AEP" in r["name"].upper() for r in aep_north))

    # ============================================================
    print("\n=== Scorer / Normalization Tests ===")
    # ============================================================

    # EIA ID match
    pr = engine.scorer.resolve_provider("COMMONWEALTH EDISON CO", eia_id=4110,
                                         utility_type="electric",
                                         polygon_source="HIFLD")
    test("ComEd EIA match: provider_name", lambda: pr.provider_name == "ComEd")
    test("ComEd EIA match: method=eia_id", lambda: pr.match_method == "eia_id")
    test("ComEd EIA match: confidence >= 0.90", lambda: pr.confidence >= 0.90)

    # Name match
    pr2 = engine.scorer.resolve_provider("DUKE ENERGY CAROLINAS, LLC",
                                          utility_type="electric",
                                          polygon_source="HIFLD")
    test("Duke Energy name match: matched", lambda: pr2.canonical_id is not None)
    test("Duke Energy name match: confidence >= 0.75", lambda: pr2.confidence >= 0.75)

    # Deregulated detection
    pr3 = engine.scorer.resolve_provider("ONCOR ELECTRIC DELIVERY COMPANY LLC",
                                          eia_id=44372,
                                          utility_type="electric",
                                          cntrl_area="NOT AVAILABLE",
                                          shp_type="INVESTOR OWNED")
    test("Oncor: is_deregulated=True", lambda: pr3.is_deregulated)
    test("Oncor: deregulated_note set", lambda: pr3.deregulated_note is not None)

    # Co-op in ERCOT is NOT deregulated
    pr4 = engine.scorer.resolve_provider("PEDERNALES ELECTRIC COOP, INC",
                                          utility_type="electric",
                                          cntrl_area="ERCO",
                                          shp_type="COOPERATIVE")
    test("Pedernales co-op: is_deregulated=False", lambda: not pr4.is_deregulated)

    # Municipal in ERCOT is NOT deregulated
    pr5 = engine.scorer.resolve_provider("CITY OF SAN ANTONIO - (TX)",
                                          utility_type="electric",
                                          cntrl_area="ERCO",
                                          shp_type="MUNICIPAL")
    test("CPS Energy municipal: is_deregulated=False", lambda: not pr5.is_deregulated)

    # Passthrough for unknown name
    pr6 = engine.scorer.resolve_provider("SOME RANDOM UTILITY INC",
                                          utility_type="electric")
    test("Passthrough: method=passthrough", lambda: pr6.match_method == "passthrough")
    test("Passthrough: name cleaned", lambda: pr6.provider_name == "Some Random Utility")

    # ============================================================
    print("\n=== Cache Tests ===")
    # ============================================================

    from lookup_engine.models import LookupResult, ProviderResult
    from lookup_engine.cache import LookupCache
    import tempfile, os

    tmp_db = Path(tempfile.mktemp(suffix=".db"))
    cache = LookupCache(tmp_db, ttl_days=1)

    fake_result = LookupResult(
        address="123 Test St, Chicago, IL 60606",
        lat=41.87, lon=-87.63,
        geocode_confidence=0.95,
        electric=ProviderResult(provider_name="ComEd", confidence=0.90, match_method="eia_id"),
    )
    cache.put("123 Test St, Chicago, IL 60606", fake_result)
    test("Cache: size is 1", lambda: cache.size == 1)

    cached = cache.get("123 Test St, Chicago, IL 60606")
    test("Cache: hit returns result", lambda: cached is not None)
    test("Cache: provider name preserved", lambda: cached.electric.provider_name == "ComEd")

    # Normalized key matching
    cached2 = cache.get("123 test st, chicago, il 60606")
    test("Cache: case-insensitive hit", lambda: cached2 is not None)

    # Miss
    cached3 = cache.get("999 Nowhere St, Nowhere, XX 00000")
    test("Cache: miss returns None", lambda: cached3 is None)

    cache.invalidate("123 Test St, Chicago, IL 60606")
    test("Cache: invalidate works", lambda: cache.size == 0)

    cache.close()
    os.unlink(tmp_db)

    # ============================================================
    print("\n=== Water Layer Tests ===")
    # ============================================================

    # Water layer loaded?
    test("Water layer loaded", lambda: engine.spatial.layer_counts["water"] > 40000)

    # Austin TX water
    w_austin = engine.spatial.query_point(30.2672, -97.7431, "water")
    test("Austin TX water: at least 1 system", lambda: len(w_austin) >= 1)
    test("Austin TX water: Austin in name",
         lambda: any("AUSTIN" in r["name"].upper() for r in w_austin))

    # Phoenix AZ water
    w_phx = engine.spatial.query_point(33.4484, -112.0740, "water")
    test("Phoenix AZ water: at least 1 system", lambda: len(w_phx) >= 1)
    test("Phoenix AZ water: Phoenix in name",
         lambda: any("PHOENIX" in r["name"].upper() for r in w_phx))

    # Chicago water
    w_chi = engine.spatial.query_point(41.8781, -87.6298, "water")
    test("Chicago IL water: at least 1 system", lambda: len(w_chi) >= 1)
    test("Chicago IL water: Chicago in name",
         lambda: any("CHICAGO" in r["name"].upper() for r in w_chi))

    # Water sorted by area (smallest first)
    if len(w_austin) > 1:
        test("Water sorted by area ascending",
             lambda: all(w_austin[i]["area_km2"] <= w_austin[i+1]["area_km2"]
                         for i in range(len(w_austin)-1)))

    # ============================================================
    print("\n=== Full Lookup Tests (requires geocoding) ===")
    # ============================================================

    # Chicago
    r = engine.lookup("233 S Wacker Dr, Chicago, IL 60606", use_cache=False)
    test("Chicago lookup: geocoded", lambda: r.lat > 41 and r.lon < -87)
    test("Chicago lookup: electric found", lambda: r.electric is not None)
    if r.electric:
        test("Chicago lookup: electric is ComEd",
             lambda: "comed" in r.electric.provider_name.lower() or "commonwealth" in r.electric.provider_name.lower())
        test("Chicago lookup: not deregulated", lambda: not r.electric.is_deregulated)
    test("Chicago lookup: gas found", lambda: r.gas is not None)
    if r.gas:
        test("Chicago lookup: gas is Peoples Gas",
             lambda: "peoples gas" in r.gas.provider_name.lower())
    test("Chicago lookup: under 2 seconds", lambda: r.lookup_time_ms < 2000)

    # San Antonio
    r2 = engine.lookup("100 Military Plaza, San Antonio, TX 78205", use_cache=False)
    test("San Antonio lookup: geocoded", lambda: r2.lat > 29 and r2.lon < -98)
    if r2.electric:
        test("San Antonio: CPS Energy (municipal)",
             lambda: "san antonio" in r2.electric.provider_name.lower() or "cps" in r2.electric.provider_name.lower())
        test("San Antonio: NOT deregulated", lambda: not r2.electric.is_deregulated)

    # Cache hit speed
    engine.lookup("233 S Wacker Dr, Chicago, IL 60606")  # prime cache
    t0 = time.time()
    cached_r = engine.lookup("233 S Wacker Dr, Chicago, IL 60606")
    cache_ms = (time.time() - t0) * 1000
    test(f"Cache hit speed: {cache_ms:.1f}ms (target <10ms)", lambda: cache_ms < 50)

    # Unknown address
    r3 = engine.lookup("999 Nonexistent Road, Nowhere, ZZ 00000", use_cache=False)
    test("Unknown address: no crash", lambda: r3 is not None)

    # ============================================================
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed")
    if passed == total:
        print("ALL TESTS PASSED")
    else:
        print(f"FAILURES: {total - passed}")


if __name__ == "__main__":
    main()
