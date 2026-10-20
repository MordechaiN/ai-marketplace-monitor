"""Offline end-to-end test: mock WhatsApp API + real forwarder process. No Facebook, no real WhatsApp.
Run: python3 tests/test_forwarder.py   (needs only the stdlib)"""
import base64, json, os, shutil, signal, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WA_PORT, ING, UI = 18700, 18701, 18702
UIAUTH = "Basic " + base64.b64encode(b"admin:correct-horse-battery").decode()
WA = {"fail": 0, "got": [], "auth_ok": True, "mode": "503"}


TOKEN = "123456:SECRET-TOKEN-abc"
WA = {"fail": 0, "got": [], "auth_ok": True, "mode": "503", "raw": []}


class MockWA(BaseHTTPRequestHandler):
    """Mock Telegram Bot API"""
    def out(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != f"/bot{TOKEN}/sendMessage":
            WA["auth_ok"] = False
            return self.out(401, {"ok": False, "description": "Unauthorized"})
        WA["raw"].append(body)
        if WA["mode"] == "401": return self.out(401, {"ok": False, "description": "Unauthorized"})
        if WA["mode"] == "badparse" and body.get("parse_mode"): return self.out(400, {"ok": False, "description": "Bad Request: can't parse entities"})
        if WA["fail"] > 0:
            WA["fail"] -= 1
            if WA["mode"] == "429": return self.out(429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 1}})
            return self.out(503, {"ok": False})
        WA["got"].append({"phone": body["chat_id"], "message": body["text"], "mode": body.get("parse_mode")})
        self.out(200, {"ok": True, "result": {}})

    def log_message(self, *a): pass


def req(url, method="GET", data=None, headers=None):
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as x: return x.status, x.read()
    except urllib.error.HTTPError as e: return e.code, e.read()


def wait(cond, t=20):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.2)
    return False


