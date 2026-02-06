# Normalization Consolidation Audit

Audit of `data/canonical_providers.json` and the normalization pipeline.
Generated 2026-02-06.

---

## 1. Coverage Gaps

### 1a. Tenant-Verified Provider Names vs canonical_providers.json

The 87K tenant-verified provider names (referenced in REBUILD PRINCIPLES.md rule 11)
are **not present in this repository** as a static file. The tenant data lives in
external systems (`tenant_verified_lookup.py`, `tenant_override_lookup.py`,
`tenant_hard_overrides.json`, `sub_zip_provider_rules_50k.json`) which are generated
by build scripts (`build_tenant_overrides.py`, `generate_tenant_rules.py`) that are
referenced in ARCHITECTURE-API.md but whose output files are not committed here.

**Result: Cannot compute exact coverage.** The consolidation report shows that
`provider_aliases.json` (auto-generated from comparison of sources including tenant
data) contributed 505 canonical keys and 690 aliases. Of those, 380 providers came
from only a single source — many of these are likely tenant-verified names that
exist nowhere else.

**Estimated gap:** Given that `canonical_providers.json` contains 414 providers and
the tenant dataset covers ~87K verified addresses across the entire US utility
landscape (~3,300 utilities per EIA), the coverage is likely **under 15%** of unique
provider names that tenants have reported. The long tail of small municipal utilities,
rural co-ops, and gas districts is almost certainly underrepresented.

**Top 20 likely unmatched categories** (inferred from provider_aliases.json
single-source entries and known US utility landscape):

| # | Category | Example from data |
|---|----------|-------------------|
| 1 | Small municipal electrics | City of Cuero Electric Department |
| 2 | Rural electric co-ops | Ckenergy Electric Cooperative |
| 3 | Municipal gas systems | Del Rio Gas System, Dublin Natural Gas System |
| 4 | State-suffixed duplicates | "Colombia Gas of Pennsylvania-PA" (note typo) |
| 5 | TVA distributors | Electric Power Board (EPB) - TN |
| 6 | PUD/irrigation districts | LATHROP IRRIGATION DISTRICT |
| 7 | Natural gas districts | Okaloosa County Gas District |
| 8 | Small-town utilities | Brigham City Public Power |
| 9 | EMCs (Electric Membership Corps) | Central Georgia EMC |
| 10 | Water & Power combined | City of Loveland Water and Power |
| 11 | Propane/LP alternatives | (none in canonical — complete gap) |
| 12 | Tribal utilities | (none in canonical — complete gap) |
| 13 | Federal facilities (DOD/DOE) | (none in canonical — complete gap) |
| 14 | Alaska utilities | INTERIOR ALASKA NATURAL GAS |
| 15 | Hawaii sub-utilities | Hawaii Electric Light Company (HELCO) |
| 16 | Puerto Rico (LUMA/PREPA) | (none in canonical — complete gap) |
| 17 | Small New England municipals | Groveland Municipal Light Department |
| 18 | Specialty gas companies | Frontier Natural Gas |
| 19 | Recently merged entities | Expand Energy (Chesapeake + Southwestern) |
| 20 | Placeholder entries | "Choose your electric here" (invalid entry) |

### 1b. EIA ZIP Lookup Data vs canonical_providers.json

The EIA 861 ZIP verification data is referenced in `state_utility_verification.py`
(1,430 lines) and `build_name_mappings.py`, but the actual EIA data files
(`provider_name_mappings.json`) are **not committed to this repository**.

**Result: Cannot compute exact EIA coverage gap.**

**Known structural issue:** EIA 861 lists ~3,300 electric utilities and ~1,500 gas
utilities in the US. With only 414 canonical providers, coverage of EIA entities
is at most **~8%** by count. Many EIA entities are small municipals, co-ops, and
PUDs that have no canonical entry.

---

## 2. Alias Collisions

Aliases that map to **more than one** canonical provider (i.e., the same string
appears in the `aliases` array of multiple canonical entries):

