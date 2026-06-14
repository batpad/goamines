"""Dump every raw location string with where it's used + frequency."""
import sqlite3
from pathlib import Path
from collections import defaultdict

con = sqlite3.connect(Path(__file__).parent / "goamines.db")
use = defaultdict(lambda: {"p_src":0,"p_dst":0,"t_src":0,"t_dst":0,"stock":0})

for (v,n) in con.execute("SELECT source_location_raw, COUNT(*) FROM permits WHERE source_location_raw IS NOT NULL GROUP BY 1"):
    use[v]["p_src"]=n
for (v,n) in con.execute("SELECT dest_location_raw, COUNT(*) FROM permits WHERE dest_location_raw IS NOT NULL GROUP BY 1"):
    use[v]["p_dst"]=n
for (v,n) in con.execute("SELECT source_location_raw, COUNT(*) FROM trips WHERE source_location_raw IS NOT NULL GROUP BY 1"):
    use[v]["t_src"]=n
for (v,n) in con.execute("SELECT dest_location_raw, COUNT(*) FROM trips WHERE dest_location_raw IS NOT NULL GROUP BY 1"):
    use[v]["t_dst"]=n
for (v,n) in con.execute("SELECT location_raw, COUNT(*) FROM closing_stock WHERE location_raw IS NOT NULL GROUP BY 1"):
    use[v]["stock"]=n

rows = sorted(use.items(), key=lambda kv: -sum(kv[1].values()))
print(f"{len(rows)} distinct raw location strings\n")
print(f"{'tot':>7} {'p_src':>6}{'p_dst':>6}{'t_src':>7}{'t_dst':>7}{'stk':>4}  name")
for v, d in rows:
    tot = sum(d.values())
    print(f"{tot:>7} {d['p_src']:>6}{d['p_dst']:>6}{d['t_src']:>7}{d['t_dst']:>7}{d['stock']:>4}  {v}")
con.close()
