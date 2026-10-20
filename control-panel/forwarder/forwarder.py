import base64, builtins, hashlib, hmac, json, os, re, secrets, select, socket, sqlite3, sys, threading, time, urllib.error, urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import availability, cfggen, panel, settings
from settings import T, lang

_print = builtins.print


def print(*a, **k):  # noqa: A001  keeps a redacted ring buffer of log lines for the System page
    try:
        panel.ring.append(f"{datetime.now():%H:%M:%S} " + panel.redact(" ".join(str(x) for x in a)))
    except Exception:
        pass
    _print(*a, **k)


panel.print = print


def env_secret(name, default=None):
    """NAME_FILE (Docker/Portainer secret or mounted file) wins over NAME; never logged."""
    path = os.environ.get(name + "_FILE")
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get(name, default)


# Local time: the 21:00 recap and 04:15/04:30 jobs use the process timezone. A TZ name that does not
# resolve would silently fall back to UTC, so fail loudly instead.
TZ_NAME = os.environ.get("TZ", "Asia/Jerusalem")
try:
    ZoneInfo(TZ_NAME)
except Exception:
    sys.exit(f"invalid TZ {TZ_NAME!r}: refusing to run with the wrong clock")
os.environ["TZ"] = TZ_NAME
time.tzset()

# Telegram Bot API (token in the URL path: never log URLs or exception text from urllib)
TG_TOKEN = env_secret("TG_BOT_TOKEN")
TG_CHAT_ENV = env_secret("TG_CHAT_ID", "")  # seed/fallback only: the chat id is edited in the panel and stored in settings
TG_API = os.environ.get("TG_API_BASE", "https://api.telegram.org").rstrip("/")
if not TG_TOKEN:
    sys.exit("TG_BOT_TOKEN is required")


def chat_id():
    return settings.get()["notifications"]["chat_id"] or TG_CHAT_ENV


def notify_enabled():
    return settings.get()["notifications"]["enabled"]

AI_HEALTH_URL = os.environ.get("AI_HEALTH_URL", "")  # gemini-guard /healthz, optional
UI_USER = env_secret("UI_USER")
UI_PASS = env_secret("UI_PASS")
if not UI_USER or not UI_PASS or len(UI_PASS) < 12:
    sys.exit("UI_USER and UI_PASS (min 12 chars) are required: the listings UI must not run unauthenticated")
INGEST_TOKEN = env_secret("INGEST_TOKEN", "")  # optional extra check on the ingest port (Authorization: Bearer)
OUTBOX_MAX_AGE = timedelta(hours=int(os.environ.get("OUTBOX_MAX_AGE_H", "24")))
BACKOFF_BASE = float(os.environ.get("BACKOFF_BASE_S", "30"))
SEND_GAP = float(os.environ.get("SEND_GAP_S", "3"))  # minimum seconds between Telegram messages (limit is ~1/s per chat)
DATA_DIR = os.environ.get("DATA_DIR", "/data")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "60"))
HEALTH = os.path.join(DATA_DIR, "health.json")
GREAT = {"gpu": 450, "ssd": 200, "ram": 150}
for _kv in os.environ.get("GREAT_DEAL", "").split(","):
    _k, _, _v = _kv.partition("=")
    if _v.strip().isdigit():
        GREAT[_k.strip().lower()] = int(_v)
FOOTER = "\n\nSent by https://github.com/BoPeng/ai-marketplace-monitor"
CHUNK = 3500
AI_MAX = 110
DIV = "━━━━━━━━━━━━━━"
BLOCK_SPLIT = re.compile(r"\n\n(?=\[[^\]\n]+\] )")
HEAD_RE = re.compile(r"^\[[^\]]*?\((\d)\)\]\s*(.*)$")
PRICE_RE = re.compile(r"^([^\d\s|,]{0,4}\s*[\d,]+(?:\.\d+)?)(?:\s*\|\s*([^\d\s|,]{0,4}\s*[\d,]+(?:\.\d+)?))?(?:,\s*(.*))?$")
NOT_EVALUATED = "Not evaluated by AI"
TITLE_RE = re.compile(r"^(?:Found (\d+) (?:new|updated|discounted)|Another look at (\d+)|Resend (\d+)) (.+?) from ")


def search_ids():
    try:
        return {p["id"] for p in settings.get()["products"]}
    except Exception:
        return set()


def item_id(raw, ids=None):
    """The search id behind AIMM's notification title. AIMM pluralises the item name with inflect ("Found 11 new p2s"), so the name in the
    title is not the id. Resolved against the real search ids; an unknown name is kept as it is (never mapped to a wrong search)."""
    ids = search_ids() if ids is None else ids
    if raw in ids or not raw:
        return raw
    for suffix in ("s", "es"):
        if raw.endswith(suffix) and raw[:-len(suffix)] in ids:
            return raw[:-len(suffix)]
    return raw
EMOJI = {"gpu": "🎮", "ssd": "💾", "ram": "🧠", "cpu": "⚙️", "monitor": "🖥️", "laptop": "💻"}
ALERTS = os.path.join(DATA_DIR, "alerts.jsonl")
STATE = os.path.join(DATA_DIR, "state.json")
BLACKLIST = os.path.join(DATA_DIR, "blacklist.jsonl")
PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "listings.html")
OUTBOX_DB = os.path.join(DATA_DIR, "outbox.db")
KEY_FILE = os.path.join(DATA_DIR, "csrf.key")
REASONS = {
    "stale": "Sold or stale",
    "wrong": "Wrong item or spec",
    "price": "Too expensive",
    "bad": "Broken or poor quality",
    "scam": "Suspicious seller",
    "other": "Other",
}
lock = threading.Lock()


def norm_url(u):
    """One canonical form for storage, blacklist and delete: no query/fragment, no trailing slash."""
    return (u or "").strip().split("?")[0].split("#")[0].rstrip("/")


def num(s):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    return int(round(float(m.group(0).replace(",", "")))) if m else None


# Currency symbol/code that Facebook shows next to a price -> ISO code ("$" and "¥" are ambiguous and stay as symbols).
CUR_SYMBOLS = {"₪": "ILS", "NIS": "ILS", "ILS": "ILS", "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP", "USD": "USD", "$": "$", "¥": "¥", "CHF": "CHF"}
SHOW = {"ILS": "₪", "EUR": "€", "GBP": "£", "USD": "$", "$": "$", "¥": "¥"}
AMBIGUOUS = {"$": {"USD", "CAD", "AUD", "NZD", "HKD", "SGD", "MXN"}, "¥": {"JPY", "CNY"}}


def currency_of(price_text):
    m = re.match(r"\s*([^\d\s|,.]+)", price_text or "")
    return CUR_SYMBOLS.get(m.group(1).upper() if m else "", None) if m else None


