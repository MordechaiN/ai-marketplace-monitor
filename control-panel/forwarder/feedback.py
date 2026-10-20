#!/usr/bin/env python3
"""Copy manual /listings removals (blacklist.jsonl) into config.toml item descriptions as
"do not rate high" examples, so the local model sees them. Called daily 04:30 by the forwarder.

Only reasons wrong/bad/scam are used (stale/price say nothing about the item itself).
Writes config.toml only when the block changed (a config change restarts the monitor).
--dry prints the new blocks and touches nothing.
"""
import json, os, re, shutil, sys
from datetime import datetime

BASE = os.environ.get("MP_BASE", os.path.expanduser("~/Projects/docker/Marketplace-Scraper"))
CONFIG = os.environ.get("MP_CONFIG", os.path.join(BASE, "data", "config.toml"))
BLACKLIST = os.environ.get("MP_BLACKLIST", os.path.join(BASE, "forwarder-data", "blacklist.jsonl"))
USE = {"wrong": "wrong item or spec", "bad": "damaged or poor quality", "scam": "suspicious seller"}
MAX_PER_ITEM = 12
START = "--- Examples the user marked as not suitable (rate similar things low) ---"
END = "--- end of examples ---"


def clean(s):
    s = re.sub(r"['\"`\\\r\n\t]+", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()[:70]


def load():
    by = {}
    try:
        with open(BLACKLIST, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("reason") in USE and r.get("item") and clean(r.get("title")):
                    by.setdefault(r["item"], []).append(r)
    except FileNotFoundError:
        pass
    return by


def block(rows):
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r.get("ts", ""), reverse=True):
        t = clean(r["title"])
        if t in seen:
            continue
        seen.add(t)
        price = f" (₪{r['price']})" if isinstance(r.get("price"), (int, float)) else ""
        out.append(f"- {t}{price}: {USE[r['reason']]}")
        if len(out) >= MAX_PER_ITEM:
            break
    return "\n".join([START, *out, END]) if out else ""


def patch(text, item, new):
    m = re.search(r"^\[item\.%s\]\s*$" % re.escape(item), text, re.M)
    if not m:
        return text
    d = text.find("description = '''", m.end())
    if d < 0:
        return text
    nxt = re.search(r"^\[", text[m.end():], re.M)
    if nxt and d > m.end() + nxt.start():
        return text
    s = d + len("description = '''")
    e = text.find("'''", s)
    if e < 0:
        return text
    body = text[s:e]
    body = re.sub(r"\n?%s.*?%s" % (re.escape(START), re.escape(END)), "", body, flags=re.S).rstrip("\n")
    if new:
        body += "\n" + new
    return text[:s] + body + text[e:]


def main(dry=None):
    if dry is None:
        dry = "--dry" in sys.argv
    with open(CONFIG, encoding="utf-8") as f:
        orig = f.read()
    by = load()
    items = set(re.findall(r"^\[item\.(\w+)\]", orig, re.M))
    text = orig
    for it in sorted(items):
        new = block(by.get(it, []))
        if dry and new:
            print(f"[{it}]\n{new}\n")
        text = patch(text, it, new)
    if text == orig:
        print("no change")
        return 0
    if dry:
        print("would write config.toml")
        return 0
    shutil.copy2(CONFIG, CONFIG + ".bak-feedback")
    with open(CONFIG + ".tmp", "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(CONFIG + ".tmp", CONFIG)
    print(datetime.now().isoformat(timespec="seconds"), "config.toml updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
