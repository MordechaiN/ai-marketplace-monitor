"""Panel settings: one validated JSON document, atomic writes. Stdlib only.
Nothing secret lives here (tokens stay in the container environment). Messages are shown to the user."""
import copy, json, os, re, threading, time
from datetime import datetime

DATA_DIR = os.environ.get("DATA_DIR", "/data")
PANEL_DIR = os.path.join(DATA_DIR, "panel")
SETTINGS_FILE = os.path.join(PANEL_DIR, "settings.json")
MAX_PRODUCTS = 30
MIN_INTERVAL_MIN = 30  # conservative floor: faster polling raises the risk of Facebook restricting the account
# Currencies AIMM 0.10.2 recognises (utils.Currency) and its rate table covers, minus obsolete ones. Facebook's search URL has NO currency
# parameter: prices are interpreted in the marketplace location's currency. The per-search currency is a RESTRICTION on listings (see forwarder),
# never a conversion.
# counters of a finished manual scan, all taken from real log lines / the ingest endpoint (never estimated)
SCAN_COUNTS = ("reviewed", "ai_rated", "below", "passed", "forwarded", "saved", "ingest_errors")


def _scan_diag(ls):
    """The AI breakdown of a scan (cache hits, rating histogram, closest rejected examples), bounded and typed."""
    r = ls.get("ratings") if isinstance(ls.get("ratings"), dict) else {}
    ex = ls.get("examples") if isinstance(ls.get("examples"), list) else []
    tm = ls.get("timing") if isinstance(ls.get("timing"), dict) else None
    extra = {"timing": {k: int(v) for k, v in tm.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}} if tm else {}
    for k in ("started", "ended"):
        if isinstance(ls.get(k), (int, float)) and not isinstance(ls.get(k), bool):
            extra[k] = int(ls[k])
    return {**extra, "ai_cached": int(ls.get("ai_cached", 0) or 0), "ratings": {k: int(v) for k, v in r.items() if k in "12345" and len(k) == 1 and isinstance(v, int)},
            "examples": [{"rating": int(e.get("rating", 0) or 0), "title": str(e.get("title", ""))[:70], "reason": str(e.get("reason", ""))[:160]} for e in ex[:5] if isinstance(e, dict)]}
CURRENCIES = ["ILS", "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY", "HKD", "SGD", "INR", "KRW", "MXN", "BRL", "ZAR", "TRY", "RUB", "PLN", "CZK", "DKK", "HUF", "NOK", "SEK", "RON", "BGN", "ISK", "IDR", "MYR", "PHP", "THB"]
CONDITIONS = ["new", "used_like_new", "used_good", "used_fair"]
DATE_LISTED = [0, 1, 7, 30]
DELIVERY = ["all", "local_pick_up", "shipping"]
lock = threading.RLock()
_lang = {"t": 0.0, "v": "he"}


def lang():
    """Interface language ('he' default | 'en'). Read straight from the settings file so validation messages can use it without recursion."""
    if time.time() - _lang["t"] > 2:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                v = json.load(f).get("ui", {}).get("language", "he")
        except (OSError, ValueError, AttributeError):
            v = "he"
        _lang.update(t=time.time(), v=v if v in ("he", "en") else "he")
    return _lang["v"]


def T(he, en):
    return en if lang() == "en" else he


DEFAULTS = {
    "version": 1,
    "scraper": {"desired": "stopped", "pause_until": None, "scan_started": None, "scan_offset": 0, "scan_inode": 0, "scan_file_offset": 0},
    "facebook": {
        "search_city": "", "radius": 30, "currency": "ILS", "search_interval_min": 60, "max_search_interval_min": 90,
        "rating": 4, "login_wait_time": 5,
        "language": "auto", "detected_language": "",
    },
    "notifications": {
        "enabled": False, "chat_id": "", "recap_enabled": True, "recap_time": "21:00",
        "recap_when_empty": True, "immediate_min_score": 5,
        "quiet_enabled": False, "quiet_from": "23:00", "quiet_to": "07:00",
    },
    "ui": {"language": "he", "last_scan": None, "telegram_tested": False, "facebook_last_ok": 0, "last_seen": 0, "setup_dismissed": False, "ever_started": False},
    "products": [],
    "product_seq": 0,  # highest search id ever handed out: ids are never reused, so old listings can never attach to a new search
}


