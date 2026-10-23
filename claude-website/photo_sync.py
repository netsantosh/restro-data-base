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


try:
    import cv2
    import numpy as np
    _FACE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
except Exception:                                   # opencv missing: skip the people check, keep everything else
    cv2 = None
    _FACE = None


def looks_bad(im):
    """True for letter avatars (flat colour with a letter) and portraits/selfies (a large face)."""
    small = im.copy()
    small.thumbnail((256, 256))
    q = small.convert("RGB").quantize(colors=8)
    hist = sorted(q.histogram(), reverse=True)
    if sum(hist[:2]) / max(1, sum(hist)) >= 0.88:
        return True
    if cv2 is None:
        return False
    g = np.array(small.convert("L"))
    g = cv2.equalizeHist(g)
    min_side = max(24, int(min(g.shape) * 0.14))
    faces = _FACE.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
    return len(faces) > 0


# ---- content check ---------------------------------------------------------------------------
# Level 2 = cheap OpenCV check only (letter avatars, large faces); trusted for business photos only.
# Level 3 = also passed a zero-shot CLIP check ("is this food, a drink, a menu or the venue, and not a person?").
# Photos attached to reviews are only used when they reach level 3.
POSITIVE = ["a photo of food on a plate", "a photo of a dish served at a restaurant", "a photo of a drink or cocktail",
            "a photo of a restaurant interior with tables and chairs", "a photo of a restaurant building seen from the street",
            "a photo of a restaurant menu", "a photo of a bar counter or kitchen", "a photo of desserts or a bakery"]
NEGATIVE = ["a selfie of a person", "a portrait photo of a person", "a close-up of a person's face",
            "a group of people posing for a photo", "a profile picture", "a person standing outdoors",
            "a poster or advertisement with a lot of text", "a blurry dark photo", "a landscape or street with no restaurant",
            "a photo of a pet or animal", "a screenshot"]
CLIP_MIN = 0.55                                     # share of probability that must fall on the positive labels
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
            tf = model.encode_text(tok(POSITIVE + NEGATIVE))
            tf = tf / tf.norm(dim=-1, keepdim=True)
        npos = len(POSITIVE)

        def score(im):
            with torch.no_grad():
                x = pre(im).unsqueeze(0)
                f = model.encode_image(x)
                f = f / f.norm(dim=-1, keepdim=True)
                p = (100.0 * f @ tf.T).softmax(dim=-1)[0]
                return float(p[:npos].sum())
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
    return s >= CLIP_MIN, 3


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
                return "people", b""
            _tl.level = level
            return "ok", _encode_im(im, size)
        except Exception:
            status = "dead"
    return status, b""


def fetch_one(store, url, size, allow=True):
    """True when the photo is cached (or downloaded now) and passed the content check."""
    if is_avatar(url):
        return False
    rank = photo_rank(url)
    ready = clip_fn() is not None
    if rank > 0 and not ready:                    # review photos are only trusted after the CLIP check
        return False
    need = 3 if ready else 2
    st = store.state(url)
    if st == "ok":
        if store.level(url) >= need:
            return True
        p = store.path(key_for(url))              # cached under a weaker check: judge it again from disk
        try:
            ok, level = judge(_open(p.read_bytes()), rank)
        except Exception:
            ok, level = False, need
        if ok:
            store.mark(url, "ok", level)
            return True
        store.mark(url, "people")
        p.unlink(missing_ok=True)
        return False
    if st:
        return False
    if not allow:
        return False
    _tl.level = 2
    try:
        status, data = download(url, size, rank=rank)
    except TypeError:                              # a replacement download() without the rank argument
        status, data = download(url, size)
    if status == "ok":
        p = store.path(key_for(url))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        store.mark(url, "ok", getattr(_tl, "level", 2))
    else:
        store.mark(url, status)
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


def sync(restaurants, per=4, min_photos=3, download_minutes=0, selenium_workers=3, workers=24, selenium=True, refresh_minutes=25, size_main=(800, 600),
         size_other=(640, 480), cache_dir=CACHE, log=print, driver_factory=make_driver, collector=collect_urls):
    """Fill r['local_photos'] (site-relative paths) for every restaurant. Returns a report dict."""
    store = Store(cache_dir)
    lock = threading.Lock()

    def work(r):
        got = []
        for u in sorted((x for x in r["photos"] if not is_avatar(x)), key=photo_rank)[:14]:
            if len(got) >= per:
                break
            if fetch_one(store, u, size_main if not got else size_other, can_download()):
                got.append(key_for(u))
        for u in store.data.get("fresh", {}).get(r["place_id"], []):   # links Selenium found on earlier runs
            if len(got) >= per:
                break
            if key_for(u) not in got and fetch_one(store, u, size_main if not got else size_other, can_download()):
                got.append(key_for(u))
        r["local_keys"] = got

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
    if selenium and missing:
        log(f"  {len(missing)} restaurants have fewer than {min_photos} working photos; refreshing from Google Maps (Selenium)")
        fixed = refresh_with_selenium(store, missing, per, size_main, size_other, refresh_minutes,
                                      driver_factory, collector, log, selenium_workers)
        for r in missing:
            for u in r.pop("_fresh", []):
                r["local_keys"].append(key_for(u))

    for r in restaurants:
        r["local_photos"] = [rel_path(k) for k in r["local_keys"]]
    n_with = sum(1 for r in restaurants if r["local_photos"])
    dead = sum(1 for e in store.data["urls"].values() if e["s"] == "dead")
    return {"restaurants": len(restaurants), "with_photo": n_with, "without_photo": len(restaurants) - n_with,
            "fixed_by_selenium": fixed, "http_codes": {str(k): v for k, v in CODES.items()}, "dead_links_known": dead, "cache_dir": str(store.dir),
            "seconds": int(time.time() - t0)}


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
