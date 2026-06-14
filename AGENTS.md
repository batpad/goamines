# AGENTS.md

Orientation for AI agents (and humans) extending this repo. Read
[`README.md`](README.md) for the operational guide and [`FINDINGS.md`](FINDINGS.md) for
the analytical context (what the data is, its discrepancies, the schema rationale, and the
answers to the data owner's questions). This file covers **conventions, architecture, and
how to extend safely**.

## Golden rules

1. **Always use `uv`.** Run Python with `uv run python …`; add deps with `uv add …`. Never
   call bare `python`/`pip` — the venv is local to this repo (`.venv`). (Unused `pandas`
   was removed; only `openpyxl` + `datasette` + `datasette-cluster-map` are needed. The
   stdlib `sqlite3` is used for all DB work — no ORM.)
2. **Don't name a script after a stdlib module.** An early `inspect.py` shadowed stdlib
   `inspect` and broke numpy import. Avoid `inspect.py`, `types.py`, `csv.py`, etc.
3. **`ingest.py` is the single source of truth for the DB.** It deletes and rebuilds
   `goamines.db` from scratch every run. All derived state (locations, balances,
   materialized aggregates, route map) is produced by it, in order. Don't hand-edit the DB.
4. **`goamines.db` and `static/routes_map.html` are build artifacts** (git-ignored).
   `locations_geocode.csv` is **curated input** and **is** checked in — never regenerate it
   blindly (see Geocoding below).

## Architecture / data flow

```
data1/*.xlsx (permits) ─┐
data2/Annexure II*.xlsx ─┤  ingest.py  ─────────────────────────────►  goamines.db
data2/Annexure III*.xlsx ┘   │
                             ├─ load permits  (column match by header name)
                             ├─ load trips    (streamed, batched)
                             ├─ load closing_stock
                             ├─ locations_build.build()   ← merges locations_geocode.csv
                             ├─ build_balances.build()    → location_balance
                             ├─ materialize routes / location_flows / location_map / monthly
                             └─ build_route_map.main()    → static/routes_map.html
```

The schema (tables + `v_*` views) is one big `executescript` string near the top of
`ingest.py` (`SCHEMA`). Views are lazy; the heavy ones are **materialized into tables** at
the end of ingest because they scan 396k trips and exceed Datasette's SQL time limit as
live views. **If you change a `v_*` view that has a materialized twin (`v_routes`→`routes`,
`v_location_flows`→`location_flows`, `v_location_map`→`location_map`, `v_monthly`→`monthly`),
the materialization step re-copies it — just re-run `ingest.py`.**

## Key design decisions (don't silently undo these)

- **One unified `permits` table** across 3 software eras (Megasoft / Old+New Bhumija) and 3
  ore streams, with `software_era` / `ore_stream` / `category` / `source_file` provenance.
  Era-specific columns (e.g. `used_qty`, `balance_qty` — Megasoft only) are NULL elsewhere.
- **Columns are matched by normalized header name**, not position (`PERMIT_SYNONYMS` /
  `HDR2FIELD` in `ingest.py`). When a new file/era shows up, add header variants there
  rather than special-casing positions.
- **Dates**: Megasoft Captive/eAuction store Excel **serials**; everything else is ISO
  datetime. `to_iso()` / `to_iso_dt()` handle both. `0000-00-00` placeholders → NULL.
- **Old↔New Bhumija overlap** is kept (lossless) with `is_superseded=1` on the Old row.
  Default analytical views filter `is_superseded=0`. Don't dedupe by deleting.
- **Balance reconstruction is trip-window only** and lives in `location_balance`
  (precomputed in Python — a live SQL window query times out). Interpret with the
  data-boundary caveats baked into `FINDINGS.md §8/§10`: mines & `external` are unbounded
  sources; export gateways are unbounded sinks; royalty has no 31-Mar-2024 baseline. The
  meaningful negative-balance signal is **only at intermediate storage**
  (`location_type IN ('jetty','plant','stockyard','port')`).

## Locations & geocoding (the most-touched area)

- **Merging** raw location strings → canonical happens in `locations_build.py`:
  - `MANUAL_GROUPS` — ordered list; first normalized-substring match wins. Use for
    high-volume or known-equivalent families (e.g. all `MPT*` → one port node). Be careful
    not to over-merge (we explicitly do **not** merge different companies that merely share
    a district — see the removed "Chitradurga" group in git history / FINDINGS §8).
  - `TYPE_OVERRIDES` — force `location_type` for specific canonical names; otherwise
    `guess_type()` keyword rules apply.
  - Inspect the raw strings + volumes with `uv run python loc_dump.py` before editing.
- **Coordinates** are decoupled from merging and **persist** in `locations_geocode.csv`
  (`canonical_name,lat,lon,source,note`). `locations_build.build()` merges this CSV into the
  `locations` table on every ingest, so coords are **never lost** when you rebuild.
  - To add/fix coords by hand: edit the CSV, re-run `ingest.py`. Set `source` to something
    like `manual` and add a `note`.
  - To geocode more programmatically: add entries to `ANCHORS` in `geocode_villages.py`
    (canonical → village/place query, plus an `in_goa` bool that triggers Goa-bbox
    validation), run `uv run python geocode_villages.py`, then re-run `ingest.py`.
  - Nominatim etiquette: keep the 1.1s sleep and the descriptive `User-Agent`. OSM does
    **not** know private jetty/mine names — anchor to the village/taluka instead.
  - `geocode_source` values: `manual`, `nominatim`, `approx_village` (and `not_found` /
    `rejected_out_of_goa` for audit). Treat `approx_village` as village-level approximate.

## Datasette

- `metadata.yaml` holds table/column docs and canned queries. Canned queries should read
  from the **materialized tables** (`routes`, `location_flows`, `location_map`,
  `location_balance`, `monthly`), not the `v_*` views, so they stay under the SQL time limit.
- The map plugin (`datasette-cluster-map`) auto-renders any table with `latitude`/`longitude`
  columns — that's why `location_map` exposes those exact names.
- The route-arc map is a **standalone** Leaflet file (`static/routes_map.html`) served via
  `--static static:static` and linked from the homepage `description_html`. It is **not** a
  Datasette plugin; regenerate it with `build_route_map.py` (or via `ingest.py`).

## Gotchas

- Re-running `ingest.py` while Datasette is serving leaves the server on the old DB file
  (it holds the fd to the deleted inode). **Restart the server** after a rebuild.
- Annexure II/III sheets have a **title row above the real header** (and one sheet reports
  16,384 phantom columns); parsers find the header defensively — keep that if editing.
- The `data2/*.zip` is a duplicate of the loose `data2/` files; `Manuals.rar` /
  `Annexure I.pdf` are software manuals (low priority, not ingested).
- `qa.py` is the quickest way to sanity-check a change end-to-end (counts, routes, tonnage,
  negative-balance, completeness). Run it after any pipeline edit.

## Good next steps (see also FINDINGS §10)

- Densify geocoding (long-tail mine leases) by extending `ANCHORS`.
- Reconcile / chase the **Navelim Jetty** e-auction anomaly with the data owner.
- Build out **Question 2** (overall-stats) narrative; add directional arrowheads to the
  route arcs; consider a per-stream / time-slider map.
