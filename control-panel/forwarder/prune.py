#!/usr/bin/env python3
"""Delete scraper cache.db entries older than 60 days (diskcache schema, stdlib sqlite3 only).
Called daily 04:15 by the forwarder. Keys 'counters' are kept. --apply deletes; default is a dry run."""
import os, pickle, sqlite3, sys, time
from collections import Counter

DB = os.environ.get("MP_CACHE", "/aimm/cache.db")
DAYS = 60


def main(apply=None):
    if apply is None:
        apply = "--apply" in sys.argv
    cut = time.time() - DAYS * 86400
    if not os.path.exists(DB):  # sqlite would CREATE an empty world-readable file before the scraper ever ran
        print("prune: no cache.db yet, nothing to do", flush=True)
        return 0
    con = sqlite3.connect(DB, timeout=30)
    try:
        total = con.execute("SELECT COUNT(*) FROM Cache").fetchone()[0]
        old, by = [], Counter()
        for rowid, key, raw in con.execute("SELECT rowid, key, raw FROM Cache WHERE store_time < ?", (cut,)).fetchall():
            k = key if raw else pickle.loads(bytes(key))
            if isinstance(k, tuple) and k and k[0] == "counters":
                continue
            old.append(rowid)
            by[k[0] if isinstance(k, tuple) and k else type(k).__name__] += 1
        print("prune", "APPLY" if apply else "DRY-RUN", f"older than {DAYS}d:", dict(by), "total", len(old), "of", total, flush=True)
        if apply and old:
            with con:
                con.executemany("DELETE FROM Cache WHERE rowid = ?", [(r,) for r in old])
            print("prune deleted", len(old), flush=True)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