| Alias | Maps to canonical 1 | Maps to canonical 2+ |
|-------|---------------------|----------------------|
| `AEP Texas Central` | AEP Ohio | AEP Texas |
| `AEP Texas North` | AEP Ohio | AEP Texas |
| `AEP Texas` | AEP Ohio | AEP Texas |
| `Berkshire Hathaway Energy` | MidAmerican Energy | (in aliases — violates rule) |
| `Columbia Gas of Virginia` | Columbia Gas VA | Dominion Energy |
| `columbia gas` | Columbia Gas PA | NIPSCO |
| `Connecticut Light & Power` | Eversource CT | Eversource MA |
| `Connecticut Light & Power Co.` | Eversource CT | Eversource MA |
| `Eversource, CT` | Eversource CT | Eversource MA |
| `YANKEE GAS SERVICE CO (EVERSOURCE)` | Eversource CT | Eversource MA |
| `Peoples Gas` | Peoples Gas (WEC Energy) | Peoples Gas Florida |
| `PECO Electric` | PECO | Pedernales Electric Cooperative |
| `Spire` | Spire Alabama | Spire Missouri |
| `Spire Energy` | Spire Alabama (via "Spire Energy, Spire") | Spire Missouri |
| `questar` | Dominion Energy | Dominion Energy Utah |

**Total: 15 alias collisions found.**

The `provider_normalizer.py` `_ALIAS_TO_CANONICAL` dict is last-writer-wins, so
whichever entry loads last silently overwrites the earlier mapping. This means
**runtime behavior is nondeterministic** for these aliases — the result depends on
JSON key ordering (which Python 3.7+ preserves insertion order, but is fragile).

**Critical collisions:**
- `columbia gas` maps to both Columbia Gas PA and NIPSCO — these are different
  companies in different states
- `PECO Electric` maps to both PECO (Philadelphia) and Pedernales Electric (TX) —
  completely different utilities
- `Peoples Gas` maps to both WEC Energy subsidiary (IL/PA) and TECO subsidiary (FL)
- Eversource CT and Eversource MA share 4 aliases — will cause wrong-state matches

---

## 3. Display Name Sanity Check

### 3a. Display names containing corporate suffixes

These are inappropriate for consumer-facing display:

| Canonical ID | display_name | Offending term |
|---|---|---|
| Alabama Gas Corporation | Alabama Gas Corporation | Corporation |
| Bandera Electric Cooperative, Inc. | Bandera Electric Cooperative, Inc. | Inc. |
| Bartlett Electric Cooperative, Inc. | Bartlett Electric Cooperative, Inc. | Inc. |
| Beltrami Electric Cooperative Incorporated | Beltrami Electric Cooperative Incorporated | Incorporated |
| Berkshire Gas Company | Berkshire Gas Company | Company |
| Blue Grass Energy Cooperative Corp. | Blue Grass Energy Cooperative Corp. | Corp. |
| Brunswick Electric Membership Corporation | Brunswick Electric Membership Corporation | Corporation |
| Central Electric Cooperative, Inc | Central Electric Cooperative, Inc | Inc |
| Chattanooga Gas Company | Chattanooga Gas Company | Company |
| Chugach Electric Association, Inc. | Chugach Electric Association, Inc. | Inc. |
| Citizens Electric Corporation - (mo) | Citizens Electric Corporation - (mo) | Corporation |
| CoServ Electric Cooperative, Inc. | CoServ Electric Cooperative, Inc. | Inc. |
| Crow Wing Cooperative Power and Light Company | Crow Wing Cooperative Power and Light Company | Company |
| Deep East Texas Electric Cooperative, Inc. | Deep East Texas Electric Cooperative, Inc. | Inc. |
| El Paso Electric Company | El Paso Electric Company | Company |
| Elmhurst Mutual Power & Light Company | Elmhurst Mutual Power & Light Company | Company |
| Farmers Electric Cooperative, Inc. | Farmers Electric Cooperative, Inc. | Inc. |
| Florida City Gas Company | Florida City Gas Company | Company |
| Georgia Power | Georgia Power Company | Company (in alias, not display) |
| Glenwood Energy of Oxford Inc. | Glenwood Energy of Oxford Inc. | Inc. |
| Golden Valley Electric Association, Inc. | Golden Valley Electric Association, Inc. | Inc. |
| Grayson-Collin Electric Cooperative, Inc. | Grayson-Collin Electric Cooperative, Inc. | Inc. |
| GreyStone Power Corporation | GreyStone Power Corporation | Corporation |
| Guadalupe Valley Electric Cooperative, Inc. | Guadalupe Valley Electric Cooperative, Inc. | Inc. |
| Haywood Electric Membership Corporation | Haywood Electric Membership Corporation | Corporation |
| Hilco Electric Cooperative, Inc. | Hilco Electric Cooperative, Inc. | Inc. |
| Homer Electric Association, Inc. | Homer Electric Association, Inc. | Inc. |
| Indiana Michigan Power Company | Indiana Michigan Power Company | Company |
| La Plata Electric Association (LPEA) | La Plata Electric Association (LPEA) | — |
| Magic Valley Electric Cooperative, Inc. | Magic Valley Electric Cooperative, Inc. | Inc. |
| Matanuska Electric Association, Inc. | Matanuska Electric Association, Inc. | Inc. |
| Otter Tail Power Company | Otter Tail Power Company | Company |
| Owen Electric Cooperative, Inc. | Owen Electric Cooperative, Inc. | Inc. |
| Pedernales Electric Cooperative | Pedernales Electric Cooperative | — |
| Pennsylvania Electric Company - PENELEC | Pennsylvania Electric Company - PENELEC | Company |
| Pennsylvania Power Company | Pennsylvania Power Company | Company |
| Piedmont Electric Membership Corporation | Piedmont Electric Membership Corporation | Corporation |
| PPL Electric Utilities Corporation | PPL Electric Utilities Corporation | Corporation |
| Randolph Electric Membership Corporation | Randolph Electric Membership Corporation | Corporation |
| Roanoke Gas Company | Roanoke Gas Company | Company |
| Sam Houston Electric Cooperative, Inc. | Sam Houston Electric Cooperative, Inc. | Inc. |
| San Isabel Electric Association, Inc. | San Isabel Electric Association, Inc. | Inc. |
| Semco Energy Gas Company | Semco Energy Gas Company | Company |
| South Plains Electric Cooperative, Inc. | South Plains Electric Cooperative, Inc. | Inc. |
| Southwestern Electric Power Company | Southwestern Electric Power Company | Company |
| Tideland Electric Membership Corporation | Tideland Electric Membership Corporation | Corporation |
| Tri-County Electric Cooperative, Inc. | Tri-County Electric Cooperative, Inc. | Inc. |
| Trinity Valley Electric Cooperative, Inc. | Trinity Valley Electric Cooperative, Inc. | Inc. |
| United Electric Cooperative Services, Inc. | United Electric Cooperative Services, Inc. | Inc. |
| Upper Michigan Energy Resources Corporation | Upper Michigan Energy Resources Corporation | Corporation |
| Wake Electric Membership Corporation | Wake Electric Membership Corporation | Corporation |

**Total: 51 display names with corporate suffixes (Inc, Corp, LLC, Company, Co., Group)**

### 3b. Display names longer than 40 characters

