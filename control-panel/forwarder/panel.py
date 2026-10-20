"""Control-panel backend: no HTTP in here. forwarder.py wires it to the server and injects hooks via init().
Secrets (bot token, guard token, agent token) are read here and never returned by any function."""
import json, os, re, threading, time, urllib.error, urllib.request
from collections import deque
from datetime import datetime

import availability, cfggen, settings

T = settings.T  # T(hebrew, english): picks the text for the language chosen in Settings (Hebrew by default)

H = {}  # hooks injected by forwarder.py: outbox_stats, send, load_day, enqueue, feedback_for, data_dir, health_path, mp_log, mp_config
ring = deque(maxlen=300)  # recent forwarder log lines, redacted
lock = threading.RLock()
rt = {"last_summary": 0, "scraper_started": 0.0, "restarts": [], "last_alert": "", "last_test": 0.0, "last_apply": None, "agent_ok": None, "boot": int(time.time()), "trk": None}

REDACT = [
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{25,}\b"), "<telegram-token>"),
    (re.compile(r"\bAI[a-zA-Z0-9_.-]{30,}\b|\bAQ\.[A-Za-z0-9_-]{20,}\b"), "<api-key>"),
    (re.compile(r"\bptr_[A-Za-z0-9+/=_-]{20,}"), "<portainer-token>"),
    (re.compile(r"(?i)(bearer|authorization:?|api[_-]?key|token|password|passwd|secret)(\s*[=:]\s*|\s+)([^\s,;'\"]{6,})"), r"\1\2<redacted>"),
    (re.compile(r"\bc_user=\d+|\bxs=[^\s;]+"), "<fb-cookie>"),
]
TAGS = re.compile(r"\x1b\[[0-9;]*m|\[/?[a-z ]{2,12}\]")


def redact(s):
    s = TAGS.sub("", str(s))
    for rx, rep in REDACT:
        s = rx.sub(rep, s)
    return s[:400]


def init(**hooks):
    H.update(hooks)


def now():
    return int(time.time())


