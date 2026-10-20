"""Control agent inside the scraper container. Stdlib only. Internal network only (no published port), Bearer token.
It is the ONLY way the panel starts/stops the scraper: no Docker socket, no Portainer token anywhere near a web app.
Responses never contain cookie values, tokens or config secrets."""
import hmac, json, os, re, signal, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("AGENT_TOKEN", "")
SESSION_DIR = os.environ.get("AIMM_SESSION_DIR", "/session")
STATE = os.path.join(SESSION_DIR, "fb-state.json")
STATUS = os.path.join(SESSION_DIR, "status.json")
HELPER_STATUS = os.path.join(SESSION_DIR, "helper.json")
SUPERVISORCTL = os.environ.get("SUPERVISORCTL", "/usr/bin/supervisorctl")
PYTHON = os.environ.get("AGENT_PYTHON", "/usr/local/bin/python")
HELPER_PY = os.environ.get("HELPER_PY", "/extras/login_helper.py")
if len(TOKEN) < 16:
    sys.exit("AGENT_TOKEN (min 16 chars) required")
lock = threading.Lock()
helper = {"proc": None, "started": 0}
# AIMM's supervisor stdout log: the authoritative record of a run (no inotify noise, one file per supervisor rotation).
AIMM_STDOUT = os.environ.get("AIMM_STDOUT_LOG", "/var/log/supervisor/aimm.log")
LOG_MAX = 2_000_000


def read_log(offset, inode):
    """Text of AIMM's stdout log since `offset` (a position returned earlier together with its `inode`); offset None only reports size/inode.
    If the file was rotated (different inode, or smaller than the offset) the offset cannot be trusted: read the current file from its start and say so."""
    try:
        with open(AIMM_STDOUT, "rb") as f:
            st = os.fstat(f.fileno())
            if offset is None:
                return {"size": st.st_size, "inode": st.st_ino, "mtime": int(st.st_mtime), "rotated": False, "truncated": False, "text": ""}
            rotated = (bool(inode) and inode != st.st_ino) or offset > st.st_size
            start = 0 if rotated else offset
            truncated = st.st_size - start > LOG_MAX
            if truncated:
                start = st.st_size - LOG_MAX
            f.seek(start)
            return {"size": st.st_size, "inode": st.st_ino, "mtime": int(st.st_mtime), "rotated": rotated, "truncated": truncated, "text": f.read(LOG_MAX).decode("utf-8", "replace")}
    except OSError:
        return {"size": 0, "inode": 0, "rotated": False, "truncated": False, "text": "", "missing": True}


