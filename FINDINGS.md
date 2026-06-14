# Goa Ore Transport Data — Data Study & Proposed Schema

## 1. What's in the two folders

### `data1/` — Permit headers (one row per transport permit)
9 files = **3 software eras × 3 ore streams**, each split into Transit / Export / Sale / (Import) sheets.

| Software era | Permit-no format | Date format | Files |
|---|---|---|---|
| **Megasoft** (pre-GEL) | `ITP DMG-00743/2020-21` | Excel serial (e.g. 43974) | Captive, eAuction, Royalty |
| **Old Bhumija** (GEL) | `ILT072400024` | real datetime | Captive, eAuction, Royalty |
| **New Bhumija** (GEL, refreshed UI) | `ILT042500109` | real datetime | Captive, eAuction, Royalty |

**Ore streams** (the column the owner calls Captive/Royalty/eAuction):
- **Captive / Imported** — ore imported from overseas or other states, for consumption or re-export (Vedanta, Hindalco, etc.)
- **Royalty paid** — ore mined in Goa on which royalty was paid
- **e-Auction** — Goa ore sold via government e-auction

**~10,370 distinct permits**, ~13,500 permit rows total. Date span Apr-2020 → Apr/May-2025.

Schema differs by era:
- Megasoft sheets: **28 columns** (rich — permit qty / used / balance, processing fee, buyer/trader codes, challan, vessel).
- Bhumija sheets: **11–18 columns** (leaner — proposed qty only, no used/balance, adds "Ore category", "Approx Grade", transport-mode legs).
- Within an era the Export/Import/Sale sheets add stream-specific columns (country/state/vessel for export, buyer for sale, etc.).

### `data2/` — Trips, closing stock, manuals
- **Annexure II** (`Annexure II.xlsx` + `Annexure II (1).xlsx`): the granular **trip sheet** — RTI item (c). 3 sheets by stream (Royalty / Imported / E-Auction). **~396,560 trip rows**. One row = one truck or barge trip:
  `Permit Number | Vehicle No/Barge name | Source Location | Trip start Date-Time | Loaded qty @Source | Destination Location | Trip Close Date-Time | Loaded qty @Destination`.
  Real timestamps to the millisecond; weights at both source and destination weighbridges. Trucks ~8–16 t, barges ~800–2,000 t. Covers **after 25-Mar-2024 only** (Bhumija era). First data row sits under a title row (row 0) — real header is **row 1**.
- **Annexure III** (3 files): **closing stock** — RTI item (d). `Location | Balance quantity (MT)` per stream.
  - 31-Mar-2024: `...as of 31 Mar 2024 (2).xlsx` — **Imported + Eauction only** (NO royalty-paid sheet).
  - 31-Mar-2025: `Annexure III.xlsx` (Royalty paid) + `Annexure III (1).xlsx` (Imported + E-Auction). Imported 2025 adds a **Stock Type** column splitting "For Consumption" vs "For Export" (same location can appear twice).
- **Annexure I.pdf** + **Manuals.rar** — software manuals / SOPs (RTI items a, b). Low priority; we can infer structure from the data itself.
- `drive-download-*.zip` is just a **duplicate** of the loose data2 files — ignore.

## 2. Key relationships / how it joins
- **Trips → permits** via `Permit Number`. 938 distinct permits appear in trips; **72%** of them are present in data1's permit headers. The missing 28% are permits issued *after* the data1 snapshot (e.g. `IFE0525...` = May 2025) — i.e. the trip export runs slightly later than the permit export. Trips reference **only Bhumija-format** permits (none of the Megasoft `… DMG-…` ones), consistent with trips being post-25-Mar-2024.
- **Permits / trips / stock → locations** via free-text location names. Same physical place is written many ways → needs canonicalization.