def _s(v):
    return v.strip() if isinstance(v, str) else v


def _list(v, field, errs, max_items, min_len=1, max_len=80):
    """list of short non-empty strings; accepts a list or a newline/comma separated string."""
    if v is None or v == "":
        return []
    if isinstance(v, str):
        v = [x for x in re.split(r"[\n,]", v)]
    if not isinstance(v, list):
        errs.append((field, T("צריכה להיות רשימה", "must be a list")))
        return []
    out = []
    for x in v:
        x = _s(x) if isinstance(x, str) else x
        if not isinstance(x, str):
            errs.append((field, T("ערך לא תקין ברשימה", "invalid value in the list")))
            continue
        if not x:
            continue
        if not (min_len <= len(x) <= max_len):
            errs.append((field, T(f"כל ערך חייב להיות באורך {min_len}-{max_len} תווים: “{x[:20]}”", f"each value must be {min_len}-{max_len} characters: \"{x[:20]}\"")))
            continue
        if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", x):
            errs.append((field, T("תווי בקרה אסורים", "control characters are not allowed")))
            continue
        if x not in out:
            out.append(x)
    if len(out) > max_items:
        errs.append((field, T(f"מקסימום {max_items} ערכים", f"at most {max_items} values")))
    return out[:max_items]


def _int(v, field, errs, lo, hi, label, allow_none=False):
    if v is None or v == "":
        if allow_none:
            return None
        errs.append((field, T(f"{label}: שדה חובה", f"{label}: required")))
        return None
    try:
        if isinstance(v, bool) or (isinstance(v, float) and v != int(v)):
            raise ValueError
        n = int(str(v).strip())
    except (ValueError, TypeError):
        errs.append((field, T(f"{label}: חייב להיות מספר שלם", f"{label}: must be a whole number")))
        return None
    if not lo <= n <= hi:
        errs.append((field, T(f"{label}: חייב להיות בין {lo} ל-{hi}", f"{label}: must be between {lo} and {hi}")))
        return None
    return n


def _city(v, field, errs, required):
    v = _s(v) or ""
    if not v:
        if required:
            errs.append((field, T("חובה להזין עיר (המזהה שמופיע בכתובת של Marketplace)", "A city is required (the part of the Marketplace address after /marketplace/)")))
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,60}", v):
        errs.append((field, T("מזהה עיר: אותיות לטיניות, ספרות, נקודה, מקף או קו תחתון בלבד (למשל telaviv). זה מה שמופיע אחרי /marketplace/ בכתובת", "City id: Latin letters, digits, dot, dash or underscore only (e.g. telaviv). It is the part of the Marketplace address after /marketplace/")))
        return ""
    return v


