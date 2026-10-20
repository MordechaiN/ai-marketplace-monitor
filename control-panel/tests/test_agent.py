"""scraper-extras/agent.py with a fake supervisorctl and fake login helper. Run: python3 tests/test_agent.py"""
import json, os, shutil, signal, subprocess, sys, tempfile, time, urllib.error, urllib.request
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(); sess = tmp + "/session"; sup = tmp + "/sup"; os.makedirs(sup)
TOK = "a" * 24; PORT = 18730; ok = fail = 0


def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)


def call(path, body=None, tok=TOK):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else (b"" if path != "/status" else None),
                               method="GET" if path == "/status" else "POST", headers={"Authorization": f"Bearer {tok}"})
    try:
        with urllib.request.urlopen(r, timeout=60) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")


def wait_for(c, t=8):
    end = time.time() + t
    while time.time() < end:
        if c(): return True
        time.sleep(0.1)
    return False


def start(logged_in="1"):
    open(sup + "/start_delay", "w").write("3")  # supervisor needs 3 s to reach RUNNING (the real one needs 15 s)
    env = {**os.environ, "AGENT_TOKEN": TOK, "AGENT_PORT": str(PORT), "AIMM_SESSION_DIR": sess, "SUPERVISORCTL": ROOT + "/tests/fakes/supervisorctl", "FAKE_SUP_DIR": sup,
           "AGENT_PYTHON": sys.executable, "HELPER_PY": ROOT + "/tests/fakes/helper.py", "FAKE_LOGGED_IN": logged_in, "AIMM_STDOUT_LOG": tmp + "/aimm.out"}
    p = subprocess.Popen([sys.executable, "-u", ROOT + "/scraper-extras/agent.py"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(50):
        try: call("/status"); return p
        except Exception: time.sleep(0.1)
    raise SystemExit("agent did not start")


p = start("0")
check("no/wrong token -> 401", call("/status", tok="x" * 24)[0] == 401)
s, st = call("/status"); check("status: scraper stopped by default", s == 200 and st["scraper_running"] is False and not st["session"]["exists"])
t0 = time.time(); r0 = call("/scraper/start", {})[0]; dt = time.time() - t0
check(f"agent start returns immediately even though supervisor takes 3 s to reach RUNNING ({dt:.2f}s)", r0 == 200 and dt < 1.5)
wait_for(lambda: call("/status")[1]["scraper_running"], 3)
check("STARTING already counts as running (so nothing may kill it)", call("/status")[1]["scraper_running"] and call("/status")[1]["programs"]["aimm"]["state"] == "STARTING")
check("then it reaches RUNNING", wait_for(lambda: call("/status")[1]["programs"]["aimm"]["state"] == "RUNNING"))
check("helper refused while scraper runs (shared display)", call("/helper/open", {})[0] == 409)
call("/scraper/stop", {}); wait_for(lambda: not call("/status")[1]["scraper_running"]); check("stop scraper", not call("/status")[1]["scraper_running"])
check("helper opens, noVNC started", call("/helper/open", {})[0] == 200 and os.path.exists(sup + "/novnc") and call("/status")[1]["helper"]["open"])
check("scraper start refused while helper open", call("/scraper/start", {})[0] == 409)
check("save before the helper is ready is refused (no signal sent)", call("/helper/save", {})[0] == 409)
time.sleep(1.2)
s, r = call("/helper/save", {}); check("save without a login reports not_logged_in and keeps window", r["result"]["state"] == "not_logged_in" and r["open"] and not os.path.exists(sess + "/fb-state.json"))
call("/helper/close", {}); check("close stops helper and noVNC", not call("/status")[1]["helper"]["open"] and not os.path.exists(sup + "/novnc"))
p.send_signal(signal.SIGKILL); p.wait()
p = start("1"); call("/helper/open", {}); time.sleep(1.2); s, r = call("/helper/save", {})
check("save after login stores session, helper closes", r["result"]["state"] == "saved" and not call("/status")[1]["helper"]["open"])
s, st = call("/status")
check("session file mode 600, has login cookies, no cookie VALUES leak via API", st["session"]["exists"] and st["session"]["mode"] == "0o600" and st["session"]["has_login_cookies"] and "SECRETVALUE" not in json.dumps(st))
check("session check runs only when idle and reports state", call("/session/check", {})[1]["state"] == "logged_in")
check("session dir is mode 700", oct(os.stat(sess).st_mode & 0o777) == "0o700")
check("delete session", call("/session/delete", {})[0] == 200 and not call("/status")[1]["session"]["exists"])
check("check with no session -> no_session", call("/session/check", {})[1]["state"] == "no_session")
# validation with AIMM's own loader if present
if os.environ.get("AIMM_LIB"):
    p.send_signal(signal.SIGKILL); p.wait(); os.environ["PYTHONPATH"] = os.environ["AIMM_LIB"]; p = start()
    good = "[marketplace.facebook]\nsearch_city='haifa'\n[user.u]\nntfy_server='http://x'\nntfy_topic='t'\n[item.a]\nsearch_phrases=['x']\n"
    check("validate: good config", call("/validate", {"toml": good})[1]["valid"] is True)
    r = call("/validate", {"toml": good + "[item.b]\nsearch_phrases=['y']\nmax_price='abc'\n"})[1]
    check("validate: AIMM's own error is returned, markup stripped", r["valid"] is False and "max_price" in r["error"] and "[cyan]" not in r["error"])
# ---- AIMM stdout log reader (authoritative scan record): offsets, rotation, auth
LOGF = tmp + "/aimm.out"
check("log endpoint needs the token", call("/scraper/log", {"offset": None}, tok="bad" * 8)[0] == 401)
check("missing log file is reported, not an error", call("/scraper/log", {"offset": 0})[1].get("missing") is True)
open(LOGF, "w").write("old run line\n")
st, mark = call("/scraper/log", {"offset": None})
check("bookmark call returns size + inode and no text", st == 200 and mark["size"] == 13 and mark["inode"] and mark["text"] == "" and mark["mtime"] > 0)
open(LOGF, "a").write("[Search] Searching facebook for p1\nnew line 2\n")
r = call("/scraper/log", {"offset": mark["size"], "inode": mark["inode"]})[1]
check("only the text written after the bookmark is returned (the previous run is not mixed in)", r["text"].startswith("[Search] Searching") and "old run" not in r["text"] and not r["rotated"])
os.rename(LOGF, LOGF + ".1"); open(LOGF, "w").write("fresh file after rotation\n")
r2 = call("/scraper/log", {"offset": r["size"], "inode": r["inode"]})[1]
check("rotation (new inode) is detected and the new file is read from its start", r2["rotated"] is True and r2["text"] == "fresh file after rotation\n")
open(LOGF, "w").write("x\n")  # truncated in place, same inode, smaller than the bookmark
r3 = call("/scraper/log", {"offset": 500, "inode": os.stat(LOGF).st_ino})[1]
check("a bookmark beyond the end of the file (truncation) is not trusted", r3["rotated"] is True and r3["text"] == "x\n")
open(LOGF, "w").write("a" * 2_100_000)
r4 = call("/scraper/log", {"offset": 0, "inode": os.stat(LOGF).st_ino})[1]
check("a huge backlog is capped and flagged as truncated", r4["truncated"] is True and len(r4["text"]) == 2_000_000)
p.send_signal(signal.SIGKILL); p.wait(); shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
