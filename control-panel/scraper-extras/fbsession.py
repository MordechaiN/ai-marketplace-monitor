"""Facebook session helpers shared by sitecustomize.py (inside AIMM) and login_helper.py.
Reads page state only. Never types, clicks, or touches credentials. Never writes cookie values anywhere except the
mode-600 Playwright storage_state file the user's own login produced.

States written to status.json:
  logged_in        authenticated page, login cookies present
  checking         on a login URL with valid-looking cookies (AIMM always visits the login URL first; Facebook normally redirects away)
  login_required   login form shown and we never had a session
  session_expired  login form shown although we restored a session (Facebook rejected it)
  checkpoint       Facebook asks for identity verification / CAPTCHA (the user must solve it by hand)
  anonymous        ordinary page, no login cookies
  unknown          could not tell
plus lang (the page's <html lang>), so the panel can tell when the account language is one AIMM cannot read."""
import json, os, time

STATE = os.environ.get("AIMM_SESSION_FILE", "/session/fb-state.json")
STATUS = os.environ.get("AIMM_SESSION_STATUS", "/session/status.json")
HOST = os.environ.get("AIMM_SESSION_HOST", "facebook.com")  # overridden only by the offline test
COOKIE_URL = os.environ.get("AIMM_SESSION_COOKIE_URL", "https://www.facebook.com")
LOGIN_COOKIES = {"c_user", "xs"}
loaded = {"login_cookies": False}  # did the context we are watching start with a saved login?


def write_json(path, obj):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def saved_has_login():
    try:
        with open(STATE) as f:
            return LOGIN_COOKIES <= {c.get("name") for c in json.load(f).get("cookies", [])}
    except (OSError, ValueError):
        return False


def _settle(page, limit=4.0):
    """AIMM navigates to the login URL even when logged in; Facebook then redirects. Give it a moment before judging."""
    end = time.time() + limit
    while time.time() < end:
        try:
            low = (page.url or "").lower()
            if not ("/login" in low or "login.php" in low):
                return
            if page.locator('input[name="pass"]').count() > 0:
                return
            page.wait_for_timeout(500)
        except Exception:
            return


def classify(page):
    """-> (state, path_only, lang)"""
    try:
        _settle(page)
        url = page.url or ""
    except Exception:
        return "unknown", "", ""
    path = "/" + url.split("?")[0].split("#")[0].split("//", 1)[-1].split("/", 1)[-1][:79]
    low = url.lower()
    try:
        lang = (page.evaluate("document.documentElement.lang") or "")[:8]
    except Exception:
        lang = ""
    if any(k in low for k in ("/checkpoint", "two_step_verification", "/recover", "captcha")):
        return "checkpoint", path, lang
    try:
        has_pass = page.locator('input[name="pass"]').count() > 0
        names = {c.get("name") for c in page.context.cookies(COOKIE_URL)}
    except Exception:
        return "unknown", path, lang
    cookies_ok = LOGIN_COOKIES <= names
    on_login = "/login" in low or "login.php" in low
    if has_pass:
        return ("session_expired" if loaded["login_cookies"] else "login_required"), path, lang
    if on_login:
        return ("checking" if cookies_ok else "login_required"), path, lang
    return ("logged_in" if cookies_ok else "anonymous"), path, lang


def record(page, source, save=True, last=[0.0]):
    state, path, lang = classify(page)
    try:
        write_json(STATUS, {"state": state, "at": int(time.time()), "path": path, "lang": lang, "source": source})
    except OSError:
        pass
    # only a logged-in context may overwrite the saved session, and not more often than every 2 minutes
    if save and state == "logged_in" and time.time() - last[0] > 120:
        last[0] = time.time()
        try:
            tmp = STATE + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.close(fd)
            page.context.storage_state(path=tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, STATE)
        except Exception:
            pass
    return state