def clean_product(p, errs, prefix):
    """Validate one product. errs gets (field, message); field is f'{prefix}.<name>'."""
    f = lambda n: f"{prefix}.{n}"
    out = {"id": _s(p.get("id")) or "", "enabled": bool(p.get("enabled", True))}
    name = _s(p.get("name")) or ""
    if not (1 <= len(name) <= 40):
        errs.append((f("name"), T("שם המוצר חובה (עד 40 תווים)", "Search name is required (up to 40 characters)")))
    out["name"] = name
    out["search_phrases"] = _list(p.get("search_phrases"), f("search_phrases"), errs, 10, 2, 60)
    if not out["search_phrases"]:
        errs.append((f("search_phrases"), T("חובה לפחות ביטוי חיפוש אחד (2 תווים ומעלה)", "At least one search phrase is required (2+ characters)")))
    out["keywords"] = _list(p.get("keywords"), f("keywords"), errs, 10, 1, 120)
    out["antikeywords"] = _list(p.get("antikeywords"), f("antikeywords"), errs, 20, 1, 120)
    d = p.get("description") or ""
    if not isinstance(d, str) or len(d.strip()) > 1500 or re.search(r"[\x00-\x08\x0b-\x1f\x7f]", d.replace("\n", "")):
        errs.append((f("description"), T("התיאור חייב להיות טקסט עד 1500 תווים", "The description must be text of up to 1500 characters")))
        d = ""
    out["description"] = d.strip()
    out["min_price"] = _int(p.get("min_price"), f("min_price"), errs, 0, 10_000_000, T("מחיר מינימום", "Minimum price"), True)
    out["max_price"] = _int(p.get("max_price"), f("max_price"), errs, 0, 10_000_000, T("מחיר מקסימום", "Maximum price"), True)
    if out["min_price"] is not None and out["max_price"] is not None and out["min_price"] > out["max_price"]:
        errs.append((f("max_price"), T("מחיר מקסימום קטן ממחיר המינימום", "Maximum price is lower than the minimum price")))
    cur = (_s(p.get("currency")) or "").upper()
    if cur and cur not in CURRENCIES:
        errs.append((f("currency"), T("מטבע לא נתמך", "Unsupported currency")))
        cur = ""
    out["currency"] = cur
    out["great_deal_price"] = _int(p.get("great_deal_price"), f("great_deal_price"), errs, 0, 10_000_000, T("מחיר מציאה", "Great-deal price"), True)
    out["search_city"] = _city(p.get("search_city"), f("search_city"), errs, False)
    out["radius"] = _int(p.get("radius"), f("radius"), errs, 1, 500, T("רדיוס (ק\"מ)", "Radius (km)"), True)
    out["rating"] = _int(p.get("rating"), f("rating"), errs, 1, 5, T("דירוג מינימלי", "Minimum rating"), True)
    out["search_interval_min"] = _int(p.get("search_interval_min"), f("search_interval_min"), errs, MIN_INTERVAL_MIN, 1440, T("תדירות חיפוש (דקות)", "Search interval (minutes)"), True)
    cond = p.get("condition") or []
    if not isinstance(cond, list) or any(c not in CONDITIONS for c in cond):
        errs.append((f("condition"), T("מצב פריט לא תקין", "invalid item condition")))
        cond = []
    out["condition"] = [c for c in CONDITIONS if c in cond]
    dl = p.get("date_listed", 0)
    dl = 0 if dl in (None, "") else dl
    try:
        dl = int(dl)
    except (ValueError, TypeError):
        dl = -1
    if dl not in DATE_LISTED:
        errs.append((f("date_listed"), T("תאריך פרסום חייב להיות: ללא הגבלה, 24 שעות, 7 ימים או 30 ימים", "Posted within must be: any time, 24 hours, 7 days or 30 days")))
        dl = 0
    out["date_listed"] = dl
    dm = p.get("delivery_method") or "all"
    if dm not in DELIVERY:
        errs.append((f("delivery_method"), T("שיטת מסירה לא תקינה", "invalid delivery method")))
        dm = "all"
    out["delivery_method"] = dm
    return out


known_ids = lambda: []  # the forwarder sets this to the search ids found in the listing history


