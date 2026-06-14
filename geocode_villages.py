"""
Second geocoding pass: anchor high-volume jetties/mines/plants to the VILLAGE/place
they sit in (which OSM knows, unlike the private facility names). Results are
approximate (village/taluka-level) and marked source='approx_village'.

Goa anchors are validated against Goa's bounding box; out-of-state suppliers are
anchored to their district. Merges into locations_geocode.csv (does not overwrite
existing manual/nominatim rows unless the row has no coords).

Run: uv run python geocode_villages.py
"""
import csv
import time
import json
import urllib.parse
import urllib.request
from pathlib import Path

CSV = Path(__file__).parent / "locations_geocode.csv"
GOA_BBOX = (14.85, 15.85, 73.60, 74.45)

# canonical_name -> (village query, in_goa?)  -- conservative, confident cases only
ANCHORS = {
    # Goa — iron-ore belt mines & river jetties (village/taluka anchors)
    "Bicholim Mineral Block - I":      ("Bicholim, North Goa, Goa, India", True),
    "Sarmanas Jetty":                  ("Sarmanas, Bicholim, Goa, India", True),
    "Navelim Jetty":                   ("Navelim, Bicholim, Goa, India", True),
    "Vedanta PID Navelim":             ("Navelim, Bicholim, Goa, India", True),
    "SESA AMONA JETTY":                ("Amona, Goa, India", True),
    "Pilgao Beneficiation Plant (NBP)":("Pilgao, Bicholim, Goa, India", True),
    "Advalpale Thivim Mineral Block - V":("Advalpale, Goa, India", True),
    "Sircaim Jetty":                   ("Sirsaim, Bardez, Goa, India", True),
    "TPL Maina Jetty":                 ("Maina, Goa, India", True),
    "TPL Jetty Capxem":                ("Sanvordem, Goa, India", True),
    "Patiem / Tudou Mine":             ("Patiem, Sanguem, Goa, India", True),
    "Sanvordem Jetty":                 ("Sanvordem, Goa, India", True),
    "Vagus Jetty":                     ("Velguem, Goa, India", True),
    "Alcon Jetty":                     ("Sanvordem, Goa, India", True),
    "EMCO JETTY":                      ("Sanvordem, Goa, India", True),
    "TOLLEM PLANT":                    ("Tollem, Goa, India", True),
    "Pilgao Beneficiation Plant (NBP)":("Pilgao, Bicholim Taluka, Goa, India", True),
    "D B Bandodkar Tixem Jetty":       ("Tuem, Pernem, Goa, India", True),
    "GUELLEIM E GAVAL PISSURLEM IRON ORE MINE 55/51": ("Pissurlem, Goa, India", True),
    "NOMOXITEMBO -DE- CAUREM 14/52":   ("Quepem, Goa, India", True),
    "Sangod Dharbandora":              ("Dharbandora, Goa, India", True),
    "Cotombi Bicholim":                ("Bicholim, Goa, India", True),
    "Goa Mineral Pvt Ltd":            ("Sanguem, Goa, India", True),
    # Out-of-Goa suppliers — anchor to district (regional, not exact)
    "Thakur Industries":               ("Koppal, Karnataka, India", False),
    "KYARKOPPA RAILWAY STATION":       ("Dharwad, Karnataka, India", False),
    "Sandur Manganese & Iron Ore Ltd": ("Sandur, Ballari, Karnataka, India", False),
    "NMDC KIOM":                       ("Sandur, Ballari, Karnataka, India", False),
    "KEJ Minerals":                    ("Ballari, Karnataka, India", False),
    "ACORE INDUSTRIES":                ("Ballari, Karnataka, India", False),
    "ZEST FERRO BENEFICIATION PLANT-Bellary": ("Ballari, Karnataka, India", False),
    "M/s RPA FERRO INDUSTRIES PRIVATE LIMITED": ("Dharwad, Karnataka, India", False),
    "VRKP Bellary":                    ("Ballari, Karnataka, India", False),
    "Doddannavar Brothers":            ("Bagalkot, Karnataka, India", False),
    "Rajmahal Silks":                  ("Karwar, Karnataka, India", False),
    "RANISAMYUKTHA MINE- Hosadurga":   ("Hosadurga, Karnataka, India", False),
    "MML 995 Mines Ubbalagandi":       ("Sandur, Ballari, Karnataka, India", False),
    "Kalane Mines":                    ("Sawantwadi, Maharashtra, India", False),
    "Karnataka Limpo Cements Industries": ("Tumakuru, Karnataka, India", False),
    "Karwar Port":                     ("Karwar, Karnataka, India", False),
    "Sri Kumaraswamy Minerals Exports":("Ballari, Karnataka, India", False),
}

def nominatim(q):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": "goamines-ore-map/0.1 (datameet)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read())
    return d[0] if d else None

def in_goa(lat, lon):
    return GOA_BBOX[0] <= lat <= GOA_BBOX[1] and GOA_BBOX[2] <= lon <= GOA_BBOX[3]

rows = {r["canonical_name"]: r for r in csv.DictReader(open(CSV))}
for cn, (query, is_goa) in ANCHORS.items():
    if rows.get(cn, {}).get("lat"):   # already has coords
        continue
    try:
        hit = nominatim(query)
    except Exception as e:
        print(f"  ERR  {cn} ({type(e).__name__})"); hit = None
    time.sleep(1.1)
    if not hit:
        print(f"  miss {cn}  <- {query}"); continue
    lat, lon = float(hit["lat"]), float(hit["lon"])
    if is_goa and not in_goa(lat, lon):
        print(f"  OOB  {cn}  <- {query} -> {hit.get('display_name','')[:50]}"); continue
    rows[cn] = {"canonical_name": cn, "lat": lat, "lon": lon,
                "source": "approx_village", "note": f"~{query} | {hit.get('display_name','')[:90]}"}
    print(f"  ok   {cn:42s} -> {lat:.4f},{lon:.4f}")

with open(CSV, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["canonical_name", "lat", "lon", "source", "note"])
    w.writeheader()
    for cn in sorted(rows):
        w.writerow(rows[cn])
got = sum(1 for r in rows.values() if r.get("lat"))
print(f"\n{got}/{len(rows)} rows now have coordinates -> {CSV.name}")
