"""
Rebuild the locations gazetteer (locations + location_aliases) with curated merging.

Strategy:
  1. MANUAL_GROUPS  — explicit canonical for high-volume / known-equivalent families
     (matched by substring on the normalized raw string).
  2. Automated normalization for the long tail (strip address suffix, company forms,
     case, punctuation) -> group raws sharing a match key.
  3. Assign location_type (keyword rules + TYPE_OVERRIDES) and state/country.

Geocoding (lat/lon) is deliberately left NULL for a later pass.

Run: uv run python locations_build.py        (rebuilds the two tables in place)
     uv run python locations_build.py --review   (also prints merge groups for eyeballing)
"""
import re
import csv
import sys
import sqlite3
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent
DB = ROOT / "goamines.db"
GEOCODE_CSV = ROOT / "locations_geocode.csv"

STATES = ("karnataka", "maharashtra", "goa", "andhra pradesh", "andhra",
          "chhatisgarh", "chhattisgarh", "telangana", "kerala", "odisha",
          "gujarat", "jharkhand", "tamil nadu")
COUNTRIES = {"china", "guinea", "japan", "singapore", "korea", "south korea"}

# ---------------------------------------------------------------------------
# 1. Manual canonical groups. key = canonical display name,
#    value = list of lowercase substrings; any raw whose normalized form
#    contains one is mapped to this canonical. Order matters (first match wins).
# ---------------------------------------------------------------------------
MANUAL_GROUPS = [
    ("Mormugao Port Trust (MPT)", ["mpt", "mormugao port"]),
    ("Mandovi River Pellets Pvt Ltd (MRPPL)", ["mandovi river pellets"]),
    ("Mandovi Pellets Division (MPL)", ["mandovi pellets division"]),
    ("Bicholim Mineral Block - I", ["bicholim mineral block", "bicholim mine amlg"]),
    ("Sarmanas Jetty", ["sarmanas"]),
    ("TPL Maina Jetty", ["tpl maina", "maina jetty", "fomento maina"]),
    ("TPL Jetty Capxem", ["tpl jetty capxem", "capxem"]),
    ("Navelim Jetty", ["navelim jetty"]),
    ("Vedanta PID Navelim", ["pid navelim"]),
    ("Vedanta PID Amona", ["pid amona"]),
    ("Sircaim Jetty", ["sircaim"]),
    ("Sanvordem Jetty", ["sanvordem"]),
    ("Vagus Jetty", ["vagus jetty"]),
    ("Bandekar Vagus Plot", ["vagus plot", "bandekar vagus"]),
    ("Alcon Jetty", ["alcon jetty"]),
    ("D B Bandodkar Tixem Jetty", ["bandodkar tixem", "bandekar kotambi tixem", "bandodkar cotambi"]),
    ("Adrem Jetty", ["adrem"]),
    ("Calvi Jetty", ["calvi jetty", "siquirem plot / calvi"]),
    ("Thakur Industries", ["thakur indus"]),
    ("KEJ Minerals", ["kej mineral"]),
    ("Sandur Manganese & Iron Ore Ltd", ["sandur manganese"]),
    ("NMDC KIOM", ["nmdc kiom", "nmdc - kiom"]),
    ("Patiem / Tudou Mine", ["patiem", "tudou", "tudau"]),
    ("JSW Dolvi", ["jsw steel ltd, dolvi", "dist:raigarh"]),
]