def clean(doc):
    """Return (clean_settings, errors[list of {field,message}]). Never raises on user data."""
    errs = []
    doc = doc if isinstance(doc, dict) else {}
    base = copy.deepcopy(DEFAULTS)
    sc = doc.get("scraper") if isinstance(doc.get("scraper"), dict) else {}
    desired = sc.get("desired", "stopped")
    base["scraper"] = {"desired": desired if desired in ("stopped", "running", "paused", "scan") else "stopped",
                       "pause_until": sc.get("pause_until") if isinstance(sc.get("pause_until"), (int, float)) else None,
                       "scan_started": sc.get("scan_started") if isinstance(sc.get("scan_started"), (int, float)) else None,
                       **{k: int(sc[k]) if isinstance(sc.get(k), (int, float)) and sc[k] >= 0 else 0 for k in ("scan_offset", "scan_inode", "scan_file_offset")}}
    fb = doc.get("facebook") if isinstance(doc.get("facebook"), dict) else {}
    g = lambda k: fb.get(k, DEFAULTS["facebook"][k])
    F = {"search_city": _city(g("search_city"), "facebook.search_city", errs, False)}
    F["radius"] = _int(g("radius"), "facebook.radius", errs, 1, 500, T("רדיוס (ק\"מ)", "Radius (km)"))
    cur = (_s(g("currency")) or "").upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        errs.append(("facebook.currency", T("מטבע: שלוש אותיות (ILS)", "Currency: three letters (ILS)")))
        cur = "ILS"
    F["currency"] = cur
    F["search_interval_min"] = _int(g("search_interval_min"), "facebook.search_interval_min", errs, MIN_INTERVAL_MIN, 1440, T("תדירות חיפוש (דקות)", "Search interval (minutes)"))
    F["max_search_interval_min"] = _int(g("max_search_interval_min"), "facebook.max_search_interval_min", errs, MIN_INTERVAL_MIN, 2880, T("תדירות מקסימלית (דקות)", "Maximum interval (minutes)"))
    if F["search_interval_min"] and F["max_search_interval_min"] and F["max_search_interval_min"] < F["search_interval_min"]:
        errs.append(("facebook.max_search_interval_min", T("התדירות המקסימלית קטנה מהמינימלית", "The maximum interval is lower than the minimum")))
    F["rating"] = _int(g("rating"), "facebook.rating", errs, 1, 5, T("דירוג מינימלי", "Minimum rating"))
    F["login_wait_time"] = _int(g("login_wait_time"), "facebook.login_wait_time", errs, 0, 300, T("זמן המתנה להתחברות (שניות)", "Login wait (seconds)"))
    lang = _s(g("language")) or "auto"
    if lang not in ("auto", "en", "he"):
        errs.append(("facebook.language", T("שפת חשבון הפייסבוק: אוטומטי, אנגלית או עברית", "Facebook account language: auto, English or Hebrew")))
        lang = "auto"
    F["language"] = lang
    det = str(_s(g("detected_language")) or "")
    F["detected_language"] = det if re.fullmatch(r"[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,4})?", det) else ""
    base["facebook"] = {k: (v if v is not None else DEFAULTS["facebook"][k]) for k, v in F.items()}
    n = doc.get("notifications") if isinstance(doc.get("notifications"), dict) else {}
    h = lambda k: n.get(k, DEFAULTS["notifications"][k])
    chat = str(_s(h("chat_id")) or "")
    if chat and not re.fullmatch(r"-?\d{3,20}|@[A-Za-z0-9_]{5,32}", chat):
        errs.append(("notifications.chat_id", T("מזהה צ'אט: מספר (למשל 123456789, או -100... לקבוצה) או @שם_ערוץ", "Chat id: a number (e.g. 123456789, or -100... for a group) or @channel_name")))
        chat = ""
    rt = str(_s(h("recap_time")) or "")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", rt):
        errs.append(("notifications.recap_time", T("שעת סיכום: HH:MM (למשל 21:00)", "Summary time: HH:MM (e.g. 21:00)")))
        rt = "21:00"
    ims = _int(h("immediate_min_score"), "notifications.immediate_min_score", errs, 4, 5, T("ציון להתראה מיידית", "Rating for instant alerts"))
    quiet = {}
    for k, dflt in (("quiet_from", "23:00"), ("quiet_to", "07:00")):
        v = str(_s(h(k)) or "")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            errs.append((f"notifications.{k}", T("שעה בפורמט HH:MM (למשל 23:00)", "Time as HH:MM (e.g. 23:00)")))
            v = dflt
        quiet[k] = v
    if bool(h("quiet_enabled")) and quiet["quiet_from"] == quiet["quiet_to"]:
        errs.append(("notifications.quiet_to", T("שעת הסיום חייבת להיות שונה משעת ההתחלה", "The end time must differ from the start time")))
    base["notifications"] = {"enabled": bool(h("enabled")), "chat_id": chat, "recap_enabled": bool(h("recap_enabled")),
                             "recap_time": rt, "recap_when_empty": bool(h("recap_when_empty")), "immediate_min_score": ims or 5,
                             "quiet_enabled": bool(h("quiet_enabled")), **quiet}
    ui = doc.get("ui") if isinstance(doc.get("ui"), dict) else {}
    ls = ui.get("last_scan")
    base["ui"] = {"language": ui.get("language") if ui.get("language") in ("he", "en") else "he", "last_scan": {"at": int(ls["at"]), "result": str(ls.get("result", ""))[:20], "new": int(ls.get("new", 0) or 0), "ai_failed": int(ls.get("ai_failed", 0) or 0), "searches": int(ls.get("searches", 0) or 0),
                                                                                                                 **{k: int(ls.get(k, 0) or 0) for k in SCAN_COUNTS}, "partial": bool(ls.get("partial")), **_scan_diag(ls)} if isinstance(ls, dict) and isinstance(ls.get("at"), (int, float)) else None,
                  "telegram_tested": bool(ui.get("telegram_tested")), "setup_dismissed": bool(ui.get("setup_dismissed")), "ever_started": bool(ui.get("ever_started")),
                  "facebook_last_ok": int(ui["facebook_last_ok"]) if isinstance(ui.get("facebook_last_ok"), (int, float)) and not isinstance(ui.get("facebook_last_ok"), bool) else 0,
                  "last_seen": int(ui["last_seen"]) if isinstance(ui.get("last_seen"), (int, float)) and not isinstance(ui.get("last_seen"), bool) else 0}
    prods = doc.get("products") if isinstance(doc.get("products"), list) else []
    if len(prods) > MAX_PRODUCTS:
        errs.append(("products", T(f"מקסימום {MAX_PRODUCTS} מוצרים", f"At most {MAX_PRODUCTS} searches")))
    seen_names, seen_ids, out = set(), set(), []
    num = lambda i: int(m.group(1)) if (m := re.fullmatch(r"p(\d+)", str(i))) else 0
    seq = max([int(doc.get("product_seq") or 0) if isinstance(doc.get("product_seq"), (int, float)) else 0] + [num(p.get("id")) for p in prods if isinstance(p, dict)])
    used = False
    nxt = seq + 1
    for i, p in enumerate(prods[:MAX_PRODUCTS]):
        if not isinstance(p, dict):
            errs.append((f"products.{i}", T("מוצר לא תקין", "invalid search")))
            continue
        cp = clean_product(p, errs, f"products.{i}")
        if not re.fullmatch(r"p\d{1,4}", cp["id"]) or cp["id"] in seen_ids:
            if not used:  # first new id of this save: also skip ids that stored listings still refer to (searches deleted before ids were tracked)
                used = True
                try:
                    nxt = max([nxt] + [num(i) + 1 for i in known_ids()])
                except Exception:
                    pass
            cp["id"] = f"p{nxt}"
            nxt += 1
        seen_ids.add(cp["id"])
        key = cp["name"].casefold()
        if key in seen_names:
            errs.append((f"products.{i}.name", T("כבר קיים מוצר בשם הזה", "A search with this name already exists")))
        seen_names.add(key)
        out.append(cp)
    base["products"] = out
    base["product_seq"] = max([seq] + [num(p["id"]) for p in out])
    return base, [{"field": f, "message": m} for f, m in errs]