# ---------------------------------------------------------------- agent (scraper container) client
def agent(path, body=None, timeout=30):
    """-> (http_status, json). status 0 = agent unreachable (scraper container not running / not deployed)."""
    url = os.environ.get("AGENT_URL", "http://marketplace-scraper:8470").rstrip("/") + path
    tok = H["agent_token"]()
    data = json.dumps(body).encode() if body is not None else (b"" if path != "/status" else None)
    req = urllib.request.Request(url, data=data, method="GET" if path == "/status" else "POST", headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rt["agent_ok"] = True
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except Exception:
        rt["agent_ok"] = False
        return 0, {}


# ---------------------------------------------------------------- config apply
def current_config_text():
    try:
        with open(H["mp_config"], encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def resolved_language(s):
    """The Facebook UI language AIMM must read. 'auto' follows what the last logged-in page actually showed."""
    fb = s["facebook"]
    if fb["language"] == "he":
        return "he"
    if fb["language"] == "en":
        return None
    return "he" if fb.get("detected_language", "").lower().startswith("he") else None


def build_config(s):
    return cfggen.build(s, H["guard_token"](), feedback_for=H["feedback_for"], language=resolved_language(s))


def validate_config(text, s):
    """-> (ok, message, authoritative). AIMM's own loader (inside the scraper container) is authoritative."""
    if not any(p for p in s["products"]):
        return True, T("אין מוצרים עדיין. התצורה תיבדק על ידי הסורק כשיתווסף מוצר", "No searches yet. The configuration will be checked by the scraper once a search is added"), False
    if not s["facebook"]["search_city"] and not all(p["search_city"] for p in s["products"]):
        return True, T("עדיין לא הוגדרה עיר חיפוש: ההגדרות נשמרו, והסורק לא יופעל עד שתוגדר עיר", "No search location is set yet: the settings were saved, and the scraper will not start until a city is set"), False
    st, r = agent("/validate", {"toml": text}, timeout=70)
    if st == 200:
        return bool(r.get("valid")), (r.get("error") or ""), True
    return True, T("מאגר הסורק לא זמין כרגע: בוצעה בדיקה בסיסית בלבד", "The scraper service is unavailable right now: only a basic check was done"), False


def write_config(text):
    path = H["mp_config"]
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def commit(doc, reason="user"):
    """Validate settings -> build config -> validate with AIMM -> write config -> persist settings. All-or-nothing.
    Returns (ok, {settings|errors|warning})."""
    with lock:
        s, errs = settings.clean(doc)
        if errs:
            return False, {"errors": errs}
        # the scraper's desired state is never changed by a settings edit
        s["scraper"] = settings.load()["scraper"]
        text = build_config(s)
        ok, msg, authoritative = validate_config(text, s)
        if not ok:
            return False, {"errors": [{"field": "config", "message": T("הסורק דחה את ההגדרות: ", "The scraper rejected the settings: ") + redact(msg)}]}
        cur = current_config_text()
        if cur != text:
            write_config(text)
        s2, e2 = settings.save(s)
        if e2:
            return False, {"errors": e2}
        rt["last_apply"] = {"at": now(), "reason": reason, "authoritative": authoritative, "changed": cur != text}
        out = {"settings": s2}
        if not authoritative:
            out["warning"] = msg
        return True, out


def regenerate(reason="feedback"):
    """Re-render config.toml from stored settings (e.g. when blacklist feedback changed). Writes only on change."""
    with lock:
        s = settings.load()
        text = build_config(s)
        if text == current_config_text():
            return False
        ok, msg, _ = validate_config(text, s)
        if not ok:
            print("config regenerate refused by validator:", redact(msg), flush=True)
            return False
        write_config(text)
        print("config regenerated:", reason, flush=True)
        return True


# ---------------------------------------------------------------- products
def _find(s, pid):
    return next((i for i, p in enumerate(s["products"]) if p["id"] == pid), -1)


def product_create(data):
    s = settings.load()
    s["products"].append({**data, "id": ""})
    return commit(s, "product-create")


def product_update(pid, data):
    s = settings.load()
    i = _find(s, pid)
    if i < 0:
        return False, {"errors": [{"field": "id", "message": T("המוצר לא נמצא", "Search not found")}]}
    s["products"][i] = {**data, "id": pid}
    return commit(s, "product-update")


def product_duplicate(pid):
    s = settings.load()
    i = _find(s, pid)
    if i < 0:
        return False, {"errors": [{"field": "id", "message": T("המוצר לא נמצא", "Search not found")}]}
    c = json.loads(json.dumps(s["products"][i]))
    base = re.sub(r" \((?:copy|עותק)( \d+)?\)$", "", c["name"])[:30]
    names = {p["name"].casefold() for p in s["products"]}
    n, name = 1, T(f"{base} (עותק)", f"{base} (copy)")
    while name.casefold() in names:
        n += 1
        name = T(f"{base} (עותק {n})", f"{base} (copy {n})")
    c.update(id="", name=name, enabled=False)  # a copy never starts searching by itself
    s["products"].insert(i + 1, c)
    return commit(s, "product-duplicate")


def product_toggle(pid, enabled=None):
    s = settings.load()
    i = _find(s, pid)
    if i < 0:
        return False, {"errors": [{"field": "id", "message": T("המוצר לא נמצא", "Search not found")}]}
    s["products"][i]["enabled"] = (not s["products"][i]["enabled"]) if enabled is None else bool(enabled)
    return commit(s, "product-toggle")


def product_delete(pid):
    s = settings.load()
    i = _find(s, pid)
    if i < 0:
        return False, {"errors": [{"field": "id", "message": T("המוצר לא נמצא", "Search not found")}]}
    del s["products"][i]
    return commit(s, "product-delete")


def section_update(name, data):
    if name not in ("facebook", "notifications"):
        return False, {"errors": [{"field": "section", "message": T("קטע לא מוכר", "Unknown section")}]}
    s = settings.load()
    s[name] = {**s[name], **(data if isinstance(data, dict) else {})}
    return commit(s, f"{name}-update")


# ---------------------------------------------------------------- scraper control
def preconditions(s, ag=None):
    why = []
    if ag is not None and not ((ag.get("session") or {}).get("has_login_cookies")):
        why.append(T("צריך להתחבר לפייסבוק (לשונית “פייסבוק”)", "You need to connect to Facebook (Facebook page)"))
    if not s["facebook"]["search_city"]:
        why.append(T("לא הוגדרה עיר חיפוש (לשונית “חיפושים”)", "No search location is set (Searches page)"))
    if not any(p["enabled"] for p in s["products"]):
        why.append(T("אין מוצר פעיל אחד לפחות", "No search is enabled"))
    return why


def ui_set(**kv):
    """Update the small UI-progress record (setup, last seen, last good Facebook check). Never touches the generated config."""
    with lock:
        cur = settings.load()
        before = dict(cur["ui"])
        cur["ui"].update(kv)
        if cur["ui"] != before:
            settings.save(cur)


def set_desired(desired, pause_until=None, scan_started=None, scan_offset=0, scan_inode=0, scan_file_offset=0):
    with lock:
        s = settings.load()
        s["scraper"] = {"desired": desired, "pause_until": pause_until, "scan_started": scan_started, "scan_offset": scan_offset, "scan_inode": scan_inode, "scan_file_offset": scan_file_offset}
        settings.save(s)


SCAN_TIMEOUT = int(os.environ.get("SCAN_TIMEOUT_S", 20 * 60))  # a manual scan that has not finished after 20 minutes is stopped
INGEST_GRACE = int(os.environ.get("INGEST_GRACE_S", 30))  # how long a finished scan waits for the listings AIMM reports to reach the forwarder
SCAN_MIN = int(os.environ.get("SCAN_MIN_S", 20))  # never call a scan finished in its first seconds


def ready_to_run(s, confirm):
    """Shared by 'start monitoring' and 'scan now'. -> (error_response | None, agent_status)"""
    st, ag = agent("/status")
    if st != 200:
        return (503, {"error": T("שירות הסורק לא זמין (הקונטיינר לא רץ?)", "The scraper service is unavailable (is the container running?)")}), None
    if ag.get("helper", {}).get("open"):
        return (409, {"error": T("חלון ההתחברות פתוח. סגרו אותו לפני הפעלת הסורק", "The login window is open. Close it before starting the scraper")}), None
    why = preconditions(s, ag)
    if why:
        return (400, {"error": T("לא ניתן להפעיל: ", "Cannot start: ") + "; ".join(why)}), None
    if not confirm:
        return (400, {"error": T("נדרש אישור מפורש להפעלת סריקה בפייסבוק", "Explicit confirmation is required to start scanning Facebook")}), None
    return None, ag


def log_size():
    try:
        return os.stat(H["mp_log"]).st_size
    except OSError:
        return 0


def log_since(offset):
    """Tail of AIMM's own rotating file log from `offset`. That file is only a FALLBACK: it is rotated every 1 MiB, and before the watchdog
    fix it was flooded with inotify noise. Noise lines are dropped; an offset past the end (= rotated) is reported, not trusted."""
    try:
        with open(H["mp_log"], "rb") as f:
            size = f.seek(0, 2)
            rotated = not 0 <= offset <= size
            f.seek(max(0, size - 400_000) if rotated else offset)
            txt = f.read().decode("utf-8", "replace")
    except OSError:
        return "", False
    return TAGS2.sub("", "\n".join(x for x in txt.split("\n") if not x.startswith("in-event "))), rotated


def run_log(sc):
    """-> (text, meta). Text of the current run, preferably from the supervisor stdout log inside the scraper container (authoritative, not
    rotated mid-run, no noise). meta: source 'supervisor'|'file', rotated (the start of the run may be missing), mtime (last write)."""
    st, r = agent("/scraper/log", {"offset": sc.get("scan_offset", 0), "inode": sc.get("scan_inode", 0)}, timeout=10)
    if st == 200 and not r.get("missing"):
        return TAGS2.sub("", r.get("text", "")), {"source": "supervisor", "rotated": bool(r.get("rotated") or r.get("truncated")), "mtime": r.get("mtime")}
    txt, rotated = log_since(sc.get("scan_file_offset", 0))
    try:
        mt = int(os.stat(H["mp_log"]).st_mtime)
    except OSError:
        mt = None
    return txt, {"source": "file", "rotated": rotated, "mtime": mt}


def scan_info(sc):
    """What the current manual scan has done, counted from AIMM's log lines (nothing is estimated). Cached for 2 s: Home polls every 3 s."""
    key = (sc.get("scan_started"), sc.get("scan_offset"), sc.get("scan_inode"))
    c = rt.get("info_cache")
    if c and c[0] == key and time.time() - c[1] < 2:
        return c[2]
    txt, meta = run_log(sc)
    info = parse_run(txt, meta["rotated"])
    info.update(source=meta["source"], partial=meta["rotated"], mtime=meta["mtime"])
    if meta["source"] == "supervisor":  # the console log prints URLs only for freshly opened pages; AIMM's DEBUG file log has every listing
        try:
            ftxt, frot = log_since(sc.get("scan_file_offset", 0))
            if not frot:
                info["items_opened"] = max(info["items_opened"], len(set(ITEM_ID.findall(ftxt))))
        except Exception:
            pass
    rt["info_cache"] = (key, time.time(), info)
    return info


CONT = re.compile(r"^ {29}\S")


def unwrap(txt, glue):
    """The supervisor log is rich console output: messages are wrapped at a fixed width and continued on lines indented 29 spaces, and long
    tokens (URLs) are split mid-word. glue=" " restores sentences (verdicts), glue="" restores split URLs (item ids)."""
    out = []
    for ln in txt.split("\n"):
        if out and CONT.match(ln):
            out[-1] = out[-1].rstrip() + glue + ln.strip()
        else:
            out.append(ln)
    return "\n".join(out)


def parse_run(txt, rotated=False):
    """The counters of one run (manual scan or one monitoring cycle), counted from AIMM's own log lines. Nothing is estimated."""
    flat, glued = unwrap(txt, " "), unwrap(txt, "")
    last_search = txt.rfind("Searching facebook for")
    # unique listings = distinct Facebook item ids. "[Retrieve] Parsing" only counts detail pages fetched fresh (cached ones skip it), so it
    # undercounted: the 3 Oct scan logged 19 parses but 68 listings and 66 AI evaluations (44 of them answered from AIMM's AI cache).
    unique = max(len(set(ITEM_ID.findall(glued))), txt.count("[Retrieve] Parsing"))
    evals = [m.groups() for m in AI_VERDICT.finditer(flat)]
    unique = max(unique, len({t.strip() for _v, _n, _r, t in evals}))  # every evaluated listing was reviewed (console logs omit cached URLs)
    ratings = {}
    for _v, n, _r, _t in evals:
        ratings[n] = ratings.get(n, 0) + 1
    thr = [int(x) for x in re.findall(r"is below threshold (\d)", flat)]
    rejected = sorted(((int(n), v, r, t) for v, n, r, t in evals if thr and int(n) < max(thr)), key=lambda e: -e[0])
    examples, seen = [], set()
    for n, _v, r, t in rejected:  # the closest misses first: they say the most about why nothing passed
        if t not in seen and len(examples) < 5:
            seen.add(t)
            examples.append({"rating": n, "title": t.strip()[:70], "reason": r.strip()[:160]})
    return {"launched": "Successfully launched" in txt, "login": "facebook.com/login" in txt or "[Login]" in txt, "searches": txt.count("Searching facebook for"),
            "items_opened": unique, "item_failed": txt.count("Failed to get item details"), "ai_rated": len(re.findall(r"\[AI\].*concludes", txt)),
            "ai_cached": txt.count("previously concluded"), "ratings": ratings, "examples": examples,
            "below": txt.count("[Skip] Rating"), "passed": sum(int(n) for n in re.findall(r"(\d+) new listings? for \S+ (?:is|are) found", txt)),
            "ai_failed": len(AI_ERR.findall(txt)), "ai_seen": "[AI]" in txt, "done": (last_search >= 0 and txt.rfind("Next job to search") > last_search) or (rotated and last_search < 0 and "Next job to search" in txt)}


def scan_result(sc):
    """None while the manual scan is still running, else 'done' | 'timeout'. AIMM prints 'Next job to search ...' once every search of a cycle has
    finished. A finished cycle is ALWAYS reported as done, even when it is noticed after the time limit."""
    elapsed = now() - (sc.get("scan_started") or now())
    if elapsed < SCAN_MIN:
        return None
    info = scan_info(sc)
    if info["done"]:
        # the notifications of the last search may still be on their way to the forwarder: wait (bounded) until what AIMM says it found has arrived
        arrived = H["ingest_stats"](sc.get("scan_started") or 0)["forwarded"]
        rt.setdefault("done_at", now())
        if info["passed"] > arrived and now() - rt["done_at"] < INGEST_GRACE:
            return None
        return "done"
    rt.pop("done_at", None)
    return "timeout" if elapsed > SCAN_TIMEOUT else None


ITEM_ID = re.compile(r"facebook\.com/marketplace/item/(\d+)")
AI_VERDICT = re.compile(r"\[AI\] \S+ concludes (.+?) \((\d)\): (.*) for listing (.*?)\.?[ \t]*$", re.M)
AI_ERR = re.compile(r"\[AI-Error\]|\[AI\].*(Failed to get an answer|did not evaluate)|Not evaluated by AI")


def new_since(ts):
    return len({r["url"] for r in H["listings_all"]() if r.get("ts") and int(datetime.fromisoformat(r["ts"]).timestamp()) >= ts})


def last_scan_record(result, sc, info=None):
    """The stored result of a manual scan: the outcome plus the real counters behind it."""
    info = info or scan_info(sc)
    ing = H["ingest_stats"](sc.get("scan_started") or 0)
    return {"at": now(), "result": result, "new": new_since(sc.get("scan_started") or now()), "ai_failed": info["ai_failed"], "searches": info["searches"], "reviewed": info["items_opened"],
            "ai_rated": info["ai_rated"], "below": info["below"], "passed": info["passed"], "forwarded": ing["forwarded"], "saved": ing["saved"], "ingest_errors": ing["errors"], "partial": info["partial"],
            "ai_cached": info.get("ai_cached", 0), "ratings": info.get("ratings", {}), "examples": info.get("examples", [])}


def classify_done(rec, info):
    """A finished cycle is only a success if it read the listings AND they reached the panel."""
    if rec["result"] != "done":
        return
    if info["searches"] == 0 and not info["partial"]:  # after a log rotation the Searching lines may be missing: do not call that a failure
        rec["result"] = "failed"
    elif info["items_opened"] and info["item_failed"] >= info["items_opened"]:
        rec["result"] = "read_failed"
    elif rec["ingest_errors"]:
        rec["result"] = "record_failed"
    elif info["passed"] > rec["forwarded"]:
        rec["result"] = "forward_failed"


# ---------------------------------------------------------------- scan timing
# Where a scan's time went. AIMM's log has no timestamps, so the scraper writes cumulative seconds per stage (scraper-extras/sitecustomize.py
# -> timing.json next to config.toml) and the AI guard reports its own upstream/wait seconds. A scan = the difference between a snapshot at
# its start and one at its end. Stages: details (listing pages), ai (evaluation; guard_wait = pacing/rate-limit/backoff inside it),
# notify (forwarding + storage), other (search-result pages, browser start, login: the remainder). Missing data -> no breakdown, never a guess.
TIMING_KEYS = ("details_s", "details_n", "details_cached", "see_more_s", "ai_s", "ai_n", "notify_s", "notify_n")


def aimm_timing():
    try:
        with open(os.path.join(os.path.dirname(H["mp_config"]), "timing.json"), encoding="utf-8") as f:
            t = json.load(f)
        return t if isinstance(t, dict) else {}
    except (OSError, ValueError, KeyError):
        return {}


def timing_snapshot():
    g = H["ai_health"]() if "ai_health" in H else {}
    t = aimm_timing()
    return {"at": now(), "pid": t.get("pid"), **{k: float(t.get(k, 0) or 0) for k in TIMING_KEYS},
            "guard_busy": g.get("busy_total"), "guard_wait": g.get("wait_total")}


def timing_breakdown(base, end_snap, elapsed):
    """-> {stage: whole seconds} or None. A new scraper process (pid changed) started from zero."""
    if not base or not end_snap or end_snap.get("pid") is None:
        return None
    b = base if base.get("pid") == end_snap["pid"] else {}
    d = {k: max(0.0, end_snap[k] - float(b.get(k, 0) or 0)) for k in TIMING_KEYS}
    out = {"details": round(d["details_s"]), "see_more": round(d["see_more_s"]), "ai": round(d["ai_s"]), "notify": round(d["notify_s"]),
           "pages_opened": int(d["details_n"] - d["details_cached"]), "ai_calls": int(d["ai_n"]), "notified": int(d["notify_n"])}
    out["other"] = max(0, round(elapsed - d["details_s"] - d["ai_s"] - d["notify_s"]))
    gb, gw = base.get("guard_wait"), end_snap.get("guard_wait")
    if isinstance(gb, (int, float)) and isinstance(gw, (int, float)) and gw >= gb:
        out["ai_wait"] = round(gw - gb)
    return out


def timing_base_path():
    return os.path.join(settings.PANEL_DIR, "scan-timing-base.json")


def save_timing_base(started):
    try:
        with open(timing_base_path(), "w", encoding="utf-8") as f:
            json.dump({"started": started, **timing_snapshot()}, f)
    except OSError:
        pass


def load_timing_base(started):
    try:
        with open(timing_base_path(), encoding="utf-8") as f:
            b = json.load(f)
        return b if b.get("started") == started else None  # survives a panel restart, never mixes two scans
    except (OSError, ValueError):
        return None


def scan_end_time(info, started):
    """A finished scan ends at the scraper's last write, not when the panel happened to notice it."""
    mt = info.get("mtime")
    return int(mt) if info.get("done") and isinstance(mt, (int, float)) and started <= mt <= now() else now()


# ---------------------------------------------------------------- scan history (manual scans AND monitoring cycles, one record format)
HISTORY_MAX = 200
hist_lock = threading.Lock()


def history_path():
    return os.path.join(settings.PANEL_DIR, "scan-history.jsonl")


def history_add(rec, kind, started=None):
    rec = {**rec, "kind": kind, "date": datetime.now().strftime("%Y-%m-%d"), "started": int(rec.get("started") or started or rec["at"])}
    try:
        with hist_lock:
            os.makedirs(settings.PANEL_DIR, exist_ok=True)
            rows = history()
            rows.append(rec)
            if len(rows) > HISTORY_MAX:
                rows = rows[-HISTORY_MAX:]
                tmp = history_path() + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
                os.replace(tmp, history_path())
            else:
                with open(history_path(), "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print("scan history not written:", type(e).__name__, flush=True)  # never let bookkeeping break a scan


def history():
    out = []
    try:
        with open(history_path(), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and isinstance(r.get("at"), (int, float)):
                    out.append(r)
    except OSError:
        pass
    return out


def scan_day(day):
    """What the scanner did on one calendar day (YYYY-MM-DD) according to the history. A scan cancelled before it read anything is not activity."""
    rows = [r for r in history() if r.get("date") == day and (r.get("result") != "cancelled" or r.get("reviewed"))]
    ok = {"done"}
    out = {"scans": len(rows), "completed": sum(1 for r in rows if r["result"] in ok), "failed": [r["result"] for r in rows if r["result"] not in ok]}
    for k in ("reviewed", "ai_rated", "ai_failed", "below", "passed", "forwarded", "saved"):
        out[k] = sum(int(r.get(k, 0) or 0) for r in rows)
    return out


def record_scan(result, sc, info=None):
    """Store the outcome of a manual scan: as the 'last scan' and in the history."""
    info = info or scan_info(sc)
    rec = last_scan_record(result, sc, info)
    classify_done(rec, info)
    started = int(sc.get("scan_started") or rec["at"])
    rec["started"], rec["ended"] = started, scan_end_time(info, started)
    tb = timing_breakdown(load_timing_base(sc.get("scan_started")), timing_snapshot(), rec["ended"] - started)
    if tb:
        rec["timing"] = tb
    ui_set(last_scan=rec)
    history_add(rec, "manual", sc.get("scan_started"))
    return rec


def track_monitor(sc):
    """While monitoring is on, close every finished search cycle into the history (same counters as a manual scan). Cycles are cut from
    AIMM's supervisor log at its 'Next job to search' line; the bookmark lives in memory (a panel restart skips the cycle in flight)."""
    if sc["desired"] != "running":
        rt["trk"] = None
        return
    t = rt.get("trk")
    if t is None:
        st, m = agent("/scraper/log", {"offset": None}, timeout=10)
        if st == 200 and not m.get("missing"):
            rt["trk"] = {"offset": int(m["size"]), "inode": int(m["inode"]), "buf": "", "began": 0, "partial": False}
        return
    st, r = agent("/scraper/log", {"offset": t["offset"], "inode": t["inode"]}, timeout=10)
    if st != 200 or r.get("missing"):
        return
    t["offset"], t["inode"] = int(r["size"]), int(r["inode"])
    t["partial"] = t["partial"] or bool(r.get("rotated") or r.get("truncated"))
    t["buf"] = (t["buf"] + TAGS2.sub("", r.get("text", "")))[-2_000_000:]
    if not t["began"] and "Searching facebook for" in t["buf"]:
        t["began"], t["tbase"] = now(), timing_snapshot()
    cut = t["buf"].rfind("Next job to search")
    eol = t["buf"].find("\n", cut) if cut >= 0 else -1
    if eol < 0:
        return
    seg, t["buf"] = t["buf"][:eol + 1], t["buf"][eol + 1:]
    if "Searching facebook for" not in seg:
        t["began"], t["partial"] = 0, False  # an idle 'next job' reminder, not a cycle
        return
    info = parse_run(seg, t["partial"])
    info.update(partial=t["partial"])
    began = t["began"] or now()
    rec = last_scan_record("done", {"scan_started": began - 25}, info)
    classify_done(rec, info)
    rec["started"], rec["ended"] = began, now()
    tb = timing_breakdown(t.get("tbase"), timing_snapshot(), rec["ended"] - began)
    if tb:
        rec["timing"] = tb
    history_add(rec, "monitor", began)
    rt["last_cycle"] = rec["at"]
    t["began"], t["partial"] = 0, False
    print(f"monitor: cycle finished ({rec['result']}, reviewed={rec['reviewed']}, passed={rec['passed']}, saved={rec['saved']})", flush=True)


def monitor_silent_hours():
    """Hours since the last finished search cycle, ONLY when monitoring is on and that is longer than it should be (a genuine problem), else None."""
    s = settings.load()
    if s["scraper"]["desired"] != "running":
        return None
    last = max([rt.get("last_cycle", 0)] + [r["at"] for r in history() if r.get("kind") == "monitor"][-1:])
    ref = max(last, rt.get("scraper_started") or 0, rt["boot"])
    limit_h = max(4.0, 3 * s["facebook"]["max_search_interval_min"] / 60)
    hrs = (now() - ref) / 3600
    return hrs if hrs > limit_h else None


def finish_scan(result):
    sc = settings.load()["scraper"]
    info = scan_info(sc)
    rec = record_scan(result, sc, info)
    agent("/scraper/stop", {})
    set_desired("stopped")
    rt.pop("done_at", None)
    print(f"scraper: manual scan finished ({rec['result']}, reviewed={rec['reviewed']}, ai={rec['ai_rated']}, passed={rec['passed']}, forwarded={rec['forwarded']}, saved={rec['saved']}, new={rec['new']}, ai_failed={rec['ai_failed']}, source={info['source']})", flush=True)


def scan_history_view(n=8):
    """The most recent scans (manual and automatic) for Home, newest first, each with the plain-language outcome."""
    out = []
    for r in reversed(history()[-n:]):
        out.append({"at": r["at"], "kind": r.get("kind", "manual"), "started": r.get("started", r["at"]), "result": r["result"], "outcome": scan_outcome(r), **{k: int(r.get(k, 0) or 0) for k in ("searches", "reviewed", "ai_rated", "ai_cached", "ai_failed", "below", "passed", "forwarded", "saved", "new", "ingest_errors")},
                    "ratings": r.get("ratings") or {}, "examples": r.get("examples") or [], "ended": int(r.get("ended") or r["at"]),
                    "duration": max(0, int(r.get("ended") or r["at"]) - int(r.get("started", r["at"]))), "timing": r.get("timing")})
    return out


def scan_stats_text(ls):
    t = T(f"נבדקו {ls.get('reviewed', 0)} מודעות · הערכות AI: {ls.get('ai_rated', 0)} · כשלי AI: {ls.get('ai_failed', 0)} · עברו את סף הרלוונטיות: {ls.get('passed', 0)} · נשמרו: {ls.get('saved', 0)}",
          f"Reviewed {ls.get('reviewed', 0)} listings · AI evaluations: {ls.get('ai_rated', 0)} · AI failures: {ls.get('ai_failed', 0)} · passed the relevance threshold: {ls.get('passed', 0)} · recorded: {ls.get('saved', 0)}")
    if ls.get("ai_cached"):
        t += T(f" · מתוכן {ls['ai_cached']} מהזיכרון (בלי פנייה חדשה ל-AI)", f" · {ls['ai_cached']} of them from cache (no new AI call)")
    if ls.get("partial"):
        t += T(" · ייתכן שהספירה חלקית (היומן התחלף במהלך הסריקה)", " · the counts may be incomplete (the log rotated during the scan)")
    return t


def scan_outcome(ls):
    """Plain-language result of the last scan, with the real counters. Zero results is only ever said for a scan that completed and read listings."""
    if not ls:
        return None
    r, n, aif = ls["result"], ls.get("new", 0), ls.get("ai_failed", 0)
    stats = {k: ls.get(k, 0) for k in ("reviewed", "ai_rated", "ai_failed", "below", "passed", "forwarded", "saved", "new", "ingest_errors")}
    detail = scan_stats_text(ls)
    if r == "done":
        if ls.get("passed", 0) == 0 and not aif and not ls.get("reviewed", 0):
            return {"level": "info", "code": "no_listings", "message": T("החיפוש לא החזיר מודעות", "The search returned no listings"), "detail": detail, "stats": stats}
        if ls.get("passed", 0) == 0 and not aif:
            return {"level": "info", "code": "no_results", "message": T("לא נמצאו מודעות מתאימות", "No matching listings found"), "detail": detail, "stats": stats}
        if n == 0 and ls.get("passed", 0) and not aif:
            return {"level": "info", "code": "known", "message": T(f"נמצאו {ls['passed']} מודעות מתאימות, אך אין בהן חדשות (כבר היו ידועות או נחסמו)", f"{ls['passed']} matching listing{'' if ls['passed'] == 1 else 's'} found, but {'it is' if ls['passed'] == 1 else 'none are'} new (already known or blocked)"), "detail": detail, "stats": stats}
        msg = T(f"הסריקה הסתיימה: נמצאו {n} מודעות מתאימות חדשות", f"Scan completed: {n} new matching listings found")
        if aif:
            msg += T(f". הדירוג החכם נכשל ב-{aif} מודעות, והן נשמרו ללא דירוג", f". The AI rating failed for {aif} listings, which were kept unrated")
        return {"level": "warn" if aif else "ok", "code": "done", "message": msg, "detail": detail, "stats": stats}
    msgs = {"cancelled": ("info", T("הסריקה בוטלה", "The scan was cancelled")), "timeout": ("bad", T(f"הסריקה לא הסתיימה בזמן ({max(1, SCAN_TIMEOUT // 60)} דקות) ונעצרה", f"The scan did not finish in time ({max(1, SCAN_TIMEOUT // 60)} minutes) and was stopped")),
            "checkpoint": ("bad", T("הסריקה נעצרה: פייסבוק ביקשה אימות", "The scan stopped: Facebook asked for verification")),
            "login": ("bad", T("הסריקה נכשלה: ההתחברות לפייסבוק פגה או נדרשת מחדש", "The scan failed: the Facebook login expired or is required again")),
            "crashed": ("bad", T("הסריקה נכשלה: הסורק קרס", "The scan failed: the scraper crashed")),
            "read_failed": ("bad", T("הסריקה נכשלה: הסורק לא הצליח לקרוא את המודעות (בדקו את שפת החשבון)", "The scan failed: the scraper could not read the listings (check the account language)")),
            "forward_failed": ("bad", T(f"נמצאו {ls.get('passed', 0)} מודעות מתאימות, אך רק {ls.get('forwarded', 0)} הגיעו ללוח הבקרה. השאר לא נשמרו", f"{ls.get('passed', 0)} matching listings were found, but only {ls.get('forwarded', 0)} reached the control panel. The rest were not saved")),
            "record_failed": ("bad", T(f"נמצאו מודעות מתאימות, אך שמירתן נכשלה ({ls.get('ingest_errors', 0)} שגיאות)", f"Matching listings were found, but saving them failed ({ls.get('ingest_errors', 0)} errors)")),
            "failed": ("bad", T("הסריקה נכשלה: לא בוצע אף חיפוש", "The scan failed: no search was performed"))}
    lvl, msg = msgs.get(r, ("bad", T("הסריקה נכשלה", "The scan failed")))
    return {"level": lvl, "code": r, "message": msg, "detail": detail if ls.get("reviewed") or ls.get("searches") else "", "stats": stats}


def ai_status():
    """Gemini health, classified so the user can tell WHY rating is not working (separate from scraping problems). None when healthy."""
    ai = H["ai_health"]()
    if not ai or ai.get("unreachable"):
        return {"code": "unreachable", "severity": "bad", "title": T("שירות הדירוג החכם לא זמין", "The AI rating service is unavailable"),
                "detail": T("השירות לא עונה. מודעות חדשות יישמרו ללא דירוג ויופיעו בסיכום היומי.", "The service is not responding. New listings will be kept unrated and appear in the daily summary.")}
    why = (ai.get("breaker_reason") or "") if ai.get("breaker_open") else ""
    err = ai.get("last_error") or ""
    if "rejected" in why or "not found" in why or "http 400" in err or "http 403" in err or "http 401" in err or "http 404" in err:
        return {"code": "misconfigured", "severity": "bad", "title": T("הדירוג החכם לא מוגדר נכון", "AI rating is misconfigured"),
                "detail": T("Gemini דחה את הבקשה: מפתח ה-API או שם המודל לא תקינים. יש לתקן ב-Portainer (GEMINI_API_KEY / GEMINI_MODEL). מודעות נשמרות ללא דירוג.", "Gemini rejected the request: the API key or model name is invalid. Fix it in Portainer (GEMINI_API_KEY / GEMINI_MODEL). Listings are kept unrated.")}
    if why:
        if "quota" in why or "cap" in why:
            return {"code": "quota", "severity": "warn", "title": T("מכסת Gemini נגמרה", "Gemini quota is used up"),
                    "detail": T(f"הדירוג יחזור מעצמו אחרי איפוס המכסה ({ai.get('breaker_until') or '?'}). עד אז מודעות נשמרות ללא דירוג.", f"Rating resumes by itself after the quota resets ({ai.get('breaker_until') or '?'}). Until then listings are kept unrated.")}
        return {"code": "error", "severity": "warn", "title": T("הדירוג החכם מושהה", "AI rating is paused"), "detail": T(f"סיבה: {why}. מודעות נשמרות ללא דירוג.", f"Reason: {why}. Listings are kept unrated.")}
    if ai.get("rate_limited") and "429" in err:
        return {"code": "rate_limited", "severity": "warn", "title": T("Gemini מגביל את קצב הבקשות", "Gemini is rate-limiting requests"), "detail": T("הדירוג איטי יותר אבל ממשיך. אין צורך בפעולה.", "Rating is slower but continues. No action needed.")}
    if (ai.get("invalid") or ai.get("timeouts")) and err:
        return {"code": "error", "severity": "warn", "title": T("שגיאות זמניות בדירוג החכם", "Temporary AI rating errors"), "detail": T(f"שגיאה אחרונה: {err}. מודעות שלא דורגו נשמרות ומסומנות.", f"Last error: {err}. Listings that could not be rated are kept and marked.")}
    return None


def scan_status(s, ag, st, conn_):
    """Live state of a manual scan for the Home page: a stage + real counters, never an invented percentage."""
    sc = s["scraper"]
    if sc["desired"] != "scan":
        return None
    info = scan_info(sc)
    total = sum(len(p["search_phrases"]) for p in s["products"] if p["enabled"])
    if info["done"]:
        stage, label = "completed", T("הסריקה הסתיימה", "Scan completed")
    elif info["ai_rated"] or info["ai_failed"]:
        stage, label = "ai", T(f"שולחים מודעות להערכת AI ({info['ai_rated']} דורגו)", f"Sending listings for AI evaluation ({info['ai_rated']} rated)")
    elif info["items_opened"]:
        stage, label = "processing", T(f"מעבדים מודעות ({info['items_opened']} נפתחו)", f"Processing listings ({info['items_opened']} opened)")
    elif info["searches"]:
        stage, label = "searching", T(f"מריצים חיפוש ({min(info['searches'], total)} מתוך {total})", f"Running search ({min(info['searches'], total)} of {total})")
    elif info["launched"] or info["login"]:
        stage, label = "connecting", T("מתחברים לפייסבוק", "Connecting to Facebook")
    else:
        stage, label = "starting", T("מתחילים סריקה", "Starting scan")
    idle = max(0, now() - info["mtime"]) if info.get("mtime") else 0
    since = now() - (sc.get("scan_started") or now())
    err = None
    if conn_["severity"] == "bad" and conn_["code"] in ("checkpoint", "session_expired", "login_required", "browser_failed", "scraper_crashed", "config_error"):
        err = {"title": conn_["title"], "detail": conn_["detail"]}
    return {"active": True, "stage": stage, "label": label, "elapsed": since, "searches": info["searches"], "total_searches": total, "items_opened": info["items_opened"], "ai_rated": info["ai_rated"], "ai_failed": info["ai_failed"], "below": info["below"], "passed": info["passed"],
            "item_failed": info["item_failed"], "stuck": since > 60 and idle > 180, "idle": idle, "error": err, **last_event(sc)}


EVENT_HE = {"opened a listing page": "נפתח דף מודעה", "AI evaluated a listing": "מודעה דורגה ב-AI", "sent a match to the panel": "מודעה מתאימה נשלחה ללוח הבקרה"}


def last_event(sc):
    """The scraper's last meaningful step during this scan (from timing.json), in the panel language."""
    t = aimm_timing()
    ev, at = str(t.get("last_event") or ""), t.get("last_event_at")
    if not ev or not isinstance(at, (int, float)) or at < (sc.get("scan_started") or 0):
        return {"last_event": None, "last_event_age": None}
    base, failed = (ev[:-9], True) if ev.endswith(" (failed)") else (ev, False)
    txt = T(EVENT_HE.get(base, base) + (" (נכשל)" if failed else ""), ev)
    return {"last_event": txt, "last_event_age": max(0, now() - int(at))}


def scraper_action(action, hours=None, confirm=False):
    s = settings.load()
    if action == "scan":
        cur = s["scraper"]["desired"]
        if cur == "scan":
            return 409, {"error": T("סריקה כבר רצה", "A scan is already running")}
        if cur != "stopped":
            return 409, {"error": T("המעקב פועל או מושהה. סריקה ידנית זמינה רק כשהמעקב כבוי", "Monitoring is on or paused. A manual scan is only available while monitoring is off")}
        err, ag = ready_to_run(s, confirm)
        if err:
            return err
        rt["scraper_started"] = now()
        rt["restarts"].clear()
        _, mark = agent("/scraper/log", {"offset": None}, timeout=10)
        rt.pop("done_at", None)
        save_timing_base(now())
        set_desired("scan", scan_started=now(), scan_offset=int(mark.get("size") or 0), scan_inode=int(mark.get("inode") or 0), scan_file_offset=log_size())  # BEFORE starting, same reason as 'start'
        st, r = agent("/scraper/start", {})
        if st != 200:
            set_desired("stopped")
            return 502, {"error": T("הפעלת הסריקה נכשלה", "Starting the scan failed")}
        print("scraper: manual scan requested from panel", flush=True)
        return 200, {"ok": True}
    if action == "start":
        err, ag = ready_to_run(s, confirm)
        if err:
            return err
        rt["scraper_started"] = now()
        rt["restarts"].clear()
        rt["trk"] = None
        track_monitor({"desired": "running"})  # bookmark the log BEFORE the scraper writes to it, so the first cycle is counted completely
        set_desired("running")  # BEFORE starting: the reconcile loop stops anything that runs while desired != running
        st, r = agent("/scraper/start", {})
        if st != 200:
            set_desired("stopped")
            return 502, {"error": T("הפעלת הסורק נכשלה", "Starting the scraper failed")}
        ui_set(ever_started=True)
        print("scraper: start requested from panel", flush=True)
        return 200, {"ok": True}
    if action == "pause":
        try:
            h = float(hours)
        except (TypeError, ValueError):
            return 400, {"error": T("משך ההשהיה בשעות חסר", "Pause duration in hours is missing")}
        if not 0.25 <= h <= 72:
            return 400, {"error": T("משך ההשהיה: בין רבע שעה ל-72 שעות", "Pause duration must be between 15 minutes and 72 hours")}
        set_desired("paused", now() + int(h * 3600))
        agent("/scraper/stop", {})
        print(f"scraper: paused for {h} h", flush=True)
        return 200, {"ok": True}
    if action == "stop":
        if s["scraper"]["desired"] == "scan":
            record_scan("cancelled", s["scraper"])
        set_desired("stopped")
        agent("/scraper/stop", {})
        print("scraper: stop requested from panel", flush=True)
        return 200, {"ok": True}
    return 400, {"error": T("פעולה לא מוכרת", "Unknown action")}


# ---------------------------------------------------------------- listing availability (bounded background re-checks)
def availability_tick(desired):
    """Start one bounded availability run when it is safe: the scraper is off (at most every AVAIL_IDLE_GAP_MIN), or monitoring has just
    finished a cycle (AIMM's browser idles until the next one). Never during a manual scan, never while a run is in flight, never past the
    daily page budget, and paused for 6 h after Facebook showed a login wall or checkpoint."""
    t = now()
    if rt.get("avail_busy") or t < rt.get("avail_block_until", 0) or desired == "scan":
        return
    if desired == "running":
        lc = rt.get("last_cycle") or 0
        if not lc or t - lc > 120 or rt.get("avail_cycle") == lc:
            return
        rt["avail_cycle"] = lc
    elif t - rt.get("avail_last", 0) < availability.IDLE_GAP_S:
        return
    d = availability.load(settings.DATA_DIR)
    left = availability.budget(d, datetime.now().strftime("%Y-%m-%d"))
    urls = [H["norm_url"](r["url"]) for r in H["listings_all"]() if r.get("url")]  # blocked listings are already out of the history
    batch = availability.due(d, list(dict.fromkeys(urls)), t)[:min(availability.BATCH, left)]
    rt["avail_last"] = t
    if not batch:
        return
    rt["avail_busy"] = True

    def run():
        try:
            st, r = agent("/listings/check", {"urls": batch, "between_cycles": desired == "running"}, timeout=80 + 15 * len(batch))
            if st != 200:
                print(f"availability: check skipped ({st} {r.get('error', '')})", flush=True)
                return
            res = r.get("results") or {}
            if any((x or {}).get("status") == "auth_failed" for x in res.values()):
                rt["avail_block_until"] = now() + 6 * 3600
            n = availability.record(settings.DATA_DIR, res, now(), datetime.now().strftime("%Y-%m-%d"))
            counts = {}
            for x in res.values():
                counts[(x or {}).get("status")] = counts.get((x or {}).get("status"), 0) + 1
            print(f"availability: checked {len(res)} {counts} newly gone {n}", flush=True)
        except Exception as e:
            print("availability: run failed:", type(e).__name__, flush=True)
        finally:
            rt["avail_busy"] = False
    threading.Thread(target=run, daemon=True).start()


def reconcile():
    """Keep the real scraper process in the state the user asked for. Default (and after any container restart) = stopped."""
    s = settings.load()
    sc = s["scraper"]
    st, ag = agent("/status")
    if st != 200:
        return
    running = ag.get("scraper_running", False)
    ls0 = ag.get("login_status") or {}
    lang = (ls0.get("lang") or "")[:3].lower()
    if ls0.get("state") == "logged_in" and lang and lang != s["facebook"].get("detected_language", "")[:3].lower():
        with lock:
            cur = settings.load()
            cur["facebook"]["detected_language"] = lang
            settings.save(cur)
        print(f"facebook: account language detected: {lang}", flush=True)
        regenerate("language")
        s = settings.load()
    if ls0.get("state") == "logged_in" and ls0.get("at", 0) > s["ui"]["facebook_last_ok"] + 120:
        ui_set(facebook_last_ok=int(ls0["at"]))
    if sc["desired"] == "paused" and sc["pause_until"] and now() >= sc["pause_until"]:
        if preconditions(s, ag):
            set_desired("stopped")
            return
        set_desired("running")
        sc = {"desired": "running", "pause_until": None}
        rt["scraper_started"] = now()
        rt["restarts"].clear()
    try:
        track_monitor(sc)
    except Exception as e:  # bookkeeping must never stop the reconcile loop
        print("monitor tracking error:", type(e).__name__, flush=True)
    try:
        if sc["desired"] != "scan" and not (ag.get("helper") or {}).get("open"):
            availability_tick("running" if sc["desired"] == "running" else "stopped")
    except Exception as e:
        print("availability tick error:", type(e).__name__, flush=True)
    if sc["desired"] == "scan":
        res = scan_result(sc)
        if res:
            finish_scan(res)
            return
        if ls0.get("state") in ("session_expired", "login_required") and ls0.get("at", 0) >= rt["scraper_started"]:
            finish_scan("login")
            return
    if sc["desired"] in ("running", "scan"):
        ls = ag.get("login_status") or {}
        if ls.get("state") == "checkpoint" and ls.get("at", 0) >= rt["scraper_started"]:
            agent("/scraper/stop", {})
            set_desired("stopped")
            if sc["desired"] == "scan":
                record_scan("checkpoint", sc)
            notify_once("checkpoint", T("🛑 פייסבוק מבקש אימות (checkpoint). הסורק נעצר אוטומטית. היכנסו ללוח הבקרה, לשונית פייסבוק, וטפלו בזה ידנית.", "🛑 Facebook is asking for verification (checkpoint). The scraper was stopped automatically. Open the control panel, Facebook page, and resolve it manually."))
            return
        if not running and not ag.get("helper", {}).get("open"):
            rt["restarts"] = [t for t in rt["restarts"] if now() - t < 600]
            if len(rt["restarts"]) >= 3:
                set_desired("stopped")
                if sc["desired"] == "scan":
                    record_scan("crashed", sc)
                notify_once("crashloop", T("🛑 הסורק קרס 3 פעמים ב-10 דקות ונעצר. בדקו את הלוגים בלוח הבקרה.", "🛑 The scraper crashed 3 times in 10 minutes and was stopped. Check the logs in the control panel."))
                return
            rt["restarts"].append(now())
            if not rt["scraper_started"]:
                rt["scraper_started"] = now()
            agent("/scraper/start", {})
            print("scraper: (re)started by reconcile", flush=True)
    elif running:
        agent("/scraper/stop", {})


def notify_once(key, text):
    k = f"{key}:{datetime.now().strftime('%Y%m%d%H')}"
    if rt["last_alert"] != k:
        rt["last_alert"] = k
        H["enqueue"]("system", [text], f"panel:{k}")  # system messages are not held by quiet hours


# ---------------------------------------------------------------- facebook
def facebook_info():
    st, ag = agent("/status")
    if st != 200:
        return {"agent": False, "connection": connection(settings.load(), st, ag, "stopped")}
    ls = ag.get("login_status") or {}
    s_ = settings.load()
    return {"agent": True, "last_ok": s_["ui"]["facebook_last_ok"], "language": s_["facebook"]["language"], "detected": s_["facebook"]["detected_language"],
            "connection": connection(s_, st, ag, s_["scraper"]["desired"]), "scraper_running": ag.get("scraper_running"), "helper_open": ag["helper"]["open"], "helper_ready": ag["helper"].get("ready", False), "helper_last": ag["helper"].get("last"),
            "session": ag.get("session"), "login_status": {"state": ls.get("state"), "at": ls.get("at"), "source": ls.get("source")} if ls else None}


def facebook_action(action):
    paths = {"open": "/helper/open", "save": "/helper/save", "close": "/helper/close", "check": "/session/check", "logout": "/session/delete"}
    if action not in paths:
        return 400, {"error": T("פעולה לא מוכרת", "Unknown action")}
    st, r = agent(paths[action], {}, timeout=130)
    if st == 0:
        return 503, {"error": T("שירות הסורק לא זמין", "The scraper service is unavailable")}
    if st == 409:
        return 409, {"error": {"scraper_running": T("הסורק פועל. עצרו אותו קודם", "The scraper is running. Stop it first"), "helper_open": T("דפדפן ההתחברות פתוח", "The login window is already open"), "busy": T("הסורק או הדפדפן פעילים", "The scraper or the browser is busy"), "not_ready": T("הדפדפן עדיין עולה, נסו שוב בעוד רגע", "The browser is still starting, try again in a moment")}.get(r.get("error"), T("הפעולה לא אפשרית כרגע", "This action is not possible right now"))}
    return st, r


# ---------------------------------------------------------------- notifications
def telegram_test():
    if now() - rt["last_test"] < 30:
        return 429, {"error": T("אפשר לשלוח הודעת בדיקה אחת ב-30 שניות", "Only one test message per 30 seconds")}
    rt["last_test"] = now()
    text = T(f"✅ הודעת בדיקה מלוח הבקרה של Marketplace Monitor\n{datetime.now().strftime('%d.%m.%Y %H:%M')}", f"✅ Test message from the Marketplace Monitor control panel\n{datetime.now().strftime('%d.%m.%Y %H:%M')}")
    status, _, note = H["send"](text)
    if 200 <= status < 300:
        ui_set(telegram_tested=True)
        return 200, {"ok": True}
    why = {401: T("הטוקן של הבוט לא תקין", "The bot token is invalid"), 403: T("הבוט נחסם או הוסר מהצ'אט", "The bot was blocked or removed from the chat"), 404: T("הבוט לא נמצא", "The bot was not found"), 400: T("מזהה הצ'אט שגוי, או שהבוט עוד לא קיבל הודעה מכם (שלחו לו /start)", "The chat id is wrong, or the bot has not received a message from you yet (send it /start)"), 429: T("מגבלת קצב של טלגרם, נסו בעוד דקה", "Telegram rate limit, try again in a minute"), 0: T("אין חיבור לטלגרם", "Cannot reach Telegram")}.get(status, T(f"שגיאה {status}", f"Error {status}"))
    return 502, {"error": why, "detail": redact(note)}


def send_summary():
    """Build today's daily summary and queue it for Telegram right now (same builder and outbox as the scheduled one)."""
    s = settings.load()
    if not s["notifications"]["enabled"]:
        return 409, {"error": T("ההתראות בטלגרם כבויות. הפעילו אותן קודם, אחרת הסיכום רק יחכה בתור.", "Telegram alerts are turned off. Turn them on first, otherwise the summary would only wait in the queue.")}
    if not H["tg_token"]() or not H["chat_id"]():
        return 409, {"error": T("טלגרם עוד לא הוגדר (חסר טוקן לבוט או מזהה צ'אט).", "Telegram is not set up yet (bot token or chat id missing).")}
    if now() - rt["last_summary"] < 20:
        return 429, {"error": T("הסיכום בדיוק התבקש. המתינו כמה שניות.", "A summary was just requested. Please wait a few seconds.")}
    rt["last_summary"] = now()
    queued, count = H["send_summary"]()
    return 200, {"queued": queued, "listings": count, "nothing": queued == 0, "message": T("אין מה לסכם: לא בוצעו סריקות ולא נמצאו מודעות היום", "Nothing to report: no scans ran and no listings were found today") if queued == 0 else ""}


def telegram_detect():
    """Find the chat id automatically: the user sends the bot any message (e.g. /start), we read who wrote to it."""
    st, chats = H["tg_updates"]()
    if st == 409:
        return 409, {"error": T("לבוט מוגדר webhook ולכן אי אפשר לזהות צ'אט אוטומטית. הזינו את המזהה ידנית.", "The bot has a webhook set, so the chat cannot be detected automatically. Enter the id manually.")}
    if st != 200:
        return 502, {"error": T("לא הצלחנו לפנות לטלגרם. בדקו את הטוקן של הבוט והחיבור לאינטרנט.", "Could not reach Telegram. Check the bot token and the internet connection.")}
    if not chats:
        return 200, {"chats": [], "hint": T("לא נמצאו הודעות. פתחו את הבוט בטלגרם, שלחו לו /start ונסו שוב.", "No messages found. Open the bot in Telegram, send it /start and try again.")}
    return 200, {"chats": chats}


def set_language(lang):
    if lang not in ("he", "en"):
        return False, {"errors": [{"field": "language", "message": T("שפה לא נתמכת", "Unsupported language")}]}
    ui_set(language=lang)
    if current_config_text():  # nothing to re-render before the first search exists
        threading.Thread(target=regenerate, args=("ui-language",), daemon=True).start()  # the AI explains its ratings in the panel's language (changes only the summary language, not the scale)
    return True, {"language": lang}


def listings_cleanup(body):
    """Clean-up of the listing history. Always previews first; deletes only when the request says confirm=true. Saved listings are never touched."""
    kind = body.get("kind")
    days = body.get("days")
    if kind not in ("orphans", "stale", "older") or (kind == "older" and days not in (7, 14, 30, 60, 90)):
        return 400, {"error": T("סוג ניקוי לא תקין", "Invalid clean-up kind")}
    urls = H["cleanup_candidates"](kind, days)
    if body.get("confirm") is not True:
        return 200, {"count": len(urls)}
    n = H["remove_listings"](urls) if urls else 0
    print(f"listings: clean-up {kind} removed {n}", flush=True)
    return 200, {"count": len(urls), "removed": n}


def setup_update(body):
    if "setup_dismissed" in body:
        ui_set(setup_dismissed=bool(body["setup_dismissed"]))
    return True, {"ok": True}


# ---------------------------------------------------------------- listings feed
def verdicts():
    return {5: (T("התאמה מצוינת", "Excellent match"), "great"), 4: (T("התאמה טובה", "Good match"), "good"), 3: (T("התאמה בינונית", "Fair match"), "mid"),
            2: (T("התאמה חלשה", "Weak match"), "low"), 1: (T("לא מתאים", "Not a match"), "low")}


def listings():
    s = settings.load()
    names = {p["id"]: p["name"] for p in s["products"]}
    saved = H["saved_set"]()
    seen = s["ui"]["last_seen"]
    av = availability.load(settings.DATA_DIR)["urls"]
    out = []
    for r in H["listings_all"]():
        try:
            ts = int(datetime.fromisoformat(r.get("ts", "")).timestamp())
        except ValueError:
            ts = 0
        score = r.get("score", 0)
        unrated = bool(r.get("unrated")) or score == 0
        label, tone = (T("לא דורג. תקלה בדירוג החכם", "Not rated: the AI rating failed"), "unrated") if unrated else verdicts().get(score, ("", "mid"))
        old, price = r.get("old"), r.get("price")
        pids = [i for i in (r.get("items") or [r.get("item")]) if i]
        pids = [i for i in pids if i in names] or pids  # searches that were deleted are not shown (unless the listing has no live search at all)
        out.append({"product_ids": pids, "currency": r.get("currency"), "prev_price": r.get("prev_price"), "price_dir": r.get("price_dir"), "url": r["url"], "product_id": r.get("item"), "product": " · ".join(names.get(i) or i for i in pids) or r.get("item"), "title": r.get("title", ""), "price": price, "old": old,
                    "drop": (old - price) if (old and price is not None and old > price) else 0, "loc": r.get("loc", ""), "score": score, "unrated": unrated,
                    "verdict": label, "tone": tone, "reason": "" if unrated else (r.get("ai_full") or r.get("ai") or ""), "found_at": ts, "new": ts > seen,
                    "saved": H["norm_url"](r["url"]) in saved, "great": (not unrated) and score >= 5, **availability.view(av.get(H["norm_url"](r["url"])))})
    out.sort(key=lambda x: x["found_at"], reverse=True)
    return {"listings": out, "products": [{"id": p["id"], "name": p["name"]} for p in s["products"]], "last_seen": seen}


def listing_action(body):
    urls = [u for u in (body.get("urls") or []) if isinstance(u, str) and u.startswith("https://") and len(u) < 500][:500]
    act = body.get("action")
    if act == "seen":
        ui_set(last_seen=now())
        return 200, {"ok": True}
    if not urls:
        return 400, {"error": T("לא נבחרו מודעות", "No listings selected")}
    if act == "delete":  # clean-up: remove from the history, WITHOUT blacklisting (it may be found again later)
        return 200, {"removed": H["remove_listings"](urls)}
    if act in ("save", "unsave"):
        return 200, {"saved": H["set_saved"](urls, act == "save")}
    if act == "block":
        reason = body.get("reason") if body.get("reason") in ("stale", "wrong", "price", "bad", "scam", "other") else "other"
        n = H["delete_listings"](urls, reason)
        print(f"listings: blocked {len(urls)} (removed {n}) reason={reason}", flush=True)
        return 200, {"removed": n, "blocked": len(urls)}
    if act == "unblock":
        return 200, {"restored": H["restore_listings"](urls)}
    return 400, {"error": T("פעולה לא מוכרת", "Unknown action")}


# ---------------------------------------------------------------- home
def setup_steps(s, st, ag, conn):
    fb_ok = st == 200 and bool((ag.get("session") or {}).get("has_login_cookies")) and conn["code"] not in ("login_required", "session_expired", "checkpoint")
    search_ok = bool(s["facebook"]["search_city"]) and any(p["enabled"] for p in s["products"])
    tg_ok = s["ui"]["telegram_tested"] or s["notifications"]["enabled"]
    started = s["ui"]["ever_started"] or s["scraper"]["desired"] == "running"
    return [{"id": "facebook", "title": T("חיבור לפייסבוק", "Connect Facebook"), "done": fb_ok, "route": "facebook", "hint": T("מתחברים פעם אחת, בחלון דפדפן אמיתי. הסיסמה לא נשמרת.", "You log in once, in a real browser window. Your password is not stored.")},
            {"id": "search", "title": T("החיפוש הראשון", "Your first search"), "done": search_ok, "route": "searches", "hint": T("מה מחפשים, איפה, ובאיזה טווח מחירים.", "What to look for, where, and the price range.")},
            {"id": "notifications", "title": T("התראות בטלגרם", "Telegram alerts"), "done": tg_ok, "route": "notifications", "hint": T("כדי לקבל הודעה כשנמצאת מודעה מעניינת.", "So you get a message when an interesting listing is found.")},
            {"id": "start", "title": T("התחלת מעקב", "Start monitoring"), "done": started, "route": "home", "hint": T("בודקים שהכול מוכן ומפעילים.", "Check that everything is ready and start.")}]


def home():
    s = settings.load()
    st, ag = agent("/status")
    sc = s["scraper"]
    conn_ = connection(s, st, ag, sc["desired"])
    steps = setup_steps(s, st, ag, conn_)
    running = bool(ag.get("scraper_running")) if st == 200 else False
    lst = listings()
    today0 = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    today = [x for x in lst["listings"] if x["found_at"] >= today0]
    health = read_json(H["health_path"]) or {}
    warnings = []
    if conn_["severity"] in ("warn", "bad") and conn_["code"] not in ("not_connected",):
        warnings.append({"code": conn_["code"], "severity": conn_["severity"], "title": conn_["title"], "detail": conn_["detail"], "action": conn_["action"]})
    aist = ai_status()
    if aist:
        warnings.append({"code": "ai_" + aist["code"], "severity": aist["severity"], "title": aist["title"], "detail": aist["detail"], "action": None})
    ob = H["outbox_stats"]()
    if s["notifications"]["enabled"] and (ob.get("last_error") or not H["tg_token"]()):
        warnings.append({"code": "telegram", "severity": "warn", "title": T("ההתראות בטלגרם לא נשלחות", "Telegram alerts are not being delivered"), "detail": T("ההודעות ממתינות בתור ויישלחו כשהחיבור יחזור. בדקו את הגדרות ההתראות.", "Messages are waiting in the queue and will be sent when the connection returns. Check the notification settings."), "action": "notifications"})
    if sc["desired"] == "scan":
        scan = {"available": False, "reason": T("סריקה כבר רצה", "a scan is already running")}
    elif sc["desired"] in ("running", "paused"):
        scan = {"available": False, "reason": T("המעקב פועל או מושהה (סריקה ידנית זמינה כשהמעקב כבוי)", "monitoring is on or paused (manual scan is available while monitoring is off)")}
    elif st != 200:
        scan = {"available": False, "reason": T("שירות הסורק לא זמין", "the scraper service is unavailable")}
    else:
        why = preconditions(s, ag)
        scan = {"available": not why, "reason": "; ".join(why)}
    nxt = next((x for x in steps if not x["done"] and x["id"] != "start"), None)
    nprod = sum(1 for p in s["products"] if p["enabled"])
    if nxt:
        state = {"code": "setup", "tone": "info", "headline": T("בואו נסיים את ההגדרה", "Let's finish the setup"), "sub": T(f"נשאר: {nxt['title']}. {nxt['hint']}", f"Remaining: {nxt['title']}. {nxt['hint']}"), "primary": {"action": "goto", "route": nxt["route"], "label": f"{nxt['title']}"}}
    elif sc["desired"] == "scan":
        state = {"code": "scanning", "tone": "info", "headline": T("סורקים עכשיו", "Scanning now"), "sub": T("בודקים את החיפושים פעם אחת. זה לוקח בדרך כלל כמה דקות, ואז הסורק נעצר מעצמו.", "Checking your searches once. This usually takes a few minutes, then the scraper stops by itself."), "primary": {"action": "stop", "label": T("ביטול הסריקה", "Cancel scan")}}
    elif running:
        state = {"code": "active", "tone": "ok", "headline": T("המעקב פעיל", "Monitoring is on"), "sub": T(f"מחפשים {nprod} {'חיפוש' if nprod == 1 else 'חיפושים'} ב{s['facebook']['search_city']}. אפשר לסגור את הדף, המעקב ממשיך ברקע.", f"Checking {nprod} {'search' if nprod == 1 else 'searches'} in {s['facebook']['search_city']}. You can close this page, monitoring keeps running."), "primary": {"action": "pause", "label": T("השהיה", "Pause")}}
    elif sc["desired"] == "paused":
        state = {"code": "paused", "tone": "warn", "headline": T("המעקב מושהה", "Monitoring is paused"), "sub": T(f"יחזור לפעול אוטומטית ב-{datetime.fromtimestamp(sc['pause_until']).strftime('%H:%M')}.", f"It resumes automatically at {datetime.fromtimestamp(sc['pause_until']).strftime('%H:%M')}.") if sc["pause_until"] else "", "primary": {"action": "start", "label": T("המשך עכשיו", "Resume now")}}
    elif conn_["severity"] == "bad" or conn_["code"] in ("login_required", "session_expired"):
        state = {"code": "attention", "tone": "bad", "headline": conn_["title"], "sub": conn_["detail"], "primary": {"action": "goto", "route": "facebook", "label": T("טיפול בחיבור", "Fix the connection")}}
    else:
        state = {"code": "stopped", "tone": "warn", "headline": T("המעקב כבוי", "Monitoring is off"), "sub": T("לא נחפשות מודעות כרגע.", "No listings are being searched right now."), "primary": {"action": "start", "label": T("התחלת מעקב", "Start monitoring")}, "secondary": {"action": "scan", "label": "Scan now"}}
    return {"state": state, "scan": scan, "scan_status": scan_status(s, ag, st, conn_), "last_scan": s["ui"]["last_scan"], "last_scan_outcome": scan_outcome(s["ui"]["last_scan"]), "scan_history": scan_history_view(), "scraper": {"running": running, "desired": sc["desired"], "pause_until": sc["pause_until"], "agent": st == 200},
            "metrics": {"new": sum(1 for x in lst["listings"] if x["new"]), "today": len(today), "great_today": sum(1 for x in today if x["great"]), "last_scan": (history()[-1:] or [{}])[0].get("at") or health.get("last_search")},
            "warnings": warnings, "setup": {"steps": steps, "done": all(x["done"] for x in steps), "dismissed": s["ui"]["setup_dismissed"]},
            "latest": lst["listings"][:5], "connection": conn_, "notifications_enabled": s["notifications"]["enabled"]}


# ---------------------------------------------------------------- status / system
def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def tail_errors(path, n=30):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    rx = re.compile(r"Traceback|\[Error\]|\bERROR\b|\[AI-Error\]|checkpoint|captcha|logged out|log in|Failed", re.I)
    out = [redact(x) for x in lines if rx.search(x) and "[Skip]" not in x]
    return out[-n:]


TAGS2 = re.compile(r"\x1b\[[0-9;]*m|\[/?[a-z ]{2,12}\]")


def log_summary():
    """What the CURRENT AIMM run did, from its own log (the part after the last version banner)."""
    path = H["mp_log"]
    try:
        mtime = os.stat(path).st_mtime
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            lines = [TAGS2.sub("", x) for x in f.read().decode("utf-8", "replace").splitlines()]
    except OSError:
        return None
    start = max([i for i, x in enumerate(lines) if "AI Marketplace Monitor, version" in x] + [0])
    run = lines[start:]
    txt = "\n".join(run)
    # NOTE: AIMM 0.10.2 logs "Failed to get search results" after EVERY search (unconditional log call in facebook.py), so it is NOT a signal.
    return {"searches": txt.count("Searching facebook for"), "item_failed": txt.count("Failed to get item details"),
            "ai_ok": len(re.findall(r"\[AI\].*concludes", txt)), "config_error": "Error parsing" in txt or "[Config]" in txt and "fail" in txt.lower(),
            "browser_failed": "No browser could be launched" in txt, "network": bool(re.search(r"Failed to navigate to|net::ERR|ERR_NAME_NOT_RESOLVED|ERR_INTERNET_DISCONNECTED|ERR_CONNECTION", txt)),
            "login_wait": "[Login] Waiting" in txt, "at": int(mtime)}


def conn(code, sev, title, detail, action=None, **facts):
    return {"code": code, "severity": sev, "title": title, "detail": detail, "action": action, "facts": facts}


def connection(s, st, ag, desired):
    """One accurate, actionable statement about the Facebook connection. severity: ok | info | warn | bad."""
    if st != 200:
        return conn("agent_down", "bad", T("שירות הסורק לא פעיל", "Scraper service is down"), T("הקונטיינר של הסורק לא זמין, ולכן אי אפשר להתחבר או לסרוק. הפעילו אותו מחדש ב-Portainer (המחסנית marketplace-monitor).", "The scraper container is unavailable, so connecting and scanning are not possible. Restart it in Portainer (stack marketplace-monitor)."), None)
    pr = ag.get("programs") or {}
    aimm = (pr.get("aimm") or {}).get("state")
    session = ag.get("session") or {}
    ls = ag.get("login_status") or {}
    state, lang = ls.get("state"), (ls.get("lang") or "").lower()
    running = bool(ag.get("scraper_running"))
    log = log_summary() if (running or (log_age_ok())) else None
    facts = {"state": state, "lang": lang, "checked_at": ls.get("at"), "session_saved": session.get("exists"), "scraper": aimm}
    if ag.get("helper", {}).get("open"):
        return conn("helper_open", "info", T("חלון ההתחברות פתוח", "Login window is open"), T("התחברו לפייסבוק בחלון שנפתח, ואז לחצו “סיימתי להתחבר”.", "Log in to Facebook in the window that opened, then press \"I've finished logging in\"."), "save_login", **facts)
    if desired in ("running", "scan") and aimm in ("FATAL", "BACKOFF", "EXITED"):
        if log and log["browser_failed"]:
            return conn("browser_failed", "bad", T("הדפדפן של הסורק לא עלה", "The scraper browser did not start"), T("הסורק לא הצליח להפעיל דפדפן (לרוב חוסר זיכרון בשרת). נסו להפעיל שוב בעוד דקה; אם זה חוזר, בדקו את זיכרון השרת.", "The scraper could not start a browser (usually low memory on the server). Try starting again in a minute; if it repeats, check the server memory."), "start", **facts)
        return conn("scraper_crashed", "bad", T("הסורק נעצר באופן בלתי צפוי", "The scraper stopped unexpectedly"), T("תהליך הסורק קרס ולא הצליח לעלות. פרטים בלשונית “הגדרות”. אפשר לנסות להפעיל שוב.", "The scraper process crashed and could not start. Details are under Settings. You can try starting again."), "start", **facts)
    if log and log["config_error"]:
        return conn("config_error", "bad", T("יש בעיה בהגדרות החיפוש", "There is a problem with the search settings"), T("הסורק דחה את ההגדרות. בדקו את החיפושים (עיר, מחירים, מילים) ושמרו שוב.", "The scraper rejected the settings. Check your searches (city, prices, words) and save again."), "searches", **facts)
    if state == "checkpoint":
        return conn("checkpoint", "bad", T("פייסבוק מבקשת אימות (checkpoint או CAPTCHA)", "Facebook is asking for verification (checkpoint or CAPTCHA)"), T("צריך לפתור את האימות בעצמכם בחלון הדפדפן. אנחנו לא עוקפים אימותים. הסורק נעצר כדי לא להסתכן בחשבון.", "You need to solve the verification yourself in the browser window. We never bypass verification. The scraper was stopped to protect your account."), "reconnect", **facts)
    if not session.get("exists") or not session.get("has_login_cookies"):
        if state in ("session_expired", "login_required"):
            return conn("login_required", "warn", T("פייסבוק מבקשת התחברות", "Facebook is asking you to log in"), T("אין התחברות פעילה. התחברו בחלון הדפדפן, הסיסמה לא נשמרת אצלנו.", "There is no active login. Log in in the browser window; your password is not stored."), "reconnect", **facts)
        return conn("not_connected", "warn", T("עדיין לא מחוברים לפייסבוק", "Not connected to Facebook yet"), T("כדי לחפש מודעות צריך להתחבר פעם אחת לחשבון פייסבוק (עדיף חשבון משני). ההתחברות נעשית בחלון דפדפן אמיתי אצלכם, והסיסמה לא נשמרת.", "To search for listings you need to log in to a Facebook account once (a secondary account is recommended). Login happens in a real browser window and your password is not stored."), "connect", **facts)
    if state == "session_expired":
        return conn("session_expired", "bad", T("ההתחברות לפייסבוק פגה", "Your Facebook login expired"), T("פייסבוק דחתה את ההתחברות השמורה (קורה אחרי החלפת סיסמה, התנתקות מכל המכשירים או זמן רב). התחברו מחדש.", "Facebook rejected the saved login (this happens after a password change, logging out of all devices, or a long time). Log in again."), "reconnect", **facts)
    if state == "login_required":
        return conn("login_required", "warn", T("פייסבוק מבקשת התחברות", "Facebook is asking you to log in"), T("הדף שנטען דרש התחברות. התחברו מחדש.", "The page that loaded required a login. Log in again."), "reconnect", **facts)
    if state == "logged_in":
        resolved = resolved_language(s)
        if lang and not lang.startswith(("en", "he")):
            return conn("language_unsupported", "bad", T(f"שפת חשבון הפייסבוק ({lang}) לא נתמכת", f"Facebook account language ({lang}) is not supported"), T("הסורק יודע לקרוא פייסבוק באנגלית ובעברית בלבד. החליפו את שפת החשבון לאנגלית (ארה\"ב) או לעברית: הגדרות ← שפה ואזור.", "The scraper can read Facebook in English and Hebrew only. Change the account language to English (US) or Hebrew: Settings > Language and region."), "language", **facts)
        if log and log["item_failed"] and not log["ai_ok"]:
            if lang.startswith("he") and resolved != "he":
                return conn("reading_failed", "bad", T("מחובר, אבל הסורק לא קורא את המודעות", "Connected, but the scraper cannot read listings"), T("חשבון הפייסבוק בעברית והתרגום לעברית כבוי. בחרו “עברית” בהגדרות ← שפת חשבון הפייסבוק.", "The Facebook account is in Hebrew and the Hebrew support is off. Choose \"Hebrew\" under Facebook > Advanced > account language."), "language", **facts)
            return conn("reading_failed", "warn", T("מחובר, אבל הסורק לא הצליח לקרוא מודעות", "Connected, but the scraper could not read listings"), T("הדפים נטענים אך הסורק לא מצליח לחלץ מהם פרטים. ייתכן שפייסבוק שינתה את מבנה העמוד או שהשפה לא נתמכת. נסו להחליף את שפת החשבון לאנגלית (ארה\"ב).", "Pages load but the scraper cannot extract details from them. Facebook may have changed the page layout or the language is unsupported. Try switching the account language to English (US)."), "language", **facts)
        if log and log["network"] and not log["searches"]:
            return conn("network_failure", "warn", T("בעיית רשת מול פייסבוק", "Network problem reaching Facebook"), T("הסורק לא הצליח לטעון עמודים. בדקו חיבור אינטרנט בשרת ונסו שוב בעוד כמה דקות.", "The scraper could not load pages. Check the server's internet connection and try again in a few minutes."), None, **facts)
        return conn("connected", "ok", T("מחובר לפייסבוק", "Connected to Facebook"), T("החיבור תקין.", "The connection is healthy.") + (T(" (עברית)", " (Hebrew)") if lang.startswith("he") else ""), None, **facts)
    if state == "checking":
        return conn("checking", "info", T("בודק את החיבור…", "Checking the connection…"), T("הסורק פותח את פייסבוק. זה לוקח כמה שניות.", "The scraper is opening Facebook. This takes a few seconds."), None, **facts)
    if log and log["network"]:
        return conn("network_failure", "warn", T("בעיית רשת מול פייסבוק", "Network problem reaching Facebook"), T("הסורק לא הצליח לטעון עמודים. בדקו חיבור אינטרנט בשרת.", "The scraper could not load pages. Check the server's internet connection."), None, **facts)
    return conn("unknown", "warn", T("החיבור לפייסבוק עוד לא נבדק", "Connection not checked yet"), T("יש התחברות שמורה אבל עדיין לא נבדקה. לחצו “בדיקת חיבור” או הפעילו את הסורק.", "A saved login exists but has not been checked yet. Press \"Check connection\" or run a scan."), "check", **facts)


def log_age_ok(minutes=30):
    try:
        return time.time() - os.stat(H["mp_log"]).st_mtime < minutes * 60
    except OSError:
        return False


def status():
    s = settings.load()
    sc = s["scraper"]
    st, ag = agent("/status")
    health = read_json(H["health_path"]) or {}
    today = datetime.now().strftime("%Y-%m-%d")
    day = H["load_day"](today)
    ob = H["outbox_stats"]()
    ai = H["ai_health"]()
    fbinfo = ag.get("login_status") if st == 200 else None
    return {
        "now": now(),
        "scraper": {"desired": sc["desired"], "pause_until": sc["pause_until"], "agent": st == 200, "running": bool(ag.get("scraper_running")) if st == 200 else False,
                    "uptime": (ag.get("programs", {}).get("aimm") or {}).get("uptime") if st == 200 else None,
                    "last_scan": health.get("last_search"), "searches_24h": health.get("searches"), "new_seen_24h": health.get("new_seen"), "errors_24h": health.get("errors"),
                    "health_at": health.get("ts"), "preconditions": preconditions(s, ag if st == 200 else None), "helper_open": bool(ag.get("helper", {}).get("open")) if st == 200 else False},
        "listings": {"found_today": len({r["url"] for r in day}), "alerted_24h": health.get("sent"), "products_enabled": sum(1 for p in s["products"] if p["enabled"]), "products_total": len(s["products"])},
        "ai": ai,
        "telegram": {"token_configured": bool(H["tg_token"]()), "enabled": s["notifications"]["enabled"], "chat_id_set": bool(H["chat_id"]()), "outbox": ob},
        "facebook": {"state": (fbinfo or {}).get("state"), "at": (fbinfo or {}).get("at"), "session_saved": (ag.get("session") or {}).get("exists") if st == 200 else None},
        "connection": connection(s, st, ag, sc["desired"]),
    }


def system():
    ob = H["outbox_stats"]()
    ai = H["ai_health"]()
    st, ag = agent("/status")
    services = [
        {"name": T("לוח בקרה / מעביר התראות", "Control panel / alert forwarder"), "ok": True, "detail": T(f"תור: {ob['pending']} ממתינות", f"Queue: {ob['pending']} waiting")},
        {"name": T("Gemini (שער AI)", "Gemini (AI rating)"), "ok": bool(ai) and not ai.get("unreachable") and not ai.get("breaker_open"),
         "detail": (T("לא זמין", "Unavailable") if not ai or ai.get("unreachable") else (T(f"מושהה: {ai.get('breaker_reason')}", f"Paused: {ai.get('breaker_reason')}") if ai.get("breaker_open") else T(f"מודל {ai.get('model')}", f"Model {ai.get('model')}")))},
        {"name": T("שירות הסורק (סוכן)", "Scraper service (agent)"), "ok": st == 200, "detail": T("זמין", "Available") if st == 200 else T("הקונטיינר לא רץ או לא זמין", "The container is not running or unavailable")},
    ]
    if st == 200:
        for n, label in (("xvfb", T("מסך וירטואלי", "Virtual display")), ("x11vnc", T("VNC (מקומי בלבד)", "VNC (local only)")), ("aimm", T("תהליך הסורק", "Scraper process"))):
            p = (ag.get("programs") or {}).get(n, {})
            services.append({"name": label, "ok": p.get("state") == "RUNNING" or (n == "aimm" and p.get("state") == "STOPPED"), "detail": p.get("state", "?")})
    errors = [{"src": "panel", "line": x} for x in list(ring)[-80:] if re.search(r"error|failed|refus|crash|http error|retry", x, re.I)][-15:]
    errors += [{"src": "scraper", "line": x} for x in tail_errors(H["mp_log"], 15)]
    if ai and ai.get("last_error"):
        errors.append({"src": "gemini", "line": redact(ai["last_error"])})
    if ob.get("last_error"):
        errors.append({"src": "telegram", "line": redact(ob["last_error"])})
    return {"services": services, "errors": errors[-30:], "last_apply": rt["last_apply"], "outbox": ob,
            "version": {"panel": "1.0"}}


def config_preview():
    """Generated config for support, with every secret masked."""
    t = current_config_text() or build_config(settings.load())
    return re.sub(r'(api_key\s*=\s*)".*?"', r'\1"********"', t)