# Canonical display -> location_type override (applied after keyword guess)
TYPE_OVERRIDES = {
    "Mormugao Port Trust (MPT)": "port",
    "Mandovi River Pellets Pvt Ltd (MRPPL)": "plant",
    "Mandovi Pellets Division (MPL)": "plant",
    "JSW Dolvi": "external",
    "Thakur Industries": "external",
    "KEJ Minerals": "external",
    "Sandur Manganese & Iron Ore Ltd": "external",
    "NMDC KIOM": "external",
    # tidy up the few unknown-type locations that actually carry trips
    "Sangod Dharbandora": "mine",
    "Cotombi Bicholim": "mine",
    "VRKP Bellary": "external",
    "Doddannavar Brothers": "external",
    "PBS": "external",
    "Belur Industrial Area - Dharwad": "external",
    "Rajmahal Silks": "external",
    "MSPL Ltd": "external",
    # out-of-Goa plants/ports mistyped as plant/port — they are import *sources*,
    # so they show as data-boundary "negatives"; mark external to exclude from Q1.
    "ACORE INDUSTRIES": "external",
    "ZEST FERRO BENEFICIATION PLANT-Bellary": "external",
    "Karnataka Limpo Cements Industries": "external",
    "M/s RPA FERRO INDUSTRIES PRIVATE LIMITED": "external",
    "Karwar Port": "external",
    "Sri Kumaraswamy Minerals Exports": "external",
}

# ---------------------------------------------------------------------------
def all_raws(con):
    use = defaultdict(int)
    for sql in (
        "SELECT source_location_raw, COUNT(*) FROM permits WHERE source_location_raw IS NOT NULL GROUP BY 1",
        "SELECT dest_location_raw, COUNT(*) FROM permits WHERE dest_location_raw IS NOT NULL GROUP BY 1",
        "SELECT source_location_raw, COUNT(*) FROM trips WHERE source_location_raw IS NOT NULL GROUP BY 1",
        "SELECT dest_location_raw, COUNT(*) FROM trips WHERE dest_location_raw IS NOT NULL GROUP BY 1",
        "SELECT location_raw, COUNT(*) FROM closing_stock WHERE location_raw IS NOT NULL GROUP BY 1",
    ):
        for v, n in con.execute(sql):
            use[v] += n
    return use

def strip_address(s):
    """Remove a trailing ', <State>, <District>, <Tehsil>' style address."""
    low = s.lower()
    # 'Maharashtra, Dist:Raigarh, Tal:Pen [INDIA]' -> keep as-is (it IS the dest)
    if re.match(r"^\s*(" + "|".join(STATES) + r")\s*,\s*dist", low):
        return s.strip(), None, "India"
    state = None
    country = None
    for st in STATES:
        m = re.search(r",\s*" + re.escape(st) + r"\b", low)
        if m:
            state = st.title()
            s = s[:m.start()]
            break
    s = re.sub(r"\[india\]", "", s, flags=re.I)
    return s.strip(" ,"), state, country