def _write(path, data):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def initial_defaults():
    """First run only: seed from the container environment so an existing deployment keeps its behaviour (notifications stay as configured)."""
    hour = os.environ.get("RECAP_HOUR", "21")
    return {"notifications": {"enabled": os.environ.get("NOTIFY_ENABLED", "false").lower() in ("1", "true", "yes"),
                              "chat_id": os.environ.get("TG_CHAT_ID", ""), "recap_enabled": not (hour.isdigit() and int(hour) > 23),
                              "recap_time": f"{int(hour):02d}:00" if hour.isdigit() and int(hour) <= 23 else "21:00"}}


_cache = {"t": 0.0, "v": None}


def load():
    with lock:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                doc = json.load(f)
        except (FileNotFoundError, ValueError):
            doc = initial_defaults()
        s, _ = clean(doc)  # tolerate hand-edited files: fall back per field, never crash
        return s


def get():
    """Cached read for hot paths (outbox worker, ingest). Saves invalidate the cache."""
    with lock:
        if _cache["v"] is None or time.time() - _cache["t"] > 2:
            _cache.update(t=time.time(), v=load())
        return _cache["v"]


def save(doc):
    """Validate + persist. Returns (settings, errors). Nothing is written when there are errors."""
    with lock:
        s, errs = clean(doc)
        if errs:
            return s, errs
        os.makedirs(PANEL_DIR, mode=0o700, exist_ok=True)
        _write(SETTINGS_FILE, json.dumps(s, ensure_ascii=False, indent=1))
        _cache["v"] = None
        _lang.update(t=time.time(), v=s["ui"]["language"])
        return s, []


def search_fingerprint(p, fb):
    """Stable hash of everything that defines WHAT a search looks for (terms, price range, currency, place, distance, filters).
    Listings remember it, so listings from an older version of an edited search can be found and cleaned up."""
    import hashlib
    key = [sorted(p.get("search_phrases", [])), sorted(p.get("keywords", [])), sorted(p.get("antikeywords", [])), p.get("min_price"), p.get("max_price"), p.get("currency", ""),
           p.get("search_city") or fb.get("search_city"), p.get("radius") or fb.get("radius"), p.get("condition", []), p.get("date_listed", 0), p.get("delivery_method", "all")]
    return hashlib.sha1(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
