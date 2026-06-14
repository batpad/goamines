# Goa Ore Transport Data

Ingests Goa government **mineral-ore transport data** — permits, truck/barge trips, and
closing stock — into a single SQLite database (`goamines.db`) and explores it with
[Datasette](https://datasette.io/), including a map of ore movement.

The data was collected by **Rahul Basu** through RTI applications to the Directorate of
Mines & Geology, Goa, and shared on the [datameet](https://datameet.org/) mailing list.
It covers transport permits from **1-Apr-2020** onward and trip-level movements from
**25-Mar-2024** onward (the 2024 mining restart, on the GEL "Bhumija" software).

> **Read [`FINDINGS.md`](FINDINGS.md) first.** It is the analytical companion to this
> repo: what's in the data, the discrepancies, the schema rationale, the location-merge
> and geocoding decisions, and the answers (with caveats) to the data owner's three
> questions. This README is the operational "how to run it" guide.

## Raw data (not in this repo)

The source spreadsheets are large and are **not committed** (`data1/` and `data2/` are
git-ignored). To build the database you must place them yourself:

```
goamines/
├── data1/                      # transport permits (9 .xlsx)
│   ├── Megasoft - Captive_*.xlsx, Megasoft - eAuction_*.xlsx, Megasoft - Royalty_*.xlsx
│   ├── Old B H U M I J A - Captive_*.xlsx, … eAuction_*.xlsx, … Royalty_*.xlsx
│   └── New B H U M I J A - Captive_*.xlsx, … eAuction_*.xlsx, … Royalty_*.xlsx
└── data2/                      # trips, closing stock, manuals
    ├── Annexure II.xlsx                 # trip sheet — Royalty
    ├── Annexure II (1).xlsx             # trip sheet — Imported + E-Auction
    ├── Annexure III.xlsx                # closing stock 31-Mar-2025 — Royalty paid
    ├── Annexure III (1).xlsx            # closing stock 31-Mar-2025 — Imported + E-Auction
    └── Annexure III-Closing stock … 31 Mar 2024 (2).xlsx   # closing stock 31-Mar-2024
```

`ingest.py` reads these exact filenames (the `Annexure …` names are hard-coded; the nine
`data1/*.xlsx` are discovered by glob). The `Annexure I.pdf` / `Manuals.rar` (software
manuals) and the `drive-download-*.zip` (a duplicate of the loose files) are **not**
required for ingestion.

> 📦 **Where to get the data:** _<add download link here>_. Download and unzip into `data1/`
> and `data2/` at the repo root as shown above.

## Quick start

```bash
# 0. put the raw data in data1/ and data2/  (see "Raw data" above)

# 1. install deps into a local venv (uses uv)
uv sync

# 2. build the database from data1/ + data2/  (~15s; one command does everything)
uv run python ingest.py

# 3. serve it
uv run datasette goamines.db -m metadata.yaml --static static:static \
  --setting sql_time_limit_ms 8000 --setting max_returned_rows 5000
```

Then open <http://127.0.0.1:8765/>:
- **🗺️ Route map** — `/static/routes_map.html` — curved arcs per route, width ∝ tonnage,
  colour by ore stream.
- **📍 Location point map** — `/goamines/location_map` — geocoded locations (cluster map).
- **Canned queries** — top routes, monthly tonnage, busiest locations, the negative-balance
  check, stock reconciliation, completeness flags (see the homepage / `metadata.yaml`).

> All Python is run through **`uv`** so the venv stays local to this repo. Use
> `uv run python …` and `uv add <pkg>` — not bare `python`/`pip`.

## What's in the database

Built by `ingest.py` from the two source folders (`data1/` permits, `data2/` trips +
closing stock + manuals). Core tables:

| table | rows | what |
|---|---|---|
| `permits` | ~10.4k | one row per transport permit, unified across 3 software eras × 3 ore streams |
| `trips` | ~396k | one truck/barge trip; weighbridge weight at source **and** destination |
| `closing_stock` | ~216 | reported stock by location & stream at 31-Mar-2024 / 31-Mar-2025 |
| `locations` / `location_aliases` | 244 / 333 | canonical gazetteer + raw-string → canonical map |
| `location_balance` | ~166 | **precomputed** per-(stream, location) running-balance reconstruction |
| `routes`, `location_flows`, `location_map`, `monthly` | small | **materialized** aggregates (for snappy Datasette) |
| `v_*` views | — | live view definitions the materialized tables are built from |

**Ore streams:** `imported` (a.k.a. *Captive* — ore from overseas/other states, for
consumption or re-export), `royalty` (Goa-mined, royalty paid), `eauction` (Goa ore sold
via government e-auction). **Software eras:** `megasoft` (pre-GEL), `old_bhumija`,
`new_bhumija` (GEL). The Old↔New Bhumija exports overlap; duplicates are kept with
`is_superseded=1` on the Old row (filter `is_superseded=0` for a de-duplicated snapshot).

See `FINDINGS.md §4` for the full schema and `§3` for the data-quality issues each column
choice addresses.

## The pipeline

`ingest.py` is the single entry point. In order it:

1. **Loads permits** (`data1/*.xlsx`) — matches columns *by header name* (robust to the 3
   different era schemas), normalizes dates (Excel-serial **and** ISO), flags superseded rows.
2. **Loads trips** (`data2/Annexure II*.xlsx`) — streamed/batched (~396k rows).
3. **Loads closing stock** (`data2/Annexure III*.xlsx`).
4. **Builds the locations gazetteer** → `locations_build.build()` (see below).
5. **Precomputes balances** → `build_balances.build()` (the running-balance walk is too slow
   as a live SQL window query, so it's materialized into `location_balance`).
6. **Materializes aggregate tables** (`routes`, `location_flows`, `location_map`, `monthly`).
7. **Regenerates the route map** → `build_route_map.main()` → `static/routes_map.html`.

Re-running `ingest.py` rebuilds `goamines.db` from scratch every time. **If the Datasette
server is running, restart it afterwards** to pick up the new file.

## Locations & geocoding

Location strings in the source are messy free text (e.g. `MPT`, `MPT STOCKYARD`,
`MORMUGAO PORT TRUST (MPT)` are all one place). Handling:

- **`locations_build.py`** merges raw strings → canonical names. High-volume / known
  families are merged via the hand-curated `MANUAL_GROUPS`; the long tail via automated
  normalization (strip `, State, District` suffixes, company forms, case, punctuation).
  `TYPE_OVERRIDES` + keyword rules set `location_type` (mine / jetty / plant / port /
  stockyard / railway / external / unknown). **Extend these two dicts by hand** to refine.
- **Coordinates** live in **`locations_geocode.csv`** — a persistent, hand-editable file
  that `locations_build.py` merges back in, so coordinates **survive re-ingest**. To add or
  fix a location's coordinates, just edit that CSV (`canonical_name,lat,lon,source,note`)
  and re-run `uv run python ingest.py`.
- Two geocoders populate the CSV (Nominatim / OpenStreetMap, 1 req/sec):
  - `geocode.py` — tries the facility name directly (low yield: OSM doesn't know private
    jetty/mine names).
  - `geocode_villages.py` — anchors high-volume facilities to their **village/taluka**
    (approximate; Goa results validated against a Goa bounding box). This is where most
    coordinates come from. **To map more locations, add entries to its `ANCHORS` dict** and
    re-run it, then re-run `ingest.py`.

  Coverage is partial and approximate (~46 of the trip-carrying locations). `geocode_source`
  records provenance (`manual` / `nominatim` / `approx_village`).

## Repo layout

```
ingest.py            # ⭐ main pipeline: data1/ + data2/  ->  goamines.db
locations_build.py   # canonical location gazetteer (MANUAL_GROUPS, TYPE_OVERRIDES) + merges geocode CSV
build_balances.py    # precompute location_balance (running-balance reconstruction)
build_route_map.py   # generate static/routes_map.html (Leaflet route arcs)
geocode.py           # Nominatim geocode by facility name        -> locations_geocode.csv
geocode_villages.py  # Nominatim geocode by village anchor (ANCHORS dict) -> locations_geocode.csv
qa.py                # validation + analysis report (run after ingest)
metadata.yaml        # Datasette config: table/column docs, canned queries, map plugin
locations_geocode.csv# persistent, hand-editable coordinates (checked in)
FINDINGS.md          # the analytical write-up — start here
AGENTS.md            # orientation for AI agents / contributors extending the pipeline
survey.py headers.py loc_dump.py dupes.py analyze.py   # ad-hoc data-inspection helpers
data1/ data2/        # raw RTI source spreadsheets (not generated)
```

## Common tasks

- **Rebuild everything:** `uv run python ingest.py`
- **Sanity-check / analysis report:** `uv run python qa.py`
- **Add/refine a location merge:** edit `MANUAL_GROUPS` / `TYPE_OVERRIDES` in
  `locations_build.py`, then re-run `ingest.py`. Inspect raw strings with
  `uv run python loc_dump.py`.
- **Add coordinates:** edit `locations_geocode.csv` directly, or add to `ANCHORS` in
  `geocode_villages.py` and run it; then re-run `ingest.py`.
- **Refresh just the route map:** `uv run python build_route_map.py`

## Deployment

Live deployment (Debian + nginx + systemd, served by Datasette behind a reverse proxy)
is documented step-by-step in **[`deploy/DEPLOY.md`](deploy/DEPLOY.md)**, with the
supporting files in `deploy/`:

- `deploy/goamines.service` — systemd unit (runs `.venv/bin/datasette` in immutable mode on `127.0.0.1:8001`)
- `deploy/nginx-goamines.conf` — nginx reverse-proxy server block (certbot adds TLS)
- `deploy/update.sh` — pull + `uv sync` + rebuild/refresh helper

The database is provisioned out-of-band (it's git-ignored): either copy a locally-built
`goamines.db` + `static/routes_map.html` to the server, or upload the raw data and run
`ingest.py` on the box. See the runbook for both paths.

## Caveats (short version — full detail in `FINDINGS.md`)

- Trip data starts **25-Mar-2024** (Bhumija era) and runs to ~Nov-2025, *past* the
  31-Mar-2025 stock snapshot — cap trips at `2025-03-31` when reconciling against it.
- **Royalty closing stock for 31-Mar-2024 is missing** from the source.
- Trip-based balance reconstruction goes "negative" at **mines & out-of-Goa suppliers**
  (ore enters as a permit origin, not a trip) and over-accumulates at **export gateways**
  (ship-loading isn't a trip) — so the negative-balance question is only meaningful at
  *intermediate storage* (jetties/plants/stockyards). The one genuine in-Goa flag found so
  far is **Navelim Jetty** (e-auction): ~348k MT shipped out with no recorded origin.
- Coordinates are partial and approximate (village-level for many).
