# Tenant Coverage Report

Generated from tenant-verified address data using `normalize_provider_multi()`.
Normalization target: `data/canonical_providers.json` (446 canonical providers).
REP detection: `data/deregulated_reps.json` (93 TX REP names).

---

## Summary by Utility Type

| Utility Type | Total | Matched | Match % | REP-Flagged | Null/Placeholder | Propane | Unmatched |
|-------------|-------|---------|---------|-------------|-----------------|---------|-----------|
| Electric | 84,752 | 79,833 | 94.2% | 4,440 | 213 | 1 | 306 |
| Gas | 48,788 | 48,335 | 99.1% | 77 | 105 | 91 | 183 |
| Water | 48,667 | 47,395 | 97.4% | 22 | 37 | 0 | 1,214 |
| Trash | 9,276 | 6,155 | 66.4% | 17 | 15 | 0 | 3,089 |
| Sewer | 5,160 | 4,937 | 95.7% | 3 | 4 | 0 | 216 |
| **Electric + Gas** | **133,540** | **128,168** | **96.0%** | **4,517** | **318** | **92** | **489** |
| *All types* | *196,643* | *186,655* | *94.9%* | | *374* | *92* | |

---

## Comma-Split Statistics

| Metric | Value |
|--------|-------|
| Comma-separated entries processed | 4,920 |
| At least one segment matched | 4,537 |
| All segments matched | 3,037 |
| Partial+ match rate | 92.2% |

---

## Top 20 REP-Flagged Entries

These entries contain known TX Retail Electric Provider names.
The correct response is to return the TDU for the address, not the REP.

| Rank | Entry | Occurrences |
|------|-------|-------------|
| 1 | Energy Texas | 1,209 |
| 2 | TXU Energy | 522 |
| 3 | Reliant Energy | 482 |
| 4 | Gexa Energy | 334 |
| 5 | Good Charlie | 186 |
| 6 | 4Change Energy | 174 |
| 7 | Ambit Energy-CYMA | 118 |
| 8 | Frontier Utilities | 115 |
| 9 | Rhythm | 114 |
| 10 | Direct Energy | 92 |
| 11 | Discount Power | 68 |
| 12 | Champion Energy Services (Spring, TX) | 61 |
| 13 | Amigo Energy | 58 |
| 14 | Veteran Energy | 52 |
| 15 | Rythym Energy | 46 |
| 16 | APG&E | 32 |
| 17 | TXU | 30 |
| 18 | BKV Energy | 26 |
| 19 | Reliant-The Mardia Group | 25 |
| 20 | Unigas | 25 |

---

## Top 50 Unmatched Provider Names — Electric + Gas

These are the most common provider name strings that do NOT match
any canonical provider, alias, or known REP.

| Rank | Provider Name | Occurrences | Category |
|------|---------------|-------------|----------|
| 1 | Eversource-NH | 17 |  |
| 2 | OEC | 7 |  |
| 3 | Stream | 6 |  |
| 4 | GoodCharlie | 4 |  |
| 5 | Spectrum | 4 |  |
| 6 | CPWS | 4 |  |
| 7 | NiSource | 3 |  |
| 8 | APGE | 3 |  |
| 9 | 4Change | 3 |  |
| 10 | XFINITY | 3 |  |
| 11 | 4changeenergy | 3 |  |
| 12 | Tri Eagle | 2 |  |
| 13 | Relaint | 2 |  |
| 14 | Acacia | 2 |  |
| 15 | Heartland REMC | 2 | Co-op |
| 16 | TPCG | 2 |  |
| 17 | Electricities | 2 |  |
| 18 | Tri eagle | 2 |  |
| 19 | Oec | 2 |  |
| 20 | Evergreen | 2 |  |
| 21 | Tipmont | 2 |  |
| 22 | Tesla | 2 |  |
| 23 | Tux | 2 |  |
| 24 | Pogo | 2 |  |
| 25 | Real property management | 2 |  |
| 26 | Clean Sky | 2 |  |
| 27 | Modesto | 2 |  |
| 28 | ED2 | 2 |  |
| 29 | EWEB | 2 |  |
| 30 | UMS | 2 |  |
| 31 | MGE | 2 |  |
| 32 | Narongdach Janthong | 2 |  |
| 33 | AES | 2 |  |
| 34 | Other | 2 |  |
| 35 | Xfinity | 2 |  |
| 36 | THIGBE | 2 |  |
| 37 | WG+E | 2 |  |
| 38 | PowerNext | 2 |  |
| 39 | Greer CPW | 2 |  |
| 40 | Dom | 2 |  |
| 41 | Wildhorse Propane | 2 |  |
| 42 | Nogas | 2 | Gas utility |
| 43 | Walton | 2 |  |
| 44 | PWC | 2 |  |
| 45 | Osterman Propane | 2 |  |
| 46 | Ciardelli Fuels | 2 |  |
| 47 | Berico | 2 |  |
| 48 | Scana | 2 |  |
| 49 | Hunter Oil & Propane | 2 |  |
| 50 | Sandhills Propane | 2 |  |

---

## Methodology

- **Data source:** `addresses_with_tenant_verification_2026-02-06T06_57_49.470044438-06_00.csv` (90,978 addresses)
- **Fields checked:** Electricity, Gas, Water, Trash, Sewer
- **Normalizer:** `normalize_provider_multi()` with comma-split preprocessing
- **REP detection:** Known TX REPs from `deregulated_reps.json` (93 names)
- **Match method:** Direct case-insensitive lookup + partial substring matching
- **Note:** Water match rates are expected to be low — water providers are mostly municipal utilities with thousands of naming variants. Focus analysis on Electric and Gas.
