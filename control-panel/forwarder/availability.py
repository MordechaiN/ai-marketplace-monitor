"""Listing availability lifecycle: is a stored listing still for sale?

Facts behind the rules (probed on the real Hebrew account, 20 Oct 2026): a live listing page carries Facebook's own flags for that listing
id (is_sold:false, is_live:true); a removed listing redirects back to the Marketplace home page. A sold listing was not available to probe,
so 'sold' is only ever set from Facebook's own is_sold:true flag for that listing id, never guessed.

States: active | sold (Facebook says so) | unavailable (the page stopped showing the listing on CONFIRM_N checks at least MIN_GAP_S apart)
| unverified (never checked successfully). A failed look (network, timeout, login wall, checkpoint, ambiguous page) changes nothing.
Records in alerts.jsonl are never touched: this is a side file, url -> state."""
import json, os, threading, time

CONFIRM_N = int(os.environ.get("AVAIL_CONFIRM_N", "2"))          # 'unavailable' needs this many separate looks
MIN_GAP_S = int(os.environ.get("AVAIL_MIN_GAP_H", "6")) * 3600    # ... at least this far apart
RECHECK_S = int(os.environ.get("AVAIL_RECHECK_H", "12")) * 3600   # an active listing is looked at again after this long
BATCH = int(os.environ.get("AVAIL_BATCH", "8"))                   # listings per check run (agent cap: 10)
DAILY_CAP = int(os.environ.get("AVAIL_DAILY_CAP", "60"))          # Facebook page loads per day for this feature
IDLE_GAP_S = int(os.environ.get("AVAIL_IDLE_GAP_MIN", "30")) * 60  # while the scraper is off: one run per this long at most
lock = threading.Lock()


def path(data_dir):
    return os.path.join(data_dir, "listing-availability.json")


def load(data_dir):
    try:
        with open(path(data_dir), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("urls"), dict) else {"urls": {}, "day": "", "checks_today": 0}
    except (OSError, ValueError):
        return {"urls": {}, "day": "", "checks_today": 0}


def save(data_dir, d):
    tmp = path(data_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, path(data_dir))


def apply(e, result, now):
    """One check result -> the new entry. Pure. e: previous entry ({} if none). result: {'status': ...} from the scraper's checker."""
    e = dict(e or {})
    st = (result or {}).get("status")
    e["last_check"] = now
    if st == "active":
        e.update(status="active", last_ok=now, misses=0, last_miss=None)
    elif st == "sold":
        if e.get("status") != "sold":
            e["changed"] = now
        e.update(status="sold", misses=0)
    elif st == "unavailable":
        if not e.get("misses") or now - (e.get("last_miss") or 0) >= MIN_GAP_S:
            e["misses"] = int(e.get("misses") or 0) + 1
            e["last_miss"] = now
        if e["misses"] >= CONFIRM_N and e.get("status") != "unavailable":
            e.update(status="unavailable", changed=now)
        e.setdefault("status", "unverified")
    else:  # unknown / auth_failed / anything unexpected: no evidence about the listing
        e["errors"] = int(e.get("errors") or 0) + 1
        e["last_error"] = str((result or {}).get("why") or st or "unknown")[:40]
        e.setdefault("status", "unverified")
    return e


def seen_in_search(d, url, now):
    """AIMM just found the listing again in a search: it is live, whatever an earlier look said (relisted / a wrong 'unavailable')."""
    e = d["urls"].get(url)
    if e and e.get("status") != "active":
        e.update(status="active", last_ok=now, misses=0, last_miss=None, changed=now)


def due(d, urls, now):
    """The listings to look at next, oldest look first: never checked, pending misses, then active ones past RECHECK_S. Sold and
    confirmed-unavailable listings are not polled again (a new search hit brings them back)."""
    out = []
    for u in urls:
        e = d["urls"].get(u) or {}
        st, last = e.get("status"), e.get("last_check") or 0
        if st in ("sold", "unavailable"):
            continue
        if not last:
            out.append((0, u))
        elif e.get("misses") and now - (e.get("last_miss") or 0) >= MIN_GAP_S:
            out.append((1, u))
        elif st == "unverified" and now - last >= 3600:
            out.append((2, u))
        elif now - last >= RECHECK_S:
            out.append((3, u))
    out.sort(key=lambda x: (x[0], (d["urls"].get(x[1]) or {}).get("last_check") or 0))
    return [u for _, u in out]


def budget(d, today):
    if d.get("day") != today:
        d["day"], d["checks_today"] = today, 0
    return max(0, DAILY_CAP - int(d.get("checks_today") or 0))


def view(e):
    """What the listing feed shows."""
    e = e or {}
    st = e.get("status") or "unverified"
    return {"availability": st, "avail_checked": e.get("last_check"), "avail_changed": e.get("changed"), "avail_misses": int(e.get("misses") or 0)}


def mark_seen(data_dir, urls, now):
    with lock:
        d = load(data_dir)
        if any((d["urls"].get(u) or {}).get("status") not in (None, "active") for u in urls):
            for u in urls:
                seen_in_search(d, u, now)
            save(data_dir, d)


def record(data_dir, results, now, today):
    """Store one check run. results: {url: result}. -> how many listings changed to sold/unavailable in this run."""
    changed = 0
    with lock:
        d = load(data_dir)
        budget(d, today)
        for u, r in results.items():
            before = (d["urls"].get(u) or {}).get("status")
            d["urls"][u] = apply(d["urls"].get(u), r, now)
            changed += before != d["urls"][u]["status"] and d["urls"][u]["status"] in ("sold", "unavailable")
            if (r or {}).get("why") != "auth_failed":  # listings skipped after a login wall were not loaded
                d["checks_today"] = int(d.get("checks_today") or 0) + 1
        save(data_dir, d)
    return changed
