"""Download, verify and self-host restaurant photos.

Google photo links in the sheet expire, so hot-linking them leaves broken images.
This step runs on the build machine (GitHub Actions has internet) and for every restaurant:

  1. downloads each photo and keeps it only if the server answers HTTP 200 with a real image,
  2. resizes it to WebP and stores it in photo_cache/ (the build copies the files it uses into dist/photos/),
  3. remembers dead links, so they are not retried on every build,
  4. for restaurants that end up with no working photo, opens the restaurant's Google Maps page in a
     headless Chrome (Selenium), reads the current photo links and downloads those instead.

The cache lives in photo_cache/ and is restored between Actions runs, so after the first run only new
or changed photos are downloaded.
"""
import collections
import hashlib
import os
import io
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image

from utils import clean_image, is_avatar, photo_rank, thumb

CODES = collections.Counter()   # HTTP status codes seen this run (for the build log)
ROOT = Path(__file__).parent
CACHE = ROOT / "photo_cache"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://www.google.com/",
    "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
}
DEAD_RETRY_DAYS = 14     # 403/404/410: the link has expired, look again only after this long
ERROR_RETRY_DAYS = 1     # timeouts, 5xx, 429: try again soon
# PHOTO_MODE=cache (site build): only use photos the photos workflow already downloaded and checked.
# No downloads, no OpenCV, no CLIP, so a deploy can never be held up or crashed by photo work.
CACHE_ONLY = os.environ.get("PHOTO_MODE", "full") == "cache"
MIN_LEVEL = int(os.environ.get("PHOTO_MIN_LEVEL", "3"))   # a photo is shown only after reaching this check level (3 = CLIP)
PEOPLE_V = 4            # people verdicts made by an older rule (lower number) are judged again
PEOPLE_RETRY_DAYS = 90   # portrait/selfie/letter-avatar images are not retried for a long time
FILTER_V = 3             # level reached by a photo that passed the full content check (see judge)
REFRESH_RETRY_DAYS = 7   # Selenium refresh for a restaurant is attempted at most once per week


def key_for(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:14]


def rel_path(key):
    return f"photos/{key[:2]}/{key}.webp"


class Store:
    def __init__(self, cache_dir=CACHE):
        self.dir = Path(cache_dir)
        self.file = self.dir / "manifest.json"
        self.lock = threading.Lock()
        self.data = {"urls": {}, "refresh": {}}
        if self.file.exists():
            try:
                self.data = json.loads(self.file.read_text(encoding="utf-8"))
            except ValueError:
                pass
        self.data.setdefault("urls", {})
        self.data.setdefault("refresh", {})

    def path(self, key):
        return self.dir / rel_path(key)

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.file.write_text(json.dumps(self.data), encoding="utf-8")

    def state(self, url):
        """'ok' | 'skip' (known dead / recent error) | None (needs a download)."""
        k = key_for(url)
        e = self.data["urls"].get(k)
        if e and e["s"] == "ok" and self.path(k).exists():
            return "ok"
        if e and e["s"] == "people" and e.get("v", 0) < PEOPLE_V:
            return None
        if e and e["s"] in ("dead", "err", "people"):
            limit = {"dead": DEAD_RETRY_DAYS, "err": ERROR_RETRY_DAYS, "people": PEOPLE_RETRY_DAYS}[e["s"]]
            if time.time() - e["t"] < limit * 86400:
                return "skip"
        return None

    def mark(self, url, status, level=2):
        with self.lock:
            self.data["urls"][key_for(url)] = {"s": status, "t": int(time.time()), "v": level}

    def level(self, url):
        e = self.data["urls"].get(key_for(url))
        return e.get("v", 1) if e else 0


_cv = {"tried": False, "cv2": None, "np": None, "face": None}
_cv_lock = threading.Lock()


def _face_detector():
    """Load OpenCV lazily (never in cache-only mode). Returns (cv2, numpy, cascade) or None."""
    with _cv_lock:
        if not _cv["tried"]:
            _cv["tried"] = True
            try:
                import cv2
                import numpy as np
                face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
                if not face.empty():
                    _cv.update(cv2=cv2, np=np, face=face)
            except Exception:
                pass
        return (_cv["cv2"], _cv["np"], _cv["face"]) if _cv["face"] is not None else None


