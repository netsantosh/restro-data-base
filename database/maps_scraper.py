"""
=======================================================================
GOOGLE MAPS -> EXCEL SCRAPER (Selenium + Python, visible Chrome window)
=======================================================================

WHAT THIS DOES
  1. Opens a real Chrome window (you SEE everything happening).
  2. Searches Google Maps for each query in QUERIES below.
  3. Scrolls the results panel to load more listings (this is Maps'
     version of "next page" - there are no numbered pages anymore,
     it is an infinite-scroll feed. The script keeps scrolling until
     it hits "You've reached the end of the list" or MAX_RESULTS).
  4. Opens each listing and extracts the fields you asked for.
  5. Opens the photo gallery and grabs up to 10 photo URLs + alt text.
  6. Writes everything to one Excel file (one row per listing).

SETUP (one time)
  pip install selenium pandas openpyxl
  You need Google Chrome installed. You do NOT need chromedriver -
  Selenium 4.6+ downloads and manages it automatically.

RUN
  python maps_scraper.py
  or with queries from the command line:
  python maps_scraper.py "cafes in Bandra Mumbai" "salons in Andheri"

TO EXPLORE / TEST A NEW QUERY
  Just edit the QUERIES list below (or pass them as arguments).
  Set MAX_RESULTS_PER_QUERY = 3 first so a test run takes ~1 minute.

HONEST NOTES
  - Scraping Google Maps is against Google's Terms of Service. This is
    fine for learning and small personal checks; do not run it at high
    volume or you will get soft-blocked / CAPTCHA'd.
  - Google changes its CSS class names often. All selectors live in the
    SELECTORS dict below so you can fix them in one place. If a column
    comes back empty, that selector is the thing to update.
  - country / state / city / area are PARSED from the address string.
    Google does not expose them as separate fields. Accuracy is good
    for Indian and US addresses, weaker for unusual formats.
  - "logo" is almost never exposed publicly by Google Business Profile.
    The script looks for a gallery item labelled "Logo" and leaves the
    column blank if there isn't one. "banner" = the main hero image.
=======================================================================
"""

import os
import re
import sys
import time
import random
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    StaleElementReferenceException,
)

# =====================================================================
# 1. CONFIG - this is the only part you normally touch
# =====================================================================

QUERIES = [
    "restaurants in Bandra West Mumbai",
    # "coffee shops in Powai Mumbai",
    # "BMW dealership Mumbai",
]

ADDITIONAL_QUERIES = [
    "restaurants in Khar Mumbai",
]

MAX_RESULTS_PER_QUERY = 70      # target per area, with a practical cap for Maps results
MAX_PHOTOS = 20                 # max photos allowed per listing
MIN_TARGET_PHOTOS = 10          # target for listings with enough photos
HARD_MAX_RESULTS_PER_AREA = 70  # absolute cap to avoid runaway runs
MIN_RESULTS_PER_AREA = 40       # try to get at least 40 listings; fall back to fewer if Maps has fewer
MIN_ACCEPTABLE_SAVED_ROWS = 10  # retry a DONE area when fewer rows were actually saved
HEADLESS = os.getenv("HEADLESS", "false").strip().lower() in {"1", "true", "yes"}
SLOW_MO = 0.45                  # short pause between actions without hiding page-load waits
PAGE_TIMEOUT = 20               # max seconds to wait for an element
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "google_maps_data.xlsx")
STATUS_OUTPUT_FILE = os.path.join(SCRIPT_DIR, "google_maps_status.xlsx")
LOG_FILE = os.path.join(SCRIPT_DIR, "maps_scraper_log.txt")
STATUS_SHEET = "status"
DATA_SHEET = "listings"
LANGUAGE = "en"                 # forces English labels -> stable parsing
AREA_SOURCE_FILE = os.path.join(SCRIPT_DIR, "India_DB.xlsx")
AREA_SOURCE_SHEET = "India"
AREA_COLUMN = "Area Name"
CITY_COLUMN = "City"
STATE_COLUMN = "State"
DEFAULT_GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1oD2W3-Z5zCXeuQkSemhEO0UHofcfUam1FPUOPeAawtE/edit?usp=sharing"
DEFAULT_GOOGLE_SERVICE_ACCOUNT_JSON = os.path.join(SCRIPT_DIR, "restro-database-49ad65bf2a5c.json")

GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL", DEFAULT_GOOGLE_SHEET_URL).strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", DEFAULT_GOOGLE_SERVICE_ACCOUNT_JSON).strip()
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Google Maps Live").strip()
GOOGLE_SYNC_EVERY_ROWS = 5

DATA_COLUMNS = [
    "query", "name", "country", "state", "city", "area", "address",
    "postal_code", "latitude", "longitude", "rating", "total_reviews",
    "price_level", "cuisine_type", "phone", "website", "google_maps_link",
    "google_reviews_link", "place_id", "banner", "logo", "photos_found",
    "business_description"
]
for i in range(1, MAX_PHOTOS + 1):
    DATA_COLUMNS += [f"photo_{i}", f"photo_{i}_alt"]
DATA_COLUMNS.append("scraped_at")
STATUS_COLUMNS = ["location", "status", "rows", "updated_at"]
_last_synced_data_rows = -1
_live_listings_ws = None
_saved_place_keys = set()
_query_location_by_key = {}
_run_lock_handle = None

# All fragile bits in one place. Fix here when Google changes its HTML.
SELECTORS = {
    "results_feed":   'div[role="feed"]',
    "result_link":    'a[href*="/maps/place/"]',
    "end_of_list":    "//span[contains(text(),\"reached the end of the list\")]",
    "name":           "h1.DUwDvf, h1.fontHeadlineLarge",
    "rating_block":   "div.F7nice",
    "review_labels":  "[aria-label*='review'], [aria-label*='Review'], [data-tooltip*='review'], [data-tooltip*='Review']",
    "review_count_button": "button[jsaction*='pane.rating.moreReviews']",
    "rating_count":   "div.F7nice > span:nth-child(2)",
    "category":       "button[jsaction*='category'], button.DkEaL",
    "address":        "button[data-item-id='address']",
    "phone":          "button[data-item-id^='phone']",
    "website":        "a[data-item-id='authority']",
    "price":          "span[aria-label*='Price'], span[aria-label*='price']",
    "hero_button":    "button.aoRNLd, button[jsaction*='heroHeaderImage'], "
                      "div[role='img'][aria-label]",
    "photos_tab":     "button[aria-label*='Photos'], button[data-tab-index='1']",
    "gallery_img":    "img[src*='googleusercontent'], img[src*='ggpht']",
    "consent_btn":    "//button[.//span[contains(.,'Accept all')] or "
                      "contains(.,'Accept all') or contains(.,'I agree') or "
                      "contains(.,'Reject all')]",
}


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def acquire_single_instance_lock():
    global _run_lock_handle
    lock_path = os.path.join(SCRIPT_DIR, ".maps_scraper.lock")
    handle = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, OSError) as exc:
        handle.close()
        log(f"Another scraper instance is already running; refusing to start ({type(exc).__name__}).")
        return False

    _run_lock_handle = handle
    return True