def money(n, cur=None):
    """Price with its currency when it is known; a bare number when it is not (never an assumed symbol, never a conversion)."""
    sym = SHOW.get(cur, cur or "")
    return f"{sym}{n:,}" if len(sym) <= 1 else f"{sym} {n:,}" if sym else f"{n:,}"


def currency_ok(it, name):
    """A search's currency is a RESTRICTION: listings priced in a clearly different currency are skipped. Unknown currency -> kept."""
    want = next((p.get("currency") for p in settings.get()["products"] if p["id"] == name), "")
    have = it.get("currency")
    if not want or not have:
        return True
    return want in AMBIGUOUS.get(have, {have})


def product_name(key):
    for p in settings.get()["products"]:
        if p["id"] == key:
            return p["name"]
    return None


def item_label(name, count=2):
    pn = product_name(name)
    if pn:
        return pn
    return name.upper() if len(name) <= 4 else name.title()


def short_ai(ai):
    ai = ai.strip().replace("\n", " ")
    ai = re.sub(r"^[A-Za-z ]+(\(\d\))?\s*-\s*", "", ai)
    m = re.search(r"\.(\s|$)", ai)
    if m:
        ai = ai[: m.start()]
    if len(ai) > AI_MAX:
        ai = ai[:AI_MAX].rsplit(" ", 1)[0].rstrip(" ,;") + "…"
    return ai


def parse_block(block):
    parts = re.split(r"\n+AI:", block, maxsplit=1)
    lines = parts[0].split("\n")
    ai = parts[1] if len(parts) > 1 else ""
    m = HEAD_RE.match(lines[0]) if lines else None
    if not m:
        return None
    score, title = int(m.group(1)), m.group(2).strip()
    price = old = cur = None
    loc = ""
    if len(lines) > 1:
        pm = PRICE_RE.match(lines[1].strip())
        if pm:
            price, old, loc = num(pm.group(1)), num(pm.group(2)), (pm.group(3) or "").strip()
            cur = currency_of(pm.group(1))
        else:
            loc = lines[1].strip()
    url = norm_url(lines[2]) if len(lines) > 2 else ""
    out = {"title": title, "score": score, "price": price, "old": old, "loc": loc, "url": url, "ai": short_ai(ai), "ai_full": " ".join(ai.split())[:700], "currency": cur}
    if NOT_EVALUATED in ai:
        # AIMM turns an AI failure (quota, timeout, bad answer) into a fake score 5 "Not evaluated by AI".
        # Never alert on that: keep it as unrated (score 0) for the recap.
        out.update(score=0, unrated=True, ai=T("לא דורג ב-AI (המודל לא היה זמין)", "Not rated by the AI (the model was unavailable)"), ai_full="")
    return out


def card(n, it, num_prefix=True):
    title = it["title"][:70]
    out = f"*{n}. {title}*" if num_prefix else f"*{title}*"
    if it["price"] is None:
        price = "💰 *" + T("מחיר לא ידוע", "price n/a") + "*"
    else:
        price = f"💰 *{money(it['price'], it.get('currency'))}*"
        if it.get("old") and it["old"] != it["price"]:
            price += f"  ~{money(it['old'], it.get('currency'))}~  ⬇️ " + T("ירידת מחיר", "price drop")
    out += f"\n{price}"
    if it["loc"]:
        out += f"\n📍 {it['loc']}"
    if it.get("unrated"):
        out += "\n⚠️ _" + T("לא דורג ב-AI, כדאי לבדוק בעצמכם", "not rated by AI, check manually") + "_"
    elif it["ai"]:
        out += f"\n💬 _{it['ai']}_"
    out += f"\n🔗 {it['url']}"
    return out


def sort_key(it):
    return it["price"] if it["price"] is not None else float("inf")


def pack(header, cards):
    chunks, cur = [], header
    for c in cards:
        if len(cur) + len(c) + 2 > CHUNK and cur != header:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{c}" if cur else c
    if cur:
        chunks.append(cur)
    return chunks


def great_price(name):
    for p in settings.get()["products"]:
        if p["id"] == name and p.get("great_deal_price") is not None:
            return p["great_deal_price"]
    return GREAT.get(name.lower())


def urgent(it, name):
    cap = great_price(name)
    if it.get("unrated"):
        return False
    return it["score"] >= settings.get()["notifications"]["immediate_min_score"] or (cap is not None and it["price"] is not None and it["price"] <= cap)


def build_alert(title, body, blocked=frozenset()):
    blocks = BLOCK_SPLIT.split(body.strip())
    items = [parse_block(b) for b in blocks]
    items = [i for i in items if i]
    if not items:
        return [f"*{title}*\n{body}"[:CHUNK]], [], ""
    items = [i for i in items if i["url"] not in blocked]
    if not items:
        return [], [], ""
    tm = TITLE_RE.match(title)
    name = item_id(tm.group(4)) if tm else "listing"
    items = [i for i in items if currency_ok(i, name)]
    if not items:
        return [], [], name
    items.sort(key=sort_key)
    shown = [i for i in items if urgent(i, name)]
    if not shown:
        return [], items, name
    held = len(items) - len(shown)
    emoji = EMOJI.get(name.lower(), "🛒")
    label = item_label(name, len(shown))
    plural = "" if len(shown) == 1 or product_name(name) else "s"
    header = T(f"{emoji} *{len(shown)} {'מודעה חדשה' if len(shown) == 1 else 'מודעות חדשות'}: {label}* · מהזולה ליקרה",
               f"{emoji} *{len(shown)} new {label}{plural}* · cheapest first")
    if held:
        header += T(f"\n_+{held} נוספות ממתינות לסיכום של הערב_", f"\n_+{held} more held for tonight's recap_")
    header += f"\n{DIV}"
    cards = [card(i, it) for i, it in enumerate(shown, 1)]
    return pack(header, cards), items, name


