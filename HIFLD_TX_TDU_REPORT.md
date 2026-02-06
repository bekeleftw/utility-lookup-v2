# HIFLD Shapefile: Texas TDU Boundary Verification

Analysis of `electric-retail-service-territories-shapefile/Electric_Retail_Service_Territories.shp`

---

## Shapefile Metadata

| Field | Value |
|-------|-------|
| Total records (nationwide) | 2,931 |
| CRS | EPSG:3857 (Web Mercator) |
| Texas records (STATE=TX) | 141 |
| Data year | 2022 |
| Source | EIA 861, U.S. Census, TIGER/Line |

### Attribute Schema

| Column | Type | Description |
|--------|------|-------------|
| `NAME` | string | Utility name |
| `STATE` | string | 2-letter state code (⚠ see AEP note) |
| `TYPE` | string | INVESTOR OWNED, COOPERATIVE, MUNICIPAL, etc. |
| `HOLDING_CO` | string | Parent/holding company |
| `CNTRL_AREA` | string | Control area (ERCOT, MISO, SWPP, etc.) |
| `CUSTOMERS` | int | Customer count (-999999 = N/A) |
| `RETAIL_MWH` | int | Retail MWh sales |
| `geometry` | Polygon/MultiPolygon | Service territory boundary |

---

## TDU Boundary Status

| TDU | Status | Name in Shapefile | STATE field | Geometry | Area |
|-----|--------|-------------------|-------------|----------|------|
| **Oncor** | ✅ FOUND | ONCOR ELECTRIC DELIVERY COMPANY LLC | TX | MultiPolygon (14 parts) | 214,050 km² |
| **CenterPoint Energy** | ✅ FOUND | CENTERPOINT ENERGY | TX | Polygon | 33,992 km² |
| **AEP Texas Central** | ⚠️ FOUND | AEP TEXAS CENTRAL COMPANY | **OK** | Polygon | 138,918 km² |
| **AEP Texas North** | ⚠️ FOUND | AEP TEXAS NORTH COMPANY | **OK** | Polygon | 174,791 km² |
| **Texas-New Mexico Power** | ✅ FOUND | TEXAS-NEW MEXICO POWER CO | TX | MultiPolygon (4 parts) | 120,065 km² |
| **Lubbock P&L** | ✅ FOUND | CITY OF LUBBOCK - (TX) | TX | Polygon (municipal) | 252 km² |

### ⚠️ Critical: AEP Texas stored under STATE=OK

AEP Texas Central and AEP Texas North have `STATE=OK` in the shapefile because AEP is headquartered in Oklahoma. Their polygons DO cover Texas territory.

**Impact on point-in-polygon lookup:** Must use **geometry intersection** (spatial join), NOT filter by `STATE` field. A query like `gdf[gdf['STATE'] == 'TX']` will miss both AEP TDUs.

---

## TDU Overlap Analysis

Significant overlaps exist between TDU polygons:

| TDU 1 | TDU 2 | Overlap | % of TDU 1 | % of TDU 2 |
|-------|-------|---------|-----------|-----------|
| TNMP | Oncor | 69,970 km² | 58.3% | 32.7% |
| AEP Texas North | Oncor | 57,371 km² | 32.8% | 26.8% |
| AEP Texas North | TNMP | 35,986 km² | 20.6% | 30.0% |
| TNMP | CenterPoint | 10,606 km² | 8.8% | 31.2% |
| AEP Texas Central | CenterPoint | 9,533 km² | 6.9% | 28.0% |
| AEP Texas North | AEP Texas Central | 5,490 km² | 3.1% | 4.0% |
| AEP Texas Central | TNMP | 4,176 km² | 3.0% | 3.5% |
| AEP Texas Central | Oncor | 4,100 km² | 3.0% | 1.9% |

### Why overlaps exist

TNMP has **non-contiguous service territories** (4 sub-polygons) scattered across the Oncor and CenterPoint regions. The HIFLD polygons are approximate boundaries — in reality, TNMP serves specific subdivisions and pockets within larger TDU territories.

