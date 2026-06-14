"""QA + analysis report for goamines.db. Run: uv run python qa.py"""
import sqlite3
from pathlib import Path
from collections import defaultdict

con = sqlite3.connect(Path(__file__).parent / "goamines.db")
con.row_factory = sqlite3.Row
def q(sql, *a): return con.execute(sql, a).fetchall()
def one(sql, *a): return con.execute(sql, a).fetchone()[0]
def hr(t): print("\n" + "="*78 + f"\n{t}\n" + "="*78)

# ---------------------------------------------------------------- counts
hr("TABLE COUNTS")
for t in ("permits", "trips", "closing_stock", "locations", "location_aliases"):
    print(f"  {t:18s} {one(f'SELECT COUNT(*) FROM {t}'):>9,}")

hr("PERMITS by era / stream / category")
for r in q("""SELECT software_era, ore_stream, category, COUNT(*) n,
              SUM(is_superseded) sup FROM permits
              GROUP BY 1,2,3 ORDER BY 1,2,3"""):
    print(f"  {r['software_era']:13s} {r['ore_stream']:9s} {r['category']:9s} "
          f"{r['n']:>6,}  superseded={r['sup']}")

hr("DATE RANGES")
print("  permit issue_date:", one("SELECT MIN(issue_date) FROM permits"),
      "→", one("SELECT MAX(issue_date) FROM permits"))
print("  trip start_dt:    ", one("SELECT MIN(start_dt) FROM trips"),
      "→", one("SELECT MAX(start_dt) FROM trips"))

# ---------------------------------------------------------------- linkage
hr("TRIP ↔ PERMIT LINKAGE")
tp = one("SELECT COUNT(DISTINCT permit_no) FROM trips")
matched = one("""SELECT COUNT(DISTINCT t.permit_no) FROM trips t
                 WHERE t.permit_no IN (SELECT permit_no FROM permits)""")
print(f"  distinct trip permits: {tp};  present in permits: {matched} ({100*matched/tp:.1f}%)")
print(f"  permits with >=1 trip: {one('''SELECT COUNT(DISTINCT permit_no) FROM permits p WHERE EXISTS(SELECT 1 FROM trips t WHERE t.permit_no=p.permit_no)'''):,}")

# ---------------------------------------------------------------- stats
hr("OVERALL STATS — tonnage")
print("  total trip tonnage @source: {:,.0f} MT".format(one("SELECT SUM(qty_source) FROM trips")))
print("  total trip tonnage @dest:   {:,.0f} MT".format(one("SELECT SUM(qty_dest) FROM trips")))
print("  source−dest leakage (delta sum): {:,.0f} MT".format(one("SELECT SUM(qty_delta) FROM trips")))
print("\n  by stream:")
for r in q("""SELECT ore_stream, COUNT(*) trips, SUM(qty_dest) t FROM trips
              GROUP BY 1 ORDER BY t DESC"""):
    print(f"    {r['ore_stream']:10s} {r['trips']:>8,} trips  {r['t']:>14,.0f} MT")

print("\n  truck vs barge (heuristic: barge name has spaces / 'M V'):")
for r in q("""SELECT CASE WHEN vehicle_or_barge LIKE 'M %' OR vehicle_or_barge LIKE '%BARGE%'
                          OR INSTR(vehicle_or_barge,' ')>0 THEN 'barge/other' ELSE 'truck' END kind,
              COUNT(*) n, SUM(qty_dest) t FROM trips GROUP BY 1"""):
    print(f"    {r['kind']:12s} {r['n']:>8,} trips  {r['t']:>14,.0f} MT")

hr("TOP 15 SOURCE→DEST ROUTES (canonical, by tonnage)")
for r in q("""
    SELECT COALESCE(sa.canonical_name,t.source_location_raw) src,
           COALESCE(da.canonical_name,t.dest_location_raw) dst,
           COUNT(*) n, SUM(t.qty_dest) tons
    FROM trips t
    LEFT JOIN location_aliases sa ON sa.raw_name=t.source_location_raw
    LEFT JOIN location_aliases da ON da.raw_name=t.dest_location_raw
    GROUP BY 1,2 ORDER BY tons DESC LIMIT 15"""):
    print(f"  {r['tons']:>12,.0f} MT  {r['n']:>6,} trips  {r['src'][:32]:32s} → {r['dst'][:30]}")

hr("TOP 10 DESTINATION (inflow) LOCATIONS")
for r in q("""SELECT COALESCE(da.canonical_name,t.dest_location_raw) dst,
              SUM(t.qty_dest) tons FROM trips t
              LEFT JOIN location_aliases da ON da.raw_name=t.dest_location_raw
              GROUP BY 1 ORDER BY tons DESC LIMIT 10"""):
    print(f"  {r['tons']:>12,.0f} MT  {r['dst']}")

hr("CLOSING STOCK summary")
for r in q("""SELECT as_of_date, ore_stream, COUNT(*) locs, SUM(balance_mt) tot
              FROM closing_stock GROUP BY 1,2 ORDER BY 1,2"""):
    print(f"  {r['as_of_date']}  {r['ore_stream']:9s}  {r['locs']:>3} locs  {r['tot']:>14,.0f} MT")

