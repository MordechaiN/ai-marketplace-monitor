"""Regressions for the 20 Oct 2026 round: availability lifecycle, scan timing, search-id resolution, AI verdict normalisation, prompt rules
and AI-cache keys. Offline: imports the real modules, no network."""
import os, sys, tempfile, time
tmp = tempfile.mkdtemp()
os.environ.update({"DATA_DIR": tmp, "TG_BOT_TOKEN": "123456:abcdefghijklmnop", "TG_CHAT_ID": "1", "UI_USER": "admin", "UI_PASS": "correct-horse-battery",
                   "INGEST_TOKEN": "i" * 24, "MP_LOG": tmp + "/x.log", "TZ": "Asia/Jerusalem", "GEMINI_API_KEY": "k" * 20, "GUARD_TOKEN": "g" * 20})
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT + "/forwarder", ROOT + "/gemini-guard"]
import availability as A, cfggen, settings, panel, forwarder as F, guard

ok = fail = 0
def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)

H6 = A.MIN_GAP_S
# ---------------- availability: one bad look is never proof
t = 1_000_000
e = A.apply({}, {"status": "active"}, t)
check("active look -> active", e["status"] == "active" and e["misses"] == 0)
for why in ("auth_failed", "load_TimeoutError", "ambiguous"):
    check(f"transient '{why}' changes nothing", A.apply(e, {"status": "unknown", "why": why}, t + 10)["status"] == "active")
e1 = A.apply(e, {"status": "unavailable"}, t + 100)
check("first 'not shown' look keeps the listing active (pending)", e1["status"] == "active" and e1["misses"] == 1)
e2 = A.apply(e1, {"status": "unavailable"}, t + 200)
check("a second look within the grace gap does not count", e2["misses"] == 1 and e2["status"] == "active")
e3 = A.apply(e2, {"status": "unavailable"}, t + 100 + H6)
check(f"CONFIRM_N={A.CONFIRM_N} separate looks -> unavailable", e3["status"] == "unavailable" and e3["changed"] == t + 100 + H6)
check("a live look later restores it (relisted / wrong guess)", A.apply(e3, {"status": "active"}, t + 2 * H6)["status"] == "active")
check("sold only from Facebook's own flag, at once", A.apply(e, {"status": "sold", "flags": {"is_sold": True}}, t)["status"] == "sold")
check("never-checked + failed look = unverified, not unavailable", A.apply({}, {"status": "unknown"}, t)["status"] == "unverified")
d = {"urls": {"u_sold": {"status": "sold", "last_check": t}, "u_gone": {"status": "unavailable", "last_check": t}, "u_fresh": {"status": "active", "last_check": t},
              "u_old": {"status": "active", "last_check": t - A.RECHECK_S}, "u_pend": {"status": "active", "misses": 1, "last_miss": t - H6, "last_check": t - H6}}}
due = A.due(d, ["u_sold", "u_gone", "u_fresh", "u_old", "u_pend", "u_new"], t)
check("due: never-checked first, then pending misses, then stale actives; sold/unavailable/fresh not polled", due == ["u_new", "u_pend", "u_old"])
d2 = {"urls": {}, "day": "2026-10-19", "checks_today": 999}
check("daily budget resets per day", A.budget(d2, "2026-10-20") == A.DAILY_CAP)
A.save(tmp, {"urls": {"u1": {"status": "unavailable", "misses": 2}}, "day": "", "checks_today": 0})
A.mark_seen(tmp, ["u1"], t)
check("found again by a search -> active, history of the look kept", A.load(tmp)["urls"]["u1"]["status"] == "active")
n = A.record(tmp, {"u2": {"status": "sold"}, "u3": {"status": "unknown", "why": "auth_failed"}}, t, "2026-10-20")
dd = A.load(tmp)
check("record: counts newly gone, page budget not charged for pages skipped after a login wall", n == 1 and dd["checks_today"] == 1 and dd["urls"]["u3"]["status"] == "unverified")
check("feed view never says sold without the flag", A.view({"status": "unavailable"})["availability"] == "unavailable" and A.view(None)["availability"] == "unverified")

# ---------------- search id: AIMM pluralises the item name in its titles
s = settings.load(); s["products"] = [{"id": "p2", "name": "x"}, {"id": "p12", "name": "y"}]
ids = {"p2", "p12"}
check("'p2s' (inflect plural) -> p2", F.item_id("p2s", ids) == "p2")
check("exact id kept; unknown names never mapped to another search", F.item_id("p12", ids) == "p12" and F.item_id("p3s", ids) == "p3s" and F.item_id("p1", ids) == "p1")
for title, want in (("Found 11 new p2s from facebook", "p2s"), ("Found 1 new p2 from facebook", "p2"), ("Found 2 discounted p2s from facebook", "p2s"), ("Another look at 3 p2s from facebook", "p2s")):
    m = F.TITLE_RE.match(title)
    check(f"title parsed: {title!r}", bool(m) and m.group(4) == want)

