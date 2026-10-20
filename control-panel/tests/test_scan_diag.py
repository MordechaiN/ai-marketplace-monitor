"""Regression for the 'SodaStream' report (2026-10-20): counters, AI breakdown and prompt context, from log text shaped like AIMM 0.10.2's."""
import os, sys, tempfile
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "forwarder"))
import panel, settings, cfggen

ok = fail = 0
def check(name, cond):
    global ok, fail
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)
    print(("PASS " if cond else "FAIL ") + name)

U = "https://www.facebook.com/marketplace/item/{}/?ref=search"
log = "\n".join([
    "[Search] Searching facebook for p2 from Haifa with radius=10",
    "[Retrieve] Navigating to " + U.format(111), "[Retrieve] Parsing סודה סטרים", "    post_url='" + U.format(111) + "',",
    "[AI] guard concludes Good match (4): fits. for listing סודה סטרים.",
    "[Retrieve] New item \"בלון\" from " + U.format(222) + " is sold by \"x\"",  # cached details: no Parsing line
    "[AI] guard previously concluded No match (1): only a cylinder. for listing בלון.",
    "[AI] guard concludes No match (1): only a cylinder. for listing בלון.",
    "[Skip] Rating No match (1) for בלון is below threshold 4.",
    "[Retrieve] New item \"מכשיר\" from " + U.format(333) + " is sold by \"y\"",
    "[AI] guard concludes Poor match (3): lacks syrup. for listing מכשיר.",
    "[Skip] Rating Poor match (3) for מכשיר is below threshold 4.",
    "[Search] 1 new listing for p2 is found.", "Next job to search p2 in 60m"])
i = panel.parse_run(log)
check("unique listings counted by item id, not by fresh detail parses", i["items_opened"] == 3)
check("each AI verdict counted once (a cache hit prints 'previously concluded' + 'concludes')", i["ai_rated"] == 3 and i["ai_cached"] == 1)
check("rating histogram", i["ratings"] == {"4": 1, "1": 1, "3": 1})
check("examples are only rejected ones, closest miss first", [e["rating"] for e in i["examples"]] == [3, 1] and i["examples"][0]["title"] == "מכשיר")
check("passed/below/done", i["passed"] == 1 and i["below"] == 2 and i["done"])

e = panel.parse_run("[Search] Searching facebook for p2 from X\nNext job to search p2")
check("a search with no listings is 'no_listings', not 'all rejected'", panel.scan_outcome({"result": "done", "reviewed": e["items_opened"], "passed": 0})["code"] == "no_listings")
check("listings found but all rejected stays 'no_results'", panel.scan_outcome({"result": "done", "reviewed": 3, "ai_rated": 3, "below": 3, "passed": 0})["code"] == "no_results")
check("AI failures are never reported as a clean zero", panel.scan_outcome({"result": "done", "reviewed": 3, "passed": 0, "ai_failed": 2})["level"] == "warn")

rec = {"at": 1, "result": "done", "ai_cached": 1, "ratings": {"4": 1, "9": 2, "x": 1}, "examples": [{"rating": 3, "title": "t" * 200, "reason": "r"}] * 9}
ls = settings._scan_diag(rec)
check("stored diagnostics are bounded and typed", ls["ratings"] == {"4": 1} and len(ls["examples"]) == 5 and len(ls["examples"][0]["title"]) == 70)

p = {"id": "p2", "name": "סודה", "enabled": True, "search_phrases": ["סודה", "סודה סטרים", "SodaStream"], "keywords": [], "antikeywords": [], "description": "", "min_price": 50,
     "max_price": 200, "search_city": "", "radius": 10, "rating": None, "search_interval_min": None, "condition": [], "date_listed": 0, "delivery_method": "all"}
blk = cfggen.item_block(p, {"search_city": "haifa"})
check("prompt says the section id is not a model and names the search", '\\"p2\\" above is only an internal search ID' in blk and 'named this search \\"סודה\\"' in blk)
check("prompt: phrases are alternatives, bundles match, parts-only do not; nothing product-specific", "alternative ways" in blk and "bundle" in blk and "sodastream" not in cfggen.extra_prompt(p).lower())
try:
    import tomllib
    check("generated item block is valid TOML", tomllib.loads(blk)["item"]["p2"]["extra_prompt"].startswith("[relevance rules v"))
except ImportError:
    pass
# the supervisor (console) log wraps messages at a fixed width: continuation lines are indented 29 spaces, URLs are split mid-token
W = " " * 29
sup = "\n".join([
    "[10/20/26 17:48:50] INFO     [Search] Searching facebook for p2 from Haifa with radius=10",
    "                    INFO     [Retrieve] Navigating to                           ",
    W + "https://www.facebook.com/marketplace/item/111386898",
    W + "7742751/?ref=search&referral_code=null              ",
    "                    INFO     [AI] guard concludes Poor match (3): המכשיר מתאים  ",
    W + "אך חסרים בקבוקים. for listing סודה סטרים.          ",
    "                    INFO     [Skip] Rating Poor match (3) for סודה סטרים is     ",
    W + "below threshold 4.",
    "                    INFO     [AI] guard concludes No match (1): שפצורים לנשק.   ",
    W + "for listing שפצורים.",
    "                    INFO     [Search] 0 new listings for p2 are found.",
    "                    INFO     Next job to search p2 scheduled"])
j = panel.parse_run(sup)
check("console log: split URL ids and wrapped verdicts are restored", j["ratings"] == {"3": 1, "1": 1} and j["examples"][0]["title"] == "סודה סטרים" and "חסרים בקבוקים" in j["examples"][0]["reason"])
check("console log: every evaluated listing counts as reviewed even when its URL is not printed", j["items_opened"] == 2 and j["ai_rated"] == 2 and j["done"])
check("file log lines (no 29-space continuation) are untouched", panel.unwrap("a\n    post_url='x',\nb", " ") == "a\n    post_url='x',\nb")
print(f"\n{ok} passed, {fail} failed"); sys.exit(1 if fail else 0)
