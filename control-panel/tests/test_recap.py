"""Daily recap vs health alert, scan history, monitoring-cycle tracking. Imports the real forwarder/panel modules (no network, no Docker).
Run: python3 tests/test_recap.py"""
import json, os, shutil, sys, tempfile, time
from datetime import datetime, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp()
os.environ.update({"DATA_DIR": tmp, "TG_BOT_TOKEN": "123456:abcdefghijklmnop", "TG_CHAT_ID": "1", "UI_USER": "admin", "UI_PASS": "correct-horse-battery", "INGEST_TOKEN": "i" * 24,
                   "MP_LOG": tmp + "/x.log", "TZ": "Asia/Jerusalem"})
sys.path.insert(0, ROOT + "/forwarder")
ok = fail = 0


def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)


import settings, panel, forwarder as F
os.makedirs(settings.PANEL_DIR, exist_ok=True)
def LANG(v):  # the interface language, set the way the panel sets it
    s_ = settings.load(); s_["ui"]["language"] = v; settings.save(s_)
LANG("en")
now = datetime.now(); today = now.strftime("%Y-%m-%d"); yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
rec = lambda **kw: {"at": int(time.time()), "result": "done", "new": 0, "ai_failed": 0, "searches": 3, "reviewed": 0, "ai_rated": 0, "below": 0, "passed": 0, "forwarded": 0, "saved": 0, "ingest_errors": 0, "partial": False, **kw}
H = lambda: panel.history()

# ---- recap: nothing happened -> nothing to send
check("no scans and no listings: no recap activity", F.recap_has_activity([], now) is False)
panel.history_add(rec(reviewed=40, ai_rated=40, below=40), "manual")
rows = H(); rows[0]["date"] = yday
open(panel.history_path(), "w").write(json.dumps(rows[0]) + "\n")
check("a scan on a PREVIOUS day does not make today worth a recap", F.recap_has_activity([], now) is False and panel.scan_day(today)["scans"] == 0)
panel.history_add(rec(result="cancelled"), "manual")
check("a scan cancelled before it read anything is not activity", panel.scan_day(today)["scans"] == 0)
check("relevant listings stored today make a recap worthwhile even without a recorded scan", F.recap_has_activity([{"url": "u", "item": "p1"}], now) is True)

# ---- recap: scans ran but nothing matched
panel.history_add(rec(reviewed=19, ai_rated=66, below=66, passed=0, saved=0), "manual")
check("a scan today -> recap is worthwhile", F.recap_has_activity([], now) is True)
text = "\n".join(F.build_recap([], now))
check("useful recap: scans, listings reviewed, AI evaluations, matching, forwarded, saved", all(x in text for x in ("1 scan", "19 listings reviewed", "66 AI evaluations", "66 below the relevance threshold", "0 matching", "0 forwarded", "0 saved")))
check("scans ran but nothing relevant -> says so", "No relevant listings today" in text)
check("the recap never contains the old health noise ('Monitor silent', 'Monitor OK', 'N rated today')", not any(x in text for x in ("Monitor silent", "Monitor OK", "rated today", "Monitor health")))
panel.history_add(rec(result="timeout", reviewed=5), "manual")
text = "\n".join(F.build_recap([], now))
check("a scan that did not finish is mentioned as a problem", "did not finish properly" in text and "(1 completed, 1 did not finish)" in text)
recs = [{"url": "https://facebook.com/marketplace/item/1/", "item": "p1", "title": "Hot tub", "price": 2500, "score": 5, "loc": "Haifa", "ai": "great", "currency": "ILS", "ts": now.isoformat(), "old": None}]
text = "\n".join(F.build_recap(recs, now))
check("with relevant listings the recap lists them AND summarises the scans", "Hot tub" in text and "relevant listing" in text and "24 listings reviewed" in text)

# ---- health alert is a separate thing
sent = []
F.enqueue = lambda kind, texts, key: sent.append((kind, texts, key)) or len(texts)
s = settings.load(); s["scraper"]["desired"] = "stopped"; settings.save(s)
F.monitor_watch(); check("monitoring off -> no health alert", not sent and panel.monitor_silent_hours() is None)
s = settings.load(); s["scraper"]["desired"] = "running"; settings.save(s)
panel.rt["boot"] = int(time.time()) - 2 * 3600; panel.rt["scraper_started"] = 0
F.monitor_watch(); check("monitoring on for 2 h (a normal gap between cycles) -> no alert", not sent)
panel.rt["boot"] = int(time.time()) - 6 * 3600
F.monitor_watch()
check("monitoring on, no finished cycle for 6 h -> ONE separate health alert (kind 'system'), not part of any recap", len(sent) == 1 and sent[0][0] == "system" and "stuck" in sent[0][1][0])
check("the alert wording is stable so enqueue's 24 h de-duplication keeps it to one message a day", sent[0][1][0] == "⚠️ Monitoring looks stuck: it is switched on, but no search has finished for several hours. Open the control panel (Facebook tab) to check the connection and the scraper.")
panel.history_add(rec(), "monitor")
check("a finished monitoring cycle resets the clock", panel.monitor_silent_hours() is None)
panel.rt["last_cycle"] = 0