# ---------------- scan timing
base = {"pid": 7, "details_s": 100, "details_n": 10, "details_cached": 2, "see_more_s": 40, "ai_s": 50, "ai_n": 10, "notify_s": 1, "notify_n": 1, "guard_wait": 10}
end = {**base, "details_s": 400, "details_n": 40, "details_cached": 5, "see_more_s": 100, "ai_s": 350, "ai_n": 40, "notify_s": 6, "notify_n": 5, "guard_wait": 70}
tb = panel.timing_breakdown(base, end, 900)
check("timing: per-stage deltas of the same scraper process", tb["details"] == 300 and tb["ai"] == 300 and tb["notify"] == 5 and tb["pages_opened"] == 27 and tb["ai_wait"] == 60 and tb["other"] == 295)
tb2 = panel.timing_breakdown(base, {**end, "pid": 8}, 900)
check("timing: a new scraper process counts from zero", tb2["details"] == 400 and tb2["ai"] == 350)
check("timing: no data -> no breakdown (never a guess)", panel.timing_breakdown(None, end, 9) is None and panel.timing_breakdown(base, {"pid": None}, 9) is None)
now = int(time.time())
check("end time = scraper's last write when the panel noticed completion late", panel.scan_end_time({"done": True, "mtime": now - 600}, now - 1200) == now - 600)
check("end time of an unfinished scan = now", panel.scan_end_time({"done": False, "mtime": now - 600}, now - 1200) == now)

# ---------------- AI verdict normalisation (guard): only a well-formed final verdict counts
cases = {"reasoning\nRating 4: good": "Rating 4: good", "* **Seller Rating:** 4.8/5\nRating 2: unclear": "Rating 2: unclear", "**Rating: 3** – thin evidence": "Rating 3: thin evidence",
         "Rating 4: ok\nSeller Rating 1 later": "Rating 4: ok", '"Rating 5: מציאה"': "Rating 5: מציאה"}
for raw, want in cases.items():
    got = guard.canonical(raw)
    check(f"verdict {raw[:30]!r}", got is not None and got.endswith(want))
for bad in ("Rating: N/A, price 150", "Rating 4.5: x", "I would rate this a 4", "Rating 0: none", ""):
    check(f"malformed {bad[:25]!r} -> invalid (retried, then unrated), never a score", guard.canonical(bad) is None)
check("earlier stray 'Rating' words cannot override the final verdict", "Rating" not in guard.canonical("Seller Rating 5\nRating 2: weak").split("\n")[0])

# ---------------- relevance rules + cache keys
P = lambda **kw: {"id": "p2", "name": "SodaStream", "enabled": True, "search_phrases": ["סודה", "SodaStream"], "keywords": [], "antikeywords": [], "description": "", "min_price": 50,
                  "max_price": 200, "search_city": "", "radius": 10, "rating": None, "search_interval_min": None, "condition": [], "date_listed": 0, "delivery_method": "all", **kw}
ep = cfggen.extra_prompt(P())
check("rules: generic titles are not evidence", "למכירה" in ep and "not evidence" in ep)
check("rules: multi-item posts are weak evidence", "several different items" in ep)
check("rules: evidence may be in the description only; short brand titles are fine", "only in the description" in ep and "short title" in ep)
check("rules: preferences never assumed; no photos claimed", "Never state that a preference" in ep and "no photos" in ep)
check("rules: nothing product-specific", "soda" not in ep.lower().replace("sodastream", "", 1) and "tami" not in ep.lower())
rp_he, rp_en = cfggen.rating_prompt("he"), cfggen.rating_prompt("en")
check("rating scale identical in both languages; only the summary language differs", rp_he.split("where <1-5>")[0] == rp_en.split("where <1-5>")[0] and "in Hebrew" in rp_he and "in English" in rp_en)
check("rating scale: 3 = uncertain / thin evidence, 4 needs clear evidence", "3 - Uncertain or weak" in rp_he and "clearly shows" in rp_he)
blk = lambda p, lang="he": cfggen.item_block(p, {"search_city": "haifa"}, "", lang)
check("cache key (item config) changes with the criteria", blk(P()) != blk(P(max_price=300)) and blk(P()) != blk(P(description="עם בקבוקים")))
check("cache key changes with the rules version", f"v{cfggen.PROMPT_VERSION}]" in blk(P()))
check("two searches never share a prompt (and so never an AI cache entry)", blk(P()) != blk(P(id="p3", name="מטהר אוויר")))
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
