"""Drives the redesigned Hebrew UI in headless Chromium against the REAL forwarder + REAL agent (fake supervisor/helper, mock Telegram).
Journeys: first-time setup, add a search, notifications, start/pause/stop, listings (filter, save, block+undo), Facebook problem states, settings.
Also: RTL, keyboard, form labels, colour contrast (light+dark), layout shift, toast overlap, mobile layout. Screenshots -> $SHOTS.
Run: LD_LIBRARY_PATH=<libs> AIMM_LIB=<dir with playwright+aimm> python3 tests/test_ui_browser.py"""
import json, os, shutil, signal, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
if not os.environ.get("AIMM_LIB"):
    print("SKIP: AIMM_LIB not set"); sys.exit(0)
sys.path.insert(0, os.environ["AIMM_LIB"])
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AG, ING, UI, TGP = 18760, 18761, 18762, 18763
SHOTS = os.environ.get("SHOTS", tempfile.mkdtemp())
tmp = tempfile.mkdtemp(); ok = fail = 0
for d in ("sup", "session", "data", "aimm"): os.makedirs(f"{tmp}/{d}")
SENT, TGMODE = [], {"updates": "one"}


class TG(BaseHTTPRequestHandler):
    def do_GET(self):
        res = [{"message": {"chat": {"id": 123456789, "type": "private", "first_name": "Alex"}}}] if "getUpdates" in self.path else []
        b = json.dumps({"ok": True, "result": res}).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        SENT.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])))["text"])
        b = b'{"ok":true}'; self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass


threading.Thread(target=HTTPServer(("127.0.0.1", TGP), TG).serve_forever, daemon=True).start()


def until(fn, t=25):
    end = time.time() + t
    while time.time() < end:
        try:
            if fn(): return True
        except Exception: pass
        time.sleep(0.25)
    return False


def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)


base = {**os.environ}
agent = subprocess.Popen([sys.executable, "-u", ROOT + "/scraper-extras/agent.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**base, "AGENT_TOKEN": "a" * 24, "AGENT_PORT": str(AG),
    "AIMM_SESSION_DIR": tmp + "/session", "SUPERVISORCTL": ROOT + "/tests/fakes/supervisorctl", "FAKE_SUP_DIR": tmp + "/sup", "AGENT_PYTHON": sys.executable, "HELPER_PY": ROOT + "/tests/fakes/helper.py", "PYTHONPATH": os.environ["AIMM_LIB"], "FAKE_LOGGED_IN": "1", "FAKE_READY_DELAY": "0.3"})
fwd = subprocess.Popen([sys.executable, "-u", ROOT + "/forwarder/forwarder.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**base, "TG_API_BASE": f"http://127.0.0.1:{TGP}", "TG_BOT_TOKEN": "123456:SECRET-TELEGRAM-TOKEN-abcdefghij",
    "TG_CHAT_ID": "", "UI_USER": "admin", "UI_PASS": "correct-horse-battery", "DATA_DIR": tmp + "/data", "PORT": str(ING), "UI_PORT": str(UI), "MP_LOG": tmp + "/aimm/log", "MP_CONFIG": tmp + "/aimm/config.toml",
    "MP_CACHE": tmp + "/aimm/cache.db", "AGENT_URL": f"http://127.0.0.1:{AG}", "AGENT_TOKEN": "a" * 24, "GUARD_TOKEN": "g" * 24, "RECONCILE_S": "2", "SEND_GAP_S": "0.1", "TZ": "Asia/Jerusalem", "RECAP_HOUR": "25", "NOTIFY_ENABLED": "false",
    "SCRAPER_HOST": "127.0.0.1"})
until(lambda: urllib.request.urlopen(f"http://127.0.0.1:{ING}/healthz", timeout=1).status in (200, 503), 20)


def seed():
    for i, (score, price, loc, ai, extra) in enumerate([(5, 900, "חיפה", "כרטיס מצוין במחיר טוב. עובד ומגיע עם קופסה מקורית ואחריות של שנה מלאה. המוכר נראה אמין, התמונות ברורות והתיאור מפורט, והמחיר נמוך משמעותית מהשוק, ולכן כדאי לתפוס מהר לפני שייעלם", ""), (4, 1400, "תל אביב", "התאמה טובה", " old"), (5, 300, "", "Not evaluated by AI", "")]):
        body = f"[Gpu ({score})] RTX 307{i} כרטיס מסך\n₪{price}, {loc}\nhttps://facebook.com/marketplace/item/80{i}/\nAI: {ai}"
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/marketplace", data=body.encode(), headers={"Title": "Found 1 new p1 from facebook"}), timeout=5).read()


def contrast(fg, bg):
    def lum(c):
        if len(c) == 4: c = "#" + "".join(ch * 2 for ch in c[1:])
        r = [int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in r]
        return 0.2126 * r[0] + 0.7152 * r[1] + 0.0722 * r[2]
    a, b = sorted([lum(fg), lum(bg)], reverse=True); return (a + 0.05) / (b + 0.05)


