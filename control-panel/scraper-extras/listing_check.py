"""Bounded availability check of stored listings, with the saved Facebook session, run by the agent ONLY while the scraper and the login
helper are idle (one browser at a time per session). Reads JSON {"urls": [...]} on stdin (at most MAX_URLS), prints one JSON result.
Never logs in, clicks or types. Modes: check (default) | probe (diagnostics of what a page exposes; no page text is printed).

Per listing the result is one of:
  active       the page shows this listing and Facebook's own data says it is live and not sold
  sold         Facebook's own data for this listing says is_sold=true
  unavailable  the page no longer shows this listing (removed, expired or hidden); counted by the panel, never final on one look
  unknown      could not tell (network, timeout, unexpected page): says nothing about the listing
and the run stops at once with auth_failed when the session is not logged in (login form, checkpoint, captcha): never "unavailable"."""
import json, os, re, sys, time

import fbsession

MAX_URLS = int(os.environ.get("LISTING_CHECK_MAX", "10"))
GAP_S = float(os.environ.get("LISTING_CHECK_GAP_S", "4"))  # a person-like pause between pages; this is never a crawl
ITEM_RE = re.compile(r"/marketplace/item/(\d+)")


def flags_near(html, lid):
    """Facebook's listing flags in the JSON embedded in the page, taken from the object that carries THIS listing's id (other listings,
    e.g. 'similar items', carry their own flags). -> {flag: bool} for is_sold / is_live / is_pending / is_hidden found next to the id."""
    out = {}
    for m in re.finditer(r'"id":"%s"' % re.escape(lid), html):
        window = html[max(0, m.start() - 3000): m.end() + 3000]
        for f in ("is_sold", "is_live", "is_pending", "is_hidden"):
            fm = re.search(r'"%s":(true|false)' % f, window)
            if fm and f not in out:
                out[f] = fm.group(1) == "true"
        if "is_sold" in out:
            break
    return out


def classify(page, url):
    lid = (ITEM_RE.search(url) or [None, None])[1]
    try:
        page.goto(url, timeout=45000)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)
    except Exception as e:
        return {"status": "unknown", "why": "load_" + type(e).__name__}
    state, _path, _lang = fbsession.classify(page)
    if state in ("checkpoint", "session_expired", "login_required"):
        return {"status": "auth_failed", "why": state}
    final = page.url or ""
    try:
        html = page.content()
    except Exception as e:
        return {"status": "unknown", "why": "content_" + type(e).__name__}
    fl = flags_near(html, lid) if lid else {}
    shown = bool(lid) and lid in final and ('"marketplace_listing_title"' in html or fl)
    if fl.get("is_sold") is True:
        return {"status": "sold", "flags": fl}
    if shown and fl.get("is_sold") is False and fl.get("is_live", True) and not fl.get("is_hidden"):
        return {"status": "active", "flags": fl}
    if lid and lid not in final and "/marketplace" in final:
        return {"status": "unavailable", "why": "redirected"}  # Facebook sends removed listings back to Marketplace
    if lid and lid in final and not fl and '"marketplace_listing_title"' not in html:
        return {"status": "unavailable", "why": "no_listing_data"}
    return {"status": "unknown", "why": "ambiguous", "flags": fl}


def probe(page, url):
    """What a listing page exposes, without page text: final URL shape, flags near the id, marker counts."""
    lid = (ITEM_RE.search(url) or [None, None])[1]
    t0 = time.time()
    page.goto(url, timeout=45000)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(2500)
    html, final = page.content(), page.url or ""
    return {"id": lid, "secs": round(time.time() - t0, 1), "same_url": bool(lid) and lid in final, "final_path": final.split("?")[0][-60:],
            "auth": fbsession.classify(page)[0], "flags": flags_near(html, lid) if lid else {},
            "markers": {k: html.count(k) for k in ('"is_sold":true', '"is_sold":false', '"is_live":true', '"is_live":false', '"is_pending":true',
                                                   '"marketplace_listing_title"', "נמכר", "Sold", "לא זמינ", "isn't available", "not available")}}


def main(mode):
    req = json.load(sys.stdin)
    urls = [u for u in req.get("urls", []) if isinstance(u, str) and ITEM_RE.search(u) and u.startswith("https://www.facebook.com/")][:MAX_URLS]
    from playwright.sync_api import sync_playwright
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        kw = {"locale": "en-US", "viewport": {"width": 1180, "height": 720}}
        if os.path.exists(fbsession.STATE):
            kw["storage_state"] = fbsession.STATE
        page = b.new_context(**kw).new_page()
        for i, u in enumerate(urls):
            if i:
                time.sleep(GAP_S)
            try:
                r = probe(page, u) if mode == "probe" else classify(page, u)
            except Exception as e:
                r = {"status": "unknown", "why": type(e).__name__}
            out[u] = r
            if r.get("status") == "auth_failed":
                for rest in urls[i + 1:]:
                    out[rest] = {"status": "unknown", "why": "auth_failed"}
                break
        b.close()
    print(json.dumps({"results": out, "checked_at": int(time.time())}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "check"))