def set_system_awake(enabled):
    if os.name != "nt":
        return False
    try:
        import ctypes

        flags = 0x80000000 | (0x00000001 if enabled else 0)
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        return False


def pause(factor=1.0):
    """Human-ish pause. Also makes the run watchable."""
    time.sleep(SLOW_MO * factor * random.uniform(0.8, 1.3))


# =====================================================================
# 2. BROWSER
# =====================================================================

def build_driver(page_load_strategy="normal", block_images=False, headless=None):
    opts = Options()
    opts.page_load_strategy = page_load_strategy
    should_headless = HEADLESS if headless is None else headless
    if should_headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-background-timer-throttling")
        opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--window-size=1450,1000")
    opts.add_argument(f"--lang={LANGUAGE}-US")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--no-sandbox")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "profile.default_content_setting_values.notifications": 2,
        "intl.accept_languages": f"{LANGUAGE},{LANGUAGE}-US",
    }
    if block_images:
        prefs["profile.managed_default_content_settings.images"] = 2
    opts.add_experimental_option("prefs", prefs)
    driver = webdriver.Chrome(options=opts)   # Selenium Manager handles the driver
    driver.set_page_load_timeout(60)
    return driver


def restart_driver(driver, page_load_strategy="normal", block_images=False, headless=None):
    """Replace a closed Chrome session while keeping only one scraper window."""
    try:
        driver.quit()
    except Exception:
        pass
    log("Chrome session ended; starting one replacement session and resuming.")
    return build_driver(
        page_load_strategy=page_load_strategy,
        block_images=block_images,
        headless=headless,
    )


def driver_is_alive(driver):
    try:
        driver.current_url
        return True
    except WebDriverException:
        return False


def handle_consent(driver):
    """Google shows a cookie/consent wall in many regions. Click through it."""
    try:
        btn = WebDriverWait(driver, 6).until(
            EC.element_to_be_clickable((By.XPATH, SELECTORS["consent_btn"]))
        )
        btn.click()
        log("Consent screen accepted.")
        pause()
    except TimeoutException:
        pass  # no consent screen, normal case


# =====================================================================
# 3. SMALL HELPERS (never let one missing field kill the run)
# =====================================================================

def txt(driver, css, default=""):
    try:
        return driver.find_element(By.CSS_SELECTOR, css).text.strip()
    except (NoSuchElementException, StaleElementReferenceException):
        return default


def attr(driver, css, attribute, default=""):
    try:
        v = driver.find_element(By.CSS_SELECTOR, css).get_attribute(attribute)
        return (v or "").strip()
    except (NoSuchElementException, StaleElementReferenceException):
        return default


def aria_value(driver, css, default=""):
    """Reads e.g. aria-label="Address: 12 Hill Road, Bandra" -> the part after ':'."""
    label = attr(driver, css, "aria-label")
    if not label:
        return default
    return label.split(":", 1)[1].strip() if ":" in label else label.strip()


def upscale(url):
    """Google image URLs end with =w86-h86-k-no. Swap for a large version."""
    if not url:
        return ""
    return re.sub(r"=w\d+-h\d+[^=]*$", "=w1600-h1200-k-no", url)


def fallback_place_name(driver):
    """Use several Google Maps title sources to avoid missing names on some layouts."""
    selectors = [
        "h1.DUwDvf",
        "h1.fontHeadlineLarge",
        "h1",
        "[role='heading']",
        "meta[property='og:title']",
        "meta[name='twitter:title']",
    ]
    for css in selectors:
        if css.startswith("meta"):
            val = driver.execute_script(
                "var sel=arguments[0]; var el=document.querySelector(sel); return el ? (el.content || '') : '';",
                css,
            )
            if val:
                return val.split(" - Google Maps", 1)[0].strip()
        else:
            try:
                val = driver.find_element(By.CSS_SELECTOR, css).text.strip()
                if val:
                    return val
            except (NoSuchElementException, StaleElementReferenceException):
                pass

    page_src = driver.page_source or ""
    for pat in [r'"name"\s*:\s*"([^"]+)"', r'"@type"\s*:\s*"Restaurant".*?"name"\s*:\s*"([^"]+)"']:
        m = re.search(pat, page_src)
        if m:
            return m.group(1).strip()

    title = (driver.title or "").strip()
    if title:
        return title.split(" - Google Maps", 1)[0].strip()
    return ""


def normalize_area(area_name):
    raw = (area_name or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+(East|West|North|South|Central)\b", "", raw, flags=re.I)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def load_area_queries():
    """Read area names from India_DB.xlsx and dedupe them, collapsing East/West variants to one area."""
    global _query_location_by_key
    if not os.path.exists(AREA_SOURCE_FILE):
        log(f"Area source file not found: {AREA_SOURCE_FILE}. Falling back to built-in queries.")
        return ["Bandra"]

    try:
        df = pd.read_excel(AREA_SOURCE_FILE, sheet_name=AREA_SOURCE_SHEET)
    except Exception as exc:
        log(f"Unable to read {AREA_SOURCE_FILE}: {exc}. Falling back to built-in queries.")
        return ["Bandra"]

    if df.empty:
        return ["Bandra"]

    queries = []
    seen = set()
    _query_location_by_key = {}
    for _, row in df.iterrows():
        area = str(row.get(AREA_COLUMN, "")).strip()
        city = str(row.get(CITY_COLUMN, "")).strip()
        state = str(row.get(STATE_COLUMN, "")).strip()
        if not area:
            continue

        norm_area = normalize_area(area)
        if not norm_area:
            continue

        if city:
            key = (city.lower(), norm_area.lower())
        else:
            key = (norm_area.lower(),)
        if key in seen:
            continue
        seen.add(key)

        if city:
            query = f"restaurants in {norm_area} {city}"
        else:
            query = f"restaurants in {norm_area}"
        queries.append(query)
        _query_location_by_key[query.casefold()] = {
            "country": "India",
            "state": state,
            "city": city,
            "area": norm_area,
        }

    if not queries:
        queries = ["restaurants in Bandra Mumbai"]

    existing_queries = {query.casefold() for query in queries}
    for query in ADDITIONAL_QUERIES:
        if query.casefold() not in existing_queries:
            queries.append(query)
            existing_queries.add(query.casefold())
    return queries


def load_completed_locations(path=STATUS_OUTPUT_FILE):
    completed = set()
    if not os.path.exists(path):
        return completed
    try:
        status_df = pd.read_excel(path, sheet_name=STATUS_SHEET)
        if not status_df.empty and 'location' in status_df.columns:
            if 'status' in status_df.columns:
                latest_rows = status_df.drop_duplicates(subset=['location'], keep='last')
                done_rows = latest_rows[
                    latest_rows['status'].astype(str).str.strip().str.upper().eq('DONE')
                ]
            else:
                done_rows = status_df.iloc[0:0]
            completed = {
                x.casefold()
                for x in done_rows['location'].dropna().astype(str).str.strip()
            }
            completed = {x for x in completed if x}
    except Exception:
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as fh:
                    latest_status = {}
                    for line in fh:
                        line = line.strip()
                        parts = line.split("|", 3)
                        if len(parts) >= 3 and parts[0] in {"START", "DONE", "PENDING"}:
                            latest_status[parts[2].strip().casefold()] = parts[0]
                    completed = {
                        location for location, state in latest_status.items()
                        if state == "DONE" and location
                    }
            except OSError:
                pass
    return completed


def append_log(action, location, details=""):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"{action}|{stamp}|{location}|{details}\n")


