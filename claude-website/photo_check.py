"""Report on how many scraped Google photo links still load.

Runs inside the GitHub Action (the runner can reach the internet). It tests a random sample of
restaurants twice: the resized link the site uses, and the original link as scraped. The result
tells us whether links are expired (both fail) or whether resizing breaks them (only resized fails),
and the build adapts: if originals work much better than resized links it stops resizing.
"""
import random
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from utils import thumb

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def check(url, referer):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0", "Referer": referer,
                                               "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            ctype = r.headers.get("Content-Type") or ""
            return "ok" if r.status in (200, 206) and ctype.startswith("image") else "odd"
    except urllib.error.HTTPError as e:
        return "dead" if e.code in (403, 404, 410) else f"http{e.code}"
    except Exception:
        return "unknown"


def run(restaurants, sample, referer):
    pool = [r for r in restaurants if r["photos"]]
    random.Random(7).shuffle(pool)
    pool = pool[:sample]

    def one(r):
        orig = r["photos"][0]
        return r, check(thumb(orig, 480, 360), referer), check(orig, referer)

    with ThreadPoolExecutor(12) as ex:
        rows = list(ex.map(one, pool))
    n = len(rows) or 1
    res = {
        "sampled": len(rows),
        "both_ok": sum(1 for _, a, b in rows if a == "ok" and b == "ok"),
        "only_original_ok": sum(1 for _, a, b in rows if a != "ok" and b == "ok"),
        "only_resized_ok": sum(1 for _, a, b in rows if a == "ok" and b != "ok"),
        "both_dead": sum(1 for _, a, b in rows if a == "dead" and b == "dead"),
        "other": 0,
    }
    res["other"] = res["sampled"] - res["both_ok"] - res["only_original_ok"] - res["only_resized_ok"] - res["both_dead"]
    res["working_pct"] = round(100 * (res["both_ok"] + res["only_original_ok"] + res["only_resized_ok"]) / n)
    res["dead_examples"] = [r["photos"][0] for r, a, b in rows if a == "dead" and b == "dead"][:3]
    return res