def sup(*args, timeout=20):
    r = subprocess.run([SUPERVISORCTL, *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def programs():
    _, out = sup("status")
    res = {}
    for line in out.splitlines():
        m = re.match(r"(\S+)\s+(RUNNING|STOPPED|STARTING|BACKOFF|STOPPING|EXITED|FATAL|UNKNOWN)\s*(.*)", line)
        if m:
            up = re.search(r"uptime (\S+)", m.group(3))
            res[m.group(1)] = {"state": m.group(2), "uptime": up.group(1) if up else None}
    return res


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def helper_alive():
    p = helper["proc"]
    if p and p.poll() is None:
        return True
    helper["proc"] = None
    return False


def helper_ready():
    """The window exists AND has loaded (the helper writes state 'open' only after Chromium is up and Facebook is loading)."""
    return helper_alive() and (read_json(HELPER_STATUS) or {}).get("state") == "open" and (read_json(HELPER_STATUS) or {}).get("at", 0) >= helper["started"]


def session_info():
    try:
        st = os.stat(STATE)
        names = {c.get("name") for c in (read_json(STATE) or {}).get("cookies", [])}
        return {"exists": True, "saved_at": int(st.st_mtime), "has_login_cookies": {"c_user", "xs"} <= names, "mode": oct(st.st_mode & 0o777)}
    except OSError:
        return {"exists": False}


def status():
    pr = programs()
    return {"programs": pr, "scraper_running": pr.get("aimm", {}).get("state") in ("RUNNING", "STARTING"),
            "helper": {"open": helper_alive(), "ready": helper_ready(), "last": read_json(HELPER_STATUS), "started": helper["started"]},
            "session": session_info(), "login_status": read_json(STATUS)}


def validate(toml_text):
    """AIMM's own loader is the authority on what is a valid configuration."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.toml")
        with open(p, "w", encoding="utf-8") as f:
            f.write(toml_text)
        code = "import sys;from pathlib import Path;from ai_marketplace_monitor.config import Config\ntry:\n Config([Path(sys.argv[1])])\nexcept Exception as e:\n print(str(e));sys.exit(2)"
        r = subprocess.run([PYTHON, "-c", code, p], capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        return {"valid": True, "error": None}
    if r.returncode != 2:  # not "config rejected" but "validator itself failed" (e.g. AIMM not importable): never block saves on that
        raise RuntimeError("validator unavailable")
    msg = re.sub(r"\[/?[a-z ]+\]", "", (r.stdout or r.stderr).strip())[:600]
    return {"valid": False, "error": msg}


def helper_open():
    with lock:
        if status()["scraper_running"]:
            return 409, {"error": "scraper_running"}
        if helper_alive():
            return 200, {"open": True}
        try:
            os.remove(HELPER_STATUS)
        except OSError:
            pass
        sup("start", "novnc")
        helper["proc"] = subprocess.Popen([PYTHON, "-u", HELPER_PY, "open"], env={**os.environ, "DISPLAY": ":99", "AIMM_SESSION_FILE": STATE, "AIMM_SESSION_STATUS": STATUS, "AIMM_HELPER_STATUS": HELPER_STATUS})
        helper["started"] = int(time.time())
        return 200, {"open": True}


def helper_close(save):
    with lock:
        p = helper["proc"]
        result = None
        if p and p.poll() is None:
            if save:
                if (read_json(HELPER_STATUS) or {}).get("state") != "open":
                    return 409, {"error": "not_ready", "open": True}
                p.send_signal(signal.SIGUSR1)
                end = time.time() + 20
                while time.time() < end and p.poll() is None:
                    h = read_json(HELPER_STATUS) or {}
                    if h.get("state") in ("saved", "not_logged_in", "error") and h.get("at", 0) >= helper["started"]:
                        result = h
                        break
                    time.sleep(0.4)
            if result is None or result.get("state") != "not_logged_in":
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(15)
                except subprocess.TimeoutExpired:
                    p.kill()
            helper["proc"] = None if p.poll() is not None else p
        if not helper_alive():
            sup("stop", "novnc")
        return 200, {"open": helper_alive(), "result": result}


def check_session():
    with lock:
        if status()["scraper_running"] or helper_alive():
            return 409, {"error": "busy"}
        if not os.path.exists(STATE):
            return 200, {"state": "no_session"}
        r = subprocess.run([PYTHON, "-u", HELPER_PY, "check"], capture_output=True, text=True, timeout=120,
                           env={**os.environ, "DISPLAY": ":99", "AIMM_SESSION_FILE": STATE, "AIMM_SESSION_STATUS": STATUS, "AIMM_HELPER_STATUS": HELPER_STATUS})
        return 200, {"state": (read_json(STATUS) or {}).get("state", "unknown"), "exit": r.returncode}


CHECK_PY = os.environ.get("LISTING_CHECK_PY", "/extras/listing_check.py")


def check_listings(req):
    """Bounded availability check (listing_check.py). One browser per session at a time: refused while a manual scan or the login helper
    runs. While monitoring, the panel calls this only right after a finished cycle (between_cycles), when AIMM's browser sits idle."""
    urls = [u for u in req.get("urls", []) if isinstance(u, str)][:10]
    if not urls:
        return 200, {"results": {}}
    with lock:
        if helper_alive() or (status()["scraper_running"] and not req.get("between_cycles")):
            return 409, {"error": "busy"}
        if not os.path.exists(STATE):
            return 409, {"error": "no_session"}
        r = subprocess.run([PYTHON, "-u", CHECK_PY, "check"], input=json.dumps({"urls": urls}), capture_output=True, text=True, timeout=60 + 15 * len(urls),
                           env={**os.environ, "DISPLAY": ":99", "AIMM_SESSION_FILE": STATE, "AIMM_SESSION_STATUS": os.path.join(SESSION_DIR, "check-status.json")})
        try:
            return 200, json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return 502, {"error": "checker_failed", "exit": r.returncode}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def out(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authed(self):
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return hmac.compare_digest(got.encode(), TOKEN.encode())

    def body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(n) if 0 < n <= 500_000 else b""

    def do_GET(self):
        if not self.authed():
            return self.out(401, {"error": "unauthorised"})
        if self.path == "/status":
            return self.out(200, status())
        self.out(404, {"error": "not found"})

    def do_POST(self):
        if not self.authed():
            return self.out(401, {"error": "unauthorised"})
        raw = self.body()
        try:
            if self.path == "/scraper/start":
                with lock:
                    if helper_alive():
                        return self.out(409, {"error": "helper_open"})
                    # supervisorctl waits for startsecs (15 s) before returning; do not hold the panel's request (or its state) hostage
                    subprocess.Popen([SUPERVISORCTL, "start", "aimm"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return self.out(200, {"result": "starting"})
            if self.path == "/scraper/stop":
                return self.out(200, {"result": sup("stop", "aimm", timeout=60)[1][:200]})
            if self.path == "/scraper/log":
                req = json.loads(raw or b"{}")
                off = req.get("offset")
                return self.out(200, read_log(int(off) if off is not None else None, int(req.get("inode") or 0)))
            if self.path == "/validate":
                return self.out(200, validate(json.loads(raw)["toml"]))
            if self.path == "/helper/open":
                return self.out(*helper_open())
            if self.path == "/helper/save":
                return self.out(*helper_close(True))
            if self.path == "/helper/close":
                return self.out(*helper_close(False))
            if self.path == "/listings/check":
                return self.out(*check_listings(json.loads(raw or b"{}")))
            if self.path == "/session/check":
                return self.out(*check_session())
            if self.path == "/session/delete":
                for f in (STATE, STATUS):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                return self.out(200, {"deleted": True})
        except (ValueError, KeyError):
            return self.out(400, {"error": "bad request"})
        except subprocess.TimeoutExpired:
            return self.out(504, {"error": "timeout"})
        except Exception as e:
            print("agent error:", type(e).__name__, flush=True)
            return self.out(500, {"error": "internal"})
        self.out(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    os.makedirs(SESSION_DIR, mode=0o700, exist_ok=True)
    os.chmod(SESSION_DIR, 0o700)
    print("scraper agent listening", flush=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("AGENT_PORT", "8470"))), H).serve_forever()
