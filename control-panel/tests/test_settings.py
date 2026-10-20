"""Settings store + config generator. If the real AIMM package is importable (AIMM_LIB=<dir>) every generated config is
also loaded with AIMM's own Config class, the authoritative validator. Run: AIMM_LIB=... python3 tests/test_settings.py"""
import json, os, random, sys, tempfile, time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
sys.path.insert(0, ROOT + "/forwarder")
if os.environ.get("AIMM_LIB"):
    sys.path.insert(0, os.environ["AIMM_LIB"])
import settings, cfggen
try:
    from ai_marketplace_monitor.config import Config
except Exception:
    Config = None
ok = fail = 0


def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)


def aimm_loads(text):
    p = Path(tmp) / "c.toml"; p.write_text(text, encoding="utf-8")
    try:
        Config([p]); return True, ""
    except Exception as e:
        return False, str(e)[:200]


GOOD = {"facebook": {"search_city": "telaviv"}, "products": [{"name": "כרטיס מסך", "search_phrases": ["rtx 3070", "כרטיס מסך"], "max_price": 1500, "min_price": 100,
        "antikeywords": ["broken"], "description": "כרטיס עובד", "condition": ["used_good"], "date_listed": 7, "great_deal_price": 900}]}
s, e = settings.clean(GOOD)
check("valid settings clean without errors", not e and s["products"][0]["id"] == "p1")
text = cfggen.build(s, "tok" * 8)
check("generated config has no secrets besides the guard token and is deterministic", cfggen.build(s, "tok" * 8) == text and "TG_" not in text)
if Config:
    r, msg = aimm_loads(text); check(f"AIMM's own loader accepts the generated config ({msg})", r)
else:
    print("SKIP AIMM loader (set AIMM_LIB)")

# validation messages
bad = {"facebook": {"search_city": "tel aviv!", "radius": 0, "search_interval_min": 5, "currency": "x"},
       "notifications": {"chat_id": "abc", "recap_time": "25:99"},
       "products": [{"name": "", "search_phrases": [], "min_price": 500, "max_price": 100, "radius": 9999, "condition": ["broken"], "date_listed": 3,
                     "delivery_method": "drone"}, {"name": "ok", "search_phrases": ["abc"]}]}
s, e = settings.clean(bad)
fields = {x["field"] for x in e}
for f in ["facebook.search_city", "facebook.radius", "facebook.search_interval_min", "facebook.currency", "notifications.chat_id", "notifications.recap_time",
          "products.0.name", "products.0.search_phrases", "products.0.max_price", "products.0.radius", "products.0.condition", "products.0.date_listed", "products.0.delivery_method"]:
    check(f"error reported for {f}", f in fields)
check("default language is Hebrew: all validation messages are Hebrew", settings.lang() == "he" and all(any("\u0590" <= c <= "\u05ff" for c in x["message"]) for x in e))
d, e = settings.clean({"products": [{"name": "A", "search_phrases": ["aa"]}, {"name": "a", "search_phrases": ["bb"]}]})
check("duplicate product names rejected (case-insensitive)", any(x["field"] == "products.1.name" for x in e))
check("save refuses invalid, writes nothing", settings.save(bad)[1] and not os.path.exists(settings.SETTINGS_FILE))
# distance filter: a per-search distance needs no per-search city; the generator writes the general city next to it so AIMM accepts it
dist = settings.clean({"facebook": {"search_city": "haifa"}, "products": [{"name": "a", "search_phrases": ["aa"], "radius": 20}]})
check("distance without a per-search city is valid", not dist[1] and dist[0]["products"][0]["radius"] == 20)
dtext = cfggen.build(dist[0], "g" * 20)
check("generated item carries radius AND the general city (AIMM rule)", 'radius = 20' in dtext.split("[item.p1]")[1] and 'search_city = "haifa"' in dtext.split("[item.p1]")[1])
if Config:
    r, msg = aimm_loads(dtext); check(f"AIMM's own loader accepts a distance-only search ({msg})", r)
check("distance outside 1-500 km is rejected", settings.clean({"products": [{"name": "a", "search_phrases": ["aa"], "radius": 0}]})[1] and settings.clean({"products": [{"name": "a", "search_phrases": ["aa"], "radius": 501}]})[1])
# currency: only currencies AIMM recognises; sent to Facebook as plain numbers (never converted)
check("empty currency means 'any currency'", settings.clean({"products": [{"name": "a", "search_phrases": ["aa"]}]})[0]["products"][0]["currency"] == "")
check("an unsupported currency is rejected", settings.clean({"products": [{"name": "a", "search_phrases": ["aa"], "currency": "ZZZ"}]})[1][0]["field"] == "products.0.currency")
if Config:
    from ai_marketplace_monitor.utils import Currency
    check("every currency offered in the form is one AIMM recognises", all(c in {x.value for x in Currency} for c in settings.CURRENCIES))