# ---------------------------------------------- NEGATIVE BALANCE RECONSTRUCTION
hr("NEGATIVE-BALANCE CHECK  (per stream+location, opening = 31-Mar-2024 stock)")
# opening stock keyed by (stream, canonical location)
def canon_of(raw):
    row = con.execute("SELECT canonical_name FROM location_aliases WHERE raw_name=?", (raw,)).fetchone()
    return row[0] if row else raw
opening = defaultdict(float)
have_baseline = set()
for r in q("SELECT ore_stream, location_raw, balance_mt FROM closing_stock WHERE as_of_date='2024-03-31'"):
    opening[(r['ore_stream'], canon_of(r['location_raw']))] += (r['balance_mt'] or 0)
    have_baseline.add(r['ore_stream'])
print("  streams WITH a 31-Mar-2024 baseline:", sorted(have_baseline),
      "| MISSING:", sorted({'royalty','imported','eauction'} - have_baseline))

# build signed flow events and walk chronologically per (stream, location)
events = defaultdict(list)   # (stream, canon) -> list of (dt, signed_qty)
for r in q("""SELECT ore_stream, source_location_raw s, dest_location_raw d,
              start_dt, end_dt, qty_source, qty_dest FROM trips"""):
    cs = canon_of(r['s']) if r['s'] else None
    cd = canon_of(r['d']) if r['d'] else None
    if cs and r['qty_source'] is not None:
        events[(r['ore_stream'], cs)].append((r['start_dt'] or '', -r['qty_source']))
    if cd and r['qty_dest'] is not None:
        events[(r['ore_stream'], cd)].append((r['end_dt'] or '', +r['qty_dest']))

reported_2025 = defaultdict(float)
for r in q("SELECT ore_stream, location_raw, balance_mt FROM closing_stock WHERE as_of_date='2025-03-31'"):
    reported_2025[(r['ore_stream'], canon_of(r['location_raw']))] += (r['balance_mt'] or 0)

neg = []          # (stream, loc, min_balance, has_baseline)
recon_rows = []   # (stream, loc, opening, reconstructed_close, reported_close, diff)
for key, evs in events.items():
    stream, loc = key
    bal = opening.get(key, 0.0)
    base = stream in have_baseline
    evs.sort(key=lambda x: x[0])
    mn = bal
    for _, dq in evs:
        bal += dq
        mn = min(mn, bal)
    if mn < -1:    # tolerance 1 MT
        neg.append((stream, loc, mn, base))
    rep = reported_2025.get(key)
    if rep is not None:
        recon_rows.append((stream, loc, opening.get(key,0.0), bal, rep, bal-rep))

print(f"\n  (stream,location) series that dip below 0 (tol 1 MT): {len(neg)}")
print("  -- WITH a real 2024 baseline (these are the meaningful ones):")
real_neg = [n for n in neg if n[3]]
for s, l, mn, _ in sorted(real_neg, key=lambda x: x[2])[:20]:
    print(f"     {mn:>14,.0f} MT min   {s:9s} {l[:45]}")
if not real_neg:
    print("     (none)")
print(f"  -- WITHOUT a baseline (royalty: negatives expected, opening unknown): "
      f"{len([n for n in neg if not n[3]])}")

hr("RECONCILIATION  reconstructed vs reported 31-Mar-2025 (top mismatches)")
print("   stream / location : opening + trip-flows = reconstructed  vs  reported  (diff)")
for s, l, op, rc, rep, df in sorted(recon_rows, key=lambda x: -abs(x[5]))[:15]:
    print(f"   {s:9s} {l[:30]:30s} {op:>11,.0f} → {rc:>12,.0f}  vs {rep:>12,.0f}  ({df:>+11,.0f})")

# ---------------------------------------------------------------- completeness
hr("COMPLETENESS FLAGS")
print("  • royalty closing stock 31-Mar-2024:",
      "PRESENT" if 'royalty' in have_baseline else "*** MISSING ***")
print("  • trip permits NOT in permit headers:",
      one("SELECT COUNT(DISTINCT permit_no) FROM trips WHERE permit_no NOT IN (SELECT permit_no FROM permits)"))
print("  • permits with zero trips:",
      one("SELECT COUNT(*) FROM permits p WHERE NOT EXISTS(SELECT 1 FROM trips t WHERE t.permit_no=p.permit_no)"))
print("  • permit rows missing issue_date:",
      one("SELECT COUNT(*) FROM permits WHERE issue_date IS NULL"))
print("  • permit rows missing source OR dest location:",
      one("SELECT COUNT(*) FROM permits WHERE source_location_raw IS NULL OR dest_location_raw IS NULL"))
print("  • trips missing a weight (source or dest):",
      one("SELECT COUNT(*) FROM trips WHERE qty_source IS NULL OR qty_dest IS NULL"))
print("  • locations still type=unknown:",
      one("SELECT COUNT(*) FROM locations WHERE location_type='unknown'"), "of",
      one("SELECT COUNT(*) FROM locations"))
con.close()
