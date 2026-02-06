#!/usr/bin/env python3
"""Tests for fuzzy matching in provider_normalizer.py (Prompt 6)."""

import sys
sys.path.insert(0, ".")

from provider_normalizer import (
    normalize_provider, normalize_provider_verbose, normalize_provider_multi,
    _HAS_RAPIDFUZZ
)

total = 0
passed = 0

def test(name, check_fn, description):
    global total, passed
    total += 1
    ok = check_fn()
    passed += ok
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {description}")
    if not ok and name:
        r = normalize_provider_verbose(name)
        print(f"         Input: \"{name}\"")
        print(f"         Got:   display=\"{r['display_name']}\" type={r['match_type']} sim={r['similarity']} on={r['matched_on']}")
    return ok


print(f"rapidfuzz available: {_HAS_RAPIDFUZZ}")
print()

# ============================================================
print("=== 1. Exact matches (case-insensitive) ===")
# ============================================================
test("PG&E", lambda: normalize_provider("PG&E") == "PG&E", "PG&E exact match")
test("Duke Energy", lambda: normalize_provider("Duke Energy") == "Duke Energy", "Duke Energy exact match")
test("ComEd", lambda: normalize_provider("ComEd") == "ComEd", "ComEd exact match")
test("comed", lambda: normalize_provider("comed") == "ComEd", "comed case-insensitive")
test("PECO", lambda: normalize_provider("PECO") == "PECO", "PECO exact match")
test("con edison", lambda: normalize_provider("con edison") == "Con Edison", "con edison case-insensitive")
test("Pedernales", lambda: normalize_provider("Pedernales") == "Pedernales Electric Cooperative",
     "Pedernales exact match")
test("PECO Energy", lambda: normalize_provider("PECO Energy") == "PECO", "PECO Energy exact alias match")

# ============================================================
print("\n=== 2. Fuzzy matches (typos) ===")
# ============================================================
r = normalize_provider_verbose("txu engery")
test("txu engery", lambda: r["is_rep"] and r["match_type"] == "fuzzy",
     "txu engery -> fuzzy REP match")

r = normalize_provider_verbose("Colombia gas of Ohio")
test("Colombia gas of Ohio", lambda: r["matched"] and r["match_type"] == "exact",
     "Colombia gas of Ohio -> exact match (misspelling alias added)")

r = normalize_provider_verbose("People's Gas")
test("People's Gas", lambda: r["matched"] and r["match_type"] == "fuzzy" and r["similarity"] >= 85,
     "People's Gas -> fuzzy match Peoples Gas (apostrophe variant)")

r = normalize_provider_verbose("Dominion Engery")
test("Dominion Engery", lambda: r["matched"] and r["match_type"] == "fuzzy" and r["similarity"] >= 85,
     "Dominion Engery -> fuzzy match Dominion Energy")

r = normalize_provider_verbose("Georgia Powre")
test("Georgia Powre", lambda: r["matched"] and r["match_type"] == "fuzzy" and "georgia power" in (r["matched_on"] or ""),
     "Georgia Powre -> fuzzy match Georgia Power")

# ============================================================
print("\n=== 3. Substring matches (embedded name in free text) ===")
# ============================================================
r = normalize_provider_verbose("Spire - It's not called Alabama Gas Corp Anymore")
test("Spire free text", lambda: r["matched"] and r["display_name"] in ("Spire Energy", "Alabama Gas Corporation"),
     "Free text with embedded utility name matches via fuzzy/substring")

# ============================================================
print("\n=== 4. REP detection (exact and fuzzy) ===")
# ============================================================
test("TXU Energy", lambda: normalize_provider_verbose("TXU Energy")["is_rep"],
     "TXU Energy -> exact REP")
test("txu", lambda: normalize_provider_verbose("txu")["is_rep"],
     "txu -> exact REP (lowercase)")
test("Rhythm", lambda: normalize_provider_verbose("Rhythm")["is_rep"],
     "Rhythm -> exact REP")
test("Rythym Energy", lambda: normalize_provider_verbose("Rythym Energy")["is_rep"],
     "Rythym Energy -> REP (typo in tenant data, stored as-is)")
test("txu engery", lambda: normalize_provider_verbose("txu engery")["is_rep"],
     "txu engery -> fuzzy REP match")

# ============================================================
print("\n=== 5. No match (should NOT match) ===")
# ============================================================
r = normalize_provider_verbose("Netflix")
test("Netflix", lambda: not r["matched"] and not r["is_rep"],
     "Netflix -> no match (completely unrelated)")

r = normalize_provider_verbose("AEP")
test("AEP", lambda: not r["matched"],
     "AEP -> no match (too short/ambiguous)")

r = normalize_provider_verbose("")
test("empty", lambda: not r["matched"],
     "Empty string -> no match")

# ============================================================
print("\n=== 6. Cross-match prevention ===")
# ============================================================
r = normalize_provider_verbose("PECO")
test("PECO", lambda: r["matched"] and "pedernales" not in (r["matched_on"] or "").lower(),
     "PECO should NOT cross-match to Pedernales")

r = normalize_provider_verbose("PECO")
test("PECO exact", lambda: r["match_type"] == "exact",
     "PECO should be exact match, not fuzzy")

# Holding companies should never match
r = normalize_provider_verbose("Berkshire Hathaway Energy")
test("Berkshire", lambda: not r["matched"],
     "Berkshire Hathaway Energy -> no match (holding company blocked)")

r = normalize_provider_verbose("NiSource")
test("NiSource", lambda: not r["matched"],
     "NiSource -> no match (holding company blocked)")

# ============================================================
print("\n=== 7. Verbose output structure ===")
# ============================================================
r = normalize_provider_verbose("Duke Energy")
test("verbose struct", lambda: all(k in r for k in ["canonical_id", "display_name", "original_segment",
                                                      "matched", "is_rep", "match_type", "similarity", "matched_on"]),
     "Verbose result has all required fields")

test("verbose exact", lambda: r["match_type"] == "exact" and r["similarity"] == 100.0,
     "Exact match has type='exact' and similarity=100.0")

r = normalize_provider_verbose("Dominion Engery")
test("verbose fuzzy", lambda: r["match_type"] == "fuzzy" and 85 <= r["similarity"] <= 100,
     "Fuzzy match has type='fuzzy' and similarity >= 85")

# ============================================================
print("\n=== 8. Backward compatibility ===")
# ============================================================
test("compat str", lambda: isinstance(normalize_provider("Duke Energy"), str),
     "normalize_provider() still returns string")

test("compat multi", lambda: isinstance(normalize_provider_multi("Duke Energy"), list),
     "normalize_provider_multi() still returns list")

test("compat comma", lambda: normalize_provider("ComEd, Nicor Gas") == "ComEd",
     "Comma-split still works in normalize_provider()")

# ============================================================
print(f"\n{'='*50}")
print(f"Results: {passed}/{total} passed")
if passed == total:
    print("ALL TESTS PASSED")
else:
    print(f"FAILURES: {total - passed}")
