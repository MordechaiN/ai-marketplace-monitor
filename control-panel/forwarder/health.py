#!/usr/bin/env python3
"""Summarise the AIMM log into forwarder-data/health.json. Called every 15 min by the forwarder.

The AIMM log has no per-line timestamps, so each run reads only the bytes added since the
previous run (offset + inode kept in a state file) and stamps those events with "now".
--dry prints the summary and touches nothing.
"""
import json, os, re, sys, time
from datetime import datetime, timedelta

BASE = os.environ.get("MP_BASE", os.path.expanduser("~/Projects/docker/Marketplace-Scraper"))
LOG = os.environ.get("MP_LOG", os.path.join(BASE, "data", "ai-marketplace-monitor.log"))
OUT = os.environ.get("MP_OUT", os.path.join(BASE, "forwarder-data", "health.json"))
STATE = os.environ.get("MP_STATE", os.path.join(BASE, "forwarder-data", "health_state.json"))
WINDOW = timedelta(hours=24)

TAG = re.compile(r"\[/?[a-z]+\]")
SEARCH = re.compile(r"\[Search\] Searching facebook for (\S+)")
FOUND = re.compile(r"\[Search\] (\d+) new listings for")
SENT = re.compile(r"\[Notify\] Sent \S+ a message with title Found (\d+) new")
ERR = re.compile(r"Traceback|\[Error\]|\bERROR\b|checkpoint|captcha|logged out|log in to", re.I)


def classify(line, c):
    line = TAG.sub("", line)
    if SEARCH.search(line):
        c["searches"] += 1
    m = FOUND.search(line)
    if m:
        c["new_seen"] += int(m.group(1))
    m = SENT.search(line)
    if m:
        c["sent"] += int(m.group(1))
    if "[Skip] Already sent" in line:
        c["dup"] += 1
    elif "[Skip] Exclude out of area" in line:
        c["area"] += 1
    elif "[Skip] Exclude" in line:
        c["excluded"] += 1
    elif "[Skip] Rating" in line:
        c["rating"] += 1
    elif ERR.search(line) and "[Skip]" not in line and "[AI" not in line and "New item" not in line:
        c["errors"] += 1


def read_new(st):
    c = dict.fromkeys(["searches", "new_seen", "sent", "dup", "area", "excluded", "rating", "errors"], 0)
    try:
        cur = os.stat(LOG)
    except FileNotFoundError:
        return c, None
    ino, off = st.get("inode"), st.get("offset", 0)
    targets = []
    if ino is not None and ino != cur.st_ino:
        for n in range(1, 6):
            p = f"{LOG}.{n}"
            if os.path.exists(p) and os.stat(p).st_ino == ino:
                targets.append((p, off))
                break
        off = 0
    elif cur.st_size < off:
        off = 0
    targets.append((LOG, off))
    for path, start in targets:
        with open(path, "rb") as f:
            f.seek(start)
            for raw in f:
                classify(raw.decode("utf-8", "replace"), c)
    return c, cur


def main(dry=None, quiet=False):
    if dry is None:
        dry = "--dry" in sys.argv
    now = datetime.now()
    try:
        with open(STATE) as f:
            st = json.load(f)
    except (FileNotFoundError, ValueError):
        st = {}
    c, cur = read_new(st)
    if cur is None:
        print("log not found:", LOG)
        return 1
    events = st.get("events", [])
    if any(c.values()):
        events.append({"ts": now.isoformat(timespec="seconds"), **c})
    cut = (now - WINDOW).isoformat(timespec="seconds")
    events = [e for e in events if e["ts"] >= cut]
    last_search = st.get("last_search")
    if c["searches"]:
        last_search = datetime.fromtimestamp(cur.st_mtime).isoformat(timespec="seconds")
    tot = {k: sum(e.get(k, 0) for e in events) for k in c}
    health = {
        "ts": now.isoformat(timespec="seconds"),
        "last_search": last_search,
        "searches": tot["searches"],
        "new_seen": tot["new_seen"],
        "sent": tot["sent"],
        "errors": tot["errors"],
        "skipped": {k: tot[k] for k in ("dup", "excluded", "rating", "area")},
    }
    if not quiet:
        print(json.dumps(health, ensure_ascii=False, indent=1))
    if dry:
        return 0
    with open(STATE + ".tmp", "w") as f:
        json.dump({"inode": cur.st_ino, "offset": cur.st_size, "events": events, "last_search": last_search}, f)
    os.replace(STATE + ".tmp", STATE)
    with open(OUT + ".tmp", "w") as f:
        json.dump(health, f)
    os.replace(OUT + ".tmp", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