errors, csp = [], []
try:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        U = f"http://127.0.0.1:{UI}"
        def newctx(w=1280, h=900, scheme="light"):
            c = b.new_context(http_credentials={"username": "admin", "password": "correct-horse-battery"}, viewport={"width": w, "height": h}, locale="en-GB", color_scheme=scheme)
            c.add_init_script("window.__cls=0;new PerformanceObserver(l=>{for(const e of l.getEntries()) if(!e.hadRecentInput) window.__cls+=e.value}).observe({type:'layout-shift',buffered:true})")
            pg = c.new_page()
            pg.on("console", lambda m: (errors if m.type == "error" and "status of 4" not in m.text else []).append(m.text) if "Content Security Policy" not in m.text else csp.append(m.text))
            pg.on("pageerror", lambda e: errors.append(str(e)))
            return pg
        pg = newctx()
        pg.goto(U + "/"); pg.wait_for_selector("h1")
        # ---------- language: Hebrew by default, both manual buttons visible, English on request
        check("DEFAULT: Hebrew, right-to-left", pg.evaluate("document.documentElement.lang") == "he" and pg.evaluate("document.documentElement.dir") == "rtl")
        check("DEFAULT: navigation is Hebrew", pg.locator("#tabs a").all_inner_texts() == ["בית", "חיפושים", "מודעות", "פייסבוק", "התראות", "הגדרות"])
        check("REGRESSION: navigation icons are outline icons (fill=none) in Hebrew, not black blobs", pg.evaluate("[...document.querySelectorAll('#tabs svg')].every(s => s.getAttribute('fill') === 'none')"))
        check("HOME: the 'סריקה ידנית' button is VISIBLE even on a fresh install (disabled, with the reason shown)", pg.locator("#scan-btn").is_visible() and pg.locator("#scan-btn").inner_text().strip() == "סריקה ידנית" and pg.locator("#scan-btn").is_disabled() and pg.locator("#scan-hint").is_visible())
        pg.screenshot(path=f"{SHOTS}/00-hebrew-home.png", full_page=True)
        pg.goto(U + "/#/notifications"); pg.reload(); pg.wait_for_selector("h1")
        check("NOTIFICATIONS: the 'שליחת סיכום יומי עכשיו' button is VISIBLE", pg.locator("button:has-text('שליחת סיכום יומי עכשיו')").is_visible())
        pg.screenshot(path=f"{SHOTS}/00-hebrew-notifications.png", full_page=True)
        pg.goto(U + "/#/settings"); pg.reload(); pg.wait_for_selector("#uilang")
        check("SETTINGS: a language selector exists, Hebrew selected", pg.locator("#uilang").input_value() == "he" and pg.locator("#uilang option").all_inner_texts() == ["עברית", "English"])
        pg.select_option("#uilang", "en"); pg.wait_for_function if False else None
        pg.wait_for_selector("html[lang=en]", state="attached", timeout=15000)
        check("SWITCH: choosing English reloads the app in English, left-to-right", pg.evaluate("document.documentElement.dir") == "ltr" and "Settings" in pg.locator("#tabs").inner_text() and "Home" in pg.locator("#tabs").inner_text())
        pg.goto(U + "/"); pg.wait_for_selector("h1")
        check("document is English LTR, one h1, main landmark", pg.evaluate("document.documentElement.dir") == "ltr" and pg.evaluate("document.documentElement.lang") == "en" and pg.locator("h1").count() == 1 and pg.locator("main").count() == 1)
        check("six task-based destinations in the nav", pg.locator("#tabs a").count() == 6 and pg.locator("#tabs a").all_inner_texts() == ["Home", "Searches", "Listings", "Facebook", "Notifications", "Settings"])
        # ---------- J1 first-time setup
        check("J1 fresh install: Home says to finish setup, shows the 4-step checklist", "Let's finish the setup" in pg.inner_text("main") and pg.locator(".step").count() == 4)
        check("J1 one obvious primary action (go to the first open step)", pg.locator("button.primary.big").count() == 1 and "Connect Facebook" in pg.locator("button.primary.big").inner_text())
        pg.screenshot(path=f"{SHOTS}/01-home-setup.png", full_page=True)
        pg.click("button.primary.big"); pg.wait_for_selector("text=Not connected to Facebook yet")
        check("J1 Facebook page: plain-language status + one clear button, no jargon", pg.locator("button.primary.big:has-text('Connect to Facebook')").count() == 1 and "cookies" not in pg.locator(".hero").inner_text().lower())
        pg.click("button.primary.big:has-text('Connect to Facebook')"); pg.wait_for_selector("text=Log in in three steps")
        pg.wait_for_selector("a:has-text('Open the window')")
        check("J1 guided 3-step login: window link opens in a new tab (authenticated), no iframe", pg.locator("a[href='/browser/'][target=_blank]").count() == 1 and pg.locator("iframe").count() == 0)
        pg.screenshot(path=f"{SHOTS}/02-facebook-flow.png", full_page=True)
        pg.click("button:has-text('I\u2019ve finished logging in')"); pg.wait_for_selector("text=The login was saved")
        open(tmp + "/session/status.json", "w").write(json.dumps({"state": "logged_in", "at": int(time.time()), "lang": "he", "source": "login-helper"}))  # what the real helper records
        until(lambda: json.load(urllib.request.urlopen(urllib.request.Request(U + "/api/facebook", headers={"Authorization": "Basic " + __import__("base64").b64encode(b"admin:correct-horse-battery").decode()}), timeout=5))["last_ok"] > 0, 15)
        pg.reload(); pg.wait_for_selector("text=Connected to Facebook")
        check("J1 after saving: 'connected', language detected (Hebrew), last good login shown", "Connected to Facebook" in pg.inner_text(".hero") and "Hebrew" in pg.inner_text("main") and "Not connected successfully yet" not in pg.inner_text("main"))
        pg.screenshot(path=f"{SHOTS}/03-facebook-connected.png", full_page=True)
        # ---------- J2 add a search
        pg.click("#tabs a:has-text('Searches')"); pg.wait_for_selector("text=No searches yet")
        check("J2 empty state explains what a search is, with one action", "something you want to find" in pg.inner_text("main") and pg.locator(".empty button.primary").count() == 1)
        check("J2 no location yet: location card asks for it", "No location chosen yet" in pg.inner_text("main"))
        pg.click("button:has-text('Choose location')"); pg.wait_for_selector("dialog[open]")
        pg.fill("#city", "https://www.facebook.com/marketplace/haifa/?ref=bookmark"); pg.wait_for_selector("text=City recognised: haifa")
        pg.fill("#city", "https://www.facebook.com/marketplace/search?query=x"); pg.wait_for_selector("dialog .err")
        check("J2 a link that is not a city is explained inline", "We could not recognise a city" in pg.inner_text("dialog"))
        pg.fill("#city", "https://www.facebook.com/marketplace/haifa/"); pg.click("dialog button.primary"); pg.wait_for_selector("text=up to 30 km", timeout=20000)
        check("J2 location saved (city + radius shown in plain Hebrew)", "haifa" in pg.inner_text("main") and "km" in pg.inner_text("main"))
        pg.click("button.primary:has-text('New search')"); pg.wait_for_selector("dialog[open] #pn")
        check("J2 form asks 4 short questions; advanced options are folded", pg.locator("dialog details.more").count() == 1 and pg.locator("dialog details.more[open]").count() == 0 and not pg.locator("#pk").is_visible())
        pg.click("dialog button.primary"); pg.wait_for_selector("dialog .err")
        check("J2 inline validation: name required (Hebrew, next to the field)", "Enter what you are looking for" in pg.inner_text("dialog"))
        pg.fill("#pn", "כרטיס מסך"); pg.fill("#pmin", "900"); pg.fill("#pmax", "100"); pg.wait_for_selector("text=The maximum price is lower than the minimum")
        pg.fill("#pmax", "1500")
        check("J2 live summary sentence reflects the inputs", "כרטיס מסך" in pg.inner_text("dialog .notice.info") and "haifa" in pg.inner_text("dialog .notice.info") and "1500" in pg.inner_text("dialog .notice.info"))
        pg.screenshot(path=f"{SHOTS}/04-search-dialog.png")
        pg.click("dialog button.primary"); pg.wait_for_selector(".search .name:has-text('כרטיס מסך')", timeout=30000)
        cfg = open(tmp + "/aimm/config.toml", encoding="utf-8").read()
        check("J2 saved: appears in the list, config generated and validated by AIMM's loader", "[item.p1]" in cfg and 'search_city = "haifa"' in cfg and "max_price = 1500" in cfg and pg.locator(".search .chip.ok:has-text('Active')").count() == 1)
        # edit an existing search: rename, new price range, distance, currency. The OLD phrase must not survive (the bug that made the rerun do nothing)
        pg.click(".search button:has-text('Edit')"); pg.wait_for_selector("dialog[open] #pn")
        check("EDIT: the extra-phrases box does not contain the main phrase (it was the source of the stale search)", pg.input_value("#pp") == "")
        pg.fill("#pn", "jacuzzi"); pg.fill("#pmin", "1000"); pg.fill("#pmax", "4000"); pg.select_option("#pr", "20"); pg.select_option("#pcur", "ILS")
        check("EDIT: summary mentions distance and currency", "20" in pg.inner_text("dialog .notice.info") and "jacuzzi" in pg.inner_text("dialog .notice.info"))
        check("EDIT: seller filters are gone from the form", pg.locator("#psl, #pex").count() == 0)
        pg.click("dialog button.primary"); pg.wait_for_selector(".search .name:has-text('jacuzzi')", timeout=30000)
        pg.wait_for_selector("text=The next scan will use it")
        cfg = open(tmp + "/aimm/config.toml", encoding="utf-8").read().split("[item.p1]")[1].split("\n[")[0]
        check("EDIT: saved in the UI -> next scan config has the new phrase, range and distance; the old phrase is gone", 'search_phrases = ["jacuzzi"]' in cfg and "min_price = 1000" in cfg and "max_price = 4000" in cfg and "radius = 20" in cfg and "כרטיס" not in cfg)
        pg.click(".search button:has-text('Edit')"); pg.wait_for_selector("dialog[open] #pn")
        check("EDIT: reopening shows the saved values (name, distance, currency)", pg.input_value("#pn") == "jacuzzi" and pg.input_value("#pr") == "20" and pg.input_value("#pcur") == "ILS")
        pg.click("dialog button.quiet")
        # the production scenario: a search with extra phrases ("cans", "drink", "zero") is renamed to something else
        def cfg_p1(): return open(tmp + "/aimm/config.toml", encoding="utf-8").read().split("[item.p1]")[1].split("\n[")[0]
        pg.click(".search button:has-text('Edit')"); pg.wait_for_selector("dialog[open] #pn")
        pg.fill("#pn", "פחית"); pg.locator("dialog details.more summary").click(); pg.fill("#pp", "שתייה\nזירו"); pg.click("dialog button.primary"); pg.wait_for_selector(".search .name:has-text('פחית')", timeout=30000)
        check("PRODUCTION CASE: three phrases saved (cans + extras)", all(x in cfg_p1() for x in ("פחית", "שתייה", "זירו")))
        pg.click(".search button:has-text('Edit')"); pg.wait_for_selector("dialog[open] #pn"); pg.fill("#pn", "ג'קוזי")
        check("PRODUCTION CASE: renaming warns, in the always-visible summary, that the old extra phrases are still searched", "שתייה" in pg.inner_text("dialog .notice.info") and "also" in pg.inner_text("dialog .notice.info"))
        pg.locator("dialog details.more summary").click(); pg.fill("#pp", ""); pg.click("dialog button.primary"); pg.wait_for_selector(".search .name:has-text(\"ג'קוזי\")", timeout=30000)
        check("PRODUCTION CASE: after clearing the extras the next scan config searches ONLY the new name", 'search_phrases = ["ג\'קוזי"]' in cfg_p1() or "search_phrases = [\"ג'קוזי\"]" in cfg_p1())
        check("PRODUCTION CASE: none of the old phrases remain in the config", not any(x in cfg_p1() for x in ("פחית", "שתייה", "זירו")))
        pg.click("button:has-text('Duplicate')"); pg.wait_for_selector(".search.off")
        check("J2 duplicate is created paused", pg.locator(".search.off").count() == 1)
        pg.locator(".search.off button:has-text('Delete')").click(); pg.wait_for_selector("dialog[open]"); pg.click("dialog button.danger")
        check("J2 delete asks for confirmation first", until(lambda: pg.locator(".search").count() == 1))
        pg.screenshot(path=f"{SHOTS}/05-searches.png", full_page=True)
        # ---------- J3 notifications
        pg.click("#tabs a:has-text('Notifications')"); pg.wait_for_selector("text=When to alert immediately")
        pg.click("button:has-text('Detect automatically')"); pg.wait_for_selector("text=Detected:")
        check("J3 chat id found automatically (user never types a number)", pg.input_value("#chat") == "123456789")
        pg.click("button:has-text('Send a test message')"); pg.wait_for_selector("text=A test message was sent")
        check("J3 test message reached Telegram", any("Test message" in t for t in SENT))
        pg.click("label.switch[for=nen]"); pg.click("label.switch[for=qen]"); pg.fill("#qf", "22:00"); pg.fill("#qt", "07:00")
        pg.click("input#imm4"); pg.click("button.primary.big:has-text('Save')"); pg.wait_for_selector("text=Settings saved")
        n = json.load(open(tmp + "/data/panel/settings.json"))["notifications"]
        check("J3 preferences persisted (enabled, chat, threshold 4, quiet hours)", n["enabled"] and n["chat_id"] == "123456789" and n["immediate_min_score"] == 4 and n["quiet_enabled"] and n["quiet_from"] == "22:00")
        pg.click("button:has-text('Send today\u2019s summary now')"); pg.wait_for_selector("text=Nothing to report")
        time.sleep(2)
        check("RECAP: a day with no scans and no listings sends NO recap (nothing 'monitor silent', no 'No relevant listings today')", not any("Daily recap" in x or "Monitor silent" in x or "rated today" in x or "No relevant listings today" in x for x in SENT))
        pg.screenshot(path=f"{SHOTS}/06-notifications.png", full_page=True)
        # ---------- J4 start / pause / stop
        pg.click("#tabs a:has-text('Home')"); pg.wait_for_selector("text=Monitoring is off")
        check("J4 Home: state unmistakable ('Monitoring is off'), checklist complete, primary = start", pg.locator("button.primary.big:has-text('Start monitoring')").count() == 1)
        check("J4 the manual scan button sits next to 'Start monitoring' on Home and is enabled when everything is ready", pg.locator("#scan-btn").is_visible() and pg.locator("#scan-btn").is_enabled())
        pg.click("#scan-btn"); pg.wait_for_selector("dialog[open]")
        check("J4 scan dialog explains: once, then stops by itself, continuous monitoring stays off; nothing runs before confirming", "stop by itself" in pg.inner_text("dialog") and "Continuous monitoring stays off" in pg.inner_text("dialog") and not os.path.exists(tmp + "/sup/aimm"))
        pg.click("dialog button.primary"); pg.wait_for_selector("text=Scanning now", timeout=20000)
        check("J4 manual scan runs the scraper while 'Monitoring' is NOT switched on (state 'Scanning now', cancel offered)", until(lambda: os.path.exists(tmp + "/sup/aimm")) and pg.locator("button.primary.big:has-text('Cancel scan')").count() == 1)
        pg.screenshot(path=f"{SHOTS}/06b-scanning.png", full_page=True)
        pg.click("button.primary.big:has-text('Cancel scan')"); pg.wait_for_selector("dialog[open]"); check("J4 cancelling asks for confirmation with scan-specific wording", "Cancel the scan?" in pg.inner_text("dialog"))
        pg.click("dialog button.primary"); pg.wait_for_selector("text=Monitoring is off", timeout=20000)
        check("J4 cancelling the scan stops the scraper", until(lambda: not os.path.exists(tmp + "/sup/aimm")))
        pg.click("button.primary.big:has-text('Start monitoring')"); pg.wait_for_selector("dialog[open]")
        check("J4 dialog explains the effect and risk; nothing runs before confirming", "The scraper will log in to Facebook" in pg.inner_text("dialog") and "may restrict" in pg.inner_text("dialog") and not os.path.exists(tmp + "/sup/aimm"))
        pg.click("dialog button.primary"); pg.wait_for_selector("text=Monitoring is on", timeout=20000)
        check("J4 confirmed -> active, supervisor running", until(lambda: os.path.exists(tmp + "/sup/aimm")))
        pg.screenshot(path=f"{SHOTS}/07-home-active.png", full_page=True)
        pg.click("button.primary.big:has-text('Pause')"); pg.wait_for_selector("dialog[open]"); pg.click("dialog button.primary"); pg.wait_for_selector("text=Monitoring is paused", timeout=20000)
        check("J4 pause -> 'paused' with automatic resume time", "It resumes automatically" in pg.inner_text(".hero"))
        pg.click("button.primary.big:has-text('Resume now')"); pg.wait_for_selector("dialog[open]"); pg.click("dialog button.primary"); pg.wait_for_selector("text=Monitoring is on", timeout=20000)
        pg.click("button:has-text('Stop')"); pg.wait_for_selector("dialog[open]"); pg.click("dialog button.primary"); pg.wait_for_selector("text=Monitoring is off", timeout=20000)
        check("J4 stop is predictable: off, process stopped", until(lambda: not os.path.exists(tmp + "/sup/aimm")))
        # ---------- J5 listings
        seed()
        pg.click("#tabs a:has-text('Listings')"); pg.wait_for_selector("#lq"); pg.wait_for_selector("article.lcard")
        check("J5 one feed (no table/compact/theme switcher), three cards, product name instead of internal id", pg.locator("article.lcard").count() == 3 and pg.locator("table").count() == 0 and "p1" not in pg.inner_text("main").replace("Great", ""))
        check("J5 AI verdict explained in words; an AI FAILURE is a distinct badge and never looks like a low rating", pg.locator(".chip.unrated:has-text('Not rated')").count() == 1 and pg.locator(".chip.great").count() == 1 and "temporary problem" in pg.inner_text("main"))
        check("J5 long AI reason is collapsed with 'read more'", pg.locator("button:has-text('Read more')").count() == 1)
        pg.click("button:has-text('Read more')"); check("J5 read more expands the full reason", "לפני שייעלם" in pg.inner_text("main"))
        pg.screenshot(path=f"{SHOTS}/08-listings.png", full_page=True)
        pg.click(".seg button:has-text('Great')"); check("J5 quick filter 'great' shows only 5-star finds", pg.locator("article.lcard").count() == 1)
        pg.click(".seg button:has-text('All')"); pg.fill("#lq", "תל אביב"); check("J5 search box filters by location text", until(lambda: pg.locator("article.lcard").count() == 1)); pg.fill("#lq", "")
        pg.locator("article.lcard button:has-text('Save')").first.click(); pg.wait_for_selector("text=Saved")
        pg.click(".seg button:has-text('Saved')"); check("J5 save works and 'saved' filter shows it", until(lambda: pg.locator("article.lcard").count() == 1)); pg.click(".seg button:has-text('All')")
        pg.locator("article.lcard button:has-text('Not relevant')").first.click(); pg.wait_for_selector("dialog[open]")
        check("J5 'not relevant' asks WHY with plain Hebrew reasons (feeds the AI)", "Wrong item or spec" in pg.inner_text("dialog") and "Too expensive" in pg.inner_text("dialog"))
        pg.click("dialog button:has-text('Too expensive')"); pg.wait_for_selector("text=Listing hidden")
        check("J5 listing hidden immediately", until(lambda: pg.locator("article.lcard").count() == 2))
        pg.click(".toast button:has-text('Undo')"); check("J5 UNDO brings it back", until(lambda: pg.locator("article.lcard").count() == 3))
        pg.click("button:has-text('Select several')"); pg.locator("article.lcard input[type=checkbox]").first.check(); check("J5 bulk mode shows an action bar", pg.locator(".bar").count() == 1); pg.click("button:has-text('Done selecting')")
        # price-change indicator, new-listing label, delete one listing (with confirmation)
        def ing(body): urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/marketplace", data=body.encode(), headers={"Title": "Found 1 new p1 from facebook"}), timeout=5).read()
        time.sleep(1.2)  # "new" means found after the last visit, measured in whole seconds
        ing("[Gpu (5)] RTX 3070 כרטיס מסך\n₪700, חיפה\nhttps://facebook.com/marketplace/item/800/\nAI: great"); ing("[Gpu (4)] Extra card\n₪300, חיפה\nhttps://facebook.com/marketplace/item/899/\nAI: ok")
        pg.reload(); pg.wait_for_selector("article.lcard")
        check("INDICATORS: price drop shows 'Price dropped' with the previous price next to the current one", pg.locator(".chip:has-text('Price dropped')").count() == 1 and "Previous price: ₪900" in pg.inner_text("main"))
        check("INDICATORS: new listings carry the 'New listing' label", pg.locator(".chip.new:has-text('New listing')").count() >= 1)
        check("INDICATORS: unchanged prices show no change chip", pg.locator(".chip:has-text('Price went up')").count() == 0 and pg.locator(".chip:has-text('Price dropped')").count() == 1)
        n0 = pg.locator("article.lcard").count()
        pg.locator("article.lcard:has-text('Extra card') button:has-text('Delete')").click(); pg.wait_for_selector("dialog[open]")
        check("DELETE: asks for confirmation first and nothing is removed yet", "cannot be undone" in pg.inner_text("dialog") and pg.locator("article.lcard").count() == n0)
        pg.click("dialog button.danger"); check("DELETE: the listing is gone after confirming", until(lambda: pg.locator("article.lcard").count() == n0 - 1))
        pg.click("button:has-text('Clean up')"); pg.wait_for_selector("dialog[open] #cu-days")
        check("CLEANUP: dialog says nothing is deleted automatically and shows counts", "Nothing is deleted automatically" in pg.inner_text("dialog") and pg.locator("#cu-n-orphans").count() == 1)
        pg.click("dialog button:has-text('Close')")
        # ---------- scan activity card (the example from the brief: 19 reviewed, 66 AI evaluations, 66 rejected, 0 matching, 0 saved)
        hist_rows = [{"at": int(time.time()) - 7200, "kind": "monitor", "started": int(time.time()) - 7500, "date": time.strftime("%Y-%m-%d"), "result": "done", "new": 0, "ai_failed": 0, "searches": 3, "reviewed": 12, "ai_rated": 30, "below": 29, "passed": 1, "forwarded": 1, "saved": 1, "ingest_errors": 0, "partial": False},
                     {"at": int(time.time()) - 3600, "kind": "manual", "started": int(time.time()) - 4000, "date": time.strftime("%Y-%m-%d"), "result": "timeout", "new": 0, "ai_failed": 0, "searches": 2, "reviewed": 8, "ai_rated": 8, "below": 8, "passed": 0, "forwarded": 0, "saved": 0, "ingest_errors": 0, "partial": False},
                     {"at": int(time.time()) - 60, "kind": "manual", "started": int(time.time()) - 900, "date": time.strftime("%Y-%m-%d"), "result": "done", "new": 0, "ai_failed": 0, "searches": 3, "reviewed": 19, "ai_rated": 66, "below": 66, "passed": 0, "forwarded": 0, "saved": 0, "ingest_errors": 0, "partial": False}]
        open(tmp + "/data/panel/scan-history.jsonl", "w").write("".join(json.dumps(r) + "\n" for r in hist_rows))
        pg.goto(U + "/#/home"); pg.wait_for_selector("#scan-stats")
        st_txt = pg.inner_text("#scan-activity")
        check("ACTIVITY: latest scan in plain words with every number from the brief", "Scan completed" in st_txt and "No matching listings found" in st_txt and all(f"{n}\n" in pg.inner_text("#scan-stats") + "\n" for n in ("19", "66")) and "listings reviewed" in st_txt and "AI evaluations" in st_txt and "rejected as below relevance threshold" in st_txt and "matching listings" in st_txt and "saved" in st_txt and "forwarded" in st_txt)
        check("ACTIVITY: explains why nothing matched", "19 listings were reviewed, but none passed the relevance threshold" in st_txt)
        check("ACTIVITY: recent scans list shows earlier scans, automatic vs manual, with a clear timeout explanation", pg.locator("#scan-history .hrow").count() == 2 and "automatic" in pg.inner_text("#scan-history") and "did not finish in time" in pg.inner_text("#scan-history"))
        check("ACTIVITY: no raw log lines / technical terms shown", not any(w in st_txt.lower() for w in ("inotify", "offset", "traceback", "watchdog", "[search]", "inode")))
        pg.screenshot(path=f"{SHOTS}/08-scan-activity.png", full_page=True)
        os.remove(tmp + "/data/panel/scan-history.jsonl")
        # ---------- per-search listings view
        def pid(name): return next(x["id"] for x in json.load(open(tmp + "/data/panel/settings.json"))["products"] if x["name"] == name)
        pg.goto(U + "/#/searches"); pg.wait_for_selector(".search")
        for nm in ("iPhone 16", "Golf cart"):
            pg.click("button.primary:has-text('New search')"); pg.wait_for_selector("dialog[open] #pn"); pg.fill("#pn", nm); pg.click("dialog button.primary"); pg.wait_for_selector(f".search .name:has-text('{nm}')", timeout=30000)
        ip, gc, jc = pid("iPhone 16"), pid("Golf cart"), pid("\u05d2\u05f3\u05e7\u05d5\u05d6\u05d9".replace("\u05f3", "'"))
        def ing3(pidv, num, title): urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/marketplace", data=f"[Gpu (5)] {title}\n\u20aa500, \u05d7\u05d9\u05e4\u05d4\nhttps://facebook.com/marketplace/item/{num}/\nAI: great".encode(), headers={"Title": f"Found 1 new {pidv} from facebook"}), timeout=5).read()
        ing3(ip, 8501, "iPhone 16 Pro 256GB"); ing3(ip, 800, "RTX 3070 \u05db\u05e8\u05d8\u05d9\u05e1 \u05de\u05e1\u05da")  # 800 matches BOTH searches
        pg.reload(); pg.wait_for_selector(".search")
        check("SEARCHES: every saved search is a link to its own listings", pg.locator("a.search-link").count() >= 3)
        pg.click(f"a.search-link[href='#/search/{ip}']"); pg.wait_for_selector("#back-all")
        check("SEARCH VIEW: shows which search is being viewed", "Listings: iPhone 16" in pg.inner_text("main h1"))
        check("SEARCH VIEW: only that search's listings (its own + the one shared with another search)", until(lambda: pg.locator("article.lcard").count() == 2) and "iPhone 16 Pro" in pg.inner_text("main") and "RTX 3071" not in pg.inner_text("main"))
        check("SEARCH VIEW: the shared listing is one card, not a copy; filters/sorting still available", pg.locator("select").count() >= 1 and pg.locator("#lq").count() == 1 and pg.locator("select[aria-label='Search']").count() == 0)
        pg.screenshot(path=f"{SHOTS}/08-search-view.png", full_page=True)
        pg.click("#back-all"); pg.wait_for_selector("article.lcard")
        check("ALL LISTINGS still shows every listing from every search", until(lambda: pg.locator("article.lcard").count() >= 4) and "iPhone 16 Pro" in pg.inner_text("main") and "RTX 3071" in pg.inner_text("main"))
        pg.goto(U + "/#/search/" + gc); pg.wait_for_selector("#back-all")
        check("SEARCH VIEW: a search without listings shows a clear empty state and a way back", "No relevant listings for \u201cGolf cart\u201d yet" in pg.inner_text("main") and pg.locator("a:has-text('All listings')").count() >= 1)
        pg.click("a:has-text('My searches')"); pg.wait_for_selector(".search"); pg.click(f"a.search-count[href='#/search/{jc}']"); pg.wait_for_selector("#back-all")
        check("SEARCH VIEW: switching to another search shows only that search's listings", until(lambda: pg.locator("article.lcard").count() >= 3) and "iPhone 16 Pro" not in pg.inner_text("main"))
        pg.goto(U + "/#/search/p9999"); pg.wait_for_selector("text=Search not found")
        check("SEARCH VIEW: an unknown/deleted search id is handled gracefully", pg.locator("a:has-text('Back to searches')").count() == 1)
        # ---------- J6 Facebook problem states (accurate, actionable)
        def fb_state(state, lang="he", cookies=True):
            open(tmp + "/session/status.json", "w").write(json.dumps({"state": state, "at": int(time.time()), "lang": lang, "source": "scraper"}))
            pg.goto(U + "/#/facebook"); pg.reload(); pg.wait_for_selector(".hero h2")
            return pg.inner_text(".hero")
        t = fb_state("session_expired"); check("J6 session expired -> says so + one reconnect button", "expired" in t and pg.locator("button.primary.big:has-text('Reconnect')").count() == 1)
        pg.screenshot(path=f"{SHOTS}/09-facebook-expired.png", full_page=True)
        t = fb_state("checkpoint"); check("J6 checkpoint/CAPTCHA -> explains manual solving, no bypass", "verification" in t and "never bypass" in t)
        t = fb_state("logged_in", lang="fr"); check("J6 unsupported language -> tells exactly what to change", "is not supported" in t)
        t = fb_state("login_required"); check("J6 login required -> reconnect", "asking you to log in" in t)
        t = fb_state("checking"); check("J6 AIMM's own login-page visit shows 'checking', never 'not connected'", "Checking" in t)
        pg.goto(U + "/#/home"); pg.reload(); pg.wait_for_selector("h1")
        os.remove(tmp + "/session/status.json")
        # ---------- J7 settings
        pg.click("#tabs a:has-text('Settings')"); pg.wait_for_selector("text=System status")
        check("J7 settings: defaults, system status, technical config folded, NO backup section", pg.locator(".svc").count() >= 4 and "Search defaults" in pg.inner_text("main") and "Backups" not in pg.inner_text("main") and "restore" not in pg.inner_text("main").lower())
        pg.click("summary:has-text('Technical configuration')"); pg.wait_for_selector("#cfg:not(:has-text('Loading'))"); check("J7 technical config is read-only with secrets masked", "g" * 24 not in pg.inner_text("#cfg") and "********" in pg.inner_text("#cfg"))
        pg.fill("#siv", "10"); pg.click("button.primary:has-text('Save')"); pg.wait_for_selector(".notice.bad"); check("J7 settings validation error is explained in Hebrew", "30" in pg.inner_text(".notice.bad"))
        pg.screenshot(path=f"{SHOTS}/10-settings.png", full_page=True)
        # ---------- quality: keyboard, labels, layout shift, toasts, contrast
        pg.goto(U + "/#/searches"); pg.reload(); pg.wait_for_selector("h1")
        pg.keyboard.press("Tab"); pg.keyboard.press("Tab"); focused = pg.evaluate("document.activeElement.tagName + ':' + (document.activeElement.getAttribute('href')||'')")
        check(f"keyboard: Tab reaches the navigation links ({focused})", focused.startswith("A:#/"))
        pg.keyboard.press("Enter"); check("keyboard: Enter activates a nav link", until(lambda: "#/" in pg.url))
        for route in ("home", "searches", "listings", "facebook", "notifications", "settings"):
            pg.goto(U + f"/#/{route}"); pg.reload(); pg.wait_for_selector("h1,h2", timeout=15000); pg.wait_for_timeout(500)
            bad = pg.evaluate("""[...document.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(e=>!(e.getAttribute('aria-label')||(e.id&&document.querySelector('label[for="'+e.id+'"]'))||e.closest('label'))).map(e=>e.id||e.type)""")
            check(f"a11y /{route}: every form control has an accessible name {bad}", not bad)
        check("layout shift on load is negligible", pg.evaluate("window.__cls") < 0.1)
        pg.evaluate("""(()=>{const b=document.getElementById('toasts');for(const t of ['First','Second','Third']){const d=document.createElement('div');d.className='toast';d.textContent=t;b.append(d)}})()""")
        bx = pg.evaluate("[...document.querySelectorAll('.toast')].map(e=>{const r=e.getBoundingClientRect();return [r.top,r.bottom]})")
        check("stacked toasts never overlap each other", all(bx[i][1] <= bx[i + 1][0] + 1 for i in range(len(bx) - 1)))
        for scheme in ("light", "dark"):
            q = newctx(scheme=scheme); q.goto(U + "/"); q.wait_for_selector("h1")
            v = q.evaluate("(()=>{const s=getComputedStyle(document.documentElement),g=n=>s.getPropertyValue(n).trim();return Object.fromEntries(['--text','--bg','--muted','--surface','--brand','--brand-ink','--ok','--ok-soft','--warn','--warn-soft','--bad','--bad-soft','--brand-soft'].map(n=>[n,g(n)]))})()")
            pairs = [("text on page", v["--text"], v["--bg"]), ("muted text on card", v["--muted"], v["--surface"]), ("primary button", v["--brand-ink"], v["--brand"]), ("ok chip", v["--ok"], v["--ok-soft"]), ("warn chip", v["--warn"], v["--warn-soft"]), ("bad chip", v["--bad"], v["--bad-soft"]), ("link/active nav", v["--brand"], v["--brand-soft"])]
            low = [(n, round(contrast(a, c), 2)) for n, a, c in pairs if contrast(a, c) < 4.5]
            check(f"contrast AA in {scheme} mode (all pairs >= 4.5:1) {low}", not low)
            q.screenshot(path=f"{SHOTS}/11-home-{scheme}.png", full_page=True)
        # ---------- mobile
        m = newctx(390, 800)
        for route in ("home", "searches", "listings", "facebook", "notifications", "settings"):
            m.goto(U + f"/#/{route}"); m.reload(); m.wait_for_selector("h1,h2", timeout=15000); m.wait_for_timeout(400)
            check(f"mobile /{route}: no horizontal scroll", m.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        pos = m.evaluate("(()=>{const r=document.getElementById('tabs').getBoundingClientRect();return [r.bottom,window.innerHeight]})()")
        check("mobile: navigation is a bottom bar", abs(pos[0] - pos[1]) < 2)
        m.goto(U + "/#/searches"); m.reload(); m.wait_for_selector("h1"); m.click("button.primary:has-text('New search')"); m.wait_for_selector("dialog[open]")
        r = m.evaluate("(()=>{const r=document.getElementById('dlg').getBoundingClientRect();return [r.left,r.right,r.bottom,window.innerWidth,window.innerHeight]})()")
        check("mobile: dialogs are full-width bottom sheets within the screen", r[0] <= 1 and r[1] >= r[3] - 1 and r[2] <= r[4] + 1)
        m.screenshot(path=f"{SHOTS}/12-mobile-dialog.png"); m.keyboard.press("Escape")
        m.goto(U + "/#/home"); m.reload(); m.wait_for_selector("h1"); m.screenshot(path=f"{SHOTS}/12-mobile-home.png", full_page=True)
        m.goto(U + "/#/listings"); m.reload(); m.wait_for_selector("article.lcard"); m.screenshot(path=f"{SHOTS}/12-mobile-listings.png", full_page=True)
        # ---------- back to Hebrew through the same selector; both buttons in a fully configured system
        pg.goto(U + "/#/settings"); pg.reload(); pg.wait_for_selector("#uilang"); pg.select_option("#uilang", "he"); pg.wait_for_selector("html[lang=he]", state="attached", timeout=15000)
        pg.goto(U + "/"); pg.wait_for_selector("h1")
        check("HEBREW again: 'סריקה ידנית' is visible AND enabled on a ready system, and the start button is Hebrew", pg.locator("#scan-btn").is_enabled() and pg.locator("button.primary.big:has-text('התחלת מעקב')").count() == 1)
        pg.click("#scan-btn"); pg.wait_for_selector("dialog[open]")
        check("HEBREW: the scan dialog is Hebrew and explains the single run", "סריקה ידנית" in pg.inner_text("dialog") or "לבצע סריקה ידנית" in pg.inner_text("dialog"))
        pg.click("dialog button.primary"); pg.wait_for_selector("text=סורקים עכשיו", timeout=20000)
        check("HEBREW: the scan runs (scraper process up) and Home says it is scanning", until(lambda: os.path.exists(tmp + "/sup/aimm")))
        pg.wait_for_selector("#scan-card", timeout=15000)
        check("SCAN UI: live progress card in Hebrew with a real stage label and counters, no percentage", "סריקה ידנית בתהליך" in pg.inner_text("#scan-card") and pg.locator("#scan-label").inner_text().strip() != "" and "%" not in pg.inner_text("#scan-card"))
        pg.click("button.primary.big:has-text('ביטול הסריקה')"); pg.wait_for_selector("dialog[open]"); pg.click("dialog button.primary"); pg.wait_for_selector("text=המעקב כבוי", timeout=20000)
        pg.wait_for_selector("#scan-outcome", timeout=15000)
        check("SCAN UI: after cancelling, the outcome is shown in Hebrew and is not a failure", "הסריקה בוטלה" in pg.inner_text("#scan-outcome"))
        check("ACTIVITY: Home has a 'scan activity' card with the latest scan and plain-language numbers (Hebrew)", "פעילות סריקות" in pg.inner_text("#scan-activity") and pg.locator("#scan-activity").count() == 1)
        pg.goto(U + "/#/notifications"); pg.reload(); pg.wait_for_selector("h1")
        time.sleep(8)  # the summary button is rate-limited to one request per 20 s and this suite is fast
        pg.click("button:has-text('שליחת סיכום יומי עכשיו')"); 
        pg.wait_for_selector("text=הסיכום נשלח לטלגרם")
        check("HEBREW: the summary button sends the summary to Telegram (the day has listings now)", until(lambda: sum("Daily recap" in x for x in SENT) >= 1))
        pg.screenshot(path=f"{SHOTS}/13-hebrew-final-notifications.png", full_page=True)
        pg.goto(U + "/"); pg.wait_for_selector("h1"); pg.screenshot(path=f"{SHOTS}/13-hebrew-final-home.png", full_page=True)
        b.close()
    check(f"no JS errors in the console ({errors[:2]})", not errors)
    check(f"no Content-Security-Policy violations ({csp[:1]})", not csp)
finally:
    for pr in (agent, fwd):
        try: pr.send_signal(signal.SIGKILL); pr.wait()
        except Exception: pass
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed; screenshots: {SHOTS}"); sys.exit(1 if fail else 0)
