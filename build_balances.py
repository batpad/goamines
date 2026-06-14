"""
Precompute the per-(stream, canonical-location) balance reconstruction once, so
Datasette can serve it instantly (the chronological running-balance walk over ~800k
trip events is too slow as a live SQL window query).

Builds table `location_balance`. Called from ingest.py after locations are built.

Definitions (trip window, 25-Mar-2024 onward):
  opening_2024        reported closing stock at 31-Mar-2024 (NULL stream baseline -> 0)
  tons_in / tons_out  total trip weight arriving (@dest) / leaving (@source)
  min_running_balance opening + cumulative signed flow, walked in time order: the
                      lowest the stock ever reaches (the negative-balance test)
  reconstructed_2025  opening + net flow of trips up to 31-Mar-2025
  reported_2025       reported closing stock at 31-Mar-2025
  recon_diff          reconstructed_2025 - reported_2025
Caveats baked into interpretation (see FINDINGS §8): mines & external suppliers are
unbounded sources; export gateways are unbounded sinks (ship-loading isn't a trip);
royalty has no 2024 baseline.
"""
import sqlite3
from pathlib import Path
from collections import defaultdict

DB = Path(__file__).parent / "goamines.db"

DDL = """
DROP TABLE IF EXISTS location_balance;
CREATE TABLE location_balance (
  ore_stream TEXT, location TEXT, location_type TEXT,
  has_2024_baseline INTEGER,
  opening_2024 REAL,
  trips_in INTEGER, tons_in REAL, trips_out INTEGER, tons_out REAL,
  net_all REAL,
  min_running_balance REAL, min_balance_dt TEXT,
  reconstructed_2025 REAL, reported_2025 REAL, recon_diff REAL,
  goes_negative INTEGER
);
"""

def build(con=None):
    own = con is None
    if own:
        con = sqlite3.connect(DB)
    con.executescript(DDL)

    types = dict(con.execute("SELECT canonical_name, location_type FROM locations"))

    opening = defaultdict(float)
    baseline_streams = set()
    for s, loc, q in con.execute("""
        SELECT cs.ore_stream, la.canonical_name, SUM(cs.balance_mt)
        FROM closing_stock cs JOIN location_aliases la ON la.raw_name=cs.location_raw
        WHERE cs.as_of_date='2024-03-31' GROUP BY 1,2"""):
        opening[(s, loc)] += (q or 0)
        baseline_streams.add(s)

    reported = defaultdict(float)
    for s, loc, q in con.execute("""
        SELECT cs.ore_stream, la.canonical_name, SUM(cs.balance_mt)
        FROM closing_stock cs JOIN location_aliases la ON la.raw_name=cs.location_raw
        WHERE cs.as_of_date='2025-03-31' GROUP BY 1,2"""):
        reported[(s, loc)] += (q or 0)

    # gather signed, timestamped events per (stream, location)
    events = defaultdict(list)   # key -> [(ts, signed_qty, is_in)]
    agg = defaultdict(lambda: [0, 0.0, 0, 0.0])   # key -> [trips_in,tons_in,trips_out,tons_out]
    for loc, s, ts, q in con.execute("""
        SELECT da.canonical_name, t.ore_stream, t.end_dt, t.qty_dest
        FROM trips t JOIN location_aliases da ON da.raw_name=t.dest_location_raw
        WHERE t.qty_dest IS NOT NULL AND da.canonical_name IS NOT NULL"""):
        k = (s, loc); events[k].append((ts or "", +q)); a = agg[k]; a[0] += 1; a[1] += q
    for loc, s, ts, q in con.execute("""
        SELECT sa.canonical_name, t.ore_stream, t.start_dt, t.qty_source
        FROM trips t JOIN location_aliases sa ON sa.raw_name=t.source_location_raw
        WHERE t.qty_source IS NOT NULL AND sa.canonical_name IS NOT NULL"""):
        k = (s, loc); events[k].append((ts or "", -q)); a = agg[k]; a[2] += 1; a[3] += q

    rows = []
    keys = set(events) | set(opening) | set(reported)
    for k in keys:
        s, loc = k
        op = opening.get(k, 0.0)
        evs = sorted(events.get(k, []), key=lambda x: x[0])
        bal = op
        mn = op
        mn_dt = "2024-03-31"
        recon = op
        for ts, dq in evs:
            bal += dq
            if bal < mn:
                mn, mn_dt = bal, ts
            if ts <= "2025-03-31":
                recon += dq
        ti, qi, to, qo = agg.get(k, [0, 0.0, 0, 0.0])
        rep = reported.get(k)
        rows.append((
            s, loc, types.get(loc, "unknown"),
            1 if s in baseline_streams else 0,
            round(op, 2),
            ti, round(qi, 2), to, round(qo, 2), round(qi - qo, 2),
            round(mn, 2), mn_dt,
            round(recon, 2), (round(rep, 2) if rep is not None else None),
            (round(recon - rep, 2) if rep is not None else None),
            1 if mn < -1 else 0,
        ))
    con.executemany("INSERT INTO location_balance VALUES (" + ",".join("?" * 16) + ")", rows)
    con.execute("CREATE INDEX ix_locbal ON location_balance(ore_stream, location)")
    con.commit()
    n_neg = sum(1 for r in rows if r[-1])
    print(f"location_balance: {len(rows)} rows ({n_neg} go negative)")
    if own:
        con.close()

if __name__ == "__main__":
    build()