def looks_bad(im):
    """True for letter avatars (flat colour with a letter) and portraits/selfies (a large face)."""
    small = im.copy()
    small.thumbnail((256, 256))
    q = small.convert("RGB").quantize(colors=8)
    hist = sorted(q.histogram(), reverse=True)
    if sum(hist[:2]) / max(1, sum(hist)) >= 0.88:
        return True
    det = _face_detector()
    if det is None:
        return False
    cv2, np, face = det
    g = np.array(small.convert("L"))
    min_side = max(24, int(min(g.shape) * 0.14))
    with _cv_lock:                       # OpenCV's CascadeClassifier is not thread-safe: one detection at a time
        g = cv2.equalizeHist(g)
        faces = face.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
    return len(faces) > 0


# ---- content check ---------------------------------------------------------------------------
# Level 2 = cheap OpenCV check only (letter avatars, large faces); trusted for business photos only.
# Level 3 = also passed a zero-shot CLIP check ("is this food, a drink, a menu or the venue, and not a person?").
# Photos attached to reviews are only used when they reach level 3.
# The goal is "no people photos", not "only perfect photos". Anything that is not clearly a person, a profile picture,
# a letter avatar or a logo is accepted: dark interiors, street stalls, menus, shop fronts and views from a restaurant.
POSITIVE = ["a photo of food on a plate", "a photo of a dish served at a restaurant", "a photo of a drink or cocktail",
            "a photo of a restaurant interior with tables and chairs", "a photo of a restaurant building seen from the street",
            "a photo of a restaurant menu", "a photo of a bar counter or kitchen", "a photo of desserts or a bakery",
            "a dark photo of a restaurant at night", "a photo of a street food stall", "a photo of a shop sign or storefront",
            "a photo of a hotel or banquet hall", "a photo of a table with several dishes", "a scenic view from a restaurant",
            "a photo of a packaged food product", "a photo of a street or building"]
PERSON = ["a selfie of a person", "a portrait photo of a person", "a close-up of a person's face",
          "a group of people posing for a photo", "a profile picture of a person", "a man posing for a photo",
          "a woman posing for a photo", "a child or a baby", "a chef or staff member posing for a photo",
          "a person holding a phone or a drink and smiling", "a letter or initial on a coloured background",
          "a logo or an icon", "a cartoon avatar"]
PERSON_MAX = 0.35                                   # reject when this much probability falls on the person labels
_tl = threading.local()                             # level reached by the last download on this thread
_clip = {"tried": False, "fn": None}
_clip_lock = threading.Lock()


def _load_clip(log=print):
    """Load a CLIP model (needs torch + open_clip_torch; installed by the photos workflow). Returns fn(PIL image) -> score."""
    try:
        import open_clip
        import torch
        model, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
        model.eval()
        tok = open_clip.get_tokenizer("ViT-B-32")
        with torch.no_grad():
            tf = model.encode_text(tok(POSITIVE + PERSON))
            tf = tf / tf.norm(dim=-1, keepdim=True)
        npos = len(POSITIVE)

        def score(im):
            with torch.no_grad():
                x = pre(im).unsqueeze(0)
                f = model.encode_image(x)
                f = f / f.norm(dim=-1, keepdim=True)
                p = (100.0 * f @ tf.T).softmax(dim=-1)[0]
                return float(p[npos:].sum())     # probability that the photo is a person / avatar / logo
        log("  content check: CLIP model loaded")
        return score
    except Exception as e:
        log(f"  content check: CLIP not available ({str(e).splitlines()[0][:100]}); only business photos are used")
        return None


def clip_fn():
    with _clip_lock:
        if not _clip["tried"]:
            _clip["tried"] = True
            _clip["fn"] = _load_clip()
        return _clip["fn"]


