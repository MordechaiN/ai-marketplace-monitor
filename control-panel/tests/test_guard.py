"""Offline test of gemini-guard against a mock Gemini API (error paths). Run: python3 tests/test_guard.py"""
import json, os, signal, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK, G = 18710, 18711
TOKEN = "t" * 24
M = {"mode": "ok", "hits": 0, "paths": []}
GOOD = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Rating[4]: good match"}]}}]}


class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        M["hits"] += 1; M["paths"].append(self.path)
        m = M["mode"]
        if m == "timeout": time.sleep(3)
        if m == "429min" and M["hits"] == 1: return self.out(429, {"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"retryDelay": "1s"}]}})
        if m == "429day": return self.out(429, {"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})
        if m == "403": return self.out(403, {"error": {"status": "PERMISSION_DENIED"}})
        if m == "trunc": return self.out(200, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "partial"}]}}]})
        if m == "garbage": return self.out(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "I think it is nice."}]}}]})
        if m == "blocked": return self.out(200, {"promptFeedback": {"blockReason": "SAFETY"}})
        self.out(200, GOOD)
    def out(self, c, o):
        b = json.dumps(o).encode(); self.send_response(c); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass


def ask(tok=TOKEN, model="gpt-4o"):
    body = json.dumps({"model": model, "messages": [{"role": "system", "content": "SECRETPROMPT sys"}, {"role": "user", "content": "SECRETPROMPT listing"}]}).encode()
    r = urllib.request.Request(f"http://127.0.0.1:{G}/v1/chat/completions", data=body, headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as x: return x.status, json.loads(x.read()), {}
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read()), dict(e.headers)


def health(): return json.load(urllib.request.urlopen(f"http://127.0.0.1:{G}/healthz", timeout=5))


ok = fail = 0
def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)


def start(tmp, **env):
    e = {**os.environ, "GEMINI_API_BASE": f"http://127.0.0.1:{MOCK}", "GEMINI_API_KEY": "KEYKEYKEY-secret", "GUARD_TOKEN": TOKEN, "PORT": str(G),
         "GUARD_STATE": tmp + "/st.json", "GEMINI_RPM": "6000", "GEMINI_TIMEOUT_S": "1", "GEMINI_MODEL": "pinned-model"}
    e.update(env)
    p = subprocess.Popen([sys.executable, "-u", ROOT + "/gemini-guard/guard.py"], env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(50):
        try: health(); return p
        except Exception: time.sleep(0.1)
    raise SystemExit("guard did not start")


def stop(p): p.send_signal(signal.SIGKILL); p.wait(); return p.stdout.read().decode()


def main():
    tmp = tempfile.mkdtemp()
    srv = HTTPServer(("127.0.0.1", MOCK), Mock); threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = start(tmp)
        s, d, _ = ask(); check("valid answer is returned in OpenAI shape, verdict normalised to 'Rating N: ...'", s == 200 and d["choices"][0]["message"]["content"] == "Rating 4: good match")
        check("model is pinned, client model ignored", M["paths"][-1] == "/models/pinned-model:generateContent")
        check("wrong bearer -> 401, no upstream call", (lambda h: ask("x" * 24)[0] == 401 and M["hits"] == h)(M["hits"]))
        M["mode"], M["hits"] = "429min", 0
        t = time.time(); s, d, _ = ask(); check("per-minute 429 waits retryDelay then succeeds", s == 200 and M["hits"] == 2 and time.time() - t < 10)
        M["mode"], M["hits"] = "trunc", 0
        s, d, _ = ask(); check("truncated (MAX_TOKENS) answer never accepted -> 502 after bounded retries", s == 502 and M["hits"] == 3)
        M["mode"], M["hits"] = "garbage", 0
        s, d, _ = ask(); check("answer without Rating -> 502, no invented rating", s == 502 and "choices" not in d)
        M["mode"], M["hits"] = "blocked", 0
        s, d, _ = ask(); check("safety-blocked response -> 502", s == 502)
        M["mode"], M["hits"] = "timeout", 0
        s, d, _ = ask(); check("upstream timeout -> 502 after bounded retries", s == 502 and M["hits"] == 3 and health()["timeouts"] >= 3)
        M["mode"], M["hits"] = "429day", 0
        s, d, h = ask(); check("daily quota 429 -> 429 + Retry-After + breaker open", s == 429 and int(h.get("Retry-After", 0)) > 3600 and health()["breaker_open"])
        n = M["hits"]; s, d, _ = ask(); check("breaker open: fast 503, zero upstream calls", s == 503 and M["hits"] == n)
        out = stop(p)
        p = start(tmp); M["mode"] = "ok"; n = M["hits"]; s, _, _ = ask()
        check("after restart the breaker is still open (state persisted)", s == 503 and M["hits"] == n)
        out += stop(p)
        os.remove(tmp + "/st.json")
        p = start(tmp); M["mode"], M["hits"] = "403", 0
        s, _, _ = ask(); n = M["hits"]; s2, _, _ = ask()
        check("403 (bad key) opens a 10 min breaker, no hammering", s == 502 and s2 == 503 and M["hits"] == n == 1)
        out += stop(p); os.remove(tmp + "/st.json")
        p = start(tmp, GEMINI_RPD="2"); M["mode"], M["hits"] = "ok", 0
        ask(); ask(); s, _, h = ask()
        check("local daily cap GEMINI_RPD enforced before Google's quota", s == 429 and M["hits"] == 2)
        out += stop(p)
        check("no API key / prompt text in guard logs", "KEYKEYKEY" not in out and "SECRETPROMPT" not in out)
    finally:
        srv.shutdown()
    print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)


main()
