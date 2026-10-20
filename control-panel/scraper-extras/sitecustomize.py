"""Loaded by Python inside the AIMM process only (PYTHONPATH=/extras). Gives AIMM's browser contexts the saved Facebook session
(AIMM 0.10.2 creates a fresh context per launch and would otherwise need a manual login every start) and records login status.
Everything is best effort: any failure here must never break AIMM."""
import logging
import os

# AIMM logs at DEBUG into ~/.ai-marketplace-monitor/ai-marketplace-monitor.log and, while it sleeps between jobs, watches that SAME directory
# for config changes with watchdog. Watchdog logs every inotify event at DEBUG, and writing that line is itself a new event: an endless
# feedback loop (~5 MB/s) that rotates the real log away within seconds. Real AIMM messages (logger "monitor", "[AI]", ...) are unaffected.
logging.getLogger("watchdog").setLevel(logging.WARNING)

if os.environ.get("AIMM_SESSION_FILE"):
    try:
        import fbsession
        from playwright.sync_api import Browser, Page

        _new_context, _goto = Browser.new_context, Page.goto

        def new_context(self, *a, **kw):
            if "storage_state" not in kw and os.path.exists(fbsession.STATE):
                kw["storage_state"] = fbsession.STATE
                fbsession.loaded["login_cookies"] = fbsession.saved_has_login()
            return _new_context(self, *a, **kw)

        def goto(self, url, *a, **kw):
            r = _goto(self, url, *a, **kw)
            try:
                if fbsession.HOST in (self.url or ""):
                    fbsession.record(self, "scraper")
            except Exception:
                pass
            return r

        Browser.new_context, Page.goto = new_context, goto
    except Exception:
        pass

# Scan timing + one measured speed fix. Cumulative seconds per pipeline stage, written to a small JSON next to AIMM's log so the panel
# can show where a scan's time went (the AIMM log has no timestamps). Stages: details = opening listing pages (incl. "See more"),
# ai = AI evaluation incl. AIMM's own retries, notify = handing a match to the panel (forwarding + storage). The rest of a scan is
# the search-result pages and the browser. Best effort: timing must never break AIMM.
# "See more": AIMM waits up to 8 s for a "See more" button on every listing page. Measured 20 Oct 2026: 61 waits for 59 opened pages,
# all ran into the timeout (the button was never there), i.e. ~8 s lost per listing. The page is fully loaded by then (parse() runs after
# verify_layout), so a short wait is enough to catch a button that is about to render; a present button is still clicked as before.
try:
    import json as _json, threading as _threading, time as _time

    TIMING = os.path.join(os.path.expanduser("~/.ai-marketplace-monitor"), "timing.json")
    SEE_MORE_MS = int(os.environ.get("AIMM_SEE_MORE_WAIT_MS", "1500"))
    _t = {"pid": os.getpid(), "started": _time.time(), "updated": 0.0, "details_s": 0.0, "details_n": 0, "details_cached": 0, "see_more_s": 0.0,
          "see_more_found": 0, "ai_s": 0.0, "ai_n": 0, "notify_s": 0.0, "notify_n": 0, "last_event": "", "last_event_at": 0.0}
    _tlock = _threading.Lock()

    def _save(stage, secs, event, **inc):
        with _tlock:
            if stage:
                _t[stage + "_s"] = round(_t[stage + "_s"] + secs, 3)
            for k, v in inc.items():
                _t[k] += v
            now = _time.time()
            _t["updated"] = now
            if event:
                _t["last_event"], _t["last_event_at"] = event[:120], now
            tmp = TIMING + ".tmp"
            with open(tmp, "w") as f:
                _json.dump(_t, f)
            os.replace(tmp, TIMING)

    def _timed(cls, name, stage, event, counter=None, result_hook=None):
        orig = getattr(cls, name)

        def wrapper(*a, **kw):
            t0 = _time.time()
            ok = False
            try:
                r = orig(*a, **kw)
                ok = True
                return r
            finally:
                try:
                    extra = {counter: 1} if counter else {}
                    if ok and result_hook:
                        extra.update(result_hook(r))
                    _save(stage, _time.time() - t0, event if ok else event + " (failed)", **extra)
                except Exception:
                    pass
        setattr(cls, name, wrapper)

    import ai_marketplace_monitor.facebook as _fbm
    import ai_marketplace_monitor.ai as _aim
    import ai_marketplace_monitor.user as _usr

    def _see_more(self):
        t0, found = _time.time(), 0
        try:
            b = self.page.locator(f'div[role="button"]:has(span:text("{self.translator("See more")}"))')
            try:
                b.first.wait_for(state="visible", timeout=SEE_MORE_MS)
            except Exception:
                return
            found = b.count()
            for i in range(found):
                b.nth(i).click(timeout=2000)
            self.page.wait_for_timeout(500)
        except KeyboardInterrupt:
            raise
        except Exception:
            pass
        finally:
            try:
                _save("see_more", _time.time() - t0, "", see_more_found=1 if found else 0)
            except Exception:
                pass

    _fbm.FacebookItemPage._expand_see_more = _see_more
    _timed(_fbm.FacebookMarketplace, "get_listing_details", "details", "opened a listing page", "details_n",
           lambda r: {"details_cached": 1} if isinstance(r, tuple) and len(r) > 1 and r[1] else {})
    _timed(_aim.OpenAIBackend, "evaluate", "ai", "AI evaluated a listing", "ai_n")
    _timed(_usr.User, "notify", "notify", "sent a match to the panel", "notify_n")
except Exception:
    pass
