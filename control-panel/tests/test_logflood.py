"""Regression: AIMM's watchdog DEBUG feedback loop (log in the watched directory) flooded and rotated away the real log.
Reproduces AIMM's own logging setup (root DEBUG + RotatingFileHandler 1 MiB x5 inside the directory a watchdog Observer watches), once WITHOUT
and once WITH scraper-extras/sitecustomize.py. Needs watchdog (AIMM_LIB). Run: AIMM_LIB=<dir> python3 tests/test_logflood.py"""
import glob, json, os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = fail = 0


def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)


if not os.environ.get("AIMM_LIB") or not glob.glob(os.environ["AIMM_LIB"] + "/watchdog"):
    print("SKIP watchdog not available (set AIMM_LIB)"); print("\n0 passed, 0 failed"); sys.exit(0)

SCRIPT = r'''
import json, logging, os, sys, time
from logging.handlers import RotatingFileHandler
d = sys.argv[1]
logging.basicConfig(level="DEBUG", format="%(message)s", handlers=[RotatingFileHandler(d + "/ai-marketplace-monitor.log", encoding="utf-8", maxBytes=1024 * 1024, backupCount=5)])
log = logging.getLogger("monitor")
open(d + "/config.toml", "w").write("x")
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
o = Observer(); o.schedule(FileSystemEventHandler(), d, recursive=False); o.start()   # what AIMM does while it sleeps between jobs
log.info("[Schedule] Next job to search p1 scheduled to run in an hour")
time.sleep(float(sys.argv[2]))
log.info("[Search] a real line written later")
time.sleep(0.5); o.stop(); o.join()
size = sum(os.path.getsize(d + "/" + f) for f in os.listdir(d) if f.startswith("ai-marketplace-monitor.log"))
txt = open(d + "/ai-marketplace-monitor.log", encoding="utf-8").read()
print(json.dumps({"bytes": size, "files": len([f for f in os.listdir(d) if f.startswith("ai-marketplace-monitor.log")]), "noise": txt.count("in-event"), "has_next_job": "Next job to search" in txt, "has_real": "a real line written later" in txt}))
'''


def run(with_patch):
    d = tempfile.mkdtemp()
    env = {**os.environ, "PYTHONPATH": os.environ["AIMM_LIB"] + (os.pathsep + ROOT + "/scraper-extras" if with_patch else "")}
    env.pop("AIMM_SESSION_FILE", None)
    out = subprocess.run([sys.executable, "-c", SCRIPT, d, "3"], env=env, capture_output=True, text=True, timeout=60).stdout.strip().splitlines()[-1]
    shutil.rmtree(d, ignore_errors=True)
    return json.loads(out)


before, after = run(False), run(True)
check(f"CONTROL: without the fix the feedback loop reproduces (>1 MiB of inotify noise in 3 s, {before['bytes']} bytes, {before['noise']} noise lines)", before["bytes"] > 1_000_000 and before["noise"] > 0)
check("CONTROL: ...and the real 'Next job' line is rotated away by the noise", not before["has_next_job"])
check(f"FIXED: with sitecustomize the log stays tiny ({after['bytes']} bytes) and holds no inotify noise", after["bytes"] < 5000 and after["noise"] == 0)
check("FIXED: normal AIMM messages are all still there (cycle-end marker and later lines)", after["has_next_job"] and after["has_real"] and after["files"] == 1)
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
