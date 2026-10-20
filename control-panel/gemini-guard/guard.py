"""OpenAI-compatible guard in front of the Google Gemini API (free tier), stdlib only.

AIMM talks to this service as if it were an OpenAI endpoint. The guard:
  * pins ONE model (GEMINI_MODEL); the model named by the client is ignored, there is no fallback model
  * paces requests (GEMINI_RPM) and caps daily calls (GEMINI_RPD, Pacific-time day = Gemini's quota day)
  * on 429 reads Google's retry hint; per-minute limits wait briefly, per-day exhaustion opens a circuit
    breaker until the next quota reset (no further upstream calls, fast 503 to the client)
  * validates answers (finishReason STOP, non-empty, contains 'Rating[N]'); never invents a rating
  * never logs prompts, answers or the API key
"""
import json, os, re, sys, threading, time, urllib.error, urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
API = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
RPM = float(os.environ.get("GEMINI_RPM", "8"))
RPD = int(os.environ.get("GEMINI_RPD", "400"))
UPSTREAM_TIMEOUT = float(os.environ.get("GEMINI_TIMEOUT_S", "45"))
MAX_WAIT = float(os.environ.get("GEMINI_MAX_WAIT_S", "60"))      # longest we hold a request for a per-minute limit
MAX_OUT = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "1024"))
STATE = os.environ.get("GUARD_STATE", "/data/guard-state.json")
RATING_RE = re.compile(r"Rating[^1-5]*[1-5]", re.S)
lock = threading.Lock()  # serialises upstream calls: free-tier friendly and keeps counters exact


def secret(name):
    p = os.environ.get(name + "_FILE")
    if p:
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return os.environ.get(name, "")


KEY, TOKEN = secret("GEMINI_API_KEY"), secret("GUARD_TOKEN")
if not KEY or len(TOKEN) < 16:
    sys.exit("GEMINI_API_KEY and GUARD_TOKEN (min 16 chars) are required")


def quota_day():
    return datetime.now(PACIFIC).strftime("%Y-%m-%d")


def next_reset():
    n = datetime.now(PACIFIC)
    return (n + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0).timestamp()


st = {"day": quota_day(), "calls": 0, "ok": 0, "invalid": 0, "rate_limited": 0, "timeouts": 0,
      "breaker_until": 0.0, "breaker_reason": None, "last_error": None, "last_call": 0.0,
      "busy_total": 0.0, "wait_total": 0.0}  # cumulative seconds (never reset by the day roll): upstream calls / pacing + 429 + backoff sleeps
try:
    with open(STATE) as f:
        old = json.load(f)
    if old.get("day") == st["day"]:
        st.update(old)
except Exception:
    pass


def save():
    try:
        with open(STATE + ".tmp", "w") as f:
            json.dump(st, f)
        os.replace(STATE + ".tmp", STATE)
    except Exception:
        pass


def roll_day():
    if st["day"] != quota_day():
        st.update(day=quota_day(), calls=0, ok=0, invalid=0, rate_limited=0, timeouts=0, breaker_until=0.0, breaker_reason=None)


class Refuse(Exception):
    def __init__(self, code, msg, retry_after=0):
        self.code, self.msg, self.retry_after = code, msg, retry_after


def to_gemini(body):
    system, contents = [], []
    for m in body.get("messages", []):
        text = m.get("content")
        if isinstance(text, list):
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        if not isinstance(text, str):
            raise Refuse(400, "unsupported message content")
        if m.get("role") == "system":
            system.append(text)
        else:
            contents.append({"role": "model" if m.get("role") == "assistant" else "user", "parts": [{"text": text}]})
    if not contents:
        raise Refuse(400, "no messages")
    out = {"contents": contents, "generationConfig": {"temperature": 0, "maxOutputTokens": MAX_OUT}}
    if system:
        out["systemInstruction"] = {"parts": [{"text": "\n".join(system)}]}
    return out


def classify_429(err_body):
    """-> (daily_exhausted, retry_seconds)"""
    txt = json.dumps(err_body)
    daily = bool(re.search(r"PerDay|per_day|RequestsPerDay|daily", txt, re.I))
    m = re.search(r'"retryDelay":\s*"([\d.]+)s"', txt) or re.search(r"retry in ([\d.]+)s", txt, re.I)
    return daily, (float(m.group(1)) if m else 30.0)


