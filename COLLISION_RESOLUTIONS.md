# Alias Collision Resolutions

Each of the 15 alias collisions from AUDIT.md §2 has been resolved.
Applied 2026-02-06 by `fix_canonical_providers.py`.

---

## Resolution Decisions

### 1. `AEP Texas Central` — AEP Ohio vs AEP Texas
**Decision:** Assign to **AEP Texas**. Remove from AEP Ohio.
**Reason:** AEP Texas Central is a Texas TDU operating division. AEP Ohio is a separate EDC.

### 2. `AEP Texas North` — AEP Ohio vs AEP Texas
**Decision:** Assign to **AEP Texas**. Remove from AEP Ohio.
**Reason:** Same as above — Texas TDU division.

### 3. `AEP Texas` — AEP Ohio vs AEP Texas (canonical)
**Decision:** Assign to **AEP Texas** (it IS the canonical). Remove from AEP Ohio.
**Reason:** AEP Texas is its own canonical entry; it should not also be an alias of AEP Ohio.

### 4. `columbia gas` — Columbia Gas PA vs NIPSCO
**Decision:** Assign to **Columbia Gas PA**. Remove from NIPSCO.
**Reason:** "Columbia Gas" is the brand name for NiSource's gas distribution subsidiaries (PA, VA, OH, KY, MA). NIPSCO is the electric/gas utility for northern Indiana. While NiSource owns both, "columbia gas" without a state qualifier most commonly refers to the PA subsidiary.

### 5. `Columbia Gas of Virginia` — Columbia Gas VA vs Dominion Energy
**Decision:** Assign to **Columbia Gas VA**. Remove from Dominion Energy.
**Reason:** Columbia Gas of Virginia is a NiSource subsidiary, not a Dominion subsidiary. Dominion Energy Virginia is the electric utility; Columbia Gas of Virginia is the gas utility. Different companies.

### 6. `Connecticut Light & Power` — Eversource CT vs Eversource MA
**Decision:** Assign to **Eversource CT**. Remove from Eversource MA.
**Reason:** CL&P (Connecticut Light & Power) is the Connecticut operating company. It has never operated in Massachusetts.

### 7. `Connecticut Light & Power Co.` — Eversource CT vs Eversource MA
**Decision:** Assign to **Eversource CT**. Remove from Eversource MA.
**Reason:** Same as above — formal name variant of CL&P.

### 8. `Eversource, CT` — Eversource CT vs Eversource MA
**Decision:** Assign to **Eversource CT**. Remove from Eversource MA.
**Reason:** The ", CT" suffix explicitly identifies Connecticut.

### 9. `YANKEE GAS SERVICE CO (EVERSOURCE)` — Eversource CT vs Eversource MA
**Decision:** Assign to **Eversource CT**. Remove from Eversource MA.
**Reason:** Yankee Gas Service Company is a Connecticut gas utility, now part of Eversource CT.

### 10. `PECO Electric` — PECO vs Pedernales Electric Cooperative
**Decision:** Assign to **PECO** (Philadelphia). Remove from Pedernales.
**Reason:** "PECO Electric" is the common consumer name for PECO Energy Company, the Philadelphia-area EDC. Pedernales Electric Cooperative (TX) abbreviates as "PEC", not "PECO Electric". The collision was caused by a coincidental abbreviation overlap.

### 11. `Peoples Gas` — Peoples Gas (WEC Energy) vs Peoples Gas Florida
**Decision:** Remove bare "Peoples Gas" from both. Add state-qualified aliases:
- `Peoples Gas IL` and `Peoples Gas PA` → Peoples Gas (WEC Energy)
- `Peoples Gas FL` → Peoples Gas Florida

**Reason:** These are completely different companies in different states owned by different parents (WEC Energy vs TECO Energy). The bare name is ambiguous and must be qualified.

### 12. `Spire` — Spire Alabama vs Spire Missouri
**Decision:** Assign to **Spire Missouri**. Remove from Spire Alabama.
**Reason:** Spire Inc. is headquartered in St. Louis. The unqualified "Spire" most commonly refers to the Missouri gas utility. Spire Alabama should use "Spire Alabama" or "Alagasco".

### 13. `Spire Energy` — Spire Alabama vs Spire Missouri
**Decision:** Assign to **Spire Missouri**. Remove from Spire Alabama.
**Reason:** Same as above — "Spire Energy" is the corporate brand, HQ'd in Missouri.

### 14. `questar` — Dominion Energy vs Dominion Energy Utah
**Decision:** Assign to **Dominion Energy Utah**. Remove from Dominion Energy.
**Reason:** Questar Gas is the Utah gas utility, now operating as Dominion Energy Utah. The parent Dominion Energy (Virginia) should not capture Utah gas lookups.

### 15. `virginia natural gas` — Dominion Energy vs Nicor Gas
**Decision:** Assign to **Dominion Energy**. Remove from Nicor Gas.
**Reason:** Virginia Natural Gas is a Dominion Energy subsidiary serving southeastern Virginia. It has no connection to Nicor Gas (an Illinois gas utility owned by Southern Company Gas). The alias was incorrectly placed on Nicor Gas.

---

## Additional Cleanup (related to collisions)

| Change | Reason |
|--------|--------|
| Removed `AEP` from AEP Ohio | Ambiguous — could be any AEP subsidiary |
| Removed `Eversource` from Eversource MA | Ambiguous holding company brand |
| Removed CT/NH aliases from Eversource MA | Wrong state (eversource connecticut, eversource nh, public service co of nh) |
| Removed `Spire Mississippi` from Spire Alabama | Separate operating entity |
| Removed `Spire Missouri` from Spire Alabama | Separate operating entity |

---

## Verification

After applying all resolutions: **0 alias collisions remain** in canonical_providers.json.