# ---- monitoring cycles are recorded with the same counters as a manual scan
LOG = {"text": "", "inode": 1}


def fake_agent(path, body=None, timeout=30):
    if path != "/scraper/log":
        return 404, {}
    off = body.get("offset")
    size = len(LOG["text"].encode())
    if off is None:
        return 200, {"size": size, "inode": LOG["inode"], "text": "", "rotated": False, "truncated": False}
    rotated = (body.get("inode") and body["inode"] != LOG["inode"]) or off > size
    return 200, {"size": size, "inode": LOG["inode"], "rotated": rotated, "truncated": False, "text": LOG["text"] if rotated else LOG["text"].encode()[off:].decode()}


panel.agent = fake_agent
panel.H["ingest_stats"] = lambda since: {"forwarded": 1, "saved": 1, "errors": 0, "events": 1, "duplicates": 0, "last_error": ""}
panel.H["listings_all"] = lambda: []
before = len(H())
panel.rt["trk"] = None
LOG["text"] = "old stuff\n[Schedule] Next job to search p1 scheduled to run in an hour\n"
run = {"desired": "running"}
panel.track_monitor(run)  # bookmark only
LOG["text"] += "[Schedule] Next job to search p1 scheduled to run in an hour\n"
panel.track_monitor(run)
check("an idle 'next job' reminder is not recorded as a scan", len(H()) == before)
CYCLE = ("[Search] Searching facebook for p1 from Haifa\n" + "".join(f"[Retrieve] Parsing item {i}\n[AI] guard concludes No match (1): x.\n[Skip] Rating No match (1) for item {i} is below threshold 4.\n" for i in range(3))
         + "[AI] guard concludes Great (5): y.\n[Search] 1 new listings for p1 are found.\n[Schedule] Next job to search p1 scheduled to run in an hour\n")
LOG["text"] += CYCLE[:120]
panel.track_monitor(run); check("a cycle in progress is not recorded early", len(H()) == before)
LOG["text"] += CYCLE[120:]
panel.track_monitor(run)
r = H()[-1]
check("a finished monitoring cycle is recorded once with the real counters", len(H()) == before + 1 and r["kind"] == "monitor" and r["reviewed"] == 3 and r["ai_rated"] == 4 and r["below"] == 3 and r["passed"] == 1 and r["forwarded"] == 1 and r["result"] == "done")
panel.track_monitor(run); check("polling again does not duplicate it", len(H()) == before + 1)
LOG["text"] += CYCLE; panel.track_monitor(run); LOG["text"] += CYCLE; panel.track_monitor(run)
check("each following cycle is its own record", len(H()) == before + 3)
LOG["inode"] = 2; LOG["text"] = CYCLE  # the supervisor log was rotated: the new file holds the next cycle
panel.track_monitor(run)
check("after a log rotation the cycle is still recorded, flagged as possibly partial", len(H()) == before + 4 and H()[-1]["partial"] is True)
panel.H["ingest_stats"] = lambda since: {"forwarded": 0, "saved": 0, "errors": 0, "events": 0, "duplicates": 0, "last_error": ""}
LOG["text"] += CYCLE; panel.track_monitor(run)
check("a cycle that found matches that never reached the panel is recorded as a failure, not as success", H()[-1]["result"] == "forward_failed")
panel.track_monitor({"desired": "stopped"}); check("tracking stops (bookmark dropped) when monitoring is off", panel.rt["trk"] is None)

# ---- history housekeeping + home view
for _ in range(210): panel.history_add(rec(), "monitor")
check("history is capped (200 newest kept)", len(H()) == 200)
v = panel.scan_history_view(5)
check("history view: newest first, each with a plain-language outcome", len(v) == 5 and v[0]["at"] >= v[-1]["at"] and all(x["outcome"]["message"] for x in v))
open(panel.history_path(), "a").write("not json\n")
check("a damaged history line is ignored, not fatal", len(H()) == 200)
shutil.rmtree(tmp, ignore_errors=True)
# ---- the same texts in Hebrew (the default interface language)
LANG("he")
panel.history_add(rec(reviewed=5, ai_rated=5, below=5), "manual")
text_he = "\n".join(F.build_recap([], now))
check("HE recap: Hebrew header, counters in Hebrew, nothing English", "סיכום יומי" in text_he and "מודעות נבדקו" in text_he and "הערכות AI" in text_he and "Daily recap" not in text_he and "listings reviewed" not in text_he)
it = {"title": "Soda Stream", "price": 120, "old": 150, "loc": "חיפה", "url": "https://www.facebook.com/marketplace/item/1/", "ai": "מכשיר סודה סטרים במצב טוב", "currency": "ILS"}
c = F.card(1, it)
check("HE card: seller title, price, place and link kept as written; labels in Hebrew", all(x in c for x in ("Soda Stream", "חיפה", "https://www.facebook.com/marketplace/item/1/", "ירידת מחיר", "מכשיר סודה סטרים")) and "price drop" not in c)
LANG("en")
check("EN card: English labels", "price drop" in F.card(1, it))
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