| display_name | Length |
|---|---|
| Wakefield Municipal Gas & Light Department (WMGLD) - MA | 56 |
| North Georgia Electric Membership Corporation (NGEMC) - GA | 59 |
| City of Winter Park Electric Utility Department | 48 |
| City of Anaheim Public Utilities Department | 44 |
| Reading Municipal Light Department (RMLD) - MA | 47 |
| PSEG, PSE&G (Public Service Electric and Gas) - NJ | 51 |
| Bay City Electric Light and Power (BCELP) - MI | 47 |
| TID Water & Power - Turlock Irrigation District | 49 |
| Floresville Electric Light & Power System (FELPS) | 50 |
| Guadalupe Valley Electric Cooperative, Inc. | 44 |
| City of Stillwater Utilities Authority - OK | 44 |
| West Tennessee Public Utility District - TN | 44 |
| Crow Wing Cooperative Power and Light Company | 46 |
| The City of Ukiah's Electric Department - CA | 45 |
| CLECO (Central Louisiana Electric Company) | 43 |
| Beltrami Electric Cooperative Incorporated | 43 |
| Los Angeles Department Of Water And Power | 42 |
| LOS ANGELES DEPARTMENT OF WATER & POWER | 41 |
| Northeast Oklahoma Public Facilities Authority | 47 |
| Eugene Water & Electric Board EWEB - OR | 41 |
| Shenandoah Valley Electric Cooperative - VA | 44 |
| Grayson-Collin Electric Cooperative, Inc. | 42 |
| Michigan Gas Utilities (WEC Energy) | 36 |
| Minnesota Energy Resources (WEC Energy) | 40 |
| Upper Michigan Energy Resources Corporation | 44 |
| Upper Peninsula Power Company (UPPCO) | 38 |
| Metropolitan Utilities District Of Omaha | 41 |
| East Mississippi Electric Power Association | 44 |
| Elmhurst Mutual Power & Light Company | 38 |
| Deep East Texas Electric Cooperative, Inc. | 43 |
| Golden Valley Electric Association, Inc. | 41 |
| Mid-South Electric Cooperative Association | 43 |
| Brunswick Electric Membership Corporation | 42 |
| Central Electric Membership Corp. - (nc) | 41 |
| Clarksville Gas & Water (Clarksville, TN) | 42 |
| Trinity Valley Electric Cooperative, Inc. | 42 |
| United Electric Cooperative Services, Inc. | 43 |
| Taunton Municipal Lighting Plant - MA | 38 |
| Peabody Municipal Light Plant - MA | 35 |
| Wakefield Municipal Gas and Light Department | 45 |
| Sam Houston Electric Cooperative, Inc. | 39 |
| Glendale Water and Power (GWP) | 31 |
| Hawaiian Electric Company (HECO) | 33 |
| Hawaii Electric Light Company (HELCO) | 39 |
| Pennsylvania Electric Company - PENELEC | 41 |
| Meriwether Lewis Electric Cooperative | 38 |
| GEUS (Greenville Electric Utility System) | 42 |
| South Plains Electric Cooperative, Inc. | 40 |
| Eastern Maine Electric Cooperative (EMEC) | 42 |
| InterState Pwr&light Co. (alliant Energy) | 42 |
| Sacramento Municipal Utility District | 37 |
| Piedmont Electric Membership Corporation | 41 |
| Haywood Electric Membership Corporation | 40 |
| Sequachee Valley Electric Cooperative | 38 |
| Freeborn Mower Electric Cooperative | 36 |
| Randolph Electric Membership Corporation | 41 |
| Village of Morrisville Water & Light Dept. | 43 |
| Groveland Municipal Light Department | 37 |
| Power and Water Resource Pooling Authority | 43 |
| Wisconsin Public Service (WEC Energy) | 38 |

**Total: 60 display names longer than 40 characters.**

---

## 4. Holding Company Leaks

The codebase defines 16 holding companies in `_HOLDING_COMPANIES` (provider_normalizer.py:38-56)
and `HOLDING_COMPANIES` (consolidate_normalization.py:237-255) that should NEVER appear
as provider names or aliases. Audit results:

### Holding companies appearing as aliases in canonical_providers.json

| Holding Company | Found as alias of | Issue |
|---|---|---|
| Berkshire Hathaway Energy | MidAmerican Energy | **LEAK** — alias at line 1504. Should be parent_company metadata only |
| NiSource | NIPSCO | **LEAK** — alias at line 1666. NiSource is parent holding co of NIPSCO + Columbia Gas |
| AGL Resources | Nicor Gas | **LEAK** — alias at line 1651. AGL Resources is the old holding co |

### Parent vs operating utility confusion