def ai_health():
    if not AI_HEALTH_URL:
        return None
    try:
        with urllib.request.urlopen(AI_HEALTH_URL, timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return {"unreachable": True}


def ai_note():
    """AI problems only. A healthy AI adds nothing to a recap (no 'N rated today' noise)."""
    h = ai_health()
    if not h:
        return ""
    if h.get("unreachable"):
        return "\n⚠️ " + T("שירות ה-AI לא זמין: מודעות חדשות לא מדורגות", "AI guard unreachable: new listings are not being rated")
    if h.get("breaker_open"):
        return "\n⚠️ " + T(f"הדירוג החכם מושהה ({h['breaker_reason']}) עד {h['breaker_until']}. מודעות שיימצאו בינתיים יישמרו ללא דירוג.",
                           f"AI paused: {h['breaker_reason']} until {h['breaker_until']}. Listings found meanwhile are unrated.")
    return ""


def monitor_watch():
    """Health alert, SEPARATE from the daily recap: monitoring is on but no search cycle has finished for far longer than the search interval.
    Called every minute from recap_loop; the identical text is queued at most once per 24 h (see enqueue)."""
    hrs = panel.monitor_silent_hours()
    if hrs:
        enqueue("system", ["⚠️ " + T("נראה שהמעקב תקוע: הוא פועל, אבל אף חיפוש לא הסתיים כבר כמה שעות. פתחו את לוח הבקרה (לשונית פייסבוק) כדי לבדוק את החיבור והסורק.",
                                     "Monitoring looks stuck: it is switched on, but no search has finished for several hours. Open the control panel (Facebook tab) to check the connection and the scraper.")], "monitor-silent")


def ai_watch(state={"open": False}):
    """Once per breaker opening, tell the user (queued like any message). Called from recap_loop every minute."""
    h = ai_health()
    if h and h.get("breaker_open") and not state["open"]:
        enqueue("system", ["⚠️ " + T(f"הדירוג החכם הושהה: {h['breaker_reason']} (עד {h['breaker_until']}). מודעות חדשות יישמרו ללא דירוג לסיכום.",
                                     f"AI rating paused: {h['breaker_reason']} (until {h['breaker_until']}). New listings will be held as unrated for the recap.")], f"ai-pause:{h['breaker_until']}")
    state["open"] = bool(h and h.get("breaker_open"))


def scan_activity(st):
    """One plain line about what the scanner did that day (empty when no scan ran)."""
    if not st["scans"]:
        return ""
    n = st["scans"]
    if lang() == "en":
        line = f"🔎 {n} scan{'s' if n != 1 else ''}"
        if st["failed"]:
            line += f" ({st['completed']} completed, {len(st['failed'])} did not finish)"
        line += (f" · {st['reviewed']} listings reviewed · {st['ai_rated']} AI evaluations" + (f" ({st['ai_failed']} failed)" if st["ai_failed"] else "")
                 + f" · {st['below']} below the relevance threshold · {st['passed']} matching · {st['forwarded']} forwarded · {st['saved']} saved")
        return line
    line = f"🔎 {n} {'סריקה' if n == 1 else 'סריקות'}"
    if st["failed"]:
        line += f" ({st['completed']} הושלמו, {len(st['failed'])} לא הסתיימו)"
    line += (f" · {st['reviewed']} מודעות נבדקו · {st['ai_rated']} הערכות AI" + (f" ({st['ai_failed']} נכשלו)" if st["ai_failed"] else "")
             + f" · {st['below']} מתחת לסף הרלוונטיות · {st['passed']} מתאימות · {st['forwarded']} הועברו · {st['saved']} נשמרו")
    return line


def recap_has_activity(records, day):
    """A recap is only worth sending when something actually happened that day: a scan ran, or relevant listings were stored."""
    return bool(records) or panel.scan_day(day.strftime("%Y-%m-%d"))["scans"] > 0


def build_recap(records, day):
    st = panel.scan_day(day.strftime("%Y-%m-%d"))
    chunks = _recap_body(records, day, st)
    foot = ai_note().strip()
    if st["failed"]:
        foot += ("\n" if foot else "") + T(f"⚠️ {len(st['failed'])} סריקות לא הסתיימו כראוי היום. פרטים: לוח הבקרה, בית, פעילות סריקות.",
                                            f"⚠️ {len(st['failed'])} scan{'s' if len(st['failed']) != 1 else ''} did not finish properly today. Details: control panel, Home, Scan activity.")
    try:
        ob = outbox_stats()
        if ob["pending"] or ob["expired_24h"]:
            foot += ("\n" if foot else "") + T(f"⚠️ תור טלגרם: {ob['pending']} ממתינות, {ob['expired_24h']} פגו ב-24 שעות ({ob['last_error'] or 'אין שגיאה'})",
                                                f"⚠️ Telegram queue: {ob['pending']} pending, {ob['expired_24h']} expired in 24h ({ob['last_error'] or 'no error'})")
    except Exception:
        pass
    if not foot:
        return chunks
    if len(chunks[-1]) + len(foot) + 2 <= CHUNK:
        chunks[-1] += f"\n\n{DIV}\n{foot}"
    else:
        chunks.append(foot)
    return chunks


def _recap_body(records, day, st):
    date_txt = day.strftime("%a %-d %b") if lang() == "en" else day.strftime("%-d.%-m.%Y")
    latest = {}
    for r in records:
        latest[r["url"]] = r
    recs = sorted(latest.values(), key=sort_key)
    if not recs:
        act = scan_activity(st)
        return [T(f"📊 *סיכום יומי · {date_txt}*\nלא נמצאו היום מודעות רלוונטיות.", f"📊 *Daily recap · {date_txt}*\nNo relevant listings today.") + (f"\n\n{act}" if act else "")]
    names = []
    for r in recs:
        if r["item"] not in names:
            names.append(r["item"])
    act = scan_activity(st)
    header = T(f"📊 *סיכום יומי · {date_txt}*\n{len(recs)} {'מודעה רלוונטית' if len(recs) == 1 else 'מודעות רלוונטיות'} היום · מהזולה ליקרה",
               f"📊 *Daily recap · {date_txt}*\n{len(recs)} relevant listing{'s' if len(recs) != 1 else ''} today · cheapest first") + f"\n{act + chr(10) if act else ''}{DIV}"
    cards = []
    if len(names) > 1:
        for nm in names:
            group = [r for r in recs if r["item"] == nm]
            cards.append(f"{EMOJI.get(nm.lower(), '🛒')} *{item_label(nm, len(group))}* ({len(group)})")
            cards.extend(card(i, r) for i, r in enumerate(group, 1))
    else:
        cards = [card(i, r) for i, r in enumerate(recs, 1)]
    return pack(header, cards)


def to_html(text):
    """Cards use chat-style *bold* _italic_ ~strike~. Telegram HTML needs all untrusted text escaped first."""
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\w)_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)", r"<i>\1</i>", t)
    return re.sub(r"(?<!\w)~(?!\s)([^~\n]+?)(?<!\s)~(?!\w)", r"<s>\1</s>", t)


