"""Small helpers shared by the builder."""
import math
import re
import unicodedata


def s(value):
    """Clean cell value -> stripped string ('' for None/NaN)."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def slugify(text):
    """URL slug. Mirrors slugify() in apps_script/Code.gs, keep both in sync."""
    text = re.sub(r"\([^)]*\)", " ", s(text))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def to_int(value):
    v = re.sub(r"[^\d]", "", s(value))
    return int(v) if v else None


def to_float(value):
    v = s(value).replace(",", "")
    try:
        return float(v)
    except ValueError:
        return None


def clean_image(url):
    """Return a usable Google-hosted photo URL, or None.

    Drops the placeholder avatars used in banner/logo, street-view thumbs and
    cuts the stray '&quot;);' that the scraper sometimes captures."""
    u = s(url)
    if not u:
        return None
    u = u.split("&quot;")[0].split('"')[0].split("\\")[0]
    if not u.startswith("https://lh3.googleusercontent.com/"):
        return None
    if "default-user" in u or "/ogw/" in u:
        return None
    return u


def thumb(url, w=400, h=300):
    """Resize a lh3 URL via its size suffix."""
    if not url:
        return url
    if re.search(r"=w\d+-h\d+(-[a-z-]+)?$", url):
        return re.sub(r"=w\d+-h\d+(-[a-z-]+)?$", f"=w{w}-h{h}-c", url)
    return url


def tel_href(phone):
    digits = re.sub(r"[^\d+]", "", s(phone))
    if not digits:
        return ""
    if digits.startswith("+"):
        return digits
    digits = digits.lstrip("0")
    if len(digits) == 10:
        return "+91" + digits
    return "+" + digits if len(digits) > 10 else digits


def clip(text, n):
    text = re.sub(r"\s+", " ", s(text))
    if len(text) <= n:
        return text
    cut = text[: n - 1].rsplit(" ", 1)[0].rstrip(",;:-. ")
    return cut + "…"


def fit(candidates, n):
    """First candidate that fits in n characters, else the last one clipped."""
    for c in candidates:
        if len(c) <= n:
            return c
    return clip(candidates[-1], n)


def paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", s(text)) if p.strip()]


def km_between(a_lat, a_lon, b_lat, b_lon):
    x = math.radians(b_lon - a_lon) * math.cos(math.radians((a_lat + b_lat) / 2))
    y = math.radians(b_lat - a_lat)
    return 6371 * math.hypot(x, y)


def initials(name):
    words = re.findall(r"[A-Za-z0-9]+", s(name))
    if not words:
        return (s(name)[:1] or "?").upper()
    return (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()


def cuisine_phrase(names):
    """'Popular cuisines include A, B and C.' (names only, no counts)"""
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return f"Popular here: {names[0]} food."
    return "Popular cuisines include " + ", ".join(names[:-1]) + f" and {names[-1]}."


def first_words(text, n=60):
    words = s(text).split()
    if len(words) <= n:
        return " ".join(words)
    cut = " ".join(words[:n])
    m = re.search(r"^(.*[.!?])\s", cut + " ")
    return m.group(1) if m and len(m.group(1).split()) >= 25 else cut.rstrip(",;:") + "."