def judge(im, rank):
    """(ok, level). Cheap OpenCV check first, then CLIP when available."""
    if looks_bad(im):
        return False, 2
    fn = clip_fn()
    if fn is None:
        return rank == 0, 2
    with _clip_lock:
        s = fn(im)
    return s < PERSON_MAX, 3


def _tiny(im):
    """160x120 JPEG used by the review page for rejected photos."""
    try:
        c = im.copy()
        c.thumbnail((160, 120))
        out = io.BytesIO()
        c.save(out, "JPEG", quality=60)
        return out.getvalue()
    except Exception:
        return b""


def _open(raw):
    im = Image.open(io.BytesIO(raw))
    im.load()
    return im.convert("RGB")


def _encode(raw, size):
    return _encode_im(_open(raw), size)


def _encode_im(im, size):
    w, h = size
    scale = max(w / im.width, h / im.height)
    if scale < 1:
        im = im.resize((max(w, round(im.width * scale)), max(h, round(im.height * scale))), Image.LANCZOS)
    left, top = max(0, (im.width - w) // 2), max(0, (im.height - h) // 2)
    im = im.crop((left, top, min(im.width, left + w), min(im.height, top + h)))
    out = io.BytesIO()
    im.save(out, "WEBP", quality=66, method=4)
    return out.getvalue()


def download(url, size, session=None, timeout=20, rank=0):
    """Return (status, webp_bytes). status: ok | dead | err. 'ok' only for HTTP 200 + a real image."""
    sess = session or requests
    tries = [thumb(url, size[0] + 200, size[1] + 150)]
    if tries[0] != url:
        tries.append(url)                    # the link exactly as scraped, if the resized one is refused
    status = "err"
    for u in tries:
        try:
            resp = sess.get(u, headers=HEADERS, timeout=timeout)
        except requests.RequestException:
            CODES["exception"] += 1
            status = "err"
            continue
        CODES[resp.status_code] += 1
        if resp.status_code in (403, 404, 410):
            status = "dead"
            continue
        if resp.status_code != 200:
            status = "err"
            continue
        if not resp.headers.get("content-type", "").startswith("image/") or len(resp.content) < 1500:
            status = "dead"
            continue
        try:
            im = _open(resp.content)
            ok, level = judge(im, rank)
            if not ok:
                return "people", _tiny(im)
            _tl.level = level
            return "ok", _encode_im(im, size)
        except Exception:
            status = "dead"
    return status, b""


def _save_rejected(store, url, thumb_bytes):
    """Keep a tiny picture of rejected photos so the review page can show what was filtered out."""
    if not thumb_bytes:
        return
    n = store.data.setdefault("rej_n", 0)
    if n >= 2500:
        return
    p = store.dir / "rejected" / f"{key_for(url)}.jpg"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(thumb_bytes)
        store.data["rej_n"] = n + 1


def fetch_one(store, url, size, allow=True):
    """True when the photo is cached (or downloaded now) and has passed the content check. Fails closed."""
    if is_avatar(url):
        return False
    if CACHE_ONLY:
        return store.state(url) == "ok" and store.level(url) >= MIN_LEVEL
    if clip_fn() is None:                          # no classifier: show nothing rather than risk people photos
        return False
    rank = photo_rank(url)
    st = store.state(url)
    if st == "ok":
        if store.level(url) >= 3:
            return True
        p = store.path(key_for(url))              # cached under a weaker check: judge it again from disk
        try:
            im = _open(p.read_bytes())
            ok, level = judge(im, rank)
        except Exception:
            im, ok, level = None, False, 3
        if ok:
            store.mark(url, "ok", level)
            return True
        store.mark(url, "people", PEOPLE_V)
        if im is not None:
            _save_rejected(store, url, _tiny(im))
        p.unlink(missing_ok=True)
        return False
    if st or not allow:
        return False
    _tl.level = 3
    try:
        status, data = download(url, size, rank=rank)
    except TypeError:                              # a replacement download() without the rank argument
        status, data = download(url, size)
    if status == "ok":
        p = store.path(key_for(url))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        store.mark(url, "ok", getattr(_tl, "level", 3))
    else:
        store.mark(url, status, PEOPLE_V if status == "people" else 2)
        if status == "people":
            _save_rejected(store, url, data)
    return status == "ok"


PHOTO_RE = re.compile(r'https://lh3\.googleusercontent\.com/(?:p|gps-cs-s|grass-cs|geougc[^/]*)/[^"\'\\\s<>]+')


def _dismiss_consent(driver):
    """Google may show a cookie page first (EU runners). Click the reject/accept button if present."""
    if "consent." not in driver.current_url:
        return
    for el in driver.find_elements("css selector", "button"):
        label = (el.text or "").strip().lower()
        if label.startswith(("reject all", "accept all")):
            el.click()
            time.sleep(2)
            return


def collect_urls(driver, maps_link, wait=3.0):
    """Open a Google Maps listing and return the photo URLs currently shown for it (no reviewer avatars)."""
    driver.get(maps_link)
    time.sleep(wait)
    _dismiss_consent(driver)
    cands = []
    for img in driver.find_elements("css selector", "img"):
        cands.append(img.get_attribute("src") or "")
    cands += [m.replace("\\u003d", "=").replace("\\u0026", "&") for m in PHOTO_RE.findall(driver.page_source)]
    found = []
    for src in sorted(cands, key=photo_rank):
        if "googleusercontent.com" not in src or is_avatar(src):
            continue
        src = clean_image(src)
        if src and src not in found:
            found.append(src)
    return found


def make_driver():
    from selenium import webdriver
    opts = webdriver.ChromeOptions()
    for a in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
              "--window-size=1280,1600", "--lang=en-US"):
        opts.add_argument(a)
    opts.add_argument("--user-agent=" + HEADERS["User-Agent"])
    opts.page_load_strategy = "eager"
    drv = webdriver.Chrome(options=opts)
    drv.set_page_load_timeout(40)
    return drv