## 3. Discrepancies & data-quality issues to handle on ingest
1. **Three different permit schemas** (Megasoft 28-col vs two Bhumija layouts) → need a unified superset permit table with `software_era` + `ore_stream` + `permit_category` (transit/export/sale/import) provenance columns.
2. **Dates in two formats** — Excel serials (Megasoft) vs ISO datetimes (Bhumija). Normalize to ISO.
3. **Header noise** — Annexure II/III have a title row above the real header; Annexure III (1) sheets report 16,384 phantom columns (trailing empty cells). Parse defensively.
4. **Inconsistent column names** even for the same concept: `Location_Name` / `Location Name` / `Location name`; `Closing Stock` / `Balance` / `Balance quantity(MT)`; `Permit No` / `Permit Number`; underscores vs spaces. Map via a per-file column dictionary.
5. **Messy location strings** — 267 distinct in data1 movement cols. Same site appears as `MPT`, `MPT STOCKYARD`, `MPT West Of Breakwaters`; `Thakur Industries` vs `Thakur Industries, Karnataka, Koppal, Koppal`; `Bicholim Mineral Block- 1` vs `Bicholim Mineral Block - I`. Need a **locations gazetteer** table (raw_name → canonical_location → lat/lon, type=mine/jetty/plant/port/stockyard, state/country).
6. **Closing-stock gaps**: no **royalty-paid stock as of 31-Mar-2024**. Only 2 of 3 streams have a 2024 baseline. (Plausibly near-zero since the 2024 mining phase had just restarted, but it's a genuine gap to flag.)
7. **`0000-00-00` placeholder dates** and blank cells scattered in Megasoft sheets (export_permit_date etc.).
8. **Weight reconciliation**: `Loaded qty @Source` vs `@Destination` differ slightly per trip (moisture/spillage/weighbridge) — keep both, derive a `delta`.
9. 96/114 closing-stock locations exact-match a data1 movement location; ~18 are naming variants → fold into the gazetteer.

## 4. Proposed SQLite schema (normalized core + provenance)

```
permits(
  permit_no PK, software_era, ore_stream, permit_category,   -- transit/export/sale/import
  permit_type, issue_date, validity_date, status, financial_year,
  org_code, org_name, mineral_type, ore_type, grade_slab, exact_grade,
  permit_qty, used_qty, balance_qty,                          -- used/balance NULL for Bhumija
  source_location_raw, dest_location_raw,
  transport_mode, state, country, district, tehsil,
  vessel_name, buyer_name, trader_name, trader_code,
  export_sale_permit_no, challan_no,
  source_file, source_sheet                                  -- provenance
)

trips(
  id PK, permit_no FK→permits, ore_stream,
  vehicle_or_barge, source_location_raw, dest_location_raw,
  start_dt, end_dt, qty_source, qty_dest, qty_delta,
  source_file, source_sheet
)

closing_stock(
  id PK, as_of_date, ore_stream, location_raw,
  balance_mt, stock_type,                                     -- stock_type only for Imported 2025
  source_file
)

locations(                                                   -- gazetteer, built semi-manually
  canonical_name PK, location_type, state, country, lat, lon, notes
)
location_aliases(raw_name PK, canonical_name FK→locations)
```

Views to build: `permit_with_trip_totals`, `location_balance_over_time` (opening stock + signed trip flows → answers the negative-balance question), `route_flows` (source→dest aggregates for the map).

## 5. Analyses this unlocks (the owner's 3 questions)
1. **Negative balances** — reconstruct per-location running balance from 31-Mar-2024 opening stock ± trip inflows/outflows; flag any time a location goes below zero (data-integrity / leakage signal).
2. **Overall stats** — top source/destination locations, busiest routes, total tonnage by stream/mineral/grade, truck vs barge vs rail split, monthly time series.
3. **Completeness** — flag: missing royalty 2024 stock; trip permits absent from permit headers; permits with zero trips; source/dest not in gazetteer.

## 6. Suggested build order
1. **Ingest** (Python + `uv`, openpyxl streaming for the 400k-row trip files) → `goamines.db` with the 4 core tables + provenance.
2. **Locations gazetteer** — generate the distinct raw-name list, cluster/canonicalize, geocode (manual + nominatim) → aliases table.
3. **Reconciliation views** + a QA report (the 3 questions).
4. **Frontend** — Datasette for fast publish/explore + a small map (datasette-cluster-map / custom Leaflet for route arcs). Django only if we need heavier custom viz/auth/editing later. _(open decision)_

---

## 7. RESULTS (after first ingest — `ingest.py` → `goamines.db`, validated by `qa.py`)

Loaded: **10,381 permits**, **396,563 trips**, **216 closing-stock rows**, **296 canonical locations** (333 raw aliases). Decisions applied: keep-both + `is_superseded` (5 old_bhumija rows flagged), locations structured now / geocode later, balance scoped to trip window.

**Headline stats**
- **7.76 M MT** moved across all trips (@destination weighbridge). By stream: imported 4.18 M (205.6k trips) · royalty 2.58 M (130.3k) · eauction 1.00 M (60.7k).
- Source vs destination weight: **+33,162 MT** net (dest heavier — moisture/weighbridge variance), worth a per-route look.
- ~99.9% of trips are trucks (~8–16 t); 305 barge trips carry 485k MT.
- Busiest routes: `MPT RAILWAY SIDING → MPT STOCKYARD` (1.42 M MT, 128.7k trips), `Bicholim Mineral Block-I → Sarmanas Jetty` (1.23 M MT), `Sarmanas Jetty → MPT West of Breakwaters` (715k MT).

**Owner's 3 questions**
1. **Negative balances** — the reconstruction framework is built and runs, BUT the raw result (38 stream+location series dipping below 0) is **dominated by location-aliasing artifacts, not real shortfalls**. E.g. `MPT RAILWAY SIDING` shows −1.42 M MT only because trips move ore *internally within MPT* (`MPT RAILWAY SIDING → MPT STOCKYARD`) while the 31-Mar-2024 opening stock for that area is filed under a *different* string (`MORMUGAO PORT TRUST (MPT)`). Until the MPT-family and other variants are merged in the gazetteer, the negative-balance answer is **not trustworthy**. → gated on a location-merge curation pass.
2. **Overall stats** — delivered (routes / inflow locations / tonnage / stream split above; all queryable).
3. **Completeness** — flagged: **royalty closing stock for 31-Mar-2024 is missing**; **259** trip-permit numbers absent from the permit headers (permits issued after the May-2025 permit export); **trips run to 2025-11-18**, i.e. ~8 months *past* the 31-Mar-2025 closing-stock snapshot (so any reconstructed-vs-reported-2025 check must cap trips at 2025-03-31); 1,734 permit rows have no source/dest (mostly Sale permits, which legitimately have a buyer not a destination).

**Biggest open work item:** the **locations gazetteer** is the linchpin. The naive keyword canonicalization leaves MPT split into 6+ strings and 72/296 locations as `type=unknown`. Curating canonical merges (and then lat/lon) is what makes both the map *and* the negative-balance analysis real.

---

## 8. LOCATION MERGE PASS (done — `locations_build.py`, wired into `ingest.py`)

Approach: curated `MANUAL_GROUPS` for the high-volume / known-equivalent families (matched by normalized-substring) + automated normalization for the long tail (strip `, State, District, Tehsil` address suffix, drop company forms `M/s/Pvt/Ltd`, lowercase, depunctuate → group raws sharing a key; display = most-used raw in the group). `TYPE_OVERRIDES` + keyword rules assign `location_type`. **Geocoding (lat/lon) deferred.**

Result: **333 raw strings → 244 canonical**. Every location that carries a trip is now typed (mine 115 / plant 37 / jetty 22 / external 14 / port 9 / railway 4 / stockyard 1; 42 `unknown` remain but are all permit-only / zero-trip).

Key merges: the whole **MPT family** (`MPT`, `MPT RAILWAY SIDING`, `MPT STOCKYARD`, `MPT BERTH`, `MPT MOORING DOLPHIN`, `MORMUGAO PORT TRUST (MPT)`, …, 342k uses) → one **Mormugao Port Trust (MPT)** node; `Bicholim Mineral Block - I` / `- 1` / `BICHOLIM MINE AMLG`; jetty spelling variants (Tixem/Cotambi, Sircaim, Sanvordem, Maina); out-of-state suppliers collapsed across their address/case/company-form variants (Thakur, KEJ, Sandur, NMDC KIOM, …). Deliberately **not** merged: different companies that merely share a district (e.g. various Chitradurga mines), and `MPL` vs `MRPPL` pellet plants (left separate, flagged uncertain). The merge map lives in `MANUAL_GROUPS` / `TYPE_OVERRIDES` and is meant to be extended by hand.

**Impact on the negative-balance question:** merging MPT removed the phantom −1.42 M MT at `MPT RAILWAY SIDING` (it was just internal `RAILWAY SIDING → STOCKYARD` shuffling). With locations merged, the remaining negatives cluster at **mines and out-of-Goa suppliers** (Thakur −390k, Kyarkoppa rail siding −336k, Sandur, KEJ, NMDC KIOM, …). These are **net sources whose supply enters from outside the trip system** (fresh mine production / rail- and sea-imported ore that arrives as a *permit* origin, not a tracked trip) — so "negative" there is a **data-boundary artifact, not leakage**. Symmetrically, export-gateway nodes (MPT, pellet plants) over-accumulate in trip-only reconstruction because the **final ship-loading leg is not a trip**. ⇒ A trustworthy negative-balance analysis must (a) restrict to **intermediate storage** (jetties/plants/stockyards), (b) treat mines/external as unbounded sources and export points as unbounded sinks, and (c) note royalty has no 31-Mar-2024 opening baseline. That refinement is the core of Question 1 and is the next analytical task after Datasette is stood up.

---

## 9. DATASETTE FRONTEND (done — `metadata.yaml`, geocode pipeline, materialized tables)

Stood up Datasette over `goamines.db`. Serve with:
```
uv run datasette goamines.db -m metadata.yaml --setting sql_time_limit_ms 8000 --setting max_returned_rows 5000
```

**Geocoding** (best-effort, partial, marked approximate via `geocode_source`):
- `geocode.py` — Nominatim over the trip/stock-carrying locations (region-biased, Goa-bbox validated). Only ~11 resolved (private jetty/mine names aren't in OSM).
- `geocode_villages.py` — anchors the **high-volume** jetties/mines/plants to their **village/taluka** (which OSM knows); Goa ones bbox-validated, out-of-state suppliers to their district. → **28 locations now mapped**, covering all the dominant nodes. Coords persist in hand-editable **`locations_geocode.csv`** (merged back by `locations_build.py`, so they survive re-ingest). Remaining misses (Maina, Capxem, Pissurlem, Kyarkoppa, …) can be filled by hand in that CSV.

**Performance**: the aggregate views scan 396k trips and blew Datasette's 1 s SQL limit, so static aggregates are **materialized into tables** during ingest: `routes`, `location_flows`, `location_map`, `monthly`, plus `location_balance` (the precomputed per-(stream, location) running-balance reconstruction — `build_balances.py`). All canned queries now read these and render instantly.

**Map**: `datasette-cluster-map` auto-renders the `location_map` table (latitude/longitude columns), sized by tonnage.

**Canned queries** (in `metadata.yaml`): `top_routes`, `monthly_tonnage`, `busiest_locations`, **`negative_balance_storage`** (Q1, intermediate storage only), `stock_reconciliation`, `trip_permits_missing`, `weight_loss_routes`.

**Refined Q1 answer** (from `location_balance`, intermediate storage only): **11** (stream, storage-location) series dip below zero. After setting aside out-of-Goa plants/ports (misclassified — should be `external`) and royalty (no 2024 baseline), the standout genuine in-Goa anomaly is **Navelim Jetty (e-auction): min −347,742 MT** — worth investigating (likely the opening e-auction stock there is understated, or inflow arrived under a different stream). Next analytical step: reclassify the remaining Bellary/Karnataka plants as `external`, then Navelim is the one real flag to chase.

---

## 10. FOLLOW-UPS (done)

**(a) External reclassification + Q1 nailed down.** Added the 6 out-of-Goa plants/ports that were mistyped (`ACORE INDUSTRIES`, `ZEST FERRO …Bellary`, `Karnataka Limpo Cements`, `RPA FERRO`, `Karwar Port`, `Sri Kumaraswamy Minerals`) to `TYPE_OVERRIDES` as `external`. Q1 (`negative_balance_storage`) now returns just **5** storage series, and the picture is clean:

| min running balance | stream | location | note |
|---|---|---|---|
| **−347,742** | eauction | **Navelim Jetty** | **genuine anomaly** (has baseline) |
| −7,760 | eauction | Trimurthy Jetty plot, Amona | minor |
| −568 | eauction | TOLLEM PLANT | negligible |
| −14,538 | royalty | SESA SURLA JETTY | royalty has no 2024 baseline → inconclusive |
| −9,576 | royalty | SESA AMONA JETTY | royalty has no 2024 baseline → inconclusive |

**Navelim Jetty, investigated:** 30,260 outbound e-auction trips = **347,742 MT leaving** (mostly → TPL Maina Jetty 318k, → Ambey Metallics 30k), with **zero inbound trips, zero opening stock (31-Mar-2024), and zero closing stock (31-Mar-2025)**. So that entire ~348k MT of e-auction ore left Navelim with **no recorded origin** in either the trip data or the stock annexures. Most likely a **pre-existing e-auction dump at Navelim Jetty that the 31-Mar-2024 closing-stock annexure omitted** (e-auction ore is typically old mine dumps); alternatively the inbound leg was never exported. **This is a concrete data-completeness gap to raise with the data owner.**

**(b) Denser geocoding.** Expanded `geocode_villages.py` anchors (retries with better village spellings + district anchors for out-of-state suppliers). Now **46 canonical locations geocoded** (up from 28), covering every endpoint of the major routes (Maina, Capxem, Kyarkoppa, Pissurlem, Caurem, …). Remaining misses (Pilgao, long-tail mine leases) fillable by hand in `locations_geocode.csv`.

**(c) Route-arc map.** `build_route_map.py` → `static/routes_map.html`: a standalone Leaflet map drawing **curved arcs for each route** (width ∝ tonnage, colour by stream) over location markers; **53 routes ≥ 2,000 MT, 46 markers**. Wired into `ingest.py` and served by Datasette (`--static static:static`), linked from the homepage. Serve command updated:
```
uv run datasette goamines.db -m metadata.yaml --static static:static \
  --setting sql_time_limit_ms 8000 --setting max_returned_rows 5000
```
Route map: `/static/routes_map.html` · point map: `/goamines/location_map`.
