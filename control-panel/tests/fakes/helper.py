import json, os, signal, sys, time
S = os.environ["AIMM_SESSION_FILE"]; H = os.environ["AIMM_HELPER_STATUS"]
def w(p, o): open(p, "w").write(json.dumps(o))
if sys.argv[1] == "check":
    w(os.environ["AIMM_SESSION_STATUS"], {"state": "logged_in", "at": int(time.time())}); sys.exit(0)
def save(*a):
    if os.environ.get("FAKE_LOGGED_IN") == "1":
        w(S, {"cookies": [{"name": "c_user", "value": "SECRETVALUE"}, {"name": "xs", "value": "SECRETVALUE2"}]}); os.chmod(S, 0o600)
        w(H, {"state": "saved", "at": int(time.time()), "logged_in": True})
    else:
        w(H, {"state": "not_logged_in", "at": int(time.time()), "logged_in": False})
signal.signal(signal.SIGUSR1, save); signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
time.sleep(float(os.environ.get("FAKE_READY_DELAY", "0.5"))); w(H, {"state": "open", "at": int(time.time())})
while True: time.sleep(0.2)
