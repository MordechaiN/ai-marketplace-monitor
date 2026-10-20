"""Control-panel API end to end: real forwarder process + REAL scraper agent (fake supervisorctl / fake login helper) + mock Telegram
+ mock noVNC. Run: AIMM_LIB=<dir> python3 tests/test_panel.py"""
import base64, json, os, shutil, signal, socket, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AG, ING, UI, TGP, VNC = 18750, 18751, 18752, 18753, 18754
TOKEN_TG = "123456:SECRET-TELEGRAM-TOKEN-abcdefghij"; GUARD = "g" * 24; AGENT = "a" * 24
AUTH = "Basic " + base64.b64encode(b"admin:correct-horse-battery").decode()
tmp = tempfile.mkdtemp(); ok = fail = 0
for d in ("sup", "session", "data", "aimm"): os.makedirs(f"{tmp}/{d}")
TG = {"mode": "ok", "got": []}


def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)


class MockTG(BaseHTTPRequestHandler):
    def do_GET(self):
        if "getUpdates" in self.path:
            mode = TG.get("updates", "none")
            if mode == "409":
                code, out = 409, {"ok": False, "description": "Conflict: webhook"}
            else:
                res = {"none": [], "one": [{"message": {"chat": {"id": 123456789, "type": "private", "first_name": "Alex"}}}],
                       "two": [{"message": {"chat": {"id": 1, "type": "private", "first_name": "A"}}}, {"message": {"chat": {"id": -1009, "type": "group", "title": "Family"}}}]}[mode]
                code, out = 200, {"ok": True, "result": res}
            raw = json.dumps(out).encode(); self.send_response(code); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if TG["mode"] == "401": code, out = 401, {"ok": False, "description": "Unauthorized"}
        else: code, out = 200, {"ok": True}; TG["got"].append(b["text"])
        raw = json.dumps(out).encode(); self.send_response(code); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def log_message(self, *a): pass


class MockVNC(BaseHTTPRequestHandler):  # stands in for websockify: static file + upgrade echo
    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.send_response(101); self.send_header("Upgrade", "websocket"); self.send_header("Connection", "Upgrade"); self.end_headers(); self.wfile.flush()
            self.connection.settimeout(5)
            try:
                while True:
                    d = self.connection.recv(1024)
                    if not d: break
                    self.connection.sendall(b"ECHO:" + d)
            except OSError: pass
            return
        body = b"<html>novnc</html>"; self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


for port, h in ((TGP, MockTG), (VNC, MockVNC)):
    threading.Thread(target=HTTPServer(("127.0.0.1", port), h).serve_forever, daemon=True).start()


def http(method, url, body=None, headers=None, raw=False):
    r = urllib.request.Request(url, data=(json.dumps(body).encode() if body is not None else None), method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=130) as x: d = x.read(); return x.status, (d if raw else (json.loads(d) if d.strip().startswith(b"{") else d))
    except urllib.error.HTTPError as e:
        d = e.read(); return e.code, (json.loads(d) if d.strip().startswith(b"{") else d)


def api(method, path, body=None, csrf=None, auth=AUTH):
    h = {"Content-Type": "application/json"}
    if auth: h["Authorization"] = auth
    if csrf: h["X-CSRF-Token"] = csrf
    return http(method, f"http://127.0.0.1:{UI}/api/{path}", body, h)


def wait(c, t=25):
    end = time.time() + t
    while time.time() < end:
        try:
            if c(): return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


