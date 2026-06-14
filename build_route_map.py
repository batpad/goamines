"""
Generate a standalone Leaflet map of ore-movement ROUTES as curved arcs
(width ~ tonnage, colour by stream) plus location markers.

Writes static/routes_map.html  (served by Datasette via --static static:static,
or just open the file directly). Re-run after ingest to refresh.

Run: uv run python build_route_map.py
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "goamines.db"
OUT = ROOT / "static" / "routes_map.html"
MIN_TONNAGE = 2000          # hide tiny routes to reduce clutter

STREAM_COLOR = {"imported": "#1f77b4", "royalty": "#2ca02c", "eauction": "#ff7f0e"}
TYPE_COLOR = {"jetty": "#0077b6", "mine": "#8d5524", "plant": "#6a4c93",
              "port": "#d00000", "stockyard": "#3a5a40", "railway": "#555",
              "external": "#999", "unknown": "#bbb"}

def fetch():
    con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
    routes = [dict(r) for r in con.execute("""
        SELECT r.source, r.destination, r.ore_stream, r.trips, r.tonnage,
               ls.lat slat, ls.lon slon, ld.lat dlat, ld.lon dlon
        FROM routes r
        JOIN locations ls ON ls.canonical_name=r.source
        JOIN locations ld ON ld.canonical_name=r.destination
        WHERE ls.lat IS NOT NULL AND ld.lat IS NOT NULL
          AND r.source <> r.destination AND r.tonnage >= ?
        ORDER BY r.tonnage DESC""", (MIN_TONNAGE,))]
    locs = [dict(r) for r in con.execute("""
        SELECT location, location_type, latitude, longitude, tonnage_in, tonnage_out
        FROM location_map""")]
    con.close()
    return routes, locs

HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Goa Ore Movement — Routes</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body,#map{height:100%;margin:0}
  .legend{background:#fff;padding:8px 10px;font:13px/1.4 sans-serif;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.3)}
  .legend b{display:block;margin-bottom:4px}
  .sw{display:inline-block;width:22px;height:4px;vertical-align:middle;margin-right:6px}
  .info{position:absolute;top:10px;left:50px;z-index:1000;background:#fff;padding:6px 10px;
        font:13px sans-serif;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.3)}
</style></head><body>
<div id="map"></div>
<div class="info">Goa ore movement — line width ∝ tonnage, colour = ore stream. Click a line or marker.</div>
<script>
const ROUTES = __ROUTES__;
const LOCS = __LOCS__;
const STREAM_COLOR = __STREAMCOLOR__;
const TYPE_COLOR = __TYPECOLOR__;

const map = L.map('map').setView([15.45, 74.2], 9);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:18, attribution:'© OpenStreetMap'}).addTo(map);

const maxT = Math.max(...ROUTES.map(r=>r.tonnage));
function weight(t){ return 1.5 + 7*Math.pow(t/maxT, 0.45); }

// quadratic-bezier curved arc between two points
function curve(a, b){
  const lat1=a[0],lng1=a[1],lat2=b[0],lng2=b[1];
  const mlat=(lat1+lat2)/2, mlng=(lng1+lng2)/2;
  const dx=lat2-lat1, dy=lng2-lng1;
  const off=0.18;                            // curvature
  const clat=mlat - dy*off, clng=mlng + dx*off;   // perpendicular offset
  const pts=[]; const N=24;
  for(let i=0;i<=N;i++){ const t=i/N, u=1-t;
    pts.push([u*u*lat1 + 2*u*t*clat + t*t*lat2, u*u*lng1 + 2*u*t*clng + t*t*lng2]); }
  return pts;
}

const bounds=[];
ROUTES.forEach(r=>{
  const a=[r.slat,r.slon], b=[r.dlat,r.dlon];
  bounds.push(a,b);
  const line=L.polyline(curve(a,b), {color:STREAM_COLOR[r.ore_stream]||'#666',
     weight:weight(r.tonnage), opacity:0.65}).addTo(map);
  line.bindPopup(`<b>${r.source} → ${r.destination}</b><br>${r.ore_stream}<br>`
     +`${r.trips.toLocaleString()} trips · ${Math.round(r.tonnage).toLocaleString()} MT`);
  // arrowhead at destination
});

LOCS.forEach(l=>{
  if(l.latitude==null) return;
  const m=L.circleMarker([l.latitude,l.longitude],
     {radius:5, color:'#222', weight:1, fillColor:TYPE_COLOR[l.location_type]||'#bbb', fillOpacity:0.9}).addTo(map);
  m.bindPopup(`<b>${l.location}</b><br>${l.location_type}<br>`
     +`in: ${(l.tonnage_in||0).toLocaleString()} MT · out: ${(l.tonnage_out||0).toLocaleString()} MT`);
});

if(bounds.length) map.fitBounds(bounds, {padding:[40,40]});

const legend=L.control({position:'bottomright'});
legend.onAdd=function(){ const d=L.DomUtil.create('div','legend');
  d.innerHTML='<b>Ore stream</b>'
   +'<div><span class="sw" style="background:#1f77b4"></span>imported</div>'
   +'<div><span class="sw" style="background:#2ca02c"></span>royalty</div>'
   +'<div><span class="sw" style="background:#ff7f0e"></span>e-auction</div>';
  return d; };
legend.addTo(map);
</script></body></html>"""

def main():
    routes, locs = fetch()
    OUT.parent.mkdir(exist_ok=True)
    html = (HTML
            .replace("__ROUTES__", json.dumps(routes))
            .replace("__LOCS__", json.dumps(locs))
            .replace("__STREAMCOLOR__", json.dumps(STREAM_COLOR))
            .replace("__TYPECOLOR__", json.dumps(TYPE_COLOR)))
    OUT.write_text(html)
    print(f"{len(routes)} routes (>= {MIN_TONNAGE} MT), {len(locs)} markers -> {OUT}")

if __name__ == "__main__":
    main()