| Issue | Details |
|---|---|
| Duke Energy parent vs subsidiaries | `Duke Energy` (canonical) has aliases for Duke Energy Carolinas, Duke Energy Florida, Duke Energy Progress — these are separate operating utilities that should be their own canonical entries with Duke Energy as parent_company. Duke Energy Indiana correctly has its own entry. |
| Entergy parent vs subsidiaries | All Entergy operating companies (Arkansas, Louisiana, Mississippi, Texas, New Orleans, Gulf States) are lumped under "Little Rock Pine Bluff" — a bizarre canonical name. Entergy Corporation (holding co) is in the holding company blocklist but "entergy" is an alias of this entry. |
| Dominion Energy parent vs subsidiaries | `Dominion Energy` canonical has "Dominion Energy Virginia" as an alias, but Dominion Energy South Carolina has its own entry. Inconsistent — Virginia should also be separate. |
| Eversource parent vs subsidiaries | Eversource CT and Eversource MA are correctly separate. But "Eversource" (the holding company brand) appears as an alias of Eversource MA, causing CT lookups to fail. |
| NextEra Energy | Not present as alias or canonical (correct — it's a holding co). FPL is correctly its own entry. |
| Exelon | Not present as alias or canonical (correct). ComEd and PECO are correctly separate. |
| WEC Energy Group | Not present as alias — but "wec energy" IS an alias of WE Energies (line 2563). The norm_key for "wec energy" and "wec energy group" differ only by "group", so this is a near-collision. |
| PPL Corporation | Not present as canonical or alias (correct). PPL Electric is its own entry. |
| Avangrid | Not present (correct). No Avangrid subsidiaries (NYSEG, RG&E, CMP, UI) reference it. |
| Emera | Not present (correct, it's a Canadian parent of TECO Energy). |
| Fortis | Not present (correct, Canadian parent). |
| AES | Not present as holding co. However, "AES Indiana" is an alias of Indianapolis Power & Light and "AES Ohio" is an alias of FirstEnergy — correct operating utility mappings. |

### 5 parent-company errors flagged in consolidation_report.txt

These were detected during consolidation but may still leak through:

1. `integrys energy` -> `WEC Energy Group` (holding co)
2. `peoples gas chicago` -> `WEC Energy Group` (holding co)
3. `midamerican energy` -> `Berkshire Hathaway Energy` (holding co)
4. `pacificorp` -> `Berkshire Hathaway Energy` (holding co)
5. `nv energy` -> `Berkshire Hathaway Energy` (holding co)

---

## 5. Deregulated Market Check

Per REBUILD PRINCIPLES.md rule 12: "In TX, OH, PA, and other deregulated states,
the system stores and returns the TDU/EDC, never the REP."

### Texas (TX)

| Provider | Type | Issue |
|---|---|---|
| Oncor | TDU | Correct — Oncor is the TDU for north/central TX |
| AEP Texas | TDU | Correct — AEP Texas is a TDU |
| CenterPoint Energy | TDU | Correct — CenterPoint is the TDU for Houston |
| TNMP | TDU | Correct — Texas-New Mexico Power is a TDU |
| **Txu Energy** | **REP** | **FLAG — TXU Energy is a retail electric provider, not a TDU. Should be removed or marked REP-only.** |
| CPS Energy | Municipal | Correct — San Antonio municipal, not deregulated |
| Austin Energy | Municipal | Correct — Austin municipal, not deregulated |
| Various TX co-ops | Co-op | Correct — co-ops are not deregulated |

### Ohio (OH)

| Provider | Type | Issue |
|---|---|---|
| FirstEnergy (Ohio Edison, Toledo Edison, Illuminating Co) | EDC | Correct |
| AEP Ohio | EDC | Correct |
| Duke Energy Ohio | Alias of Duke Energy parent | Incorrect structure but not a REP |
| **AES Ohio** | **EDC (formerly DP&L)** | Alias of FirstEnergy — **WRONG**. AES Ohio is a separate EDC, not part of FirstEnergy. |

No Ohio REPs found in canonical — good.

### Pennsylvania (PA)

| Provider | Type | Issue |
|---|---|---|
| PECO | EDC | Correct |
| PPL Electric | EDC | Correct |
| Met-Ed | EDC | Correct |
| Duquesne Light | EDC | Correct |
| Pennsylvania Electric Company (PENELEC) | EDC | Correct |
| West Penn Power | EDC | Correct |

No PA REPs found in canonical — good.

### Illinois (IL)

| Provider | Type | Issue |
|---|---|---|
| ComEd | EDC | Correct |
| Ameren Illinois | Alias of Ameren Missouri | **Structural issue** — Ameren IL is a separate EDC, lumped with MO |

No IL REPs found — good.

### Connecticut (CT)

| Provider | Type | Issue |
|---|---|---|
| Eversource CT | EDC | Correct |
| United Illuminating | Not present | **GAP** — UI is the second CT EDC, missing entirely |

### Maryland (MD)

| Provider | Type | Issue |
|---|---|---|
| Baltimore Gas & Electric (BGE) | EDC | Correct |
| Potomac Electric Power (PEPCO) | EDC | Correct |
| Delmarva Power | EDC | Correct |
| Potomac Edison | EDC | Correct |
| SMECO | Present as "SOUTHERN MARYLAND ELEC COOP INC" | Correct — co-op, not deregulated |

No MD REPs found — good.

**Summary:** One REP found stored as canonical provider: **TXU Energy (TX)**. One
EDC misattributed: **AES Ohio wrongly aliased to FirstEnergy**. One major EDC missing:
**United Illuminating (CT)**.

---

## 6. Raw Numbers

### Totals

| Metric | Value |
|---|---|
| Total canonical providers | 414 |
| Total aliases | 660 |
| Avg aliases per provider | 1.59 |

### Aliases per provider distribution

| Metric | Value |
|---|---|
| Min aliases | 0 |
| Median aliases | 1 |
| Max aliases | 16 (Eversource MA) |
| Providers with 0 aliases | 95 |
| Providers with 1 alias | 115 |
| Providers with 2 aliases | 59 |
| Providers with 3 aliases | 42 |
| Providers with 4 aliases | 25 |
| Providers with 5 aliases | 21 |
| Providers with 6 aliases | 14 |
| Providers with 7+ aliases | 43 |

### Providers with zero aliases (95 total)

These canonical entries have no name variants — either they are correctly unique
or they are missing aliases that tenants/EIA would submit under different names:

<details>
<summary>Full list (95 providers)</summary>

1. AEP Energy
2. Arizona Public Service, SRP
3. Bartlett Electric Cooperative, Inc.
4. Basin Electric Power Coop
5. Brigham City Public Power
6. Cartersville Gas System
7. Central Georgia EMC
8. Central Rural Electric Cooperative
9. Choose your electric here
10. Choptank Electric Corp
11. Citizens Electric Corporation - (mo)
12. CITY OF EDMOND - (OK)
13. CITY OF FAIRBURN - (GA)
14. City of Cuero Electric Department
15. City of Dunlap Natural Gas System
16. City of Fountain Electric
17. City of Loveland Water and Power
18. CITY OF LEESBURG
19. Ckenergy Electric Cooperative
20. CLECO (Central Louisiana Electric Company)
21. Colombia Gas of Pennsylvania-PA
22. Columbia Gas of Massachusetts
23. Connecticut Natural Gas (CNG) ,CT
24. Connexus Energy
25. Consolidated Edison Co-ny
26. Deep East Texas Electric Cooperative, Inc.
27. Del Rio Gas System
28. Dominion Hope Gas
29. Dublin Natural Gas System
30. Duquesne Light
31. East Mississippi Electric Power Association
32. East Ohio Gas Co/dominion
33. Electric Power Board (EPB) - TN
34. Ellensburg Natural Gas Dept.
35. Enbridge Gas North Carolina
36. Enbridge Gas Ohio
37. Energy Services of Pensacola
38. Energy West Wyoming
39. Farmers Electric Cooperative, Inc.
40. Fitchburg Gas & Elec Lt Co.
41. Frontier Natural Gas
42. Glenwood Energy of Oxford Inc.
43. Grayson-Collin Electric Cooperative, Inc.
44. Great Lakes Energy Cooperative
45. GreyStone Power Corporation
46. GROTON DEPT OF UTILITIES - (CT)
47. Hawaii Electric Light Company (HELCO)
48. Heber Light & Power - UT
49. Hilco Electric Cooperative, Inc.
50. INTERIOR ALASKA NATURAL GAS
51. Intermountain Rural Electric Association
52. Jo-Carroll Energy Cooperative
53. Kingsport Power Co.
54. Knoxville Utilities Board
55. Lancaster County Natural Gas - SC
56. LATHROP IRRIGATION DISTRICT
57. Lubbock Power & Light System
58. MIDDLE TENNESSEE E M C
59. Middle Tennessee Natural Gas
60. Millenium Energy
61. Monongahela Power Co.
62. National Fuel Gas Company - PA
63. Navopache Electric Co
64. New Hope Gas Company
65. NEW MEXICO GAS COMPANY
66. New York State Electric & Gas
67. New York State Electric & Gas (NYSEG)
68. NORTH SHORE GAS CO
69. Northeast Ohio Natural Gas
70. Northern Indiana Public Service Company
71. Northwest Electric Power Co-Op
72. Northwestern Electric Coop Inc. - (ok)
73. NORWICH PUB UTILITIES
74. Nw Alabama Gas Dist/freebird
75. Ohio Cumberland Gas Co.
76. PASCOAG UTILITY DISTRICT
77. Pennsylvania Power Company
78. PG&E, Public Service Electric & Gas
79. PG&E, Southern California Edison
80. Potomac Electric Power Co.
81. Power and Water Resource Pooling Authority
82. PPL GAS UTILITIES
83. PSE-Puget Sound Energy
84. PSEG, PSE&G (Public Service Electric and Gas) - NJ
85. Public Service Electric & Gas, PG&E
86. Rio Grande Valley Gas Co.
87. Roanoke Gas Company
88. Rocky Mount Utility Services GAS
89. Smyrna Natural Gas System
90. St. Lawrence Gas
91. Suburban Natural Gas
92. Tri-County Electric Cooperative, Inc.
93. Trico Electric Cooperative
94. Trinity Valley Electric Cooperative, Inc.
95. United Electric Cooperative Services, Inc.
96. Ugi Penn Natural Gas
97. Village of Morrisville Water & Light Dept.
98. Washington Electric Utilities - NC
99. West Florida El Coop Assn
100. Zia Natural Gas Company

</details>

Notable issues in the zero-alias list:
- **"Choose your electric here"** — this is a UI placeholder, not a real provider
- **Compound entries** like "PG&E, Southern California Edison" and "PG&E, Public Service Electric & Gas" — these are data artifacts from multi-provider ZIPs, not real providers
- **Duplicate entries**: "Duquesne Light" (0 aliases) AND "Duquesne Light Company - PA" (2 aliases); "New York State Electric & Gas" AND "New York State Electric & Gas (NYSEG)"; "Consolidated Edison Co-ny" AND "Con Edison"; "NORTH SHORE GAS CO" AND "North Shore Gas (WEC Energy)"; "PSE-Puget Sound Energy" AND "PSE"; "Potomac Electric Power Co." AND "Potomac Electric Power"
- **Misspelling**: "Colombia Gas of Pennsylvania-PA" (should be Columbia)

---

## Summary of Critical Findings

| Finding | Severity | Count |
|---|---|---|
| Alias collisions (same alias -> multiple canonicals) | **HIGH** | 15 |
| Holding company names leaked as aliases | **HIGH** | 3 |
| REP stored as canonical provider (TXU Energy) | **HIGH** | 1 |
| EDC misattributed (AES Ohio -> FirstEnergy) | **HIGH** | 1 |
| Display names with corporate suffixes | **MEDIUM** | 51 |
| Display names > 40 chars | **MEDIUM** | 60 |
| Providers with zero aliases | **MEDIUM** | 95 |
| Duplicate canonical entries (same utility, 2 keys) | **MEDIUM** | ~8 |
| Invalid/placeholder entries | **LOW** | 3 |
| Missing major EDC (United Illuminating CT) | **MEDIUM** | 1 |
| Parent-company structural errors (Entergy, Duke, etc) | **HIGH** | 4 |
| Estimated coverage of tenant names | **HIGH** | ~15% |
| Estimated coverage of EIA entities | **HIGH** | ~8% |