procs = {}
def start_agent():
    env = {**os.environ, "AGENT_TOKEN": AGENT, "AGENT_PORT": str(AG), "AIMM_SESSION_DIR": tmp + "/session", "SUPERVISORCTL": ROOT + "/tests/fakes/supervisorctl", "FAKE_SUP_DIR": tmp + "/sup",
           "AGENT_PYTHON": sys.executable, "HELPER_PY": ROOT + "/tests/fakes/helper.py", "FAKE_LOGGED_IN": "1", "FAKE_READY_DELAY": "0.3", "AIMM_STDOUT_LOG": tmp + "/aimm/stdout.log"}
    if os.environ.get("AIMM_LIB"): env["PYTHONPATH"] = os.environ["AIMM_LIB"]
    procs["agent"] = subprocess.Popen([sys.executable, "-u", ROOT + "/scraper-extras/agent.py"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert wait(lambda: http("GET", f"http://127.0.0.1:{AG}/status", headers={"Authorization": "Bearer " + AGENT})[0] == 200, 10)


def start_fwd():
    env = {**os.environ, "TG_API_BASE": f"http://127.0.0.1:{TGP}", "TG_BOT_TOKEN": TOKEN_TG, "TG_CHAT_ID": "123456789", "UI_USER": "admin", "UI_PASS": "correct-horse-battery",
           "DATA_DIR": tmp + "/data", "PORT": str(ING), "UI_PORT": str(UI), "MP_LOG": tmp + "/aimm/log", "MP_CONFIG": tmp + "/aimm/config.toml", "MP_CACHE": tmp + "/aimm/cache.db",
           "AGENT_URL": f"http://127.0.0.1:{AG}", "AGENT_TOKEN": AGENT, "GUARD_TOKEN": GUARD, "SCRAPER_HOST": "127.0.0.1", "NOVNC_PORT": str(VNC), "RECONCILE_S": "1", "INGEST_GRACE_S": "3", "SCAN_MIN_S": "1", "SCAN_TIMEOUT_S": "12", "SEND_GAP_S": "0.1",
           "TZ": "Asia/Jerusalem", "NOTIFY_ENABLED": "false", "RECAP_HOUR": "25", "MP_BLACKLIST": tmp + "/data/blacklist.jsonl"}
    procs["fwd"] = subprocess.Popen([sys.executable, "-u", ROOT + "/forwarder/forwarder.py"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert wait(lambda: api("GET", "me")[0] == 200, 15)


def stop(n):
    procs[n].send_signal(signal.SIGKILL); procs[n].wait(); return procs[n].stdout.read().decode() if procs[n].stdout else ""


def set_delay(n): open(tmp + "/sup/start_delay", "w").write(str(n))  # seconds the fake supervisor needs to reach RUNNING


def fb_login():
    api("POST", "facebook/open", {}, csrf); time.sleep(1.3); return api("POST", "facebook/save", {}, csrf)


def sup_state(): return http("GET", f"http://127.0.0.1:{AG}/status", headers={"Authorization": "Bearer " + AGENT})[1]


PROD = {"name": "כרטיס מסך", "search_phrases": ["rtx 3070"], "max_price": 1500, "description": "כרטיס עובד", "great_deal_price": 800}
try:
    start_agent(); start_fwd()
    s, me = api("GET", "me"); csrf = me["csrf"]
    # ---- language: Hebrew is the default; English is a switch in Settings
    pageh = http("GET", f"http://127.0.0.1:{UI}/", headers={"Authorization": AUTH}, raw=True)[1]
    check("DEFAULT: the app is served as Hebrew right-to-left", b'lang="he" dir="rtl"' in pageh)
    s, r = api("POST", "scraper", {"action": "start", "confirm": True}, csrf)
    check("DEFAULT: backend messages are Hebrew", s == 400 and any("\u0590" <= ch <= "\u05ff" for ch in r["error"]))
    check("DEFAULT: home sentences are Hebrew", any("\u0590" <= ch <= "\u05ff" for ch in api("GET", "home")[1]["state"]["headline"]))
    check("unsupported language is rejected", api("POST", "language", {"language": "fr"}, csrf)[0] == 400)
    check("language needs auth + CSRF", api("POST", "language", {"language": "en"}, auth=None)[0] == 401 and api("POST", "language", {"language": "en"})[0] == 403)
    s, r = api("POST", "language", {"language": "en"}, csrf); check("switch to English", s == 200 and api("GET", "settings")[1]["settings"]["ui"]["language"] == "en")
    check("English: the app is served left-to-right in English", b'lang="en" dir="ltr"' in http("GET", f"http://127.0.0.1:{UI}/", headers={"Authorization": AUTH}, raw=True)[1])
    s, r = api("POST", "scraper", {"action": "start", "confirm": True}, csrf)
    check("English: the same refusal is now English", s == 400 and r["error"].isascii())
    # ---- security
    check("API without credentials -> 401", api("GET", "status", auth=None)[0] == 401)
    check("state change without CSRF -> 403", api("POST", "products", PROD)[0] == 403)
    check("state change with foreign Origin -> 403", http("POST", f"http://127.0.0.1:{UI}/api/products", PROD, {"Authorization": AUTH, "X-CSRF-Token": csrf, "Origin": "http://evil.example", "Content-Type": "application/json"})[0] == 403)
    check("GET /api/me gives csrf", len(csrf) == 64)
    # ---- defaults
    s, st = api("GET", "status")
    check("fresh install: scraper desired=stopped and not running", st["scraper"]["desired"] == "stopped" and st["scraper"]["running"] is False and st["scraper"]["agent"] is True)
    check("fresh install: notifications OFF, token configured, no secret in status", st["telegram"]["enabled"] is False and st["telegram"]["token_configured"] and "SECRET" not in json.dumps(st))
    # ---- products CRUD + config generation
    s, r = api("POST", "products", {**PROD, "search_phrases": []}, csrf); check("invalid product -> 400 with Hebrew field errors", s == 400 and r["errors"][0]["field"].endswith("search_phrases"))
    check("invalid product wrote no config", not os.path.exists(tmp + "/aimm/config.toml"))
    open(tmp + "/data/blacklist.jsonl", "w", encoding="utf-8").write(json.dumps({"url": "https://facebook.com/marketplace/item/1", "title": "כרטיס שגוי GTX 1050", "item": "p1", "price": 100, "reason": "wrong", "ts": "2026-10-01T10:00:00"}, ensure_ascii=False) + "\n")
    s, r = api("POST", "products", PROD, csrf); check("create product", s == 200 and r["settings"]["products"][0]["id"] == "p1")
    cfg = open(tmp + "/aimm/config.toml", encoding="utf-8").read()
    check("config.toml generated: item, non-English description kept intact, guard token", "[item.p1]" in cfg and "כרטיס עובד" in cfg and GUARD in cfg and "ai = ['guard']" in cfg)
    check("blacklist feedback (manual removals) flows into the generated item description for the AI", "Examples the user marked as not suitable" in cfg and "כרטיס שגוי GTX 1050" in cfg)
    check("config.toml mode 600", oct(os.stat(tmp + "/aimm/config.toml").st_mode & 0o777) == "0o600")
    s, r = api("PUT", "products/p1", {**PROD, "max_price": 1200, "enabled": True}, csrf); check("edit product", s == 200 and r["settings"]["products"][0]["max_price"] == 1200 and "max_price = 1200" in open(tmp + "/aimm/config.toml").read())
    s, r = api("POST", "products/p1/duplicate", {}, csrf); check("duplicate: copy is DISABLED and uniquely named", s == 200 and len(r["settings"]["products"]) == 2 and r["settings"]["products"][1]["enabled"] is False and r["settings"]["products"][1]["name"] != PROD["name"])
    check("disabled product written as enabled = false", "enabled = false" in open(tmp + "/aimm/config.toml").read())
    s, r = api("POST", "products/p2/toggle", {}, csrf); check("toggle enables", r["settings"]["products"][1]["enabled"] is True)
    s, r = api("DELETE", "products/p2", None, csrf); check("delete product", s == 200 and len(r["settings"]["products"]) == 1)
    check("delete unknown -> 400 Hebrew", api("DELETE", "products/p9", None, csrf)[0] == 400)
    s, r = api("PUT", "settings/facebook", {"search_city": "bad city!"}, csrf); check("facebook section validation error explained", s == 400 and r["errors"][0]["field"] == "facebook.search_city")
    s, r = api("PUT", "settings/facebook", {"search_city": "telaviv", "radius": 25}, csrf); check("facebook section saved", s == 200 and "search_city = \"telaviv\"" in open(tmp + "/aimm/config.toml").read())
    check("no backup directory is created any more", not os.path.exists(tmp + "/data/panel/backups"))
    if os.environ.get("AIMM_LIB"):
        before = open(tmp + "/aimm/config.toml").read()
        s, r = api("PUT", "settings/facebook", {"currency": "ZZZ"}, csrf)
        check("input the panel accepts but AIMM's own loader rejects is refused, message shown, config untouched",
              s == 400 and "The scraper rejected" in r["errors"][0]["message"] and "ZZZ" in r["errors"][0]["message"] and open(tmp + "/aimm/config.toml").read() == before)
    # ---- scraper control
    s, r = api("PUT", "settings/facebook", {"search_city": ""}, csrf)
    s, r = api("POST", "scraper", {"action": "start", "confirm": True}, csrf); check("start refused without a city (reason in English)", s == 400 and "location" in r["error"])
    api("PUT", "settings/facebook", {"search_city": "telaviv"}, csrf)
    s, r = api("POST", "scraper", {"action": "start", "confirm": True}, csrf); check("start refused while Facebook is not connected (English reason)", s == 400 and "Facebook" in r["error"] and not sup_state()["scraper_running"])
    s, r = fb_login(); check("manual login saved (fake helper)", s == 200 and r["result"]["state"] == "saved")
    s, r = api("POST", "scraper", {"action": "start"}, csrf); check("start refused without explicit confirmation", s == 400 and not sup_state()["scraper_running"])
    set_delay(3)  # REGRESSION: the real supervisor needs 15 s to reach RUNNING; my reconcile loop used to kill the scraper in that window
    s, r = api("POST", "scraper", {"action": "start", "confirm": True}, csrf); check("start with confirmation -> accepted immediately", s == 200)
    states = []
    for _ in range(24):
        states.append(sup_state()["programs"]["aimm"]["state"]); time.sleep(0.4)
    check(f"REGRESSION: scraper is never stopped while starting (states seen: {sorted(set(states))})", "STOPPED" not in states and states[-1] == "RUNNING")
    set_delay(0)
    check("desired=running persisted", json.load(open(tmp + "/data/panel/settings.json"))["scraper"]["desired"] == "running")
    os.remove(tmp + "/sup/aimm"); check("crashed scraper is restarted by reconcile", wait(lambda: sup_state()["scraper_running"], 8))
    api("POST", "scraper", {"action": "stop"}, csrf); check("stop -> stopped, stays stopped", wait(lambda: not sup_state()["scraper_running"], 5) and (time.sleep(2.5) or not sup_state()["scraper_running"]))
    api("POST", "scraper", {"action": "start", "confirm": True}, csrf)
    end = time.time() + 30  # crash loop breaker: keep crashing the scraper whenever it is up until the panel gives up
    while time.time() < end and json.load(open(tmp + "/data/panel/settings.json"))["scraper"]["desired"] == "running":
        if os.path.exists(tmp + "/sup/aimm"): os.remove(tmp + "/sup/aimm")
        time.sleep(0.5)
    check("crash loop (3 restarts in 10 min) stops the scraper and records desired=stopped", json.load(open(tmp + "/data/panel/settings.json"))["scraper"]["desired"] == "stopped")
    api("POST", "scraper", {"action": "start", "confirm": True}, csrf)
    open(tmp + "/session/status.json", "w").write(json.dumps({"state": "checkpoint", "at": int(time.time()) + 5, "source": "scraper"}))
    check("Facebook checkpoint auto-stops the scraper (no retries, no bypass)", wait(lambda: not sup_state()["scraper_running"] and api("GET", "status")[1]["scraper"]["desired"] == "stopped", 10))
    os.remove(tmp + "/session/status.json")
    s, r = api("POST", "scraper", {"action": "pause", "hours": 0.001}, csrf); check("pause rejects silly duration", s == 400)
    api("POST", "scraper", {"action": "start", "confirm": True}, csrf)
    s, r = api("POST", "scraper", {"action": "pause", "hours": 1}, csrf)
    wait(lambda: not sup_state()["scraper_running"], 6)
    st = api("GET", "status")[1]["scraper"]; check("pause: stopped now, desired=paused with resume time ~1h", s == 200 and not sup_state()["scraper_running"] and st["desired"] == "paused" and 3500 < st["pause_until"] - time.time() < 3700)
    d = json.load(open(tmp + "/data/panel/settings.json")); d["scraper"]["pause_until"] = int(time.time()) - 1; open(tmp + "/data/panel/settings.json", "w").write(json.dumps(d))
    check("expired pause auto-resumes", wait(lambda: sup_state()["scraper_running"], 8))
    api("POST", "scraper", {"action": "stop"}, csrf)
    # ---- persistence across container restart: panel restarts, scraper process restarts stopped
    stop("fwd"); start_fwd()
    s, r = api("GET", "settings"); check("settings (products, city) persist across restart", r["settings"]["products"][0]["name"] == "כרטיס מסך" and r["settings"]["facebook"]["search_city"] == "telaviv")
    check("after restart scraper is still stopped (no auto-start)", not sup_state()["scraper_running"])
    # ---- facebook connection
    api("POST", "facebook/logout", {}, csrf)
    s, r = api("GET", "facebook"); check("facebook info: no session yet, status says so in Hebrew", r["agent"] and r["session"]["exists"] is False and r["connection"]["code"] == "not_connected")
    check("open login browser", api("POST", "facebook/open", {}, csrf)[0] == 200 and api("GET", "facebook")[1]["helper_open"])
    s, r = api("POST", "scraper", {"action": "start", "confirm": True}, csrf); check("scraper start refused while login browser is open", s == 409)
    # noVNC bridge
    s, b = http("GET", f"http://127.0.0.1:{UI}/browser/vnc.html", headers={"Authorization": AUTH}, raw=True); check("noVNC static proxied while helper open (authenticated)", s == 200 and b"novnc" in b)
    check("noVNC proxy requires auth", http("GET", f"http://127.0.0.1:{UI}/browser/vnc.html")[0] == 401)
    check("noVNC proxy rejects path tricks", http("GET", f"http://127.0.0.1:{UI}/browser/..%2f..%2fetc/passwd", headers={"Authorization": AUTH})[0] in (400, 404))
    def ws(origin=None, auth=AUTH):
        sk = socket.create_connection(("127.0.0.1", UI), timeout=5)
        req = f"GET /browser-ws HTTP/1.1\r\nHost: 127.0.0.1:{UI}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n" + (f"Authorization: {auth}\r\n" if auth else "") + (f"Origin: {origin}\r\n" if origin else "") + "\r\n"
        sk.sendall(req.encode()); time.sleep(0.5); first = sk.recv(4096)
        return sk, first
    sk, first = ws(origin=f"http://127.0.0.1:{UI}"); sk.sendall(b"hello"); time.sleep(0.4); got = sk.recv(4096); sk.close()
    check("WebSocket bridge: upgrade passes through and bytes flow both ways", b"101" in first and b"ECHO:hello" in (first + got))
    sk, first = ws(origin="http://evil.example"); sk.close(); check("WebSocket from a foreign Origin refused (CSWSH)", b"403" in first)
    sk, first = ws(auth=None); sk.close(); check("WebSocket without credentials refused", b"401" in first)
    s, r = api("POST", "facebook/save", {}, csrf); check("save after login stores the session; helper closes", s == 200 and r["result"]["state"] == "saved" and not api("GET", "facebook")[1]["helper_open"])
    info = api("GET", "facebook")[1]; check("session reported (no cookie values anywhere)", info["session"]["exists"] and info["session"]["has_login_cookies"] and "SECRETVALUE" not in json.dumps(info))
    open(tmp + "/session/status.json", "w").write(json.dumps({"state": "logged_in", "at": int(time.time()), "lang": "he", "source": "scraper"}))
    check("account language (he) detected from a logged-in page -> remembered", wait(lambda: api("GET", "settings")[1]["settings"]["facebook"]["detected_language"] == "he", 8))
    check("config regenerated with the Hebrew translation", wait(lambda: 'language = "he"' in open(tmp + "/aimm/config.toml", encoding="utf-8").read() and "[translation.he]" in open(tmp + "/aimm/config.toml", encoding="utf-8").read(), 8))
    open(tmp + "/session/status.json", "w").write(json.dumps({"state": "logged_in", "at": int(time.time()), "lang": "en", "source": "scraper"}))
    check("switching the account to English removes the translation again", wait(lambda: "[translation.he]" not in open(tmp + "/aimm/config.toml", encoding="utf-8").read(), 8))
    os.remove(tmp + "/session/status.json")
    time.sleep(3.2); s, b = http("GET", f"http://127.0.0.1:{UI}/browser/vnc.html", headers={"Authorization": AUTH}); check("noVNC bridge closed again once the helper is closed", s == 503)
    check("session check endpoint", api("POST", "facebook/check", {}, csrf)[1].get("state") == "logged_in")
    check("logout deletes the saved session", api("POST", "facebook/logout", {}, csrf)[0] == 200 and not api("GET", "facebook")[1]["session"]["exists"])
    # ---- notifications
    s, r = api("PUT", "settings/notifications", {"chat_id": "abc"}, csrf); check("bad chat id explained", s == 400 and r["errors"][0]["field"] == "notifications.chat_id")
    s, r = api("PUT", "settings/notifications", {"recap_time": "22:30", "immediate_min_score": 4}, csrf); check("recap time + threshold saved", s == 200 and r["settings"]["notifications"]["recap_time"] == "22:30")
    s, r = api("POST", "notifications/test", {}, csrf); check("test message delivered even while notifications are disabled", s == 200 and any("Test message" in t for t in TG["got"]))
    check("test message rate-limited", api("POST", "notifications/test", {}, csrf)[0] == 429)
    TG["mode"] = "401"; procs  # next test after rate-limit window: reset limiter by restart
    stop("fwd"); start_fwd(); csrf = api("GET", "me")[1]["csrf"]
    s, r = api("POST", "notifications/test", {}, csrf); check("bad bot token -> clear English reason, no token leaked", s == 502 and "token" in r["error"] and "SECRET" not in json.dumps(r))
    TG["mode"] = "ok"
    # ---- redesign: home state machine, setup checklist, detect chat, quiet hours, last good login
    s, hm = api("GET", "home")
    check("home: city + product exist but Telegram untested/not started -> setup checklist drives the user", s == 200 and hm["state"]["code"] in ("setup", "stopped") and len(hm["setup"]["steps"]) == 4)
    steps = {x["id"]: x["done"] for x in hm["setup"]["steps"]}
    check(f"setup steps reflect reality (facebook/search/notifications/start): {steps}", steps["search"] is True and steps["notifications"] is True and steps["facebook"] is False and steps["start"] is True)
    api("POST", "facebook/logout", {}, csrf)
    s, hm = api("GET", "home"); st0 = {x["id"]: x["done"] for x in hm["setup"]["steps"]}
    check("home: manual scan reported unavailable while Facebook is not connected, with the reason", hm["scan"]["available"] is False and "Facebook" in hm["scan"]["reason"])
    check("home: without a Facebook login the first open step is Facebook", st0["facebook"] is False and hm["state"]["code"] == "setup" and hm["state"]["primary"]["route"] == "facebook")
    fb_login()
    s, hm = api("GET", "home"); check("home: everything ready -> 'stopped' with a start action", hm["state"]["code"] == "stopped" and hm["state"]["primary"]["action"] == "start")
    check("home: manual scan is AVAILABLE when monitoring is off and everything is set up", hm["scan"]["available"] is True)
    s, r = api("POST", "scraper", {"action": "start", "confirm": True}, csrf)
    s, hm = api("GET", "home"); check("home: running -> 'active' offering pause", hm["state"]["code"] == "active" and hm["state"]["primary"]["action"] == "pause" and hm["scraper"]["running"])
    check("home: manual scan reported UNAVAILABLE while monitoring runs, with the reason", hm["scan"]["available"] is False and "monitoring" in hm["scan"]["reason"])
    api("POST", "scraper", {"action": "pause", "hours": 1}, csrf)
    s, hm = api("GET", "home"); check("home: paused -> 'paused' offering resume", hm["state"]["code"] == "paused" and hm["state"]["primary"]["action"] == "start")
    api("POST", "scraper", {"action": "stop"}, csrf)
    check("home sentences are English", all(not any("\u0590" <= ch <= "\u05ff" for ch in hm["state"][k]) and hm["state"][k] for k in ("headline",)))
    api("POST", "setup", {"setup_dismissed": True}, csrf); check("setup checklist can be dismissed (persisted)", api("GET", "home")[1]["setup"]["dismissed"] is True)
    TG["updates"] = "none"; s, r = api("POST", "notifications/detect", {}, csrf); check("detect chat: nothing yet -> helpful hint (send /start)", s == 200 and r["chats"] == [] and "/start" in r["hint"])
    TG["updates"] = "one"; s, r = api("POST", "notifications/detect", {}, csrf); check("detect chat: one chat found -> its id", r["chats"][0]["id"] == "123456789")
    TG["updates"] = "two"; s, r = api("POST", "notifications/detect", {}, csrf); check("detect chat: several chats are offered for choosing", len(r["chats"]) == 2)
    TG["updates"] = "409"; s, r = api("POST", "notifications/detect", {}, csrf); check("detect chat: webhook conflict explained", s == 409 and "webhook" in r["error"])
    s, r = api("PUT", "settings/notifications", {"quiet_enabled": True, "quiet_from": "23:00", "quiet_to": "23:00"}, csrf); check("quiet hours with identical start/end rejected in Hebrew", s == 400 and r["errors"][0]["field"] == "notifications.quiet_to")
    s, r = api("PUT", "settings/notifications", {"quiet_enabled": True, "quiet_from": "22:00", "quiet_to": "07:30"}, csrf); check("valid quiet hours saved", s == 200 and r["settings"]["notifications"]["quiet_to"] == "07:30")
    open(tmp + "/session/status.json", "w").write(json.dumps({"state": "logged_in", "at": int(time.time()), "lang": "en", "source": "scraper"}))
    check("last successful Facebook login time is remembered", wait(lambda: api("GET", "facebook")[1]["last_ok"] > 0, 8))
    check("facebook info exposes language setting and detected language", api("GET", "facebook")[1]["language"] == "auto")
    os.remove(tmp + "/session/status.json")
    # ---- manual scan (scan once without continuous monitoring)
    LOG = tmp + "/aimm/log"
    open(LOG, "w").write("[VERSION] old run\n[Search] Searching facebook for old\n[Schedule] Next job to search old scheduled to run in an hour\n")
    fb_login(); api("PUT", "settings/facebook", {"search_city": "telaviv"}, csrf)
    s, r = api("POST", "scraper", {"action": "scan"}, csrf); check("scan needs explicit confirmation", s == 400 and not sup_state()["scraper_running"])
    s, r = api("POST", "scraper", {"action": "scan", "confirm": True}, csrf); check("scan starts the scraper without enabling monitoring", s == 200 and wait(lambda: sup_state()["scraper_running"], 8) and api("GET", "status")[1]["scraper"]["desired"] == "scan")
    hm = api("GET", "home")[1]; check("home shows 'scanning' with a cancel action; scan button reported unavailable", hm["state"]["code"] == "scanning" and hm["state"]["primary"]["action"] == "stop" and hm["scan"]["available"] is False and "already running" in hm["scan"]["reason"])
    s, r = api("POST", "scraper", {"action": "scan", "confirm": True}, csrf); check("a second scan while one runs -> 409", s == 409 and "already running" in r["error"])
    time.sleep(3)
    check("REGRESSION: the PREVIOUS run's 'Next job' line in the log does not end a new scan early", api("GET", "status")[1]["scraper"]["desired"] == "scan" and sup_state()["scraper_running"])
    open(LOG, "a").write("[VERSION] new run\n[Search] Searching facebook for x from Tel Aviv\n")
    time.sleep(3); check("scan keeps running while a search is still in progress", api("GET", "status")[1]["scraper"]["desired"] == "scan")
    open(LOG, "a").write("[Schedule] Next job to search x scheduled to run in an hour at 2026-10-03 22:00:00\n")
    check("scan ends by itself when AIMM reports the cycle finished: scraper stopped, desired=stopped", wait(lambda: api("GET", "status")[1]["scraper"]["desired"] == "stopped" and not sup_state()["scraper_running"], 12))
    ls = api("GET", "home")[1]["last_scan"]; check("the finished scan is recorded (result 'done')", ls and ls["result"] == "done")
    api("POST", "scraper", {"action": "start", "confirm": True}, csrf)
    s, r = api("POST", "scraper", {"action": "scan", "confirm": True}, csrf); check("scan refused while monitoring is on", s == 409 and "only available while monitoring is off" in r["error"])
    api("POST", "scraper", {"action": "stop"}, csrf); wait(lambda: not sup_state()["scraper_running"], 6)
    api("POST", "scraper", {"action": "scan", "confirm": True}, csrf); time.sleep(2)
    api("POST", "scraper", {"action": "stop"}, csrf)
    check("cancelling a scan stops it", wait(lambda: not sup_state()["scraper_running"], 8) and api("GET", "status")[1]["scraper"]["desired"] == "stopped")
    api("POST", "scraper", {"action": "scan", "confirm": True}, csrf)
    check("a scan that never finishes is stopped by the timeout (12 s in this test)", wait(lambda: api("GET", "status")[1]["scraper"]["desired"] == "stopped" and not sup_state()["scraper_running"], 25) and api("GET", "home")[1]["last_scan"]["result"] == "timeout")
    # ---- scan progress stages, outcomes (no results vs failure vs cancelled), edit-and-rerun, listing management
    def scan_stage(): return (api("GET", "home")[1].get("scan_status") or {}).get("stage")
    def run_scan(lines, settle=0):
        open(LOG, "a").write("[VERSION] run\n"); api("POST", "scraper", {"action": "scan", "confirm": True}, csrf); wait(lambda: scan_stage() == "starting" or scan_stage() == "connecting", 8)
        stages = []
        for ln in lines:
            open(LOG, "a").write(ln); time.sleep(2.2); stages.append(scan_stage())
        return stages
    st = run_scan(["Successfully launched Chromium\n", "[Search] Searching facebook for x\n", "https://www.facebook.com/marketplace/item/1/\n", "[AI] ai concludes 4\n"])
    check(f"scan progress shows real stages in order (got {st})", st == ["connecting", "searching", "processing", "ai"])
    ss = api("GET", "home")[1]["scan_status"]; check("scan status carries counters and no percentage", ss["searches"] == 1 and ss["items_opened"] == 1 and ss["ai_rated"] == 1 and "percent" not in ss)
    open(LOG, "a").write("[Schedule] Next job to search x scheduled to run in an hour\n")
    check("scan finishes", wait(lambda: api("GET", "status")[1]["scraper"]["desired"] == "stopped", 12))
    oc = api("GET", "home")[1]["last_scan_outcome"]; check("zero new listings + no errors -> explicit 'no matching listings' (not a failure)", oc["code"] == "no_results" and oc["level"] == "info" and oc["message"] == "No matching listings found")
    run_scan(["[Search] Searching facebook for x\n", "https://www.facebook.com/marketplace/item/1/\n[Item] Failed to get item details\n[Schedule] Next job to search x scheduled to run in an hour\n"]); wait(lambda: api("GET", "status")[1]["scraper"]["desired"] == "stopped", 12)
    oc = api("GET", "home")[1]["last_scan_outcome"]; check("every listing unreadable -> scraper failure, not 'no results'", oc["code"] == "read_failed" and oc["level"] == "bad")
    run_scan(["[Search] Searching facebook for x\n"]); api("POST", "scraper", {"action": "stop"}, csrf)
    check("cancelling is recorded as 'cancelled', not failure", wait(lambda: (api("GET", "home")[1]["last_scan_outcome"] or {}).get("code") == "cancelled", 8))
    run_scan(["[Search] Searching facebook for x\n", "[AI] [AI-Error] Failed to get an answer\n", "[Schedule] Next job to search x scheduled to run in an hour\n"]); wait(lambda: api("GET", "status")[1]["scraper"]["desired"] == "stopped", 12)
    ls = api("GET", "home")[1]["last_scan"]; check("AI failures during a scan are counted separately from scrape failures", ls["ai_failed"] >= 1 and ls["result"] == "done")
    # ---- scan tracking from the supervisor stdout log (authoritative), result reporting, protection against silent listing loss
    OUT = tmp + "/aimm/stdout.log"
    open(OUT, "w").write("[10/03/26 12:00:00] INFO [Search] Searching facebook for OLD\n[Search] 7 new listings for p1 are found.\n[Schedule] Next job to search p1 scheduled to run in an hour\n")
    SEARCH = "[Search] Searching facebook for p1 from Haifa\n"
    REVIEW = lambda n: "".join(f"[Retrieve] Parsing item {i}\n[AI] guard concludes No match (1): not a match for listing {i}.\n[Skip] Rating No match (1) for item {i} is below threshold 4.\n" for i in range(n))
    END = lambda n: f"[Search] {n} new listings for p1 are found.\n[Schedule] Next job to search p1 scheduled to run in an hour at 2026-10-03 22:00:00\n"
    def scan2(parts, ingest_during=None, wait_s=25):
        api("POST", "scraper", {"action": "scan", "confirm": True}, csrf); wait(lambda: scan_stage() is not None, 8)
        for txt in parts:
            open(OUT, "a").write(txt); time.sleep(1.3)
            if ingest_during and txt is parts[-2]: ingest_during()
        wait(lambda: api("GET", "status")[1]["scraper"]["desired"] == "stopped", wait_s)
        return api("GET", "home")[1]
    def stats_of(hm): return hm["last_scan_outcome"]["stats"]
    hm = scan2([SEARCH, REVIEW(3), END(0)])
    oc = hm["last_scan_outcome"]
    check("SCAN (supervisor log): finished scan with only below-threshold listings -> 'no matching listings', never 'timeout'", oc["code"] == "no_results" and oc["message"] == "No matching listings found")
    check("SCAN: the previous run's lines in the same log are ignored (7 old 'new listings' not counted)", stats_of(hm)["passed"] == 0 and stats_of(hm)["reviewed"] == 3 and stats_of(hm)["ai_rated"] == 3 and stats_of(hm)["below"] == 3)
    check("SCAN: the outcome text carries the real counters", "Reviewed 3 listings" in oc["detail"] and "AI evaluations: 3" in oc["detail"] and "passed the relevance threshold: 0" in oc["detail"])
    open(LOG, "a").write("in-event <InotifyEvent: src_path=b'/x', mask=IN_MODIFY>\n" * 40000)  # the OLD failure mode: the file log full of noise
    hm = scan2([SEARCH, REVIEW(2), END(0)])
    check("REGRESSION: a flooded/rotated AIMM file log no longer hides completion (scan reported done, not timeout)", hm["last_scan_outcome"]["code"] == "no_results" and stats_of(hm)["reviewed"] == 2)
    # log rotated in the middle of the scan: the new file is read from its start and the counters are flagged as possibly partial
    api("POST", "scraper", {"action": "scan", "confirm": True}, csrf); wait(lambda: scan_stage() is not None, 8)
    open(OUT, "a").write(SEARCH + REVIEW(2)); time.sleep(1.5)
    os.rename(OUT, OUT + ".1"); open(OUT, "w").write(REVIEW(1) + END(0)); wait(lambda: api("GET", "status")[1]["scraper"]["desired"] == "stopped", 20)
    hm = api("GET", "home")[1]
    check("ROTATION: scan whose supervisor log rotated mid-run still completes (cycle end found in the new file) and says the counts may be partial", hm["last_scan"]["result"] == "done" and hm["last_scan"]["partial"] is True and "may be incomplete" in hm["last_scan_outcome"]["detail"])
    # matching listings that reach the forwarder and are stored
    s, r = api("PUT", "products/p1", {**PROD, "name": "jacuzzi", "search_phrases": ["jacuzzi"], "currency": ""}, csrf)
    def ingest_ok(): urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/", data="[Gpu (5)] Jacuzzi 6 seats\n\u20aa2500, Haifa\nhttps://facebook.com/marketplace/item/7001/\nAI: great".encode(), headers={"Title": "Found 1 new p1 from facebook"}), timeout=10).read()
    hm = scan2([SEARCH, REVIEW(2), "[AI] guard concludes Great (5): matches listing 3.\n", END(1)], ingest_during=ingest_ok)
    oc = hm["last_scan_outcome"]; st_ = stats_of(hm)
    check("SCAN: matching listing that was forwarded and recorded -> success with counts (passed 1, recorded 1, new 1)", oc["code"] == "done" and oc["level"] == "ok" and st_["passed"] == 1 and st_["forwarded"] == 1 and st_["saved"] == 1 and st_["new"] == 1)
    check("SCAN: the listing really is in the stored history and the feed", any(x["url"].rstrip("/").endswith("7001") for x in api("GET", "listings")[1]["listings"]))
    n_lines = lambda: len([l for l in open(tmp + "/data/alerts.jsonl", encoding="utf-8") if "7001" in l])
    ingest_ok(); ingest_ok()
    check("DUPLICATES: the same delivery repeated does not create another record", n_lines() == 1)
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/", data="[Gpu (5)] Jacuzzi 6 seats\n\u20aa2500, Haifa\nhttps://facebook.com/marketplace/item/7001/\nAI: great".encode(), headers={"Title": "Found 1 new p1 from facebook "}), timeout=10).read()
    check("DUPLICATES: an identical listing arriving in a new event is not stored twice either", n_lines() == 1)
    time.sleep(1.2)
    # AIMM says it found 2 matching listings but nothing reaches the forwarder -> reported as a failure, not as success/no-results
    hm = scan2([SEARCH, REVIEW(1), END(2)], wait_s=40)
    oc = hm["last_scan_outcome"]
    check("SILENT LOSS: matching listings that never reached the panel are reported as a failure with the numbers", oc["code"] == "forward_failed" and oc["level"] == "bad" and "2 matching listings" in oc["message"] and "only 0 reached" in oc["message"])
    # storage failure: alerts.jsonl cannot be written -> the ingest answers 500 (sender may retry), the error is counted and the scan says so
    AL = tmp + "/data/alerts.jsonl"; os.rename(AL, AL + ".bak"); os.symlink(tmp + "/vanished/alerts.jsonl", AL)
    def ingest_bad():
        try: urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/", data="[Gpu (5)] Jacuzzi 8 seats\n\u20aa3500, Haifa\nhttps://facebook.com/marketplace/item/7002/\nAI: great".encode(), headers={"Title": "Found 1 new p1 from facebook"}), timeout=10).read()
        except urllib.error.HTTPError as e: check("SILENT LOSS: a storage failure is answered with HTTP 500 so the sender can retry", e.code == 500)
    hm = scan2([SEARCH, REVIEW(1), "[AI] guard concludes Great (5): good.\n", END(1)], ingest_during=ingest_bad)
    oc = hm["last_scan_outcome"]
    check("SILENT LOSS: a failed save is reported in the scan result (record_failed, error count)", oc["code"] == "record_failed" and oc["level"] == "bad" and stats_of(hm)["ingest_errors"] >= 1)
    os.remove(AL); os.rename(AL + ".bak", AL)
    ingest_ok_retry = lambda: urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/", data="[Gpu (5)] Jacuzzi 8 seats\n\u20aa3500, Haifa\nhttps://facebook.com/marketplace/item/7002/\nAI: great".encode(), headers={"Title": "Found 1 new p1 from facebook"}), timeout=10).read()
    ingest_ok_retry(); ingest_ok_retry()
    check("RETRY after the failed save stores the listing exactly once", len([l for l in open(AL, encoding="utf-8") if "7002" in l]) == 1)
    # AI failure during the scan: listing is kept (unrated), counted, and the result says so
    def ingest_unrated(): urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/", data="[Gpu (5)] Jacuzzi 9 seats\n\u20aa900, Haifa\nhttps://facebook.com/marketplace/item/7003/\nAI: Not evaluated by AI: the model did not answer".encode(), headers={"Title": "Found 1 new p1 from facebook"}), timeout=10).read()
    hm = scan2([SEARCH, REVIEW(1), "[AI] [AI-Error] Failed to get an answer\n", END(1)], ingest_during=ingest_unrated)
    oc = hm["last_scan_outcome"]; u = {x["url"].rstrip("/").split("/")[-1]: x for x in api("GET", "listings")[1]["listings"]}.get("7003")
    check("AI FAILURE: the listing is kept as 'unrated' (not discarded) and the result reports the AI failure", u is not None and u["unrated"] is True and oc["level"] == "warn" and stats_of(hm)["ai_failed"] >= 1 and "AI failures: 1" in oc["detail"])
    # genuine timeout keeps working: no end marker -> timeout, with the partial counters, never 'no results'
    hm = scan2([SEARCH, REVIEW(2)], wait_s=25)
    check("TIMEOUT (genuine): no cycle end -> 'timeout' with partial counters, never 'no matching listings'", hm["last_scan_outcome"]["code"] == "timeout" and stats_of(hm)["reviewed"] == 2 and "Reviewed 2 listings" in hm["last_scan_outcome"]["detail"])
    # edit an existing search (cans -> jacuzzi) and make sure the NEXT scan config uses every changed field
    s, r = api("PUT", "products/p1", {**PROD, "name": "jacuzzi", "search_phrases": ["jacuzzi"], "min_price": 1000, "max_price": 4000, "search_city": "haifa", "radius": 20, "currency": "ILS"}, csrf)
    check("editing a search is accepted", s == 200)
    p1 = [x for x in api("GET", "settings")[1]["settings"]["products"] if x["id"] == "p1"][0]
    check("REGRESSION: every edited field persisted (name, phrase, price range, city, distance, currency)", p1["name"] == "jacuzzi" and p1["search_phrases"] == ["jacuzzi"] and (p1["min_price"], p1["max_price"], p1["search_city"], p1["radius"], p1["currency"]) == (1000, 4000, "haifa", 20, "ILS"))
    cfg = open(tmp + "/aimm/config.toml", encoding="utf-8").read().split("[item.p1]")[1].split("\n[")[0]
    check("REGRESSION: the generated scraper config now searches 'jacuzzi' with the new range, city and distance", 'search_phrases = ["jacuzzi"]' in cfg and "min_price = 1000" in cfg and "max_price = 4000" in cfg and 'search_city = "haifa"' in cfg and "radius = 20" in cfg and "cans" not in cfg)
    check("the edited search can be run right away (scan available)", api("GET", "home")[1]["scan"]["available"] is True)
    s, r = api("PUT", "products/p1", {**PROD, "currency": "ZZZ"}, csrf); check("unsupported currency rejected", s == 400)
    # price-change indicators, currency, delete, cleanup
    ingest0 = lambda body: urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/", data=body.encode(), headers={"Title": "Found 1 new p1 from facebook"}), timeout=10).read()
    ingest0("[Gpu (3)] Dollar spa\n$400, Haifa\nhttps://facebook.com/marketplace/item/9100/\nAI: ok")
    check("a search restricted to ILS skips a listing priced in $", not any(x["url"].rstrip("/").endswith("9100") for x in api("GET", "listings")[1]["listings"]))
    api("PUT", "products/p1", {**PROD, "name": "jacuzzi", "search_phrases": ["jacuzzi"], "currency": ""}, csrf)
    def ingest(body): urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/", data=body.encode(), headers={"Title": "Found 1 new p1 from facebook"}), timeout=10).read()
    U = "https://facebook.com/marketplace/item/9001/"
    ingest(f"[Gpu (4)] Hot tub\n\u20aa3000, Haifa\n{U}\nAI: good"); time.sleep(1.1)
    ingest(f"[Gpu (4)] Hot tub\n\u20aa2500, Haifa\n{U}\nAI: good")
    ingest("[Gpu (3)] Spa\n$400, Haifa\nhttps://facebook.com/marketplace/item/9002/\nAI: ok")
    ingest("[Gpu (3)] Free thing\nHaifa\nhttps://facebook.com/marketplace/item/9003/\nAI: ok")
    L = {x["url"].rstrip("/").split("/")[-1]: x for x in api("GET", "listings")[1]["listings"]}
    check("price drop: previous price and direction come from stored history", L["9001"]["price"] == 2500 and L["9001"]["prev_price"] == 3000 and L["9001"]["price_dir"] == "down")
    check("unchanged / first-seen price shows no change; missing price is safe", L["9002"]["price_dir"] is None and L["9003"]["price"] is None and L["9003"]["price_dir"] is None)
    check("currency is kept per listing ($ stays $, no silent conversion, ₪ -> ILS)", L["9002"]["currency"] == "$" and L["9001"]["currency"] == "ILS" and L["9002"]["price"] == 400)
    ingest(f"[Gpu (4)] Hot tub\n\u20aa2800, Haifa\n{U}\nAI: good")
    L = {x["url"].rstrip("/").split("/")[-1]: x for x in api("GET", "listings")[1]["listings"]}; check("price rise detected against the previous price", L["9001"]["price_dir"] == "up" and L["9001"]["prev_price"] == 2500)
    s, r = api("POST", "listings/action", {"urls": ["https://facebook.com/marketplace/item/9003/"], "action": "delete"}, csrf)
    L = {x["url"].rstrip("/").split("/")[-1] for x in api("GET", "listings")[1]["listings"]}; check("deleting a listing removes it (and does not blacklist it)", s == 200 and r["removed"] == 1 and "9003" not in L and "9001" in L)
    s, r = api("POST", "listings/cleanup", {"kind": "older", "days": 30}, csrf); check("clean-up previews first and deletes nothing without confirm", s == 200 and api("GET", "listings")[1]["listings"] and r.get("removed") is None)
    s, r = api("POST", "listings/cleanup", {"kind": "older", "days": 5}, csrf); check("clean-up rejects an arbitrary age", s == 400)
    api("POST", "listings/action", {"urls": ["https://facebook.com/marketplace/item/9002/"], "action": "save"}, csrf)
    api("DELETE", "products/p1", None, csrf)
    s, r = api("POST", "listings/cleanup", {"kind": "orphans"}, csrf); n0 = r["count"]
    check("listings of a deleted search are cleanup candidates; SAVED listings are exempt", s == 200 and n0 >= 1 and not any("9002" in x["url"] and False for x in []))
    s, r = api("POST", "listings/cleanup", {"kind": "orphans", "confirm": True}, csrf); L = {x["url"].rstrip("/").split("/")[-1] for x in api("GET", "listings")[1]["listings"]}
    check("confirmed clean-up removes orphans but keeps the saved listing", r["removed"] == n0 and "9002" in L and "9001" not in L)
    # ---- manual daily summary
    s, r = api("POST", "summary/send", {}, csrf); check("summary refused while Telegram alerts are off (clear English reason)", s == 409 and "turned off" in r["error"])
    api("PUT", "settings/notifications", {"enabled": True, "chat_id": "123456789"}, csrf)
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/", data="[Gpu (4)] RTX 3070 card\n\u20aa500, Haifa\nhttps://facebook.com/marketplace/item/777/\nAI: good".encode(), headers={"Title": "Found 1 new p1 from facebook"}), timeout=10).read()
    before = len(TG["got"])
    s, r = api("POST", "summary/send", {}, csrf); check("manual summary is queued immediately", s == 200 and r["queued"] >= 1 and r["listings"] >= 1)
    okk = wait(lambda: any("Daily recap" in x and "RTX 3070" in x for x in TG["got"][before:]), 15)
    if not okk: print("   telegram got after request:", [x[:120] for x in TG["got"][before:]], "| outbox:", api("GET", "status")[1]["telegram"]["outbox"])
    check("manual summary reaches Telegram with the real recap content", okk)
    check("summary requests are rate-limited", api("POST", "summary/send", {}, csrf)[0] == 429)
    api("PUT", "settings/notifications", {"enabled": False}, csrf)
    # ---- system + secret hygiene
    s, sysd = api("GET", "system"); check("system page: services and errors, no backups", s == 200 and len(sysd["services"]) >= 4 and "backups" not in sysd)
    allj = json.dumps([api("GET", p)[1] for p in ("status", "settings", "facebook", "system", "config", "me")])
    check("no secrets in any API response (telegram, guard, agent tokens)", "SECRET-TELEGRAM" not in allj and GUARD not in allj and AGENT not in allj and "correct-horse" not in allj)
    out = stop("fwd")
    check("no secrets in forwarder logs", "SECRET-TELEGRAM" not in out and GUARD not in out and AGENT not in out and "correct-horse" not in out)
    start_fwd(); csrf = api("GET", "me")[1]["csrf"]
    # ---- back to Hebrew: backend messages follow the language
    check("switch back to Hebrew", api("POST", "language", {"language": "he"}, csrf)[0] == 200)
    hm = api("GET", "home")[1]
    check("HEBREW: home headline, scan reason and connection title are Hebrew", all(any("\u0590" <= ch <= "\u05ff" for ch in x) for x in (hm["state"]["headline"], hm["connection"]["title"])))
    s, r = api("POST", "summary/send", {}, csrf); check("HEBREW: summary refusal is Hebrew", s == 409 and any("\u0590" <= ch <= "\u05ff" for ch in r["error"]))
    check("HEBREW: the app is served right-to-left again", b'lang="he" dir="rtl"' in http("GET", f"http://127.0.0.1:{UI}/", headers={"Authorization": AUTH}, raw=True)[1])
    # ---- scan activity history (manual scans + monitoring cycles share one record format) and the per-search listing relationship
    hv = api("GET", "home")[1]["scan_history"]
    check("HOME: scan history lists the earlier manual scans, newest first, each with a plain outcome", len(hv) >= 5 and all(x["kind"] == "manual" and x["outcome"]["message"] for x in hv) and hv[0]["at"] >= hv[-1]["at"])
    check("HOME: history rows carry the counters (reviewed / AI / below / passed / forwarded / saved)", all(k in hv[0] for k in ("reviewed", "ai_rated", "below", "passed", "forwarded", "saved")))
    check("HOME: raw log text never leaks into the history payload", "Parsing" not in json.dumps(hv) and "inotify" not in json.dumps(hv).lower() and "offset" not in json.dumps(hv))
    mk = lambda n: api("POST", "products", {**PROD, "name": n, "search_phrases": [n.lower()]}, csrf)
    sA, rA = mk("Search A"); sB, rB = mk("Search B")
    ids = {x["name"]: x["id"] for x in rB["settings"]["products"]}
    api("POST", "scraper", {"action": "start", "confirm": True}, csrf); wait(lambda: sup_state()["scraper_running"], 8); time.sleep(2)
    open(OUT, "a").write(SEARCH + REVIEW(2) + END(0))
    check("MONITORING: a finished automatic cycle appears in the history (kind 'monitor') with its counters", wait(lambda: any(x["kind"] == "monitor" and x["reviewed"] == 2 for x in api("GET", "home")[1]["scan_history"]), 15))
    api("POST", "scraper", {"action": "stop"}, csrf); wait(lambda: not sup_state()["scraper_running"], 8)
    iA, iB = ids["Search A"], ids["Search B"]
    def ing2(pid, num, title): urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{ING}/", data=f"[Gpu (5)] {title}\n\u20aa100, Haifa\nhttps://facebook.com/marketplace/item/{num}/\nAI: great".encode(), headers={"Title": f"Found 1 new {pid} from facebook"}), timeout=10).read()
    ing2(iA, 9501, "Only A"); ing2(iB, 9502, "Only B"); ing2(iA, 9503, "Both"); ing2(iB, 9503, "Both")
    Lx = {x["url"].rstrip("/").split("/")[-1]: x for x in api("GET", "listings")[1]["listings"]}
    check("SEARCH VIEW data: every listing knows which searches matched it", Lx["9501"]["product_ids"] == [iA] and Lx["9502"]["product_ids"] == [iB] and sorted(Lx["9503"]["product_ids"]) == sorted([iA, iB]))
    check("SEARCH VIEW data: a listing matching two searches is ONE listing (not duplicated)", sum(1 for x in api("GET", "listings")[1]["listings"] if x["url"].rstrip("/").endswith("9503")) == 1)
    api("DELETE", f"products/{iB}", None, csrf)
    s_, r_ = api("POST", "listings/cleanup", {"kind": "orphans", "confirm": True}, csrf); Lo = {x["url"].rstrip("/").split("/")[-1]: x for x in api("GET", "listings")[1]["listings"]}
    check("CLEANUP: a listing whose OTHER search still exists is kept (and shows only live searches); one whose only search was deleted is an orphan", "9503" in Lo and Lo["9503"]["product_ids"] == [iA] and "9502" not in Lo and "9501" in Lo)
    sC, rC = mk("Search C"); idC = [x["id"] for x in rC["settings"]["products"] if x["name"] == "Search C"][0]
    check("IDS are never reused: a new search never inherits the listings of a deleted one", idC not in (iA, iB) and int(idC[1:]) > int(iB[1:]))
    # ---- backups are gone
    check("backup/restore endpoint no longer exists", api("POST", "backups/restore", {"name": "x"}, csrf)[0] == 404)
finally:
    if fail and os.environ.get("DEBUG_LOG") and "fwd" in procs:
        try:
            procs["fwd"].send_signal(signal.SIGKILL); procs["fwd"].wait(); print("---- forwarder log"); print(procs["fwd"].stdout.read().decode()[-3500:])
        except Exception: pass
    for n in list(procs):
        try: procs[n].send_signal(signal.SIGKILL); procs[n].wait()
        except Exception: pass
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
