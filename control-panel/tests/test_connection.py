"""Every connection-status code of panel.connection(), driven with synthetic agent state + AIMM log text. Deterministic, no processes.
Run: python3 tests/test_connection.py"""
import os, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(); os.environ["DATA_DIR"] = tmp
sys.path.insert(0, ROOT + "/forwarder")
import panel, settings
LOG = tmp + "/aimm.log"
panel.init(mp_log=LOG, guard_token=lambda: "g" * 20, agent_token=lambda: "a" * 20, feedback_for=lambda k: "", mp_config=tmp + "/c.toml")
ok = fail = 0


def check(n, c):
    global ok, fail
    print(("PASS " if c else "FAIL ") + n); ok, fail = ok + bool(c), fail + (not c)


def log(*lines):
    open(LOG, "w").write("[VERSION] AI Marketplace Monitor, version 0.10.2\n" + "\n".join(lines) + "\n")


def ag(state=None, lang="en", exists=True, cookies=True, running=True, aimm="RUNNING", helper=False):
    return {"programs": {"aimm": {"state": aimm}}, "scraper_running": running, "helper": {"open": helper},
            "session": {"exists": exists, "has_login_cookies": cookies}, "login_status": {"state": state, "at": int(time.time()), "lang": lang} if state else None}


def code(s=None, st=200, a=None, desired="running"):
    return panel.connection(s or settings.load(), st, a if a is not None else ag("logged_in"), desired)["code"]


check("default language is Hebrew: connection statuses are Hebrew", any("\u0590" <= c <= "\u05ff" for c in panel.connection(settings.load(), 200, ag("session_expired"), "running")["title"]))
settings.save({"ui": {"language": "en"}})  # the rest of this file asserts the English texts
S0 = settings.load()
log("[Search] Searching facebook for x from Haifa")
check("agent unreachable -> agent_down", code(st=0, a={}) == "agent_down")
check("no session, nothing seen yet -> not_connected", code(a=ag(None, exists=False, cookies=False, running=False, aimm="STOPPED"), desired="stopped") == "not_connected")
check("login page shown and no session -> login_required", code(a=ag("login_required", exists=False, cookies=False)) == "login_required")
check("login page although a session exists and was loaded -> session_expired", code(a=ag("session_expired")) == "session_expired")
check("checkpoint/CAPTCHA -> checkpoint", code(a=ag("checkpoint")) == "checkpoint")
check("AIMM's own first login-URL visit -> checking (never 'not connected')", code(a=ag("checking")) == "checking")
check("healthy English account -> connected", code(a=ag("logged_in", lang="en")) == "connected")
check("login window open -> helper_open", code(a=ag("logged_in", helper=True)) == "helper_open")
check("saved session never checked -> unknown (honest), with a 'check' action", panel.connection(S0, 200, ag(None), "stopped")["action"] == "check")
check("unsupported account language (fr) -> language_unsupported", code(a=ag("logged_in", lang="fr")) == "language_unsupported")
log("[Search] Searching facebook for x from Haifa", "[Retrieve] Failed to get item details: Failed to get item details of listing https://x")
s_auto_he = settings.clean({"facebook": {"language": "auto", "detected_language": "he"}})[0]
s_en = settings.clean({"facebook": {"language": "en", "detected_language": "he"}})[0]
check("Hebrew account, translation ON, items still unreadable -> reading_failed (warn)", panel.connection(s_auto_he, 200, ag("logged_in", lang="he"), "running")["code"] == "reading_failed")
c = panel.connection(s_en, 200, ag("logged_in", lang="he"), "running")
check("Hebrew account but language forced to English -> reading_failed telling the user to choose Hebrew (in English)", c["code"] == "reading_failed" and c["severity"] == "bad" and "Hebrew" in c["detail"])
log("[Search] Searching facebook for x", "[AI] x concludes Good match (4): fine", "[Retrieve] Failed to get item details: x")
check("some listings rated by the AI -> connected (a few failed items are not a connection problem)", code(a=ag("logged_in")) == "connected")
log("[Retrieve] Failed to navigate to https://www.facebook.com/ after 10 attempts. net::ERR_NAME_NOT_RESOLVED")
check("DNS/navigation failures -> network_failure", code(a=ag("logged_in")) == "network_failure")
log("[Config] Error parsing: bad value")
check("AIMM rejected the config -> config_error", code(a=ag("logged_in")) == "config_error")
log("No browser could be launched. Please ensure Chromium is installed.")
check("browser could not start and AIMM is FATAL -> browser_failed", code(a=ag("logged_in", running=False, aimm="FATAL")) == "browser_failed")
log("[Search] ok")
check("AIMM FATAL without a browser error -> scraper_crashed", code(a=ag("logged_in", running=False, aimm="BACKOFF")) == "scraper_crashed")
check("a stopped scraper with an old failing log is NOT blamed for a connection problem", (log("[Retrieve] Failed to get item details: x") or True) and (os.utime(LOG, (time.time() - 7200, time.time() - 7200)) or True) and code(a=ag("logged_in", running=False, aimm="STOPPED"), desired="stopped") == "connected")
for k in ("title", "detail"):
    msgs = [panel.connection(S0, 200, ag(st), "stopped")[k] for st in ("logged_in", "session_expired", "checkpoint", "checking", "login_required")]
    check(f"every {k} is English (no Hebrew UI text)", all(not any("\u0590" <= ch <= "\u05ff" for ch in m) and any(c.isalpha() for c in m) for m in msgs))
# language resolution -> generated config
check("auto + detected he -> Hebrew translation", panel.resolved_language(s_auto_he) == "he")
check("auto + detected en -> no translation", panel.resolved_language(settings.clean({"facebook": {"detected_language": "en"}})[0]) is None)
check("explicit en wins over detection", panel.resolved_language(s_en) is None)
import cfggen
text = cfggen.build(settings.clean({"facebook": {"search_city": "haifa"}, "products": [{"name": "x", "search_phrases": ["aa"]}]})[0], "g" * 20, language="he")
check("Hebrew config has language + translation block with the verified strings", 'language = "he"' in text and "[translation.he]" in text and '"Condition" = "מצב"' in text and "אוסף פריטי Marketplace" in text)
if os.environ.get("AIMM_LIB"):
    sys.path.insert(0, os.environ["AIMM_LIB"])
    from pathlib import Path
    from ai_marketplace_monitor.config import Config
    open(tmp + "/he.toml", "w", encoding="utf-8").write(text)
    cfg = Config([Path(tmp + "/he.toml")])
    check("AIMM's own loader accepts the Hebrew config and builds a Hebrew translator", cfg.marketplace["facebook"].language == "he")
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