def refresh_with_selenium(store, missing, per, size_main, size_other, max_minutes, driver_factory=make_driver,
                          collector=collect_urls, log=print, workers=3):
    """For restaurants with too few working photos: read fresh links from their Google Maps page.

    Several headless Chrome windows work in parallel; each restaurant is tried at most once a week.
    """
    deadline = time.time() + max_minutes * 60
    todo = [r for r in missing if r.get("google_maps_link")
            and time.time() - store.data["refresh"].get(r["place_id"], 0) > REFRESH_RETRY_DAYS * 86400]
    if not todo:
        return 0
    todo_q = collections.deque(todo)
    qlock = threading.Lock()
    stats = {"fixed": 0, "tried": 0, "no_links": 0}

    def handle(driver, r):
        store.data["refresh"][r["place_id"]] = int(time.time())
        try:
            urls = collector(driver, r["google_maps_link"])
        except Exception:
            return
        if not urls:
            stats["no_links"] += 1
        have = list(r["local_keys"])
        new = []
        for u in urls[:per + 8]:
            if len(have) + len(new) >= per:
                break
            if key_for(u) in have:
                continue
            if fetch_one(store, u, size_main if not have and not new else size_other):
                new.append(u)
        if new:
            stats["fixed"] += 1
            fresh = store.data.setdefault("fresh", {})
            fresh[r["place_id"]] = (fresh.get(r["place_id"], []) + new)[:12]
            r["_fresh"] = new

    def worker():
        try:
            driver = driver_factory()
        except Exception as e:                                     # Chrome or selenium not installed
            log(f"  selenium refresh skipped: {str(e).splitlines()[0][:120]}")
            return
        try:
            while time.time() < deadline:
                with qlock:
                    if not todo_q:
                        return
                    r = todo_q.popleft()
                    stats["tried"] += 1
                    n = stats["tried"]
                handle(driver, r)
                if n % 25 == 0:
                    store.save()
                    log(f"  selenium: {n}/{len(todo)} tried, {stats['fixed']} got new photos, {stats['no_links']} had no links")
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    threads = [threading.Thread(target=worker) for _ in range(max(1, workers))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    store.save()
    left = len(todo_q)
    if left:
        log(f"  selenium stopped at the time limit; {left} restaurants continue on the next run")
    log(f"  selenium done: {stats['tried']} tried, {stats['fixed']} got new photos, {stats['no_links']} had no links")
    return stats["fixed"]


def sync(restaurants, per=4, export_per=0, min_photos=3, download_minutes=0, selenium_workers=3, workers=24, selenium=True, refresh_minutes=25, size_main=(800, 600),
         size_other=(640, 480), cache_dir=CACHE, log=print, driver_factory=make_driver, collector=collect_urls):
    """Fill r['local_photos'] (site-relative paths) for every restaurant. Returns a report dict."""
    store = Store(cache_dir)
    lock = threading.Lock()
    if not CACHE_ONLY and store.data.get("judge_v") != PEOPLE_V:
        import shutil
        shutil.rmtree(store.dir / "rejected", ignore_errors=True)
        store.data["rej_n"] = 0
        store.data["judge_v"] = PEOPLE_V
    if not CACHE_ONLY and clip_fn() is None:
        raise SystemExit("The photo classifier (CLIP) could not be loaded, so no photo can be approved. "
                         "Check the 'content check' line above; the live site keeps its current photos.")

    want = max(per, export_per)          # the site shows `per` photos; the images database export wants up to `export_per`

    def work(r):
        got, urls = [], []
        for u in sorted((x for x in r["photos"] if not is_avatar(x)), key=photo_rank)[:24 if want > per else 14]:
            if len(got) >= want:
                break
            if fetch_one(store, u, size_main if not got else size_other, can_download()):
                got.append(key_for(u))
                urls.append(u)
        for u in store.data.get("fresh", {}).get(r["place_id"], []):   # links Selenium found on earlier runs
            if len(got) >= want:
                break
            if key_for(u) not in got and fetch_one(store, u, size_main if not got else size_other, can_download()):
                got.append(key_for(u))
                urls.append(u)
        r["local_keys"] = got
        r["verified_urls"] = urls

    t0 = time.time()
    dl_deadline = t0 + download_minutes * 60 if download_minutes else None

    def can_download():
        return dl_deadline is None or time.time() < dl_deadline

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, _ in enumerate(ex.map(work, restaurants), 1):
            if n % 500 == 0:
                store.save()
                log(f"  photos: {n}/{len(restaurants)} restaurants checked ({int(time.time() - t0)}s)")
    store.save()

    # restaurants with fewer photos than the target get topped up from Google Maps, emptiest first
    missing = sorted((r for r in restaurants if len(r["local_keys"]) < min_photos), key=lambda r: len(r["local_keys"]))
    fixed = 0
    if selenium and missing and not CACHE_ONLY:
        log(f"  {len(missing)} restaurants have fewer than {min_photos} working photos; refreshing from Google Maps (Selenium)")
        fixed = refresh_with_selenium(store, missing, per, size_main, size_other, refresh_minutes,
                                      driver_factory, collector, log, selenium_workers)
        for r in missing:
            for u in r.pop("_fresh", []):
                r["local_keys"].append(key_for(u))
                r["verified_urls"].append(u)

    for r in restaurants:
        r["local_photos"] = [rel_path(k) for k in r["local_keys"][:per]]
    n_with = sum(1 for r in restaurants if r["local_photos"])
    dead = sum(1 for e in store.data["urls"].values() if e["s"] == "dead")
    return {"restaurants": len(restaurants), "with_photo": n_with, "without_photo": len(restaurants) - n_with,
            "fixed_by_selenium": fixed, "http_codes": {str(k): v for k, v in CODES.items()}, "dead_links_known": dead, "cache_dir": str(store.dir),
            "seconds": int(time.time() - t0)}


def write_images_database(restaurants, path, n_photos=10, n_reviews=8):
    """CSV for the WordPress plugin: one row per restaurant with its verified photo links and review texts.

    Photo links are the original Google links that just passed the checks (HTTP 200, no people, no logos), best first.
    They expire after a while, so the plugin should download them soon after this file is made (see checked_at).
    """
    import csv
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    head = ["place_id", "name", "country", "state", "city", "area", "photo_count", "review_count", "checked_at"]
    head += [f"photo_{i}" for i in range(1, n_photos + 1)]
    for i in range(1, n_reviews + 1):
        head += [f"review_{i}_text", f"review_{i}_author", f"review_{i}_rating", f"review_{i}_time"]
    rows = 0
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:      # utf-8-sig so Excel opens accents correctly
        w = csv.writer(f)
        w.writerow(head)
        for r in restaurants:
            urls = r.get("verified_urls", [])[:n_photos]
            revs = r.get("review_list", [])[:n_reviews]
            if not urls and not revs:
                continue
            row = [r["place_id"], r["name"], r["country"], r["state"], r["city"], r["area"], len(urls), len(revs), now]
            row += urls + [""] * (n_photos - len(urls))
            for i in range(n_reviews):
                v = revs[i] if i < len(revs) else None
                row += [v["text"], v["author"], v["rating"], v["when"]] if v else ["", "", "", ""]
            w.writerow(row)
            rows += 1
    return rows


def copy_used(restaurants, out_dir, cache_dir=CACHE):
    """Copy only the photos that pages use into the site output. Returns (files, megabytes)."""
    import shutil
    n = 0
    size = 0
    for r in restaurants:
        for rp in r.get("local_photos", []):
            dst = Path(out_dir) / rp
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(cache_dir) / rp, dst)
                n += 1
                size += dst.stat().st_size
    return n, round(size / 1e6)