def tg_post(payload):
    req = urllib.request.Request(f"{TG_API}/bot{TG_TOKEN}/sendMessage", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def send_summary_now():
    """Today's summary, built exactly like the scheduled one, queued immediately. -> (messages queued, listings today)"""
    n = datetime.now()
    recs = load_day(n.strftime("%Y-%m-%d"))
    if not recap_has_activity(recs, n):
        return 0, 0  # nothing happened today: nothing to report
    queued = enqueue("recap", build_recap(recs, n), f"recap-manual:{int(time.time())}")
    return queued, len({r["url"] for r in recs})


def tg_updates():
    """Recent chats that wrote to the bot (to find the chat id automatically). -> (status, [ {id,type,name} ])"""
    req = urllib.request.Request(f"{TG_API}/bot{TG_TOKEN}/getUpdates?limit=50&timeout=0")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, []
    except Exception:
        return 0, []
    seen = {}
    for u in body.get("result", []):
        m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        c = m.get("chat") or {}
        if c.get("id") is not None:
            seen[c["id"]] = {"id": str(c["id"]), "type": c.get("type"), "name": c.get("title") or c.get("first_name") or c.get("username") or ""}
    return 200, list(seen.values())


def send(text):
    """One Telegram message. Returns (http_status, retry_after_seconds, note). Status 0 = network error/timeout.
    note is a short, secret-free reason that ends up in outbox.last_error."""
    base = {"chat_id": chat_id(), "link_preview_options": {"is_disabled": True}}
    try:
        status, body = tg_post({**base, "text": to_html(text), "parse_mode": "HTML"})
        if status == 400 and "parse" in str(body.get("description", "")).lower():
            status, body = tg_post({**base, "text": text})  # formatting rejected: deliver as plain text rather than loop forever
    except Exception as e:
        print("telegram error:", type(e).__name__, flush=True)
        return 0, 0, f"network {type(e).__name__}"
    if 200 <= status < 300:
        return status, 0, ""
    desc = str(body.get("description", ""))[:80]
    ra = int((body.get("parameters") or {}).get("retry_after", 0) or 0)
    if status in (400, 401, 403, 404):  # wrong token/chat, bot blocked or removed: configuration problem, keep queued, back off hard
        ra = max(ra, 600)
    print("telegram http error:", status, desc, flush=True)
    return status, ra, f"http {status} {desc}".strip()


# ---- durable outbox: a message is queued (fsync'd sqlite) before the HTTP request is answered, then a
# single worker delivers in order with exponential backoff. Delivery is at-least-once; the dedupe key
# stops identical messages (AIMM re-POST, double recap) from being queued twice.
db_lock = threading.Lock()


def db():
    con = sqlite3.connect(OUTBOX_DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS outbox(
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, dedupe TEXT NOT NULL, text TEXT NOT NULL,
        created TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        next_try REAL NOT NULL DEFAULT 0, last_error TEXT, sent_at TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_outbox_dedupe ON outbox(dedupe, created)")
    return con


def enqueue(kind, texts, dedupe_prefix):
    """Queue texts atomically. Returns how many were new (0 = duplicate of a message queued in the last 24h)."""
    now = datetime.now()
    cut = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    n = 0
    with db_lock:
        con = db()
        try:
            with con:
                for i, t in enumerate(texts):
                    key = f"{dedupe_prefix}:{i}:{hashlib.sha1(t.encode()).hexdigest()}"
                    if con.execute("SELECT 1 FROM outbox WHERE dedupe=? AND created>=? AND status!='expired'", (key, cut)).fetchone():
                        continue
                    con.execute("INSERT INTO outbox(kind,dedupe,text,created) VALUES(?,?,?,?)", (kind, key, t, now.isoformat(timespec="seconds")))
                    n += 1
        finally:
            con.close()
    return n


def backoff(attempts):
    return min(BACKOFF_BASE * 2 ** min(attempts, 6), 1800)  # 30 s ... 30 min by default


def in_quiet_hours(now=None):
    """Alerts about listings wait while the user's quiet hours are active (recap and system messages are not held)."""
    n = settings.get()["notifications"]
    if not n["quiet_enabled"]:
        return False
    now = now or datetime.now()
    cur, a, b = now.strftime("%H:%M"), n["quiet_from"], n["quiet_to"]
    return (a <= cur < b) if a < b else (cur >= a or cur < b)


def deliver_once():
    """Try the oldest due message. Returns seconds to sleep before calling again."""
    with db_lock:
        con = db()
        try:
            cut = (datetime.now() - OUTBOX_MAX_AGE).isoformat(timespec="seconds")
            with con:
                con.execute("UPDATE outbox SET status='expired', last_error='older than OUTBOX_MAX_AGE' WHERE status='pending' AND created<?", (cut,))
            quiet = in_quiet_hours()
            row = con.execute("SELECT id,text,attempts,next_try FROM outbox WHERE status='pending' AND (?=0 OR kind!='alert') ORDER BY id LIMIT 1", (1 if quiet else 0,)).fetchone()
        finally:
            con.close()
    if not row:
        return 5
    mid, text, attempts, next_try = row
    if not notify_enabled():
        return 5  # re-check often: switching alerts on (or pressing "send summary now") must not wait half a minute
    wait = next_try - time.time()
    if wait > 0:  # strict ordering: do not skip past a message that is backing off
        return min(wait, 30)
    status, retry_after, note = send(text)
    with db_lock:
        con = db()
        try:
            with con:
                if 200 <= status < 300:
                    con.execute("UPDATE outbox SET status='sent', sent_at=?, attempts=attempts+1, last_error=NULL WHERE id=?", (datetime.now().isoformat(timespec="seconds"), mid))
                else:
                    delay = max(backoff(attempts), retry_after)
                    con.execute("UPDATE outbox SET attempts=attempts+1, next_try=?, last_error=? WHERE id=?", (time.time() + delay, note or f"http {status}", mid))
        finally:
            con.close()
    if 200 <= status < 300:
        print(f"outbox sent id={mid}", flush=True)
        return SEND_GAP
    print(f"outbox retry id={mid} status={status} attempts={attempts + 1}", flush=True)
    return 5


def outbox_loop():
    while True:
        try:
            time.sleep(deliver_once())
        except Exception as e:
            print("outbox error:", repr(e), flush=True)
            time.sleep(15)


def outbox_stats():
    with db_lock:
        con = db()
        try:
            n, oldest = con.execute("SELECT COUNT(*), MIN(created) FROM outbox WHERE status='pending'").fetchone()
            err = con.execute("SELECT last_error FROM outbox WHERE status='pending' AND last_error IS NOT NULL ORDER BY id LIMIT 1").fetchone()
            sent24 = con.execute("SELECT COUNT(*) FROM outbox WHERE status='sent' AND sent_at>=?", ((datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds"),)).fetchone()[0]
            exp24 = con.execute("SELECT COUNT(*) FROM outbox WHERE status='expired' AND created>=?", ((datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds"),)).fetchone()[0]
        finally:
            con.close()
    return {"pending": n, "oldest_pending": oldest, "last_error": err[0] if err else None, "sent_24h": sent24, "expired_24h": exp24, "notify_enabled": notify_enabled()}


SAME_KEYS = ("item", "price", "score", "unrated", "ai_full", "currency", "title", "loc")


def save_alerts(items, name):
    """Append one record per listing and make sure it reached the disk. A listing identical to the stored latest record (same event
    delivered twice, a sender retry) is not written again. Returns (saved, duplicates)."""
    ids = search_ids()
    now = datetime.now()
    cfg = settings.get()
    prod = next((p for p in cfg["products"] if p["id"] == name), None)
    fp = settings.search_fingerprint(prod, cfg["facebook"]) if prod else None
    saved = dup = 0
    with lock:
        latest = {}
        try:
            with open(ALERTS, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("url"):
                        r["item"] = item_id(r.get("item"), ids)  # 'p2s' records (before the id fix) are the same search
                        latest[(r["url"], r["item"])] = r
        except FileNotFoundError:
            pass
        with open(ALERTS, "a", encoding="utf-8") as f:
            for it in items:
                rec = {**it, "fp": fp, "item": name, "ts": now.isoformat(timespec="seconds"), "date": now.strftime("%Y-%m-%d")}
                prev = latest.get((it.get("url"), name))
                if prev and all(prev.get(k) == rec.get(k) for k in SAME_KEYS):
                    dup += 1
                    continue
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                latest[(it.get("url"), name)] = rec
                saved += 1
            f.flush()
            os.fsync(f.fileno())
    try:  # found again by a search = live, whatever an earlier availability look said
        availability.mark_seen(DATA_DIR, [norm_url(it["url"]) for it in items if it.get("url")], int(time.time()))
    except Exception as e:
        print("availability: mark_seen failed:", type(e).__name__, flush=True)
    return saved, dup


# What the ingest endpoint did with each delivery. The manual-scan result compares this with what AIMM says it forwarded, so a listing
# that was found but never arrived / never got stored is reported instead of silently missing.
INGEST_LOG, EVENTS, ev_lock = [], {}, threading.Lock()


def ingest_note(**kw):
    with ev_lock:
        INGEST_LOG.append({"at": int(time.time()), **kw})
        del INGEST_LOG[:-1000]


def ingest_stats(since):
    with ev_lock:
        rows = [r for r in INGEST_LOG if r["at"] >= since]
    return {"events": len(rows), "forwarded": sum(r.get("raw", 0) for r in rows), "saved": sum(r.get("saved", 0) for r in rows), "duplicates": sum(r.get("dup", 0) for r in rows) + sum(1 for r in rows if r.get("replay")),
            "errors": sum(1 for r in rows if r.get("error")), "last_error": next((r["error"] for r in reversed(rows) if r.get("error")), "")}


def load_day(day_str):
    out = []
    try:
        with open(ALERTS, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("date") == day_str:
                    out.append(r)
    except OSError:
        pass
    return out


def load_all():
    """One row per listing (latest record, first-seen time) plus its price history: previous price and direction are derived from the
    stored records, so nothing new is needed in the database. A change is only reported when the price really differs."""
    first, latest, hist, items = {}, {}, {}, {}
    ids = search_ids()
    try:
        with open(ALERTS, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                u = r.get("url")
                if not u:
                    continue
                if r.get("item"):
                    r["item"] = item_id(r["item"], ids)  # records stored before the pluralisation fix ("p2s") belong to their real search
                first.setdefault(u, r.get("ts", ""))
                latest[u] = r
                if r.get("item") and r["item"] not in items.setdefault(u, []):
                    items[u].append(r["item"])  # the same listing can legitimately match several searches
                p = r.get("price")
                h = hist.setdefault(u, [])
                if p is not None and (not h or h[-1] != p):
                    h.append(p)
    except OSError:
        pass
    out = []
    for u, r in latest.items():
        row = {**r, "ts": first[u], "prev_price": None, "price_dir": None, "items": items.get(u) or [r.get("item")]}
        h, cur = hist.get(u, []), r.get("price")
        if cur is not None and len(h) >= 2 and h[-1] == cur and h[-2] != cur:
            row["prev_price"], row["price_dir"] = h[-2], ("down" if cur < h[-2] else "up")
        elif cur is not None and r.get("old") and r["old"] > cur:  # AIMM itself reported "new | old" on a listing we saw for the first time
            row["prev_price"], row["price_dir"] = r["old"], "down"
        out.append(row)
    return out


def remove_listings(urls):
    """Delete listings from the history WITHOUT blacklisting them (clean-up). Returns how many distinct listings were removed."""
    urls = {norm_url(u) for u in urls}
    with lock:
        try:
            with open(ALERTS, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return 0
        keep, gone = [], set()
        for line in lines:
            try:
                u = norm_url(json.loads(line).get("url"))
            except ValueError:
                keep.append(line)
                continue
            if u in urls:
                gone.add(u)
            else:
                keep.append(line)
        if gone:
            with open(ALERTS + ".tmp", "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(ALERTS + ".tmp", ALERTS)
    return len(gone)


def cleanup_candidates(kind, days=None):
    """URLs a clean-up would remove. Saved listings are never included. kinds: orphans (search deleted), stale (search edited since), older (not seen for N days)."""
    cfg = settings.get()
    prods = {p["id"]: p for p in cfg["products"]}
    saved = saved_set()
    cut = (datetime.now() - timedelta(days=int(days or 0))).strftime("%Y-%m-%d")
    latest, searches = {}, {}
    try:
        with open(ALERTS, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("url"):
                    latest[r["url"]] = r
                    searches.setdefault(r["url"], set()).add(r.get("item"))
    except OSError:
        pass
    out = []
    for u, r in latest.items():
        if norm_url(u) in saved:
            continue
        if kind == "orphans" and not (searches.get(u, set()) & set(prods)):
            out.append(u)
        elif kind == "stale" and r.get("item") in prods and r.get("fp") and r["fp"] != settings.search_fingerprint(prods[r["item"]], cfg["facebook"]):
            out.append(u)
        elif kind == "older" and days and r.get("date", "9999") < cut:
            out.append(u)
    return out


def prune_alerts(days):
    cut = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    keep_urls = saved_set()  # saved listings are never aged out
    with lock:
        try:
            with open(ALERTS, encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return 0
        keep = []
        for line in lines:
            try:
                rr = json.loads(line)
                if rr.get("date", "9999") < cut and norm_url(rr.get("url")) not in keep_urls:
                    continue
            except ValueError:
                pass
            keep.append(line)
        if len(keep) != len(lines):
            tmp = ALERTS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(tmp, ALERTS)
        return len(lines) - len(keep)


def load_blocked():
    out = set()
    try:
        with open(BLACKLIST, encoding="utf-8") as f:
            for line in f:
                try:
                    out.add(norm_url(json.loads(line)["url"]))
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    return out


LISTING_STATE = os.path.join(DATA_DIR, "listing-state.json")
state_lock = threading.Lock()


def saved_set():
    try:
        with open(LISTING_STATE, encoding="utf-8") as f:
            return {norm_url(u) for u in json.load(f).get("saved", {})}
    except (FileNotFoundError, ValueError):
        return set()


def set_saved(urls, on):
    with state_lock:
        try:
            with open(LISTING_STATE, encoding="utf-8") as f:
                d = json.load(f)
        except (FileNotFoundError, ValueError):
            d = {}
        sv = d.setdefault("saved", {})
        for u in {norm_url(x) for x in urls}:
            if on:
                sv[u] = datetime.now().isoformat(timespec="seconds")
            else:
                sv.pop(u, None)
        tmp = LISTING_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, LISTING_STATE)
    return len(sv)


def restore_listings(urls):
    """Undo a block: put the removed listing back into the history and drop the blacklist entries (needs the stored record)."""
    urls = {norm_url(u) for u in urls}
    n = 0
    with lock:
        try:
            with open(BLACKLIST, encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return 0
        keep, back = [], []
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                keep.append(line)
                continue
            if norm_url(r.get("url")) in urls:
                if isinstance(r.get("record"), dict):
                    back.append(r["record"])
                continue
            keep.append(line)
        with open(BLACKLIST + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(BLACKLIST + ".tmp", BLACKLIST)
        with open(ALERTS, "a", encoding="utf-8") as f:
            for rec in back:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    return n


def delete_listings(urls, reason):
    urls = {norm_url(u) for u in urls}
    now = datetime.now().isoformat(timespec="seconds")
    with lock:
        try:
            with open(ALERTS, encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        keep, seen = [], {}
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                keep.append(line)
                continue
            if norm_url(r.get("url")) in urls:
                seen[norm_url(r["url"])] = r
            else:
                keep.append(line)
        if len(keep) != len(lines):
            tmp = ALERTS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(tmp, ALERTS)
        with open(BLACKLIST, "a", encoding="utf-8") as f:
            for u in sorted(urls):
                r = seen.get(u, {})
                f.write(json.dumps({"url": u, "title": r.get("title", ""), "item": r.get("item", ""), "price": r.get("price"), "reason": reason, "ts": now, **({"record": r} if r else {})}, ensure_ascii=False) + "\n")
    return len(seen)


def read_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def write_state(st):
    with open(STATE + ".tmp", "w") as f:
        json.dump(st, f)
    os.replace(STATE + ".tmp", STATE)


def recap_loop():
    while True:
        try:
            ai_watch()
            monitor_watch()
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            st = read_state()
            nset = settings.get()["notifications"]
            rh, rm = (int(x) for x in nset["recap_time"].split(":"))
            due = (now.hour, now.minute) >= (rh, rm)
            if "last_recap" not in st:
                st["last_recap"] = today if due else ""
                write_state(st)
            if st.get("last_prune") != today:
                dropped = 0  # listing history is only ever deleted by the user (Listings > Clean up); retention applies to the message queue only
                with db_lock:
                    con = db()
                    try:
                        with con:
                            con.execute("DELETE FROM outbox WHERE status!='pending' AND created<?", ((now - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds"),))
                    finally:
                        con.close()
                st["last_prune"] = today
                write_state(st)
                print(f"prune alerts older than {RETENTION_DAYS}d dropped={dropped}", flush=True)
            if due and st["last_recap"] != today:
                recs = load_day(today)
                if nset["recap_enabled"] and recap_has_activity(recs, now) and (recs or nset["recap_when_empty"]):
                    chunks = build_recap(recs, now)
                    enqueue("recap", chunks, f"recap:{today}")  # durable; the worker retries, so mark the day done once queued
                    print(f"recap day={today} queued chunks={len(chunks)}", flush=True)
                st["last_recap"] = today
                write_state(st)
        except Exception as e:
            print("recap error:", repr(e), flush=True)
        time.sleep(60)


def csrf_key():
    try:
        with open(KEY_FILE, "rb") as f:
            return f.read()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key


def csrf_token():
    return hmac.new(csrf_key(), b"listings-delete-v1", hashlib.sha256).hexdigest()


fails, fails_lock = {}, threading.Lock()


class Base(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    MAX_BODY = 1_000_000

    def reply(self, code, body=b"", ctype="application/json", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = -1
        if n < 0 or n > self.MAX_BODY:
            return None
        return self.rfile.read(n).decode("utf-8", "replace")

    def log_message(self, *a):
        pass


class IngestHandler(Base):
    """Port 8080, reachable only from the private scraper network. Accepts AIMM's ntfy-style POST, nothing else."""

    def authorised(self):
        if not INGEST_TOKEN:
            return True
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return hmac.compare_digest(got.encode(), INGEST_TOKEN.encode())

    def do_POST(self):
        if not self.authorised():
            return self.reply(401, b'{"error":"unauthorised"}')
        raw = self.read_body()
        if raw is None:
            return self.reply(413, b'{"error":"too large"}')
        body = raw.removesuffix(FOOTER)
        title = self.headers.get("Title", "")[:200]
        key = hashlib.sha1((title + "\n" + body).encode("utf-8", "replace")).hexdigest()
        now_t = time.time()
        with ev_lock:
            for k in [k for k, v in EVENTS.items() if now_t - v["at"] > 600]:
                del EVENTS[k]
            ev = EVENTS.setdefault(key, {"at": now_t, "queued": 0, "done": False})
        if ev["done"]:  # the same delivery again (sender retry): already stored and queued, do nothing twice
            ingest_note(replay=True)
            print("ingest: duplicate delivery ignored", flush=True)
            return self.reply(200, b'{"queued":0,"duplicate":true}')
        raw_items = 0
        try:
            raw_items = sum(1 for b in BLOCK_SPLIT.split(body.strip()) if parse_block(b))
            chunks, items, name = build_alert(title, body, load_blocked())
            # queue first (durable), then store history: a crash between the two costs a history row
            # (still in tonight's recap if saved) but never a lost alert. A retry after a failed save must not queue the alert again.
            queued = ev["queued"]
            if chunks and not ev["queued"]:
                queued = ev["queued"] = enqueue("alert", chunks, "alert")
            saved, dup = save_alerts(items, name) if items else (0, 0)
            ev["done"] = True
        except Exception as e:
            ingest_note(raw=raw_items, error=type(e).__name__)
            print("ingest error:", repr(e), flush=True)
            return self.reply(500, b'{"error":"internal"}')  # nothing was acknowledged; the sender may retry
        ingest_note(raw=raw_items, saved=saved, dup=dup)
        print(f"ingest raw={len(raw)} items={len(items)} held={len(items) - sum(urgent(i, name) for i in items) if items else 0} chunks={len(chunks)} queued={queued} saved={saved} dup={dup}", flush=True)
        self.reply(200, b'{"queued":%d}' % queued)

    def do_GET(self):
        if self.path.split("?")[0] == "/healthz":
            alive = [t.name for t in threading.enumerate()]
            ok = all(n in alive for n in ("outbox", "recap", "housekeeping", "reconcile"))
            return self.reply(200 if ok else 503, json.dumps({"ok": ok, "outbox": outbox_stats()}).encode())
        self.reply(404, b"")


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_TYPES = {"app.js": "text/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8", "app.html": "text/html; charset=utf-8"}
APP_CSP = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
SCRAPER_HOST = os.environ.get("SCRAPER_HOST", "marketplace-scraper")
NOVNC_PORT = int(os.environ.get("NOVNC_PORT", "6080"))
browser_gate = {"t": 0.0, "open": False}


def browser_open():
    """The noVNC bridge exists only while the login helper is open (checked at most every 3 s)."""
    if time.time() - browser_gate["t"] > 3:
        st, ag = panel.agent("/status", timeout=5)
        browser_gate.update(t=time.time(), open=st == 200 and bool(ag.get("helper", {}).get("open")))
    return browser_gate["open"]


class UiHandler(Base):
    """Port 8081 (published on the LAN IP only). Basic auth on everything; every state change also needs a CSRF token
    and a same-origin request. The noVNC bridge is only reachable through here, after authentication."""
    MAX_BODY = 65536

    def authenticated(self):
        ip = self.client_address[0]
        with fails_lock:
            n, first = fails.get(ip, (0, time.time()))
            if time.time() - first > 300:
                n, first = 0, time.time()
            if n >= 5:
                self.reply(429, b"too many attempts", "text/plain", [("Retry-After", "300")])
                return False
        h = self.headers.get("Authorization", "")
        ok = False
        if h.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(h[6:]).decode("utf-8").partition(":")
                ok = hmac.compare_digest(u.encode(), UI_USER.encode()) & hmac.compare_digest(p.encode(), UI_PASS.encode())
            except Exception:
                ok = False
        if not ok:
            with fails_lock:
                fails[ip] = (n + (1 if h else 0), first)
            self.reply(401, b"authentication required", "text/plain", [("WWW-Authenticate", 'Basic realm="Marketplace Monitor", charset="UTF-8"')])
            return False
        return True

    def same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True  # non-browser client; the CSRF token is still required for changes
        return origin.split("://", 1)[-1] == self.headers.get("Host", "")

    def csrf_ok(self):
        return self.same_origin() and hmac.compare_digest(self.headers.get("X-CSRF-Token", "").encode(), csrf_token().encode())

    def send_obj(self, code, obj):
        self.reply(code, json.dumps(obj, ensure_ascii=False).encode())

    def page(self, name, ctype, csp, transform=None):
        with open(os.path.join(STATIC_DIR if name in STATIC_TYPES else os.path.dirname(os.path.abspath(__file__)), name), encoding="utf-8") as f:
            body = f.read()
        if transform:
            body = transform(body)
        if name == "app.html" and settings.lang() == "en":
            body = body.replace('lang="he" dir="rtl"', 'lang="en" dir="ltr"').replace("<title>ניטור מרקטפלייס</title>", "<title>Marketplace Monitor</title>").replace('aria-label="ניווט ראשי"', 'aria-label="Main navigation"')
        self.reply(200, body.encode(), ctype, [("Content-Security-Policy", csp)])

    # ---- routing -------------------------------------------------------
    def do_GET(self):
        if not self.authenticated():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self.page("app.html", STATIC_TYPES["app.html"], APP_CSP)
        if path in ("/static/app.js", "/static/app.css"):
            n = path.rsplit("/", 1)[1]
            return self.page(n, STATIC_TYPES[n], APP_CSP)
        if path.rstrip("/") == "/listings":
            return self.reply(302, b"", extra=[("Location", "/#/listings")])
        if path.startswith("/api/"):
            return self.api("GET", path)
        if path == "/browser-ws":
            return self.ws_proxy()
        if path == "/browser" or path.startswith("/browser/"):
            return self.browser_proxy(path)
        self.reply(404, b"", "text/plain")

    def do_POST(self):
        self.mutate("POST")

    def do_PUT(self):
        self.mutate("PUT")

    def do_DELETE(self):
        self.mutate("DELETE")

    def mutate(self, method):
        if not self.authenticated():
            return
        path = self.path.split("?")[0]
        if not self.csrf_ok():
            return self.send_obj(403, {"error": "forbidden"})
        if path.startswith("/api/"):
            return self.api(method, path)
        self.reply(404, b"")

    def api(self, method, path):
        body = {}
        if method in ("POST", "PUT"):
            raw = self.read_body()
            if raw is None:
                return self.send_obj(413, {"error": settings.T("גוף הבקשה גדול מדי", "Request body too large")})
            try:
                body = json.loads(raw) if raw.strip() else {}
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self.send_obj(400, {"error": settings.T("בקשה לא תקינה", "Invalid request")})
        parts = [x for x in path[len("/api/"):].split("/") if x]
        try:
            code, obj = self.dispatch(method, parts, body)
        except Exception as e:
            print("api error:", type(e).__name__, panel.redact(str(e)), flush=True)
            code, obj = 500, {"error": settings.T("שגיאה פנימית", "Internal error")}
        self.send_obj(code, obj)

    def dispatch(self, method, parts, body):
        r = tuple(parts[:1])
        if method == "GET":
            if parts == ["me"]:
                return 200, {"user": UI_USER, "csrf": csrf_token(), "tz": TZ_NAME}
            if parts == ["status"]:
                return 200, panel.status()
            if parts == ["settings"]:
                s = settings.load()
                return 200, {"settings": s, "limits": {"min_interval": settings.MIN_INTERVAL_MIN, "max_products": settings.MAX_PRODUCTS, "conditions": settings.CONDITIONS,
                                                       "date_listed": settings.DATE_LISTED, "delivery": settings.DELIVERY, "currencies": settings.CURRENCIES}}
            if parts == ["home"]:
                return 200, panel.home()
            if parts == ["listings"]:
                return 200, panel.listings()
            if parts == ["facebook"]:
                return 200, panel.facebook_info()
            if parts == ["system"]:
                return 200, panel.system()
            if parts == ["config"]:
                return 200, {"text": panel.config_preview()}
            return 404, {"error": settings.T("לא נמצא", "Not found")}
        ok_wrap = lambda res: (200, res[1]) if res[0] else (400, res[1])
        if parts == ["products"] and method == "POST":
            return ok_wrap(panel.product_create(body))
        if len(parts) == 2 and parts[0] == "products":
            if method == "PUT":
                return ok_wrap(panel.product_update(parts[1], body))
            if method == "DELETE":
                return ok_wrap(panel.product_delete(parts[1]))
        if len(parts) == 3 and parts[0] == "products" and method == "POST":
            if parts[2] == "duplicate":
                return ok_wrap(panel.product_duplicate(parts[1]))
            if parts[2] == "toggle":
                return ok_wrap(panel.product_toggle(parts[1], body.get("enabled")))
        if len(parts) == 2 and parts[0] == "settings" and method == "PUT":
            return ok_wrap(panel.section_update(parts[1], body))
        if parts == ["scraper"] and method == "POST":
            return panel.scraper_action(body.get("action"), body.get("hours"), body.get("confirm") is True)
        if len(parts) == 2 and parts[0] == "facebook" and method == "POST":
            return panel.facebook_action(parts[1])
        if parts == ["listings", "action"] and method == "POST":
            return panel.listing_action(body)
        if parts == ["setup"] and method == "POST":
            return ok_wrap(panel.setup_update(body))
        if parts == ["notifications", "detect"] and method == "POST":
            return panel.telegram_detect()
        if parts == ["notifications", "test"] and method == "POST":
            return panel.telegram_test()
        if parts == ["listings", "cleanup"] and method == "POST":
            return panel.listings_cleanup(body)
        if parts == ["language"] and method == "POST":
            return ok_wrap(panel.set_language(body.get("language")))
        if parts == ["summary", "send"] and method == "POST":
            return panel.send_summary()
        return 404, {"error": settings.T("לא נמצא", "Not found")}

    # ---- noVNC bridge (only while the login helper is open) ---------------
    def browser_proxy(self, path):
        if not browser_open():
            return self.reply(503, settings.T("דפדפן ההתחברות סגור. פתחו אותו מלשונית פייסבוק בלוח הבקרה.", "The login browser is closed. Open it from the Facebook page of the control panel.").encode(), "text/plain; charset=utf-8")
        rest = path[len("/browser"):] or "/"
        if rest in ("", "/"):
            return self.reply(302, b"", extra=[("Location", "/browser/vnc.html?autoconnect=1&resize=scale&path=browser-ws")])
        if ".." in rest or "//" in rest or not re.fullmatch(r"/[A-Za-z0-9_./-]+", rest):
            return self.reply(400, b"")
        try:
            req = urllib.request.Request(f"http://{SCRAPER_HOST}:{NOVNC_PORT}{rest}")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read(5_000_000)
                ct = r.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as e:
            return self.reply(e.code, b"")
        except Exception:
            return self.reply(502, b"")
        self.reply(200, data, ct)

    def ws_proxy(self):
        if self.headers.get("Upgrade", "").lower() != "websocket":
            return self.reply(400, b"")
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != self.headers.get("Host", ""):
            return self.reply(403, b"")  # cross-site WebSocket hijacking guard
        if not browser_open():
            return self.reply(503, b"")
        try:
            up = socket.create_connection((SCRAPER_HOST, NOVNC_PORT), timeout=10)
        except OSError:
            return self.reply(502, b"")
        lines = ["GET /websockify HTTP/1.1", f"Host: {SCRAPER_HOST}:{NOVNC_PORT}"]
        for h in ("Upgrade", "Connection", "Sec-WebSocket-Key", "Sec-WebSocket-Version", "Sec-WebSocket-Protocol", "Sec-WebSocket-Extensions"):
            if self.headers.get(h):
                lines.append(f"{h}: {self.headers[h]}")
        up.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        cl = self.connection
        cl.settimeout(None)
        up.settimeout(None)
        self.close_connection = True
        try:
            socks = [cl, up]
            while True:
                r, _, _ = select.select(socks, [], [], 60)
                if not r:
                    continue
                for sk in r:
                    data = sk.recv(65536)
                    if not data:
                        return
                    (up if sk is cl else cl).sendall(data)
        except OSError:
            pass
        finally:
            up.close()


def housekeeping_loop():
    """Health summary every 15 min; daily 04:15 cache prune, 04:30 copy of manual removals into config.toml."""
    os.environ.setdefault("MP_OUT", os.path.join(DATA_DIR, "health.json"))
    os.environ.setdefault("MP_STATE", os.path.join(DATA_DIR, "health_state.json"))
    os.environ.setdefault("MP_BLACKLIST", os.path.join(DATA_DIR, "blacklist.jsonl"))
    import health, feedback, prune
    last_health, last_feedback, last_prune = 0.0, None, None
    if datetime.now().strftime("%H:%M") >= "04:15":
        last_prune = datetime.now().date()
    if datetime.now().strftime("%H:%M") >= "04:30":
        last_feedback = datetime.now().date()
    while True:
        now = datetime.now()
        if time.time() - last_health >= 900:
            last_health = time.time()
            try:
                health.main(dry=False, quiet=True)
            except Exception as e:
                print("health failed:", e, flush=True)
        if now.strftime("%H:%M") >= "04:15" and last_prune != now.date():
            last_prune = now.date()
            try:
                prune.main(apply=True)
            except Exception as e:
                print("prune failed:", e, flush=True)
        if now.strftime("%H:%M") >= "04:30" and last_feedback != now.date():
            last_feedback = now.date()
            try:
                panel.regenerate("feedback")  # blacklist reasons become AI examples inside the generated config.toml
            except Exception as e:
                print("feedback failed:", e, flush=True)
        time.sleep(60)


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    os.umask(0o077)
    csrf_key()
    db().close()
    os.makedirs(settings.PANEL_DIR, mode=0o700, exist_ok=True)

    def _feedback_for(key):
        import feedback
        return feedback.block(feedback.load().get(key, []))

    settings.known_ids = lambda: {r.get("item") for r in load_all() if r.get("item")}
    panel.init(ingest_stats=ingest_stats, remove_listings=remove_listings, cleanup_candidates=cleanup_candidates, send_summary=send_summary_now, listings_all=load_all, saved_set=saved_set, set_saved=set_saved, delete_listings=delete_listings, restore_listings=restore_listings, tg_updates=tg_updates, norm_url=norm_url,
               outbox_stats=outbox_stats, send=send, load_day=load_day, enqueue=enqueue, feedback_for=_feedback_for, ai_health=lambda: ai_health() or {},
               agent_token=lambda: env_secret("AGENT_TOKEN", ""), guard_token=lambda: env_secret("GUARD_TOKEN", ""), tg_token=lambda: TG_TOKEN, chat_id=chat_id,
               health_path=HEALTH, mp_log=os.environ.get("MP_LOG", "/aimm/ai-marketplace-monitor.log"), mp_config=os.environ.get("MP_CONFIG", "/aimm/config.toml"))
    ns = settings.get()["notifications"]
    print("start tz", TZ_NAME, "local", datetime.now().isoformat(timespec="seconds"), "recap", ns["recap_time"], "notify_enabled", ns["enabled"], flush=True)

    def reconcile_loop():
        try:  # a deploy that changes the generator (cfggen.py) applies to the stored settings at once; writes only if the text differs
            if panel.current_config_text():  # a fresh install gets its first config from the setup flow, not here
                panel.regenerate("startup")
        except Exception as e:
            print("config regenerate error:", type(e).__name__, flush=True)
        while True:
            try:
                panel.reconcile()
            except Exception as e:
                print("reconcile error:", type(e).__name__, flush=True)
            time.sleep(float(os.environ.get("RECONCILE_S", "20")))

    for fn, nm in ((outbox_loop, "outbox"), (recap_loop, "recap"), (housekeeping_loop, "housekeeping"), (reconcile_loop, "reconcile")):
        threading.Thread(target=fn, name=nm, daemon=True).start()
    ui = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("UI_PORT", "8081"))), UiHandler)
    threading.Thread(target=ui.serve_forever, name="ui", daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), IngestHandler).serve_forever()
