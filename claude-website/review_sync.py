"""Collect a few Google Maps review texts per restaurant (headless Chrome) into review_cache/reviews.json.

Runs in its own workflow (reviews.yml), separate from the site build. The site build only READS the cache,
so a slow or failed Google page never breaks a deploy. Each restaurant is retried at most every 30 days.

Usage: python review_sync.py [--minutes 120] [--workers 3] [--limit N]
"""
import argparse
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import data_sources
from photo_sync import make_driver, _dismiss_consent

ROOT = Path(__file__).parent
CACHE = ROOT / "review_cache" / "reviews.json"
RETRY_DAYS = 30
PER = 8                      # reviews kept per restaurant

# Extracts review cards from the open Maps panel. Uses stable attributes first (data-review-id, aria-label)
# and falls back to class names, because Google changes class names often.
JS_EXTRACT = r"""
const out = [];
const cards = document.querySelectorAll('div[data-review-id]');
const seen = new Set();
cards.forEach(c => {
  const id = c.getAttribute('data-review-id');
  if (!id || seen.has(id)) return; seen.add(id);
  const textEl = c.querySelector('span.wiI7pd') || c.querySelector('[class*="wiI7pd"]');
  const text = textEl ? textEl.innerText.trim() : '';
  if (!text) return;
  const starEl = c.querySelector('[role="img"][aria-label*="star" i]');
  const m = starEl ? (starEl.getAttribute('aria-label') || '').match(/(\d)/) : null;
  const authorEl = c.querySelector('.d4r55') || c.querySelector('button[aria-label] .d4r55') || c.querySelector('[class*="d4r55"]');
  const timeEl = c.querySelector('.rsqaWe') || c.querySelector('[class*="rsqaWe"]');
  out.push({text: text, rating: m ? parseInt(m[1]) : 0,
            author: authorEl ? authorEl.innerText.trim() : '', when: timeEl ? timeEl.innerText.trim() : ''});
});
return out;
"""


def open_reviews(driver, link, wait=3.0):
    driver.get(link)
    time.sleep(wait)
    _dismiss_consent(driver)
    tab = None
    for el in driver.find_elements("css selector", 'button[role="tab"]'):
        if "review" in ((el.get_attribute("aria-label") or "") + (el.text or "")).lower():
            tab = el
            break
    if tab is None:
        for el in driver.find_elements("css selector", "button[jsaction*='moreReviews'], a[href*='reviews']"):
            tab = el
            break
    if tab is not None:
        try:
            tab.click()
            time.sleep(2.5)
        except Exception:
            pass


def expand_and_read(driver):
    for _ in range(3):                                     # open truncated reviews ("More")
        for b in driver.find_elements("css selector", "button.w8nwRe, button[aria-label='See more' i]")[:PER * 2]:
            try:
                b.click()
            except Exception:
                pass
        time.sleep(0.4)
        panes = driver.find_elements("css selector", "div.m6QErb.DxyBCb, div[role='main'] div.m6QErb")
        if panes:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", panes[-1])
        time.sleep(1.2)
        if len(driver.execute_script(JS_EXTRACT)) >= PER:
            break
    return driver.execute_script(JS_EXTRACT)


def clean(rows):
    out, seen = [], set()
    for r in rows:
        t = re.sub(r"\s+", " ", r.get("text", "")).strip()
        t = re.sub(r"\s*\(Translated by Google\).*$", "", t, flags=re.S)
        t = re.sub(r"^\(Original\).*$", "", t).strip()
        if len(t) < 40 or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append({"text": t[:600], "rating": int(r.get("rating") or 0), "author": (r.get("author") or "")[:60],
                    "when": (r.get("when") or "")[:30]})
    out.sort(key=lambda r: (-(r["rating"] >= 4), -len(r["text"])))   # positive and informative first
    return out[:PER]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=float(os.environ.get("REVIEW_MINUTES", "120")))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    src = os.environ.get("DB_SOURCE") or f"gsheet:{os.environ['DB_SHEET_ID']}"
    df = data_sources.read_listings(src)
    rows = [r for r in df.to_dict("records") if str(r.get("place_id") or "").strip() and str(r.get("google_maps_link") or "").startswith("http")]
    rows.sort(key=lambda r: -(float(r.get("total_reviews") or 0) if str(r.get("total_reviews") or "").replace(".", "").isdigit() else 0))
    CACHE.parent.mkdir(exist_ok=True)
    data = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}
    now = time.time()
    todo = [r for r in rows if now - data.get(str(r["place_id"]), {}).get("t", 0) > RETRY_DAYS * 86400]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(rows)} restaurants, {len(todo)} to check, {sum(1 for v in data.values() if v.get('reviews'))} already have reviews")
    deadline, lock, stats = now + a.minutes * 60, threading.Lock(), {"ok": 0, "empty": 0, "err": 0}
    queue = list(reversed(todo))

    def worker():
        drv = make_driver()
        try:
            while time.time() < deadline:
                with lock:
                    if not queue:
                        return
                    r = queue.pop()
                pid = str(r["place_id"])
                try:
                    open_reviews(drv, str(r["google_maps_link"]))
                    got = clean(expand_and_read(drv))
                    with lock:
                        data[pid] = {"t": time.time(), "reviews": got}
                        stats["ok" if got else "empty"] += 1
                        n = stats["ok"] + stats["empty"]
                        if n % 25 == 0:
                            CACHE.write_text(json.dumps(data), "utf-8")
                            print(f"  checked {n}, with reviews {stats['ok']}", flush=True)
                except Exception as e:
                    with lock:
                        stats["err"] += 1
                    if stats["err"] <= 5:
                        print("  error", type(e).__name__, str(e)[:120])
                    try:
                        drv.quit()
                    except Exception:
                        pass
                    drv = make_driver()
        finally:
            try:
                drv.quit()
            except Exception:
                pass

    with ThreadPoolExecutor(a.workers) as ex:
        for _ in range(a.workers):
            ex.submit(worker)
    CACHE.write_text(json.dumps(data), "utf-8")
    print("done", stats)


if __name__ == "__main__":
    main()