def load_existing_rows(path=OUTPUT_FILE):
    if not os.path.exists(path):
        return pd.DataFrame(), set()
    try:
        df = pd.read_excel(path, sheet_name=DATA_SHEET)
    except Exception:
        return pd.DataFrame(), set()
    keys = set()
    for _, row in df.iterrows():
        place_key = str(row.get("place_id") or "").strip()
        if not place_key:
            place_key = str(row.get("google_maps_link") or "").strip()
        if not place_key:
            place_key = f"{str(row.get('query') or '').strip()}|{str(row.get('name') or '').strip()}"
        if place_key:
            keys.add(place_key)
    return df, keys


def canonical_listing_url(url):
    """Normalize a Maps listing URL so a saved listing is not scraped twice."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def filter_unsaved_listing_urls(urls, saved_urls):
    return [url for url in urls if canonical_listing_url(url) not in saved_urls]


def extract_business_description(driver):
    """Best-effort extraction of Google Maps business summary/description."""
    candidates = [
        "meta[name='description']",
        "meta[property='og:description']",
        "div[data-snippet*='description']",
        "div[jsan*='description']",
        "span[data-tooltip='About']",
        "div[role='button'][aria-label*='About']",
    ]
    for css in candidates:
        try:
            el = driver.find_element(By.CSS_SELECTOR, css)
            text = (el.get_attribute("content") or el.text or "").strip()
            if text:
                return text
        except (NoSuchElementException, StaleElementReferenceException):
            pass

    page_src = driver.page_source or ""
    for pat in [
        r'"description"\s*:\s*"((?:\\.|[^"\\])+)"',
        r'"snippet"\s*:\s*"((?:\\.|[^"\\])+)"',
        r'"about"\s*:\s*"((?:\\.|[^"\\])+)"',
    ]:
        m = re.search(pat, page_src)
        if m:
            val = m.group(1).replace('\\u003d', '=').replace('\\n', ' ').replace('\\/', '/').strip()
            if val:
                return val
    return ""


def parse_review_count(text, allow_bare=False):
    """Parse a review total from Google Maps text, including compact counts like 1.2K."""
    number = r"(?:\d{1,3}(?:[,\u00a0\u202f ]\d{3})+|\d+(?:[.,]\d+)?)\s*[KMB]?"
    patterns = [
        rf"(?P<count>{number})\s*(?:Google\s+)?reviews?\b",
        rf"\breviews?\s*[:\-]?\s*(?P<count>{number})\b",
        r'"(?:user_ratings_total|reviewCount|review_count)"\s*:\s*(?P<count>\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        return normalize_review_count(match.group("count"))

    match = re.search(rf"\(\s*(?P<count>{number})\s*\)", text, flags=re.I)
    if not match and allow_bare:
        match = re.fullmatch(rf"\s*(?P<count>{number})\s*", text, flags=re.I)
    if match:
        return normalize_review_count(match.group("count"))
    return ""


def normalize_review_count(token):
    token = token.strip()
    suffix_match = re.search(r"([KMB])$", token, flags=re.I)
    suffix = suffix_match.group(1).upper() if suffix_match else ""
    numeric = re.sub(r"\s*[KMB]$", "", token, flags=re.I).strip()
    if suffix:
        if "," in numeric and "." in numeric:
            numeric = numeric.replace(",", "")
        elif "," in numeric:
            groups = numeric.split(",")
            numeric = "".join(groups) if all(len(group) == 3 for group in groups[1:]) else numeric.replace(",", ".")
        try:
            multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
            return str(int(float(numeric) * multiplier))
        except ValueError:
            return ""
    digits = re.sub(r"\D", "", numeric)
    if digits:
        return digits
    return ""


def extract_review_count_from_controls(driver):
    for selector in (SELECTORS["review_count_button"], SELECTORS["rating_count"]):
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException:
            continue
        for element in elements:
            values = [
                element.get_attribute(attribute) or ""
                for attribute in ("aria-label", "data-tooltip", "title")
            ]
            values.append(element.text or "")
            for value in values:
                reviews = parse_review_count(value.strip(), allow_bare=True)
                if reviews:
                    return reviews
    return ""


def extract_rating_and_reviews(driver):
    """Read ratings/review totals from visible labels and Maps page payloads."""
    texts = []
    rating_block = ""
    try:
        rating_block = txt(driver, SELECTORS["rating_block"])
        if rating_block:
            texts.append(rating_block)
    except Exception:
        pass

    try:
        for element in driver.find_elements(By.CSS_SELECTOR, SELECTORS["review_labels"]):
            for attribute in ("aria-label", "data-tooltip", "title"):
                value = (element.get_attribute(attribute) or "").strip()
                if value:
                    texts.append(value)
            value = (element.text or "").strip()
            if value:
                texts.append(value)
    except Exception:
        pass

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        if body_text:
            texts.append(body_text)
    except Exception:
        pass

    try:
        page_source = driver.page_source or ""
        if page_source:
            texts.append(page_source)
    except WebDriverException:
        pass

    combined = "\n".join(texts)
    rating = ""
    reviews = ""
    rating_match = re.search(r"(?<!\d)([1-5](?:[.,]\d)?)(?:\s*(?:/\s*5|out of 5))?", combined)
    if rating_match:
        rating = rating_match.group(1).replace(",", ".")

    reviews = parse_review_count(combined)
    if not reviews and rating_block:
        reviews = parse_review_count(rating_block)
    if not reviews:
        reviews = extract_review_count_from_controls(driver)
    if not reviews:
        try:
            WebDriverWait(driver, 1.5).until(extract_review_count_from_controls)
        except TimeoutException:
            pass
        reviews = extract_review_count_from_controls(driver)

    return rating, reviews


# =====================================================================
# 4. ADDRESS PARSING -> country / state / city / area
# =====================================================================

def parse_address(address):
    """
    Best-effort split. Example:
    "Shop 4, Linking Road, Bandra West, Mumbai, Maharashtra 400050, India"
      area    = Bandra West
      city    = Mumbai
      state   = Maharashtra
      pincode = 400050
      country = India
    """
    out = {"country": "", "state": "", "city": "", "area": "", "postal_code": ""}
    if not address:
        return out

    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return out

    has_indian_pin = bool(re.search(r"(?<!\d)\d{6}(?!\d)", parts[-1]))
    if parts[-1].casefold() == "india":
        out["country"] = parts[-1]
        location_parts = parts[:-1]
    elif has_indian_pin:
        out["country"] = "India"
        location_parts = parts
    else:
        out["country"] = parts[-1] if len(parts) >= 2 else ""
        location_parts = parts[:-1] if len(parts) >= 2 else parts

    # second-from-last usually holds "State 400050" or "CA 94105"
    if location_parts:
        chunk = location_parts[-1]
        pin = re.search(r"\b(\d{5,6}(?:-\d{4})?)\b", chunk)
        if pin:
            out["postal_code"] = pin.group(1)
            chunk = chunk.replace(pin.group(1), "").strip()
        out["state"] = chunk.strip(" ,-")

    if len(location_parts) >= 2:
        out["city"] = location_parts[-2]
    if len(location_parts) >= 3:
        out["area"] = location_parts[-3]

    # short address like "Bandra, Mumbai, India": treat middle as city
    if not out["city"] and len(location_parts) == 2:
        out["city"] = location_parts[0]
    return out


# =====================================================================
# 5. RESULTS FEED - this is the "pagination" part
# =====================================================================

def collect_result_links(driver, target):
    """
    Scrolls the left results panel until we have `target` listings or the
    feed says it has ended. Returns a list of place URLs.
    """
    try:
        feed = WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["results_feed"]))
        )
    except TimeoutException:
        # Single strong match: Maps jumped straight into one place page
        if "/maps/place/" in driver.current_url:
            log("Query resolved to a single place page.")
            return [driver.current_url]
        log("No results feed found for this query.")
        return []

    seen, stale_rounds = [], 0
    while len(seen) < target and stale_rounds < 10:
        cards = driver.find_elements(By.CSS_SELECTOR, SELECTORS["result_link"])
        before = len(seen)
        for c in cards:
            try:
                href = c.get_attribute("href")
            except StaleElementReferenceException:
                continue
            if href and href not in seen:
                seen.append(href)

        log(f"  loaded {len(seen)} listings so far...")
        if len(seen) >= target:
            break

        # end-of-feed marker
        if driver.find_elements(By.XPATH, SELECTORS["end_of_list"]):
            log("  reached the end of the results list.")
            break

        # scroll the feed container (the "next page" action on Maps)
        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", feed)
        pause(1.8)
        stale_rounds = stale_rounds + 1 if len(seen) == before else 0

    return seen[:target]


# =====================================================================
# 6. PHOTOS (+ alt text)
# =====================================================================

def collect_photos(driver, max_photos=MAX_PHOTOS):
    """Collect up to 20 photos; there are often more, but Maps often exposes only a subset in page HTML."""
    photos, banner, logo = [], "", ""
    place_url = driver.current_url
    seen = set()

    def add_photo(src, alt=""):
        nonlocal logo
        src = upscale(src or "")
        if not src or src in seen:
            return
        seen.add(src)
        photos.append({"url": src, "alt": (alt or "").strip()})
        if "logo" in (alt or "").lower() and not logo:
            logo = src

    # 1) Use the page source and visible images first; this is the most reliable fallback on Maps.
    page_images = re.findall(r'https://lh3\.googleusercontent\.com[^\"\'\s<>]+', driver.page_source)
    for url in page_images:
        add_photo(url)

    for css in ["button.aoRNLd img", "div[role='img'] img", "img[src*='googleusercontent']", "img[src*='ggpht']"]:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, css):
                add_photo(el.get_attribute("src") or "", el.get_attribute("alt") or "")
            if photos:
                break
        except Exception:
            pass

    if len(photos) > 30:
        photos = photos[:20]
    elif len(photos) >= MIN_TARGET_PHOTOS:
        photos = photos[:MIN_TARGET_PHOTOS]
    elif len(photos) < MIN_TARGET_PHOTOS and len(photos) > 0:
        photos = photos[:len(photos)]

    opened = False
    gallery_needed = len(photos) < MIN_TARGET_PHOTOS
    for css in [SELECTORS["hero_button"], SELECTORS["photos_tab"]] if gallery_needed else []:
        try:
            el = driver.find_element(By.CSS_SELECTOR, css)
            driver.execute_script("arguments[0].click();", el)
            opened = True
            pause(0.6)
            break
        except (NoSuchElementException, WebDriverException):
            continue

    if opened:
        for _ in range(2):
            driver.execute_script("window.scrollBy(0, 600);")
            try:
                grid = driver.find_element(By.CSS_SELECTOR, "div[role='main']")
                driver.execute_script("arguments[0].scrollTop += 800;", grid)
            except NoSuchElementException:
                pass
            pause(0.35)

        for css in [SELECTORS["gallery_img"], "img[src*='googleusercontent']", "img[src*='ggpht']"]:
            try:
                for img in driver.find_elements(By.CSS_SELECTOR, css):
                    try:
                        add_photo(img.get_attribute("src") or "", img.get_attribute("alt") or "")
                    except StaleElementReferenceException:
                        continue
            except Exception:
                pass

        if len(photos) > 30:
            photos = photos[:20]
        elif len(photos) >= MIN_TARGET_PHOTOS:
            photos = photos[:MIN_TARGET_PHOTOS]

        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            pause(0.25)
            if driver.current_url != place_url:
                driver.get(place_url)
                pause(0.5)
        except WebDriverException:
            driver.get(place_url)
            pause(1.2)

    if not banner and photos:
        banner = photos[0]["url"]
    return photos[:max_photos], banner, logo


# =====================================================================
# 7. SCRAPE ONE LISTING
# =====================================================================

def scrape_place(driver, url, query):
    for attempt in range(1, 4):
        try:
            driver.get(url)
            WebDriverWait(driver, PAGE_TIMEOUT + 10).until(
                lambda current_driver: (
                    current_driver.find_elements(By.CSS_SELECTOR, SELECTORS["name"])
                    and current_driver.find_element(By.CSS_SELECTOR, SELECTORS["name"]).text.strip()
                )
            )
            break
        except (TimeoutException, WebDriverException) as exc:
            if attempt == 3:
                log(f"  !! detail panel failed after 3 attempts ({type(exc).__name__}): {url[:70]}")
                return None
            log(f"  .. detail page retry {attempt}/2")
            pause(1.5)
    pause()

    name = txt(driver, SELECTORS["name"]) or fallback_place_name(driver)
    if not name or name.casefold() == "google maps":
        log(f"  !! business profile did not load: {url[:70]}")
        return None
    log(f"  -> {name or 'MISSING_NAME'}")

    address = aria_value(driver, SELECTORS["address"]) or txt(driver, SELECTORS["address"]) or (driver.title or "")
    loc = parse_address(address)
    query_location = _query_location_by_key.get(query.casefold())
    if query_location:
        loc.update(query_location)

    phone = aria_value(driver, SELECTORS["phone"])
    website = attr(driver, SELECTORS["website"], "href")
    cuisine = txt(driver, SELECTORS["category"])

    rating, reviews = extract_rating_and_reviews(driver)

    price = attr(driver, SELECTORS["price"], "aria-label")
    if not price:
        m = re.search(r"[₹$€£¥]{1,4}", driver.find_element(By.TAG_NAME, "body").text)
        price = m.group(0) if m else ""

    # coordinates live in the URL
    lat = lng = ""
    cur = driver.current_url
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", cur) or \
        re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", cur)
    if m:
        lat, lng = m.group(1), m.group(2)

    # place_id is buried in the page payload
    place_id = ""
    try:
        m = re.search(r'"(ChI[A-Za-z0-9_\-]{20,})"', driver.page_source)
        if m:
            place_id = m.group(1)
    except WebDriverException:
        pass

    reviews_link = (f"https://search.google.com/local/reviews?placeid={place_id}"
                    if place_id else cur)

    photos, banner, logo = collect_photos(driver)

    row = {
        "query": query,
        "name": name,
        "country": loc["country"],
        "state": loc["state"],
        "city": loc["city"],
        "area": loc["area"],
        "address": address,
        "postal_code": loc["postal_code"],
        "latitude": lat,
        "longitude": lng,
        "rating": rating,
        "total_reviews": reviews,
        "price_level": price,
        "cuisine_type": cuisine,
        "phone": phone,
        "website": website,
        "google_maps_link": cur,
        "google_reviews_link": reviews_link,
        "place_id": place_id,
        "banner": banner,
        "logo": logo,
        "photos_found": len(photos),
        "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for i in range(1, MAX_PHOTOS + 1):
        p = photos[i - 1] if i <= len(photos) else {"url": "", "alt": ""}
        row[f"photo_{i}"] = p["url"]
        row[f"photo_{i}_alt"] = p["alt"]
    return row


def scrape_place_review_count(driver, url, wait_timeout=PAGE_TIMEOUT + 10, max_attempts=3):
    """Load a saved Maps listing and extract its review count without collecting other fields."""
    for attempt in range(1, max_attempts + 1):
        try:
            driver.get(url)
            return WebDriverWait(driver, wait_timeout, poll_frequency=0.25).until(
                lambda current_driver: parse_review_count(
                    current_driver.find_element(By.TAG_NAME, "body").text
                ) or False
            )
        except (TimeoutException, WebDriverException) as exc:
            if not driver_is_alive(driver):
                log("  .. review count browser session ended; will restart it.")
                return ""
            if attempt == max_attempts:
                log(f"  !! review count page failed after {max_attempts} attempts ({type(exc).__name__}): {url[:70]}")
                return ""
            log(f"  .. review count page retry {attempt}/{max_attempts - 1}")
            pause(0.5)
    return ""


# =====================================================================
# 8. EXCEL OUTPUT
# =====================================================================

def sync_live_google_sheet(data_df, status_df, force=False, sync_data=True):
    """Mirror the workbook to Google Sheets for live browser visibility."""
    global _last_synced_data_rows, _live_listings_ws
    data_row_count = len(data_df.index)
    sync_data_now = sync_data and (
        force
        or _last_synced_data_rows < 0
        or data_row_count - _last_synced_data_rows >= GOOGLE_SYNC_EVERY_ROWS
    )
    if not GOOGLE_SHEET_URL or not GOOGLE_SERVICE_ACCOUNT_JSON:
        return False
    if not os.path.exists(GOOGLE_SERVICE_ACCOUNT_JSON):
        log(f"Google Sheets sync disabled: credentials file not found at {GOOGLE_SERVICE_ACCOUNT_JSON}")
        return False

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception as exc:
        log(f"Google Sheets sync disabled: {exc}")
        return False

    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_JSON, scopes=scopes)
        client = gspread.authorize(credentials)
        sheet = client.open_by_url(GOOGLE_SHEET_URL)

        worksheet_titles = [ws.title for ws in sheet.worksheets()]
        listings_ws = sheet.worksheet("Listings") if "Listings" in worksheet_titles else sheet.add_worksheet(title="Listings", rows="2000", cols="80")
        status_ws = sheet.worksheet("Status") if "Status" in worksheet_titles else sheet.add_worksheet(title="Status", rows="500", cols="20")
        _live_listings_ws = listings_ws

        if sync_data_now:
            final_data = data_df.reindex(columns=DATA_COLUMNS, fill_value="")
            listing_rows = [DATA_COLUMNS] + final_data.fillna("").astype(str).values.tolist()

            def replace_listing_values(worksheet):
                worksheet.clear()
                if worksheet.row_count < len(listing_rows):
                    worksheet.resize(rows=len(listing_rows), cols=max(worksheet.col_count, len(DATA_COLUMNS)))
                for start in range(0, len(listing_rows), 500):
                    chunk = listing_rows[start:start + 500]
                    worksheet.update(chunk, f"A{start + 1}", value_input_option="RAW")

            replace_listing_values(listings_ws)

            _last_synced_data_rows = data_row_count

        final_status = status_df.reindex(columns=STATUS_COLUMNS, fill_value="")
        status_rows = [STATUS_COLUMNS] + final_status.fillna("").astype(str).values.tolist()
        status_ws.clear()
        if status_ws.row_count < len(status_rows):
            status_ws.resize(rows=len(status_rows), cols=max(status_ws.col_count, len(STATUS_COLUMNS)))
        status_ws.update(status_rows, "A1", value_input_option="RAW")

        log("Synced live data and status to Google Sheets." if sync_data_now else "Synced status to Google Sheets.")
        return True
    except Exception as exc:
        log(f"Google Sheets sync failed: {exc}")
        return False


def sync_live_listing(row):
    """Append one newly saved listing immediately after its local Excel write."""
    global _last_synced_data_rows
    if _live_listings_ws is None:
        log("Live listing sync deferred until the initial Google Sheets reconciliation.")
        return False
    try:
        values = [row.get(column, "") for column in DATA_COLUMNS]
        _live_listings_ws.append_row(values, value_input_option="RAW")
        if _last_synced_data_rows >= 0:
            _last_synced_data_rows += 1
        log(f"Live-synced listing: {row.get('name', '')}")
        return True
    except Exception as exc:
        _last_synced_data_rows = -1
        log(f"Live listing sync failed: {type(exc).__name__}: {str(exc)[:120]}")
        return False


def sync_repaired_listing(row, data_df, status_df):
    """Update the existing Google Sheet row for one repaired profile immediately."""
    global _live_listings_ws
    try:
        if _live_listings_ws is None:
            if not sync_live_google_sheet(data_df, status_df, force=True):
                return False

        import gspread
        from google.oauth2.service_account import Credentials

        credentials = Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_JSON,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        sheet = gspread.authorize(credentials).open_by_url(GOOGLE_SHEET_URL)
        row_key = canonical_listing_url(row.get("google_maps_link", ""))
        values = [
            "" if pd.isna(row.get(column, "")) else str(row.get(column, ""))
            for column in DATA_COLUMNS
        ]
        updated = False

        worksheet = sheet.worksheet("Listings")
        all_values = worksheet.get_all_values()
        if all_values:
            headers = all_values[0]
            if "google_maps_link" in headers:
                key_column = headers.index("google_maps_link")
                for sheet_row, values_row in enumerate(all_values[1:], start=2):
                    if key_column < len(values_row) and canonical_listing_url(values_row[key_column]) == row_key:
                        worksheet.update([values], f"A{sheet_row}", value_input_option="RAW")
                        updated = True
                        break

        if updated:
            log(f"Live-synced repaired review data: {row.get('name', '')}")
        else:
            log(f"Live repair row not found: {row.get('name', '')}")
        return updated
    except Exception as exc:
        log(f"Live repaired-listing sync failed: {type(exc).__name__}: {str(exc)[:120]}")
        return False


def repair_missing_reviews_parallel(data_df, missing, save_repaired_data, page_load_strategy, worker_count):
    worker_state = threading.local()
    drivers = []
    drivers_lock = threading.Lock()
    changed = 0
    pending_writes = 0

    def get_worker_driver():
        driver = getattr(worker_state, "driver", None)
        if driver is None:
            driver = build_driver(
                page_load_strategy=page_load_strategy,
                block_images=True,
                headless=False,
            )
            driver.set_page_load_timeout(15)
            worker_state.driver = driver
            with drivers_lock:
                drivers.append(driver)
        elif not driver_is_alive(driver):
            driver = restart_driver(
                driver,
                page_load_strategy,
                block_images=True,
                headless=False,
            )
            driver.set_page_load_timeout(15)
            worker_state.driver = driver
            with drivers_lock:
                drivers.append(driver)
        return driver

    def scrape_one(index, attempt_number, url):
        driver = get_worker_driver()
        review_count = scrape_place_review_count(driver, url, wait_timeout=8, max_attempts=1)
        if not driver_is_alive(driver):
            driver = restart_driver(
                driver,
                page_load_strategy,
                block_images=True,
                headless=False,
            )
            driver.set_page_load_timeout(15)
            worker_state.driver = driver
            with drivers_lock:
                drivers.append(driver)
            review_count = scrape_place_review_count(
                driver, url, wait_timeout=8, max_attempts=1
            )
        pause(0.35)
        return index, attempt_number, url, review_count

    jobs = []
    for attempt_number, (index, saved_row) in enumerate(missing.iterrows(), start=1):
        url = str(saved_row.get("google_maps_link", "")).strip()
        if url:
            jobs.append((index, attempt_number, url))

    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(scrape_one, index, attempt_number, url): (index, attempt_number, url)
                for index, attempt_number, url in jobs
            }
            for future in as_completed(futures):
                index, attempt_number, url = futures[future]
                try:
                    _, _, _, review_count = future.result()
                except Exception as exc:
                    log(f"  !! review repair skipped ({type(exc).__name__}: {str(exc)[:80]})")
                    continue
                if not review_count:
                    log(f"  .. review count is still unavailable ({attempt_number}/{len(missing)}): {url[:70]}")
                    continue

                data_df.at[index, "total_reviews"] = review_count
                changed += 1
                pending_writes += 1
                if pending_writes >= 10:
                    save_repaired_data()
                    pending_writes = 0
                    log(f"Saved review repair checkpoint ({changed} listings).")
    finally:
        for driver in drivers:
            try:
                driver.quit()
            except WebDriverException:
                pass

    if pending_writes:
        save_repaired_data()
    return changed


def save_status(status_rows=None, path=STATUS_OUTPUT_FILE):
    if not status_rows:
        return

    if os.path.exists(path):
        try:
            existing = pd.read_excel(path, sheet_name=STATUS_SHEET)
        except Exception:
            existing = pd.DataFrame(columns=STATUS_COLUMNS)
    else:
        existing = pd.DataFrame(columns=STATUS_COLUMNS)

    combined = pd.concat([existing, pd.DataFrame(status_rows)], ignore_index=True)
    if not combined.empty:
        combined = combined[STATUS_COLUMNS]
        combined = combined.drop_duplicates(subset=["location", "status"], keep="last")

    try:
        with pd.ExcelWriter(path, engine="openpyxl") as xl:
            combined.to_excel(xl, index=False, sheet_name=STATUS_SHEET)
        log(f"Saved status file -> {os.path.abspath(path)}")
    except PermissionError:
        log(f"Status file is locked; retry on next loop. Resume state will still be tracked by the scraper log.")

    try:
        status_df = combined if not combined.empty else pd.DataFrame(columns=STATUS_COLUMNS)
        data_df = pd.read_excel(OUTPUT_FILE, sheet_name=DATA_SHEET) if os.path.exists(OUTPUT_FILE) else pd.DataFrame(columns=DATA_COLUMNS)
        sync_live_google_sheet(data_df, status_df, sync_data=False)
    except Exception as exc:
        log(f"Status-triggered Google Sheets sync failed: {type(exc).__name__}: {str(exc)[:120]}")


def save_excel(rows, status_rows=None, path=OUTPUT_FILE):
    global _saved_place_keys
    if not rows:
        return

    if not _saved_place_keys:
        _, _saved_place_keys = load_existing_rows(path)
    new_rows = []
    pending_place_keys = set()
    known_place_keys = _saved_place_keys.copy()
    for row in rows:
        place_key = str(row.get("place_id") or "").strip()
        if not place_key:
            place_key = str(row.get("google_maps_link") or "").strip()
        if not place_key:
            place_key = f"{str(row.get('query') or '').strip()}|{str(row.get('name') or '').strip()}"
        if place_key and place_key in known_place_keys:
            continue
        if place_key:
            known_place_keys.add(place_key)
            pending_place_keys.add(place_key)
        new_rows.append(row)

    if not new_rows:
        return

    new_df = pd.DataFrame(new_rows)
    if not new_df.empty:
        new_df = new_df[[c for c in DATA_COLUMNS if c in new_df.columns]]

    try:
        from openpyxl import Workbook, load_workbook

        if os.path.exists(path):
            workbook = load_workbook(path)
            worksheet = workbook[DATA_SHEET] if DATA_SHEET in workbook.sheetnames else workbook.create_sheet(DATA_SHEET)
            if worksheet.max_row == 1 and worksheet.cell(1, 1).value is None:
                worksheet.append(DATA_COLUMNS)
        else:
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = DATA_SHEET
            worksheet.append(DATA_COLUMNS)

        for row in new_rows:
            worksheet.append(["" if pd.isna(row.get(column, "")) else row.get(column, "") for column in DATA_COLUMNS])
        worksheet.freeze_panes = "B2"
        temporary_path = f"{path}.tmp"
        workbook.save(temporary_path)
        os.replace(temporary_path, path)
        log(f"Appended {len(new_rows)} rows -> {os.path.abspath(path)}")
    except PermissionError:
        log(f"Data file is locked; skipping write for now. The program will retry on the next save cycle.")
        return

    _saved_place_keys.update(pending_place_keys)

    if status_rows:
        save_status(status_rows=status_rows)

    for row in new_rows:
        sync_live_listing(row)


def repair_missing_reviews(
    driver=None, page_load_strategy="normal", workers=1,
    zero_reviews_from_sheet_row=None,
):
    """Refetch only saved listings with a missing review count."""
    if not os.path.exists(OUTPUT_FILE):
        log(f"Data file not found: {OUTPUT_FILE}")
        return driver

    data_df = pd.read_excel(OUTPUT_FILE, sheet_name=DATA_SHEET)
    review_values = data_df["total_reviews"].fillna("").astype(str).str.strip()
    missing_mask = review_values.eq("")
    if zero_reviews_from_sheet_row is not None:
        sheet_rows = data_df.index + 2
        missing_mask |= review_values.eq("0") & (sheet_rows >= zero_reviews_from_sheet_row)
    missing = data_df[missing_mask]
    log(f"Review repair mode: {len(missing)} listings have blank or targeted zero review counts.")
    if missing.empty:
        return driver

    # Excel may infer these columns as float64 when some cells are blank.
    # Repair values are strings, so use object columns before assigning them.
    data_df["rating"] = data_df["rating"].astype(object)
    data_df["total_reviews"] = data_df["total_reviews"].astype(object)

    def save_repaired_data():
        temporary_path = f"{OUTPUT_FILE}.repair.tmp.xlsx"
        with pd.ExcelWriter(temporary_path, engine="openpyxl") as xl:
            data_df.reindex(columns=DATA_COLUMNS, fill_value="").to_excel(
                xl, index=False, sheet_name=DATA_SHEET
            )
        os.replace(temporary_path, OUTPUT_FILE)

    status_path = STATUS_OUTPUT_FILE
    status_df = (
        pd.read_excel(status_path, sheet_name=STATUS_SHEET)
        if os.path.exists(status_path)
        else pd.DataFrame(columns=STATUS_COLUMNS)
    )

    owns_driver = driver is None
    if owns_driver and workers == 1:
        driver = build_driver(page_load_strategy=page_load_strategy)
    if workers > 1:
        changed = repair_missing_reviews_parallel(
            data_df, missing, save_repaired_data, page_load_strategy, workers
        )
        driver = None
    else:
        changed = 0
        try:
            for attempt_number, (index, saved_row) in enumerate(missing.iterrows(), start=1):
                url = str(saved_row.get("google_maps_link", "")).strip()
                if not url:
                    continue
                try:
                    if not driver_is_alive(driver):
                        driver = restart_driver(driver, page_load_strategy)
                    log(f"Checking missing review count {attempt_number}/{len(missing)}.")
                    review_count = scrape_place_review_count(driver, url)
                    if not driver_is_alive(driver):
                        driver = restart_driver(driver, page_load_strategy)
                        review_count = scrape_place_review_count(driver, url)
                    if review_count:
                        data_df.at[index, "total_reviews"] = review_count
                        save_repaired_data()
                        changed += 1
                        log(f"Saved review repair {changed}/{len(missing)} -> {os.path.abspath(OUTPUT_FILE)}")
                    else:
                        log(f"  .. review count is still unavailable: {url[:70]}")
                except Exception as exc:
                    log(f"  !! review repair skipped ({type(exc).__name__}: {str(exc)[:80]})")
                    if not driver_is_alive(driver):
                        raise
                pause(1.0)
        finally:
            if owns_driver:
                try:
                    driver.quit()
                except WebDriverException:
                    pass

    if workers == 1:
        save_repaired_data()
    log(f"Repaired review data for {changed} listings -> {os.path.abspath(OUTPUT_FILE)}")

    sync_live_google_sheet(data_df, status_df, force=True)
    return driver


# =====================================================================
# 9. MAIN
# =====================================================================

def main():
    if not acquire_single_instance_lock():
        return 1

    repair_only = "--repair-missing-reviews-only" in sys.argv[1:]
    repair_mode = "--repair-missing-reviews" in sys.argv[1:] or repair_only
    if "--headless" in sys.argv[1:]:
        global HEADLESS
        HEADLESS = True

    special_flags = {"--repair-missing-reviews", "--repair-missing-reviews-only", "--headless"}
    queries = [arg for arg in sys.argv[1:] if arg not in special_flags]
    keep_awake = os.getenv("KEEP_AWAKE", "false").strip().lower() in {"1", "true", "yes"}
    if keep_awake:
        if set_system_awake(True):
            import atexit

            atexit.register(set_system_awake, False)
            log("System sleep prevention enabled; the display may still turn off or lock.")
        else:
            log("Could not enable Windows sleep prevention.")
    area_queries = load_area_queries()
    if not queries:
        queries = area_queries
    completed = load_completed_locations()
    pending_queries = [
        query for query in queries
        if re.sub(r"^restaurants in\s+", "", query, flags=re.I).strip().casefold() not in completed
    ]
    log(f"Resume status: skipped {len(queries) - len(pending_queries)} completed areas; {len(pending_queries)} pending areas.")
    queries = pending_queries
    if not queries and not repair_mode:
        log("No pending areas to scrape.")
        return

    existing_data, _ = load_existing_rows()
    existing_listing_urls = {
        canonical_listing_url(url)
        for url in existing_data.get("google_maps_link", pd.Series(dtype=str)).dropna()
    }
    log(f"Queries: {queries}")
    driver = None if repair_only else build_driver()
    rows = []
    status_rows = []

    if not os.path.exists(OUTPUT_FILE):
        pd.DataFrame(columns=DATA_COLUMNS).to_excel(OUTPUT_FILE, index=False, sheet_name=DATA_SHEET)
    if not os.path.exists(STATUS_OUTPUT_FILE):
        pd.DataFrame(columns=STATUS_COLUMNS).to_excel(STATUS_OUTPUT_FILE, index=False, sheet_name=STATUS_SHEET)

    if not repair_only:
        try:
            current_data = pd.read_excel(OUTPUT_FILE, sheet_name=DATA_SHEET)
            current_status = pd.read_excel(STATUS_OUTPUT_FILE, sheet_name=STATUS_SHEET)
            sync_live_google_sheet(current_data, current_status, force=True)
        except Exception as exc:
            log(f"Initial Google Sheets sync failed: {type(exc).__name__}: {str(exc)[:120]}")

    if repair_mode:
        log("Repair mode: checking saved listings for missing review counts.")
        if repair_only:
            try:
                repair_missing_reviews(
                    page_load_strategy="none",
                    workers=4,
                    zero_reviews_from_sheet_row=4330,
                )
            finally:
                if driver is not None:
                    try:
                        driver.quit()
                    except WebDriverException:
                        pass
                if keep_awake:
                    set_system_awake(False)
            log("Review repair-only run complete; no new-area scraping was started.")
            return 0
        log("Continuing with the normal scrape after review repair.")
        driver = repair_missing_reviews(driver) or driver

    try:
        for q in queries:
            location_name = re.sub(r"^restaurants in\s+", "", q, flags=re.I).strip()
            location_key = location_name.casefold()
            if location_key in completed:
                log(f"Skipping completed location: {location_name}")
                continue

            if not driver_is_alive(driver):
                driver = restart_driver(driver)

            status_rows.append({
                "location": location_name,
                "status": "START",
                "rows": 0,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            append_log("START", location_name, "searching")
            save_status(status_rows=[status_rows[-1]])

            log(f"=== SEARCHING: {q} ===")
            search_url = ("https://www.google.com/maps/search/"
                          + urllib.parse.quote_plus(q) + f"?hl={LANGUAGE}")
            try:
                driver.get(search_url)
            except WebDriverException:
                driver = restart_driver(driver)
                driver.get(search_url)
            handle_consent(driver)
            pause(1.5)

            target = max(MIN_RESULTS_PER_AREA, min(MAX_RESULTS_PER_QUERY, HARD_MAX_RESULTS_PER_AREA))
            try:
                links = collect_result_links(driver, target)
            except WebDriverException:
                driver = restart_driver(driver)
                driver.get(search_url)
                handle_consent(driver)
                pause(1.5)
                links = collect_result_links(driver, target)
            if len(links) < MIN_RESULTS_PER_AREA:
                log(f"Only {len(links)} results found for {location_name}; keeping available data.")
            log(f"Collected {len(links)} listing URLs for this query.")
            pending_links = filter_unsaved_listing_urls(links, existing_listing_urls)
            skipped_saved_count = len(links) - len(pending_links)
            if skipped_saved_count:
                log(
                    f"Resume point: skipped {skipped_saved_count} already-saved listings; "
                    f"{len(pending_links)} listings remain to process."
                )

            area_rows = []
            failed_details = 0
            for n, link in enumerate(pending_links, 1):
                log(f"[{n}/{len(pending_links)}] opening listing...")
                try:
                    row = scrape_place(driver, link, q)
                    if row:
                        rows.append(row)
                        area_rows.append(row)
                        existing_listing_urls.add(canonical_listing_url(row.get("google_maps_link")))
                        save_excel(rows)
                    else:
                        failed_details += 1
                except Exception as e:
                    failed_details += 1
                    log(f"  !! skipped ({type(e).__name__}: {str(e)[:80]})")
                pause(1.3)

            area_saved_count = len(area_rows)
            area_incomplete = area_saved_count < MIN_ACCEPTABLE_SAVED_ROWS and failed_details
            area_status = "PENDING" if area_incomplete else "DONE"
            if area_incomplete:
                log(f"Area incomplete: {failed_details} detail pages failed; it will retry next run.")
            else:
                completed.add(location_key)

            status_rows.append({
                "location": location_name,
                "status": area_status,
                "rows": area_saved_count,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            append_log(area_status, location_name, f"rows={area_saved_count}")
            save_status(status_rows=[status_rows[-1]])
            log(f"Area {area_status.lower()}: {location_name}. Moving to next area.")

    except KeyboardInterrupt:
        log("Stopped by user. Saving what we have...")
    finally:
        if rows:
            save_excel(rows, status_rows=status_rows)
        if status_rows:
            save_status(status_rows=status_rows)
        try:
            final_data = pd.read_excel(OUTPUT_FILE, sheet_name=DATA_SHEET)
            final_status = pd.read_excel(STATUS_OUTPUT_FILE, sheet_name=STATUS_SHEET)
            sync_live_google_sheet(final_data, final_status, force=True)
        except Exception as exc:
            log(f"Final Google Sheets sync failed: {type(exc).__name__}: {str(exc)[:120]}")
        log("Scrape complete. Closing browser...")
        try:
            driver.quit()
        finally:
            if keep_awake:
                set_system_awake(False)


if __name__ == "__main__":
    raise SystemExit(main())
