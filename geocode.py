"""
Geocode the map-relevant canonical locations (those carrying trips or closing stock)
into a PERSISTENT, hand-editable CSV: locations_geocode.csv.

locations_build.py merges this CSV back into the locations table, so coordinates
survive re-ingest. Re-running only geocodes names not already in the CSV
(use --refresh to redo all, --all to include permit-only locations).

Nominatim usage policy: max 1 req/sec, descriptive User-Agent. Goa-typed results
are validated against Goa's bounding box; out-of-box hits are discarded (left blank
for manual fill) rather than trusted.

Run: uv run python geocode.py
"""
import csv
import sys
import time
import json
import urllib.parse
import urllib.request
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "goamines.db"
CSV = ROOT / "locations_geocode.csv"
GOA_BBOX = (14.85, 15.85, 73.60, 74.45)   # lat_min, lat_max, lon_min, lon_max

# Manually verified coordinates for major nodes (take precedence over Nominatim).
# Approximate but reliable port/river-jetty/plant positions.
KNOWN_COORDS = {
    "Mormugao Port Trust (MPT)":              (15.4070, 73.8025, "Mormugao Port, Vasco, Goa"),
    "Vedanta PID Amona":                      (15.5360, 73.9870, "Vedanta/Sesa pig-iron plant, Amona"),
    "Mandovi River Pellets Pvt Ltd (MRPPL)":  (15.5350, 73.9900, "Mandovi River Pellets, Amona"),
    "Mandovi Pellets Division (MPL)":         (15.5350, 73.9900, "Mandovi Pellets, Amona"),
}

def needing(con, include_all):
    """canonical names to geocode, with their type/state, ordered by trip volume."""
    rows = con.execute("""
      WITH vol AS (
        SELECT canonical_name, SUM(n) tot FROM (
          SELECT la.canonical_name, COUNT(*) n FROM trips t JOIN location_aliases la ON la.raw_name=t.source_location_raw GROUP BY 1
          UNION ALL
          SELECT la.canonical_name, COUNT(*) n FROM trips t JOIN location_aliases la ON la.raw_name=t.dest_location_raw GROUP BY 1
          UNION ALL
          SELECT la.canonical_name, COUNT(*) n FROM closing_stock cs JOIN location_aliases la ON la.raw_name=cs.location_raw GROUP BY 1
        ) GROUP BY 1)
      SELECT l.canonical_name, l.location_type, l.state, l.country, COALESCE(v.tot,0) vol
      FROM locations l LEFT JOIN vol v ON v.canonical_name=l.canonical_name
      ORDER BY vol DESC
    """).fetchall()
    if include_all:
        return rows
    return [r for r in rows if r[4] > 0]

def nominatim(query):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": "goamines-ore-map/0.1 (datameet)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data[0] if data else None

def in_goa(lat, lon):
    return GOA_BBOX[0] <= lat <= GOA_BBOX[1] and GOA_BBOX[2] <= lon <= GOA_BBOX[3]

def load_csv():
    if not CSV.exists():
        return {}
    with open(CSV) as f:
        return {row["canonical_name"]: row for row in csv.DictReader(f)}

def save_csv(rows):
    with open(CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["canonical_name", "lat", "lon", "source", "note"])
        w.writeheader()
        for cn in sorted(rows):
            w.writerow(rows[cn])

def main():
    refresh = "--refresh" in sys.argv
    include_all = "--all" in sys.argv
    con = sqlite3.connect(DB)
    existing = load_csv()
    todo = needing(con, include_all)
    print(f"{len(todo)} candidate locations; {len(existing)} already in CSV")
    done = dict(existing)
    n_query = 0
    for cn, ltype, state, country, vol in todo:
        if cn in done and done[cn].get("lat") and not refresh:
            continue
        # manual override
        if cn in KNOWN_COORDS:
            lat, lon, note = KNOWN_COORDS[cn]
            done[cn] = {"canonical_name": cn, "lat": lat, "lon": lon, "source": "manual", "note": note}
            print(f"  manual   {vol:>7,}  {cn}")
            continue
        # build a region-biased query
        is_goa = (ltype != "external") and not country
        region = "Goa, India" if is_goa else (
            f"{state}, India" if state else (country or "India"))
        query = f"{cn}, {region}"
        try:
            hit = nominatim(query)
        except Exception as e:
            print(f"  ERR      {vol:>7,}  {cn}  ({type(e).__name__})")
            hit = None
        n_query += 1
        time.sleep(1.1)   # Nominatim rate limit
        if hit:
            lat, lon = float(hit["lat"]), float(hit["lon"])
            if is_goa and not in_goa(lat, lon):
                done[cn] = {"canonical_name": cn, "lat": "", "lon": "", "source": "rejected_out_of_goa",
                            "note": hit.get("display_name", "")[:120]}
                print(f"  reject   {vol:>7,}  {cn}  -> {hit.get('display_name','')[:60]}")
            else:
                done[cn] = {"canonical_name": cn, "lat": lat, "lon": lon, "source": "nominatim",
                            "note": hit.get("display_name", "")[:120]}
                print(f"  osm      {vol:>7,}  {cn}  -> {lat:.4f},{lon:.4f}")
        else:
            done[cn] = {"canonical_name": cn, "lat": "", "lon": "", "source": "not_found", "note": ""}
            print(f"  miss     {vol:>7,}  {cn}")
        if n_query % 10 == 0:
            save_csv(done)   # checkpoint
    save_csv(done)
    con.close()
    got = sum(1 for r in done.values() if r.get("lat"))
    print(f"\n{n_query} queries; {got}/{len(done)} have coordinates. -> {CSV.name}")

if __name__ == "__main__":
    main()