def norm(s):
    """Normalization key for grouping the long tail."""
    s = s.lower()
    s = re.sub(r"\bm/s\.?\b", " ", s)
    s = re.sub(r"\b(private|pvt|limited|ltd|company|co|corporation|the)\b\.?", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def guess_type(name):
    n = name.lower()
    if re.match(r"^\s*(" + "|".join(STATES) + r")\s*,\s*dist", n): return "external"
    if any(c in n for c in COUNTRIES): return "external"
    if "jetty" in n or "mooring" in n or "dolphin" in n or "berth" in n: return "jetty"
    if "railway" in n or "station" in n: return "railway"
    if "port" in n: return "port"
    if "stockyard" in n or "stock yard" in n: return "stockyard"
    if any(k in n for k in ("plant", "washing", "bnf", "pellet", "beneficiation",
                            "sponge", "ispat", "cement", "steel", "ferro", "pid",
                            "metallic", "industries", "alloys")):
        return "plant"
    if any(k in n for k in ("mine", "block", "lease", "amlg", "plot", "dongor",
                            "soddo", "tembo", "mat ", "iron ore", "bauxite",
                            "minerals", "mineral")) or re.search(r"\d+/\d+", n):
        return "mine"
    return "unknown"

def canonical_for(raw, base):
    """Return (canonical_display, state, country) for a raw string."""
    n = norm(raw)
    for disp, subs in MANUAL_GROUPS:
        for sub in subs:
            if sub in n:
                _, st, ctry = strip_address(raw)
                return disp, st, ctry
    # automated: strip address, then the display = base (most-common raw in key group)
    head, st, ctry = strip_address(raw)
    for c in COUNTRIES:
        if c in raw.lower():
            ctry = raw.strip().title()
    return base, st, ctry

def build(review=False):
    con = sqlite3.connect(DB)
    use = all_raws(con)

    # group raws by normalized key to pick a display "base" for the automated tail
    key_groups = defaultdict(list)
    for raw in use:
        key_groups[norm(strip_address(raw)[0])].append(raw)
    base_of = {}
    for key, raws in key_groups.items():
        best = max(raws, key=lambda r: use[r])      # most-used raw in the group
        head = strip_address(best)[0]
        for r in raws:
            base_of[r] = head

    aliases = {}     # raw -> canonical
    meta = {}        # canonical -> (type, state, country)
    for raw in use:
        canon, st, ctry = canonical_for(raw, base_of[raw])
        aliases[raw] = canon
        typ = TYPE_OVERRIDES.get(canon) or guess_type(canon)
        # keep first non-null state/country seen per canonical
        prev = meta.get(canon)
        if prev:
            typ = prev[0] if prev[0] != "unknown" else typ
            st = prev[1] or st
            ctry = prev[2] or ctry
        meta[canon] = (typ, st, ctry)

    # rewrite tables
    con.execute("DELETE FROM locations")
    con.execute("DELETE FROM location_aliases")
    con.executemany(
        "INSERT INTO locations (canonical_name, location_type, state, country) VALUES (?,?,?,?)",
        [(c, m[0], m[1], m[2]) for c, m in meta.items()])
    con.executemany(
        "INSERT INTO location_aliases (raw_name, canonical_name) VALUES (?,?)",
        list(aliases.items()))
    con.commit()

    # merge persistent geocoding (if present) so coords survive re-ingest
    n_geo = 0
    if GEOCODE_CSV.exists():
        with open(GEOCODE_CSV) as f:
            for row in csv.DictReader(f):
                if row.get("lat") and row.get("lon"):
                    con.execute(
                        "UPDATE locations SET lat=?, lon=?, geocode_source=?, geocode_note=? "
                        "WHERE canonical_name=?",
                        (float(row["lat"]), float(row["lon"]), row.get("source"),
                         row.get("note"), row["canonical_name"]))
                    n_geo += con.total_changes and 1 or 0
        con.commit()
        n_geo = con.execute("SELECT COUNT(*) FROM locations WHERE lat IS NOT NULL").fetchone()[0]

    n_raw = len(aliases)
    n_canon = len(meta)
    print(f"locations rebuilt: {n_raw} raw aliases -> {n_canon} canonical"
          f"{f'; {n_geo} geocoded' if n_geo else ''}")
    # type distribution
    print("\nby type:")
    for t, c in con.execute("SELECT location_type, COUNT(*) FROM locations GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {t:10s} {c}")

    if review:
        # show canonical groups that merge >1 raw, by total volume
        canon_vol = defaultdict(int)
        canon_raws = defaultdict(list)
        for raw, c in aliases.items():
            canon_vol[c] += use[raw]
            canon_raws[c].append(raw)
        print("\n" + "="*78)
        print("MERGE GROUPS (canonical <- multiple raw), by volume")
        print("="*78)
        for c in sorted(canon_vol, key=lambda x: -canon_vol[x]):
            raws = canon_raws[c]
            if len(raws) > 1:
                t, st, ctry = meta[c]
                print(f"\n[{canon_vol[c]:>7,}] {c}   ({t}{', '+st if st else ''}{', '+ctry if ctry else ''})")
                for r in sorted(raws, key=lambda r: -use[r]):
                    print(f"        {use[r]:>7,}  {r}")
    con.close()

if __name__ == "__main__":
    build(review="--review" in sys.argv)
