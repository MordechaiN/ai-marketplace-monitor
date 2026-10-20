"""Opens a visible browser window on the container's virtual display so the USER can log in to Facebook by hand
(through the panel's authenticated noVNC view). This script never types, clicks or scrolls. Modes:
  open   window stays until SIGUSR1 (save session) or SIGTERM (discard) or 20 min timeout
  check  one page load of facebook.com/marketplace with the saved session, records whether it is still logged in
CAPTCHAs and checkpoints are solved by the user in that window; nothing here tries to bypass them."""
import json, os, signal, sys, time

import fbsession

HELPER = os.environ.get("AIMM_HELPER_STATUS", "/session/helper.json")
LOCALE = "en-US"  # AIMM extracts text from English Facebook pages
flags = {"save": False, "stop": False}


def hstat(state, **kw):
    fbsession.write_json(HELPER, {"state": state, "at": int(time.time()), **kw})


def launch(p):
    b = p.chromium.launch(headless=False)
    kw = {"locale": LOCALE, "viewport": {"width": 1180, "height": 720}}
    if os.path.exists(fbsession.STATE):
        kw["storage_state"] = fbsession.STATE
    return b, b.new_context(**kw)


def save(ctx, page):
    names = {c.get("name") for c in ctx.cookies(fbsession.COOKIE_URL)}
    if not ({"c_user", "xs"} <= names):
        return False
    tmp = fbsession.STATE + ".tmp"
    os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
    ctx.storage_state(path=tmp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, fbsession.STATE)
    fbsession.loaded["login_cookies"] = True
    fbsession.record(page, "login-helper", save=False)
    return True


def main(mode):
    signal.signal(signal.SIGUSR1, lambda *a: flags.update(save=True))  # first thing: a signal must never kill us
    signal.signal(signal.SIGTERM, lambda *a: flags.update(stop=True))
    from playwright.sync_api import sync_playwright
    os.makedirs(os.path.dirname(fbsession.STATE), mode=0o700, exist_ok=True)
    with sync_playwright() as p:
        browser, ctx = launch(p)
        page = ctx.new_page()
        if mode == "check":
            page.goto("https://www.facebook.com/marketplace/", timeout=60000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(4000)
            state = fbsession.record(page, "check", save=True)
            browser.close()
            return 0 if state == "logged_in" else 3
        page.goto("https://www.facebook.com/", timeout=60000)
        hstat("open")
        end = time.time() + 20 * 60
        while time.time() < end and not flags["stop"] and browser.is_connected():
            if flags["save"]:
                flags["save"] = False
                ok = save(ctx, page)
                hstat("saved" if ok else "not_logged_in", logged_in=ok)
                if ok:
                    break
            page.wait_for_timeout(500)
        browser.close()
        hstat("closed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "open"))
    except Exception as e:
        hstat("error", error=type(e).__name__)
        sys.exit(1)