**Impact on point-in-polygon:** For addresses in overlap zones, the lookup must use a **priority order** or additional data (e.g., ZIP-level mapping) to disambiguate. Suggested priority:
1. TNMP (smallest, most specific territories)
2. CenterPoint Energy (Houston metro, well-defined)
3. AEP Texas Central / AEP Texas North
4. Oncor (largest, default for DFW and surrounding)

---

## Non-Deregulated TX Utilities with Boundary Polygons

These are the correct answer for addresses in their territory (NOT deregulated — no REP applies).

### Cooperatives (57 with polygons)

Top 15 by customer count:

| Name | Customers |
|------|-----------|
| PEDERNALES ELECTRIC COOP, INC | 417,944 |
| DENTON COUNTY ELEC COOP, INC | 294,353 |
| MAGIC VALLEY ELECTRIC COOP INC | 133,394 |
| TRI-COUNTY ELECTRIC COOP, INC (TX) | 131,936 |
| BLUEBONNET ELECTRIC COOP, INC | 119,740 |
| UNITED ELECTRIC COOP SERVICE INC - (TX) | 98,632 |
| GUADALUPE VALLEY ELEC COOP INC | 95,781 |
| FARMERS ELECTRIC COOP, INC - (TX) | 88,070 |
| SAM HOUSTON ELECTRIC COOP INC | 87,022 |
| TRINITY VALLEY ELEC COOP INC | 83,220 |
| GRAYSON-COLLIN ELEC COOP, INC | 75,221 |
| SOUTH PLAINS ELECTRIC COOP INC | 69,941 |
| UPSHUR RURAL ELEC COOP CORP | 49,327 |
| NUECES ELECTRIC COOPERATIVE | 47,739 |
| CENTRAL TEXAS ELEC COOP, INC | 44,944 |

### Municipals (16 with polygons)

| Name | Customers |
|------|-----------|
| CITY OF SAN ANTONIO - (TX) (CPS Energy) | 918,463 |
| AUSTIN ENERGY | 533,427 |
| CITY OF GARLAND - (TX) | 74,180 |
| CITY OF BRYAN - (TX) | 63,337 |
| CITY OF DENTON - (TX) | 60,081 |
| BROWNSVILLE PUBLIC UTILITIES BOARD | 53,485 |
| CITY OF NEW BRAUNFELS - (TX) | 52,503 |
| CITY OF COLLEGE STATION - (TX) | 44,577 |
| CITY OF GEORGETOWN - (TX) | 30,999 |
| CITY OF SAN MARCOS - (TX) | 25,492 |

### Investor-Owned (regulated, non-TDU) (3)

| Name | Customers | Notes |
|------|-----------|-------|
| ENTERGY TEXAS INC. | 493,592 | East TX, not in ERCOT deregulated zone |
| EL PASO ELECTRIC CO | 455,303 | Far west TX, not in ERCOT |
| SOUTHWESTERN PUBLIC SERVICE CO | 403,678 | Panhandle TX, not in ERCOT |

---

## Summary

| Finding | Status |
|---------|--------|
| All 6 TDUs have boundary polygons | ✅ Yes |
| AEP Texas stored under wrong STATE | ⚠️ Must use geometry, not STATE filter |
| Significant TDU overlaps exist | ⚠️ Need priority order for disambiguation |
| Co-ops have polygons (57) | ✅ Good — these are the correct answer for non-deregulated areas |
| Municipals have polygons (16) | ✅ Good — includes CPS Energy (918K), Austin Energy (533K) |
| Regulated IOUs have polygons (3) | ✅ Entergy TX, El Paso Electric, SPS — not in ERCOT |

### Recommendations for Point-in-Polygon Engine

1. **Do NOT filter by STATE field** — use spatial join on geometry only
2. **Load all utilities nationwide** (or at minimum TX + OK for AEP) into the spatial index
3. **Handle overlaps** with priority ordering: co-ops/municipals first (most specific), then TNMP, then CenterPoint, then AEP, then Oncor
4. **Use CNTRL_AREA='ERCOT'** to identify deregulated territory (but verify — this field may not be populated for all entries)
5. **Lubbock P&L** is listed as MUNICIPAL but joined ERCOT deregulation in 2021 — treat as TDU for deregulated lookups
