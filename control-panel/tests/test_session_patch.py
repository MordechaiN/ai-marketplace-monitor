"""Session persistence patch against a REAL Chromium (headless shell) and a local fake 'facebook'. Skipped if Playwright is missing.
Run: AIMM_LIB=<dir with playwright> python3 tests/test_session_patch.py"""
import json, os, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.environ.get("AIMM_LIB"):
    print("SKIP: AIMM_LIB not set"); sys.exit(0)
tmp = tempfile.mkdtemp(); PORT = 18740; ok = fail = 0


def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html")
        if self.path.startswith("/home"):
            self.send_header("Set-Cookie", "c_user=12345; Path=/"); self.send_header("Set-Cookie", "xs=SECRET; Path=/")
            body = '<html lang="he"><body><h1>marketplace</h1></body></html>'
        elif self.path.startswith("/loginstart"):
            body = "<h1>redirecting...</h1>"  # a login URL WITHOUT a login form: what AIMM's first navigation looks like before Facebook redirects
        elif self.path.startswith("/login"):
            body = '<form><input name="email"><input name="pass" type="password"></form>'
        elif self.path.startswith("/checkpoint"):
            body = "<h1>checkpoint</h1>"
        else:
            body = "<h1>anon</h1>"
        self.end_headers(); self.wfile.write(body.encode())
    def log_message(self, *a): pass


threading.Thread(target=HTTPServer(("127.0.0.1", PORT), Site).serve_forever, daemon=True).start()
env = {**os.environ, "PYTHONPATH": ROOT + "/scraper-extras:" + os.environ["AIMM_LIB"], "AIMM_SESSION_FILE": tmp + "/fb-state.json", "AIMM_SESSION_STATUS": tmp + "/status.json",
       "AIMM_SESSION_HOST": "127.0.0.1", "AIMM_SESSION_COOKIE_URL": f"http://127.0.0.1:{PORT}"}


def run(code):
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    return r.stdout.strip(), r.stderr[-400:]


def status(): return json.load(open(tmp + "/status.json"))


# NOTE: python imports sitecustomize automatically because PYTHONPATH has scraper-extras first, exactly like inside the AIMM process
base = f"http://127.0.0.1:{PORT}"
pre = "from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n b=p.chromium.launch(headless=True)\n c=b.new_context()\n pg=c.new_page()\n"
out, err = run(pre + f" pg.goto('{base}/login')\n b.close()")
check(f"login page recorded as login_required ({err[-80:]})", os.path.exists(tmp + "/status.json") and status()["state"] == "login_required")
check("no session file written while logged out", not os.path.exists(tmp + "/fb-state.json"))
run(pre + f" pg.goto('{base}/checkpoint')\n b.close()"); check("checkpoint page recorded as checkpoint", status()["state"] == "checkpoint")
run(pre + f" pg.goto('{base}/other')\n b.close()"); check("no login cookies and no form -> anonymous", status()["state"] == "anonymous")
run(pre + f" pg.goto('{base}/home')\n b.close()")
check("logged-in page recorded as logged_in", status()["state"] == "logged_in")
check("account language recorded from the page (<html lang>)", status().get("lang") == "he")
check("session file saved with mode 600", os.path.exists(tmp + "/fb-state.json") and oct(os.stat(tmp + "/fb-state.json").st_mode & 0o777) == "0o600")
check("status file never contains cookie values or query strings", "SECRET" not in open(tmp + "/status.json").read() and "?" not in open(tmp + "/status.json").read())
out, err = run(pre + f" print(sorted(x['name'] for x in c.cookies('{base}')))\n b.close()")
check(f"NEW browser context starts with the saved session (restart persistence) -> {out}", out == "['c_user', 'xs']")
before = open(tmp + "/fb-state.json").read()
t0 = __import__("time").time()
run(pre + f" pg.goto('{base}/loginstart')\n b.close()")
check("REGRESSION: AIMM's first visit to a login URL with a valid saved session is 'checking', NOT 'login_required'", status()["state"] == "checking")
run(pre + f" pg.goto('{base}/login')\n b.close()")
check("login form shown although a session was restored -> 'session_expired'", status()["state"] == "session_expired")
check("a later logged-out page does not erase the saved session", open(tmp + "/fb-state.json").read() == before)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