class Fwd:
    def __init__(self, tmp, **env):
        self.tmp = tmp
        e = {**os.environ, "TG_API_BASE": f"http://127.0.0.1:{WA_PORT}", "TG_BOT_TOKEN": TOKEN, "TG_CHAT_ID": "-100123",
             "UI_USER": "admin", "UI_PASS": "correct-horse-battery", "DATA_DIR": tmp + "/data", "PORT": str(ING), "UI_PORT": str(UI),
             "MP_LOG": tmp + "/aimm/x.log", "MP_CONFIG": tmp + "/aimm/config.toml", "MP_CACHE": tmp + "/aimm/cache.db",
             "SEND_GAP_S": "0.2", "BACKOFF_BASE_S": "1", "TZ": "Asia/Jerusalem", "NOTIFY_ENABLED": "true", "RECAP_HOUR": "25"}
        e.update(env)
        self.p = subprocess.Popen([sys.executable, "-u", ROOT + "/forwarder/forwarder.py"], env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        assert wait(lambda: req(f"http://127.0.0.1:{ING}/healthz")[0] in (200, 503) if self._up() else False, 10), "forwarder did not start"
    def _up(self):
        try: req(f"http://127.0.0.1:{ING}/healthz"); return True
        except Exception: return False
    def stop(self):
        self.p.send_signal(signal.SIGKILL); self.p.wait()
        return self.p.stdout.read().decode()


BODY = "[Test GPU (5)] RTX 3070\n₪400, Somewhere\nhttps://facebook.com/marketplace/item/%s/?ref=x\nAI: Great card at a good price."
H = {"Title": "Found 1 new gpu from facebook"}
ok = fail = 0


def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


def main():
    tmp = tempfile.mkdtemp(); os.makedirs(tmp + "/aimm")
    srv = HTTPServer(("127.0.0.1", WA_PORT), MockWA); threading.Thread(target=srv.serve_forever, daemon=True).start()
    I = f"http://127.0.0.1:{ING}/"; U = f"http://127.0.0.1:{UI}"
    try:
        # 1. WhatsApp down (503): alert is queued durably, request still answered 200, delivered after retries
        WA["fail"] = 2
        f = Fwd(tmp)
        s, b = req(I, "POST", (BODY % "111").encode(), H)
        check("ingest answers 200 + queued=1 while WhatsApp is failing", s == 200 and json.loads(b)["queued"] == 1)
        check("alert delivered after 503 retries", wait(lambda: len(WA["got"]) == 1, 20))
        check("Telegram payload shape: chat_id, HTML parse mode, bot token in path", WA["got"] and WA["got"][0]["phone"] == "-100123" and WA["got"][0]["mode"] == "HTML" and "RTX 3070" in WA["got"][0]["message"] and WA["auth_ok"])
        # 2. duplicate POST is not queued twice
        s, b = req(I, "POST", (BODY % "111").encode(), H)
        check("duplicate POST not re-queued", json.loads(b)["queued"] == 0)
        # 3. 429 with Retry-After
        WA["fail"], WA["mode"] = 1, "429"
        req(I, "POST", (BODY % "222").encode(), H)
        check("429 retried and delivered once", wait(lambda: len(WA["got"]) == 2, 20) and len(WA["got"]) == 2)
        # 4. persistence: WhatsApp down, kill -9 forwarder, restart, then it is delivered exactly once
        WA["fail"], WA["mode"] = 10**6, "503"
        req(I, "POST", (BODY % "333").encode(), H)
        time.sleep(1.5); f.stop()
        WA["fail"] = 0
        f = Fwd(tmp)
        check("queued alert survives kill -9 and is delivered after restart", wait(lambda: len(WA["got"]) == 3, 20))
        time.sleep(3); check("no duplicate after restart", len(WA["got"]) == 3)
        # 5. listing history is visible, auth enforced
        AP = lambda x: U + "/api/" + x
        def listing_urls(): return [x["url"] for x in json.loads(req(AP("listings"), headers={"Authorization": UIAUTH})[1])["listings"]]
        check("/api/listings without auth -> 401", req(AP("listings"))[0] == 401)
        check("/api/listings wrong password -> 401", req(AP("listings"), headers={"Authorization": "Basic " + base64.b64encode(b"admin:nope").decode()})[0] == 401)
        check("/api/listings with auth returns the stored listing", any(u.endswith("item/111") for u in listing_urls()))
        s, me = req(AP("me"), headers={"Authorization": UIAUTH}); tok = json.loads(me)["csrf"]
        s, pg = req(U + "/listings", headers={"Authorization": UIAUTH})
        check("old /listings URL redirects into the new app (single UI)", s == 200 and b"/static/app.js" in pg)
        check("ingest port does not serve the UI", req(I.rstrip("/") + "/api/listings")[0] == 404)
        check("UI port does not accept alert POST", req(U + "/", "POST", b"x", {"Authorization": UIAUTH})[0] in (403, 404))
        # 6. block hardening
        d = json.dumps({"urls": ["https://facebook.com/marketplace/item/111"], "action": "block", "reason": "wrong"}).encode()
        j = {"Authorization": UIAUTH, "Content-Type": "application/json"}
        check("block with only the old X-Requested-With header -> 403", req(AP("listings/action"), "POST", d, {**j, "X-Requested-With": "mm"})[0] == 403)
        check("block without auth -> 401", req(AP("listings/action"), "POST", d, {"X-CSRF-Token": tok})[0] == 401)
        check("block with foreign Origin -> 403", req(AP("listings/action"), "POST", d, {**j, "X-CSRF-Token": tok, "Origin": "http://evil.example"})[0] == 403)
        check("block with auth + CSRF -> 200", req(AP("listings/action"), "POST", d, {**j, "X-CSRF-Token": tok})[0] == 200)
        n = len(WA["got"]); req(I, "POST", (BODY % "111").encode(), H); time.sleep(1)
        check("blacklisted listing never alerts again", len(WA["got"]) == n)
        # 7. malformed / hostile ingest bodies never crash the process
        for junk in [b"", b"garbage", b"\x00\xff" * 50, ("[x] " * 5000).encode(), b"[T (9)] a\n\n[T (x)] b\nAI:", "[T (5)] ‮ evil\n₪abc\nnot a url\nAI: x".encode()]:
            s, _ = req(I, "POST", junk, H)
            check(f"malformed body {junk[:12]!r} handled (status {s})", s in (200,))
        check("process still healthy", req(I + "healthz")[0] == 200)
        s, _ = req(I, "POST", b"x" * 2_000_000, H)
        check("oversize body rejected 413", s == 413)
        # 8. brute force throttle
        codes = [req(AP("listings"), headers={"Authorization": "Basic " + base64.b64encode(b"admin:x%d" % i).decode()})[0] for i in range(8)]
        check("repeated bad passwords get throttled (429)", 429 in codes)
        f.stop()
        # 9. notifications disabled: queue only, nothing sent
        shutil.rmtree(tmp + "/data"); WA["got"].clear()
        f = Fwd(tmp, NOTIFY_ENABLED="false")
        req(I, "POST", (BODY % "444").encode(), H); time.sleep(3)
        s, b = req(I + "healthz")
        check("NOTIFY_ENABLED=false holds messages", not WA["got"] and json.loads(b)["outbox"]["pending"] == 1)
        f.stop()
        # 10. recap enqueued once at/after RECAP_HOUR, with health footer + queue warning, not duplicated on restart
        WA["got"].clear(); shutil.rmtree(tmp + "/data")
        f = Fwd(tmp, RECAP_HOUR="0")
        req(I, "POST", ("[Test RAM (4)] DDR4 16GB\n₪300, Haifa\nhttps://facebook.com/marketplace/item/555/\nAI: ok" ).encode(), {"Title": "Found 1 new ram from facebook"})
        time.sleep(1)
        # recap fires at first loop tick with an empty day (state initialised as 'today' because hour>=0) -> verify no spurious send loop
        time.sleep(2); f.stop(); n = len(WA["got"])
        f = Fwd(tmp, RECAP_HOUR="0"); time.sleep(3)
        check("restart does not re-send the day's recap", len(WA["got"]) == n)
        out = f.stop()
        check("no secrets in logs", "SECRET-TOKEN" not in out and "correct-horse" not in out)
        check("start log shows Jerusalem tz", "tz Asia/Jerusalem" in out)
        # 11. Telegram specifics
        shutil.rmtree(tmp + "/data"); WA["got"].clear(); WA["raw"].clear()
        f = Fwd(tmp)
        req(I, "POST", "[T <b>&</b> (5)] <script>x</script> *star* _it_\n\u20aa500, A&B\nhttps://facebook.com/marketplace/item/901/\nAI: ok <i>".encode(), H)
        wait(lambda: len(WA["got"]) == 1, 10)
        m = WA["got"][0]["message"] if WA["got"] else ""
        check("untrusted HTML in listing text is escaped for Telegram", "<script>" not in m and "&lt;script&gt;" in m and "<b>&</b>" not in m)
        check("bold markup converted to <b>", "<b>" in m)
        WA["fail"], WA["mode"] = 0, "badparse"; n = len(WA["got"])
        req(I, "POST", (BODY % "902").encode(), H)
        check("400 parse error falls back to plain text and is delivered", wait(lambda: len(WA["got"]) == n + 1, 10) and WA["got"][-1]["mode"] is None)
        WA["mode"] = "401"; n = len(WA["got"])
        req(I, "POST", (BODY % "903").encode(), H); time.sleep(3)
        s, b = req(I + "healthz"); ob = json.loads(b)["outbox"]
        check("bad token/chat (401) keeps message queued with a visible error, not dropped", len(WA["got"]) == n and ob["pending"] == 1 and "401" in (ob["last_error"] or ""))
        WA["mode"] = "ok"
        # 12. AIMM failure marker must never become an immediate alert
        n = len(WA["got"])
        req(I, "POST", "[Gpu (5)] Mystery card\n\u20aa100, X\nhttps://facebook.com/marketplace/item/904/\nAI: Not evaluated by AI".encode(), H); time.sleep(2)
        L = {x["url"].rsplit("/", 1)[-1]: x for x in json.loads(req(AP("listings"), headers={"Authorization": UIAUTH})[1])["listings"]}
        check("'Not evaluated by AI' (fake 5 from AIMM) is held, not alerted, shown as unrated (not as a low rating)", len(WA["got"]) == n and "904" in L and L["904"]["unrated"] is True and L["904"]["score"] == 0 and L["904"]["tone"] == "unrated" and L["904"]["reason"] == "")
        # 13. URL variants (trailing slash / query) must not bypass delete + blacklist
        req(I, "POST", "[T (5)] Slash\n\u20aa100, X\nhttps://facebook.com/marketplace/item/905/?x=1\nAI: ok".encode(), H)
        wait(lambda: any(u.endswith("item/905") for u in listing_urls()), 5)
        tok = json.loads(req(AP("me"), headers={"Authorization": UIAUTH})[1])["csrf"]  # new data dir -> new CSRF key
        d = json.dumps({"urls": ["https://facebook.com/marketplace/item/905"], "action": "block", "reason": "wrong"}).encode()
        s, b = req(AP("listings/action"), "POST", d, {"Authorization": UIAUTH, "Content-Type": "application/json", "X-CSRF-Token": tok})
        check("block matches the stored URL regardless of trailing slash", json.loads(b)["removed"] == 1)
        n = len(WA["got"]); req(I, "POST", "[T (5)] Slash\n\u20aa100, X\nhttps://facebook.com/marketplace/item/905/\nAI: ok".encode(), H); time.sleep(1.5)
        check("blacklist holds against the trailing-slash variant", len(WA["got"]) == n)
        # 14. undo + saved listings + quiet hours (new in the redesign)
        J = lambda body: req(AP("listings/action"), "POST", json.dumps(body).encode(), {"Authorization": UIAUTH, "Content-Type": "application/json", "X-CSRF-Token": tok})
        check("blocked listing is gone from the feed", not any(u.endswith("item/905") for u in listing_urls()))
        s, b = J({"urls": ["https://facebook.com/marketplace/item/905"], "action": "unblock"})
        check("undo (unblock) restores the listing to the feed", json.loads(b)["restored"] == 1 and any(u.endswith("item/905") for u in listing_urls()))
        n = len(WA["got"]); req(I, "POST", "[T (5)] Slash\n\u20aa100, X\nhttps://facebook.com/marketplace/item/905/\nAI: ok".encode(), H); time.sleep(1.5)
        check("after undo the listing may alert again (it is no longer blacklisted)", len(WA["got"]) >= n)
        J({"urls": ["https://facebook.com/marketplace/item/905/"], "action": "save"})
        check("save marks the listing as saved", [x for x in json.loads(req(AP("listings"), headers={"Authorization": UIAUTH})[1])["listings"] if x["url"].endswith("905")][0]["saved"] is True)
        check("saving works with URL variants and unsave reverses it", J({"urls": ["https://facebook.com/marketplace/item/905"], "action": "unsave"})[0] == 200 and not [x for x in json.loads(req(AP("listings"), headers={"Authorization": UIAUTH})[1])["listings"] if x["url"].endswith("905")][0]["saved"])
        check("unknown action -> 400, no urls -> 400", J({"urls": ["https://facebook.com/x"], "action": "explode"})[0] == 400 and J({"urls": [], "action": "save"})[0] == 400)
        f.stop()
        # 15. quiet hours hold ALERTS but not system messages
        shutil.rmtree(tmp + "/data"); WA["got"].clear(); os.makedirs(tmp + "/data/panel")
        qs = {"notifications": {"enabled": True, "chat_id": "-100123", "quiet_enabled": True, "quiet_from": "00:00", "quiet_to": "23:59", "recap_time": "21:00"}}
        open(tmp + "/data/panel/settings.json", "w").write(json.dumps(qs))
        f = Fwd(tmp)
        req(I, "POST", (BODY % "950").encode(), H); time.sleep(3)
        s, b = req(I + "healthz")
        check("quiet hours: a listing alert is held (not sent), still safely queued", not WA["got"] and json.loads(b)["outbox"]["pending"] == 1)
        f.stop()
    finally:
        srv.shutdown(); shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)


main()