def call_upstream(payload):
    """One HTTP call. Returns (status, json_or_None, retry_after)."""
    req = urllib.request.Request(f"{API}/models/{MODEL}:generateContent", data=json.dumps(payload).encode(),
                                 headers={"x-goog-api-key": KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as r:
            return r.status, json.loads(r.read()), 0
    except urllib.error.HTTPError as e:
        try:
            b = json.loads(e.read())
        except Exception:
            b = {}
        return e.code, b, 0
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return 0, {"error": {"message": type(e).__name__}}, 0


def extract(d):
    """Validated answer text or None."""
    try:
        c = d["candidates"][0]
        if c.get("finishReason") != "STOP":
            return None
        text = "".join(p.get("text", "") for p in c["content"]["parts"] if not p.get("thought")).strip()
    except (KeyError, IndexError, TypeError):
        return None
    return canonical(text) if text else None


# AIMM 0.10.2 takes the LAST line matching r".*Rating[^1-5]*([1-5])" and appends every later line to the comment, so "Rating: N/A (price
# 150)" would parse as 1, and a stray "Rating" in the reasoning after the verdict would override it. Only a well-formed verdict line counts;
# everything after it is dropped, and the word is neutralised in the reasoning above it. No such line -> invalid (retried, never invented).
VERDICT_RE = re.compile(r"^[\s*_#>\-\"'`]*Rating[\s*_\"'`\[(]*:?[\s*_\"'`\[(]*([1-5])(?![\d.,/])[\s*_\"'`\])]*[:\-–—.]?\s*(.*)$")


def canonical(text):
    lines = text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        m = VERDICT_RE.match(lines[i].strip())
        if m:
            summary = m.group(2).strip().strip("*_\"'` ")
            above = [re.sub(r"(?i)rating", "score", ln) for ln in lines[:i]]
            return "\n".join(above + [f"Rating {m.group(1)}: {summary}"]).strip()
    return None


def evaluate(body):
    payload = to_gemini(body)
    deadline = time.time() + MAX_WAIT + UPSTREAM_TIMEOUT * 2
    with lock:
        for attempt in range(3):
            roll_day()
            if st["breaker_until"] > time.time():
                raise Refuse(503, f"circuit open: {st['breaker_reason']}", int(st["breaker_until"] - time.time()))
            if st["calls"] >= RPD:
                st.update(breaker_until=next_reset(), breaker_reason="local daily cap GEMINI_RPD reached")
                save()
                raise Refuse(429, "local daily cap reached", int(next_reset() - time.time()))
            gap = 60.0 / RPM - (time.time() - st["last_call"])
            if gap > 0:
                time.sleep(gap)
                st["wait_total"] += gap
            st["last_call"] = time.time()
            st["calls"] += 1
            t0 = time.time()
            status, d, _ = call_upstream(payload)
            st["busy_total"] += time.time() - t0
            if status == 200:
                text = extract(d)
                if text:
                    st["ok"] += 1
                    save()
                    return text
                st["invalid"] += 1
                st["last_error"] = "invalid model response (empty, truncated, blocked or no Rating)"
                save()
                continue  # one more try; temperature 0 usually repeats, so we give up after 3 and never invent a rating
            err = (d or {}).get("error", {})
            st["last_error"] = f"http {status} {err.get('status', '')}".strip()
            if status == 429:
                st["rate_limited"] += 1
                daily, wait = classify_429(d)
                if daily:
                    st.update(breaker_until=next_reset(), breaker_reason="Gemini daily quota exhausted")
                    save()
                    raise Refuse(429, "Gemini daily quota exhausted", int(next_reset() - time.time()))
                if wait > MAX_WAIT or time.time() + wait > deadline:
                    save()
                    raise Refuse(429, "rate limited", int(wait))
                time.sleep(wait + 1)
                st["wait_total"] += wait + 1
                continue
            if status in (400, 401, 403):  # bad key / model not allowed / API disabled: do not hammer
                st.update(breaker_until=time.time() + 600, breaker_reason=f"upstream rejected request ({status})")
                save()
                raise Refuse(502, f"upstream rejected request ({status})", 600)
            if status == 0:
                st["timeouts"] += 1
            if status == 404:
                st.update(breaker_until=time.time() + 600, breaker_reason="model not found (retired?)")
                save()
                raise Refuse(502, "model not found", 600)
            time.sleep(min(5 * 2 ** attempt, 20))  # timeouts / 5xx
            st["wait_total"] += min(5 * 2 ** attempt, 20)
        save()
        raise Refuse(502, "no valid answer from Gemini")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def send_json(self, code, obj, extra=()):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/") == "/healthz":
            roll_day()
            br = st["breaker_until"] > time.time()
            return self.send_json(200, {"model": MODEL, "day": st["day"], "calls": st["calls"], "daily_cap": RPD, "ok": st["ok"],
                                        "invalid": st["invalid"], "rate_limited": st["rate_limited"], "timeouts": st["timeouts"],
                                        "breaker_open": br, "breaker_reason": st["breaker_reason"] if br else None,
                                        "breaker_until": datetime.fromtimestamp(st["breaker_until"]).isoformat(timespec="seconds") if br else None,
                                        "last_error": st["last_error"], "busy_total": round(st["busy_total"], 1), "wait_total": round(st["wait_total"], 1)})
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self.send_json(404, {"error": "not found"})
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not got or got != TOKEN:
            return self.send_json(401, {"error": {"message": "invalid api key", "type": "invalid_request_error"}})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n < 0 or n > 200_000:
                return self.send_json(413, {"error": {"message": "too large"}})
            body = json.loads(self.rfile.read(n))
            text = evaluate(body)
        except Refuse as r:
            extra = [("Retry-After", str(max(r.retry_after, 1)))] if r.retry_after else []
            print(f"refuse {r.code}: {r.msg}", flush=True)
            return self.send_json(r.code, {"error": {"message": r.msg, "type": "upstream_error"}}, extra)
        except (ValueError, TypeError):
            return self.send_json(400, {"error": {"message": "bad request"}})
        except Exception as e:
            print("guard error:", type(e).__name__, flush=True)
            return self.send_json(500, {"error": {"message": "internal error"}})
        self.send_json(200, {"id": "chatcmpl-guard", "object": "chat.completion", "created": int(time.time()), "model": MODEL,
                             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                             "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"gemini-guard model={MODEL} rpm={RPM} rpd={RPD} quota_day={st['day']} calls_today={st['calls']}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8090"))), H).serve_forever()