if __name__ == "__main__":
    import argparse
    import sys
    import data_sources
    import build
    ap = argparse.ArgumentParser(description="Run only the photo sync (warms photo_cache/ without building).")
    ap.add_argument("--db", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-selenium", action="store_true")
    a = ap.parse_args()
    df = data_sources.read_listings(a.db)
    if a.limit:
        df = df.head(a.limit)
    rs = build.load_restaurants(df, {"skipped_incomplete": [], "duplicates_dropped": 0, "skipped_junk_name": 0,
                                     "name_variants": set()}, build.load_config().get("ignore_names", []))
    print(json.dumps(sync(rs, selenium=not a.no_selenium), indent=1))
    sys.exit(0)


def write_review_page(restaurants, out_dir, base_path, cache_dir=CACHE, n=90):
    """A hidden page (/photo-audit/) with random photos that are shown and random ones that were rejected."""
    import html
    import random
    import shutil
    out = Path(out_dir) / "photo-audit"
    rej_src = Path(cache_dir) / "rejected"
    rej = sorted(rej_src.glob("*.jpg")) if rej_src.exists() else []
    random.Random(5).shuffle(rej)
    (out / "rej").mkdir(parents=True, exist_ok=True)
    cells_rej = []
    for p in rej[:n]:
        shutil.copyfile(p, out / "rej" / p.name)
        cells_rej.append(f'<img src="{base_path}/photo-audit/rej/{p.name}" alt="" loading="lazy">')
    shown = [(r["name"], r["local_photos"][0]) for r in restaurants if r.get("local_photos")]
    random.Random(6).shuffle(shown)
    cells_ok = [f'<figure><img src="{base_path}/{rp}" alt="" loading="lazy"><figcaption>{html.escape(nm)}</figcaption></figure>'
                for nm, rp in shown[:n]]
    page = ("<!doctype html><meta charset=utf-8><meta name=robots content=noindex,nofollow><title>Photo review</title>"
            "<style>body{font:14px system-ui;margin:20px}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px}"
            "img{width:100%;aspect-ratio:4/3;object-fit:cover;display:block}figure{margin:0}figcaption{font-size:11px;color:#555}</style>"
            f"<h1>Shown on the site (random main photos)</h1><div class=g>{''.join(cells_ok)}</div>"
            f"<h1>Rejected by the content check (random)</h1><div class=g>{''.join(cells_rej)}</div>")
    (out / "index.html").write_text(page, encoding="utf-8")
    return len(cells_ok), len(cells_rej)