ctext = cfggen.build(settings.clean({"facebook": {"search_city": "haifa"}, "products": [{"name": "a", "search_phrases": ["aa"], "min_price": 10, "max_price": 99, "currency": "USD"}]})[0], "g" * 20)
check("prices are written as plain numbers: no hidden currency conversion by AIMM", "max_price = 99\n" in ctext and "USD" not in ctext.split("[item.p1]")[1])
# seller filters are gone; old settings files that still contain them keep working
old = settings.clean({"facebook": {"search_city": "haifa", "seller_locations": ["x"], "exclude_sellers": ["y"]}, "products": [{"name": "a", "search_phrases": ["aa"], "seller_locations": ["x"], "exclude_sellers": ["y"]}]})
check("old settings containing seller filters still load; the keys are dropped", not old[1] and "seller_locations" not in old[0]["products"][0] and "exclude_sellers" not in old[0]["facebook"])
check("generated config never contains seller filters", "seller" not in cfggen.build(old[0], "g" * 20))
# search fingerprint: changes when the search changes, not when only the name or enabled flag changes
p0 = settings.clean({"facebook": {"search_city": "haifa"}, "products": [{"name": "a", "search_phrases": ["cans"], "max_price": 5}]})[0]
fp0 = settings.search_fingerprint(p0["products"][0], p0["facebook"])
p1 = json.loads(json.dumps(p0)); p1["products"][0]["search_phrases"] = ["jacuzzi"]
p2 = json.loads(json.dumps(p0)); p2["products"][0]["enabled"] = False; p2["products"][0]["name"] = "renamed"
check("fingerprint changes when the search terms change", settings.search_fingerprint(p1["products"][0], p1["facebook"]) != fp0)
check("fingerprint ignores name/enabled", settings.search_fingerprint(p2["products"][0], p2["facebook"]) == fp0)
check("scraper defaults to stopped", settings.load()["scraper"]["desired"] == "stopped")
settings.save({"ui": {"language": "en"}})
check("after choosing English the same validation messages are English", settings.lang() == "en" and all(x["message"].isascii() or "\u20aa" in x["message"] for x in settings.clean(bad)[1]))
check("an unsupported language falls back to Hebrew", settings.clean({"ui": {"language": "fr"}})[0]["ui"]["language"] == "he")

# persistence
s, e = settings.save(GOOD); check("save ok", not e and os.path.exists(settings.SETTINGS_FILE))
check("settings file is mode 600", oct(os.stat(settings.SETTINGS_FILE).st_mode & 0o777) == "0o600")
g2 = json.loads(json.dumps(GOOD)); g2["products"][0]["max_price"] = 1200; g2["scraper"] = {"desired": "running"}
settings.save(g2)
check("reload returns persisted value", settings.load()["products"][0]["max_price"] == 1200 and settings.load()["scraper"]["desired"] == "running")
open(settings.SETTINGS_FILE, "w").write("{broken json")
sc, _ = settings.clean({"scraper": {"desired": "scan", "scan_started": 100, "scan_offset": 55}})
check("manual-scan state is valid and persists its fields", sc["scraper"] == {"desired": "scan", "pause_until": None, "scan_started": 100, "scan_offset": 55, "scan_inode": 0, "scan_file_offset": 0})
check("garbage scraper state falls back to stopped", settings.clean({"scraper": {"desired": "explode", "scan_offset": -5}})[0]["scraper"]["desired"] == "stopped")
check("no backup feature remains in the settings module", not any(hasattr(settings, n) for n in ("backup", "list_backups", "restore", "read_backup", "BACKUP_DIR")))
check("corrupt settings file falls back to defaults, no crash", settings.load()["products"] == [])

# hostile text and a random fuzz through AIMM's loader
nasty = ['quote " and \\ backslash', "line1\nline2", "'''triple'''", "\"\"\"x\"\"\"", "# not a comment", "[item.evil]", "טקסט בעברית ‏RTL", "emoji 🙂", "tab\there"]
rnd = random.Random(7); n_ok = n_total = 0
for i in range(60):
    prods = []
    for j in range(rnd.randint(1, 4)):
        prods.append({"name": f"מוצר {i}-{j}", "enabled": rnd.random() > .3, "search_phrases": [rnd.choice(nasty)[:50] + "xx" for _ in range(rnd.randint(1, 3))],
                      "keywords": rnd.sample(nasty, rnd.randint(0, 2)), "antikeywords": rnd.sample(nasty, rnd.randint(0, 2)), "description": "\n".join(rnd.sample(nasty, 3)),
                      "min_price": rnd.choice([None, 0, 50]), "max_price": rnd.choice([None, 100, 5000]), **rnd.choice([{}, {"search_city": "haifa", "radius": 20}]), "rating": rnd.choice([None, 3, 5]),
                      "search_interval_min": rnd.choice([None, 30, 120]), "condition": rnd.sample(settings.CONDITIONS, rnd.randint(0, 3)), "date_listed": rnd.choice(settings.DATE_LISTED),
                      "delivery_method": rnd.choice(settings.DELIVERY), "currency": rnd.choice(["", "ILS", "USD"])})
    s, e = settings.clean({"facebook": {"search_city": "haifa"}, "products": prods})
    if e: continue
    n_total += 1
    t = cfggen.build(s, "g" * 20, feedback_for=lambda k: "--- דוגמאות ---\n- bad item 'x': \"y\"\n--- סוף ---")
    if Config:
        r, msg = aimm_loads(t)
        if r: n_ok += 1
        else: print("   rejected:", msg)
    else: n_ok += 1
check(f"{n_total} fuzzed valid settings -> {n_ok} accepted by {'AIMM Config' if Config else 'generator (AIMM missing)'}", n_total >= 30 and n_ok == n_total)
check("no injected sections: hostile '[item.evil]' text stays inside a string", "[item.evil]\n" not in cfggen.build(settings.clean({"products": [{"name": "x", "search_phrases": ["[item.evil]"], "description": "[item.evil]\n[item.evil2]"}]})[0], "g" * 20).replace('"[item.evil]', "").replace("[item.evil]\\n", ""))
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
