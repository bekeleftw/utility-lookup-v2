#!/usr/bin/env python3
"""Tests for normalize_provider_multi() and comma-split behavior."""

import sys
sys.path.insert(0, ".")

from provider_normalizer import normalize_provider, normalize_provider_multi

def test(name, result, check_fn, description):
    passed = check_fn(result)
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {description}")
    if not passed:
        print(f"         Got: {result}")
    return passed

total = 0
passed = 0

print("=== normalize_provider_multi() tests ===\n")

# Test 1: "Energy Texas, TXU Energy" returns two results
r = normalize_provider_multi("Energy Texas, TXU Energy")
total += 1; passed += test("t1", r, lambda x: len(x) == 2, "Comma-split returns 2 segments for 'Energy Texas, TXU Energy'")
total += 1; passed += test("t1b", r, lambda x: any(s["is_rep"] for s in x), "TXU Energy flagged as REP")

# Test 2: "Columbia Gas of Pennsylvania, Peoples Gas - PA" returns two matches
r = normalize_provider_multi("Columbia Gas of Pennsylvania, Peoples Gas - PA")
total += 1; passed += test("t2", r, lambda x: len(x) == 2, "Comma-split returns 2 segments for Columbia Gas + Peoples Gas")
total += 1; passed += test("t2b", r, lambda x: sum(1 for s in x if s["matched"]) == 2, "Both segments matched")

# Test 3: "Duke Energy" (no comma) returns single match
r = normalize_provider_multi("Duke Energy")
total += 1; passed += test("t3", r, lambda x: len(x) == 1, "No-comma input returns single result")
total += 1; passed += test("t3b", r, lambda x: x[0]["matched"], "Duke Energy matched")

# Test 4: Trailing noise handled gracefully
r = normalize_provider_multi("Just Energy, Reliant Energy okay so we don't pay that")
total += 1; passed += test("t4", r, lambda x: len(x) == 2, "Splits into 2 segments")
# "Just Energy" may or may not match depending on data; "Reliant Energy okay..." likely won't exact-match
# The key is it doesn't crash and returns results for each segment

# Test 5: Empty segments after split are ignored
r = normalize_provider_multi("ComEd, , , ")
total += 1; passed += test("t5", r, lambda x: len(x) == 1, "Empty segments filtered out")
total += 1; passed += test("t5b", r, lambda x: x[0]["matched"] and x[0]["display_name"] == "ComEd", "ComEd matched correctly")

# Test 6: normalize_provider() backward compat — single name
r = normalize_provider("Duke Energy")
total += 1; passed += test("t6", r, lambda x: isinstance(x, str), "normalize_provider still returns string")
total += 1; passed += test("t6b", r, lambda x: x == "Duke Energy", "Duke Energy display name correct")

# Test 7: normalize_provider() with comma — returns first match
r = normalize_provider("ComEd, Nicor Gas")
total += 1; passed += test("t7", r, lambda x: isinstance(x, str), "normalize_provider with comma returns string")
total += 1; passed += test("t7b", r, lambda x: x == "ComEd", "Returns first matched segment (ComEd)")

# Test 8: Empty input
r = normalize_provider_multi("")
total += 1; passed += test("t8", r, lambda x: x == [], "Empty input returns empty list")

r = normalize_provider("")
total += 1; passed += test("t8b", r, lambda x: x == "", "normalize_provider empty returns empty string")

# Test 9: COLUMBIA GAS OF OHIO, First Energy — both should match
r = normalize_provider_multi("COLUMBIA GAS OF OHIO, First Energy")
total += 1; passed += test("t9", r, lambda x: len(x) == 2, "Two segments for Columbia Gas + FirstEnergy")
matched = [s for s in r if s["matched"]]
total += 1; passed += test("t9b", matched, lambda x: len(x) == 2, "Both segments matched")

# Test 10: Ameren MO, Spire Energy
r = normalize_provider_multi("Ameren MO, Spire Energy")
total += 1; passed += test("t10", r, lambda x: len(x) == 2, "Two segments for Ameren + Spire")

print(f"\n{'='*40}")
print(f"Results: {passed}/{total} passed")
if passed == total:
    print("ALL TESTS PASSED")
else:
    print(f"FAILURES: {total - passed}")
