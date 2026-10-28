#!/usr/bin/env python3
"""Static site builder for the restaurant directory.

Reads the Restaurants DB sheet (Listings tab) and the Content CMS sheet
(Country / State / City tabs) and writes a complete static site to dist/.

Local test (files):
    python build.py --db ../data/google_maps_data.xlsx --content sample/content_sample.xlsx

Live (Google Sheets, used by the GitHub Action):
    GOOGLE_SERVICE_ACCOUNT_JSON=key.json python build.py \
        --db gsheet:<DB_SHEET_ID> --content gsheet:<CONTENT_SHEET_ID>
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

import data_sources
import photo_check
import photo_sync
from utils import (clean_image, clip, cuisine_phrase, first_words, fit, initials, km_between,
                   paragraphs, s, slugify, tel_href, thumb, to_float, to_int)

ROOT = Path(__file__).resolve().parent
GENERIC_CUISINES = {"", "restaurant"}
MAX_SECTIONS = 8
MAX_FAQS = 8


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
def load_config():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    for env, key in (("SITE_URL", "base_url"), ("BASE_PATH", "base_path"),
                     ("SITE_NAME", "site_name"), ("PHOTO_API", "photo_api"),
                     ("CONTACT_API", "contact_api"), ("CUSTOM_DOMAIN", "custom_domain")):
        if os.environ.get(env, "").strip():
            cfg[key] = os.environ[env].strip()
    cfg.setdefault("photo_api", "")
    cfg.setdefault("contact_api", "")
    cfg.setdefault("custom_domain", "")
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    bp = cfg.get("base_path", "").strip().strip("/")
    cfg["base_path"] = f"/{bp}" if bp else ""
    return cfg


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------
class Node:
    def __init__(self, kind, name, slug, parent=None):
        self.kind, self.name, self.slug, self.parent = kind, name, slug, parent
        self.path = f"{parent.path}/{slug}" if parent is not None and parent.path else slug
        self.children = {}
        self.restaurants = []

    def child(self, kind, name):
        slug = slugify(name)
        if slug not in self.children:
            self.children[slug] = Node(kind, name, slug, self)
        return self.children[slug]

    def ancestors(self):
        chain, n = [], self
        while n:
            chain.append(n)
            n = n.parent
        return list(reversed(chain))


CURRENCY = {"usa": "$", "united states": "$", "canada": "$", "australia": "$", "france": "€", "spain": "€", "italy": "€",
            "germany": "€", "uk": "£", "united kingdom": "£", "india": "₹"}


def local_price(level, country):
    """The scraper ran with an Indian locale, so price tiers come as rupee signs. Show the local currency sign instead."""
    if level and re.fullmatch(r"[₹$€£]{1,4}", level):
        return CURRENCY.get(country.strip().lower(), "$")[0] * len(level)
    return "" if level and "₹" in level else level


def load_reviews(rec, limit=10):
    """Optional columns review_1_text, review_1_author, review_1_rating, review_1_time ... (up to 10)."""
    out = []
    for n in range(1, limit + 1):
        text = re.sub(r"\s+", " ", s(rec.get(f"review_{n}_text"))).strip()
        if len(text) < 15:
            continue
        rt = to_float(rec.get(f"review_{n}_rating"))
        out.append({"text": text[:600], "author": s(rec.get(f"review_{n}_author")) or "Google user",
                    "rating": int(round(rt)) if rt else 0, "when": s(rec.get(f"review_{n}_time"))})
    return out


def load_restaurants(df, report, ignore_names=()):
    ignore = {n.strip().lower() for n in ignore_names}
    rows, seen = [], {}
    photo_cols = [c for c in df.columns if re.fullmatch(r"photo_\d+", c)]
    photo_cols.sort(key=lambda c: int(c.split("_")[1]))
    for i, rec in enumerate(df.to_dict("records"), start=2):
        r = {k: s(rec.get(k)) for k in (
            "name", "country", "state", "city", "area", "address", "postal_code",
            "cuisine_type", "phone", "website", "google_maps_link", "place_id",
            "price_level", "scraped_at")}
        if not r["area"] and r["city"]:          # tabs without an area column value: use the postal code, else the centre
            r["area"] = f"{r['city']} {r['postal_code']}".strip() if r["postal_code"] else f"{r['city']} Centre"
        missing = [k for k in ("name", "country", "state", "city", "area", "place_id") if not r[k]]
        if missing:
            report["skipped_incomplete"].append(f"row {i}: {r['name'] or '(no name)'} missing {','.join(missing)}")
            continue
        if r["name"].lower() in ignore:           # scraper capture errors, e.g. name = "Google Maps"
            report["skipped_junk_name"] += 1
            continue
        r["rating"] = to_float(rec.get("rating"))
        r["reviews"] = to_int(rec.get("total_reviews"))
        r["lat"], r["lon"] = to_float(rec.get("latitude")), to_float(rec.get("longitude"))
        cuisine = re.sub(r"\s+restaurants?$", "", r["cuisine_type"], flags=re.I).strip()
        r["cuisine_short"] = cuisine if cuisine.lower() not in GENERIC_CUISINES else ""
        r["price"] = local_price(r["price_level"], r["country"])
        site = r["website"]
        r["website"] = site if site.lower().startswith(("http://", "https://")) else ""
        r["tel"] = tel_href(r["phone"], r["country"])
        photos = []
        for c in photo_cols:
            u = clean_image(rec.get(c))
            if u and u not in photos:
                photos.append(u)
        r["photos"] = photos
        r["reviews_link"] = s(rec.get("google_reviews_link"))
        r["review_list"] = load_reviews(rec)
        pid = r["place_id"]
        if pid in seen:                     # duplicate place_id: keep the newest scrape
            report["duplicates_dropped"] += 1
            if r["scraped_at"] < rows[seen[pid]]["scraped_at"]:
                continue
            rows[seen[pid]] = r
            continue
        seen[pid] = len(rows)
        rows.append(r)
    return rows


def build_tree(restaurants, report):
    root = Node("root", "root", "")
    for r in restaurants:
        c = root.child("country", r["country"])
        st = c.child("state", r["state"])
        ct = st.child("city", r["city"])
        ar = ct.child("area", r["area"])
        for node, label in ((c, r["country"]), (st, r["state"]), (ct, r["city"]), (ar, r["area"])):
            if node.name != label:
                report["name_variants"].add(f"{label!r} merged into {node.name!r}")
        ar.restaurants.append(r)
        r["area_node"] = ar
    # slugs for restaurants (stable: first-seen keeps the plain slug)
    def walk(n):
        yield n
        for ch in n.children.values():
            yield from walk(ch)
    for area in (n for n in walk(root) if n.kind == "area"):
        groups = defaultdict(list)
        for r in area.restaurants:
            groups[slugify(r["name"]) or "restaurant"].append(r)
        for base, grp in groups.items():
            grp.sort(key=lambda r: (r["scraped_at"], r["place_id"]))
            used = set()
            for idx, r in enumerate(grp):
                slug = base
                if idx:
                    slug = f"{base}-{re.sub(r'[^a-z0-9]', '', r['place_id'].lower())[-6:]}"
                    while slug in used:
                        slug += "x"
                used.add(slug)
                r["slug"] = slug
                r["path"] = f"{area.path}/{slug}"
    # roll restaurants up the tree
    for area in (n for n in walk(root) if n.kind == "area"):
        for anc in area.ancestors()[1:-1]:      # country, state, city (not the area itself)
            anc.restaurants.extend(area.restaurants)
    return root, walk


def score(r, mean, m=50):
    v = r["reviews"] or 0
    return (v / (v + m)) * (r["rating"] or 0) + (m / (v + m)) * mean


def compute_stats(root, walk, all_rest):
    rated = [r["rating"] for r in all_rest if r["rating"]]
    mean = sum(rated) / len(rated) if rated else 4.0
    for r in all_rest:
        r["score"] = score(r, mean)
    for n in walk(root):
        if n.kind == "root":
            continue
        rs = n.restaurants
        ratings = [r["rating"] for r in rs if r["rating"]]
        n.count = len(rs)
        n.avg = round(sum(ratings) / len(ratings), 1) if ratings else None
        n.cuisines = Counter(r["cuisine_short"] for r in rs if r["cuisine_short"]).most_common(10)
        n.ranked = sorted(rs, key=lambda r: r["score"], reverse=True)
        n.top = n.ranked[:12]
        lats = [r["lat"] for r in rs if r["lat"] is not None]
        lons = [r["lon"] for r in rs if r["lon"] is not None]
        n.center = (sum(lats) / len(lats), sum(lons) / len(lons)) if lats and lons else None
        n.lastmod = max((r["scraped_at"][:10] for r in rs if re.match(r"\d{4}-\d{2}-\d{2}", r["scraped_at"])),
                        default="")


# --------------------------------------------------------------------------
# content sheet
# --------------------------------------------------------------------------
def load_content(tables, include_review, report):
    content = {}
    ok_status = {"approved"} | ({"review"} if include_review else set())
    for level, df in tables.items():
        if df is None or df.empty or "path" not in df.columns:
            continue
        for rec in df.to_dict("records"):
            path = s(rec.get("path")).strip("/")
            if not path or s(rec.get("status")).lower() not in ok_status:
                continue
            sections = []
            for i in range(1, MAX_SECTIONS + 1):
                h, b = s(rec.get(f"s{i}_h2")), s(rec.get(f"s{i}_body"))
                if h and b:
                    sections.append({"h2": h, "body": b})
            faqs = []
            for i in range(1, MAX_FAQS + 1):
                q, a = s(rec.get(f"faq{i}_q")), s(rec.get(f"faq{i}_a"))
                if q and a:
                    faqs.append({"q": q, "a": a})
            words = sum(len(x.split()) for x in [s(rec.get("intro"))] + [x["body"] for x in sections])
            content[path] = {
                "level": level, "h1": s(rec.get("h1")), "meta_title": s(rec.get("meta_title")),
                "meta_description": s(rec.get("meta_description")), "intro": s(rec.get("intro")),
                "short_intro": s(rec.get("short_intro")),
                "sections": sections, "faqs": faqs, "words": words,
            }
    return content


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
BUILD_ID = datetime.now().strftime("%Y%m%d%H%M%S")


class Site:
    def __init__(self, cfg, out):
        self.cfg, self.out = cfg, out
        self.pages = []              # (path, lastmod, indexable)
        self.page_images = {}        # path -> absolute photo URLs (image sitemap)
        self.env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                               autoescape=select_autoescape(["html"]),
                               trim_blocks=True, lstrip_blocks=True)
        self.env.filters["paragraphs"] = paragraphs
        self.env.globals.update(thumb=self.thumb, initials=initials, site=cfg, u=self.u, asset=self.asset,
                                year=datetime.now().year, built_on=datetime.now().strftime("%d %B %Y").lstrip("0"))

    def thumb(self, url, w=400, h=300):
        return thumb(url, w, h) if self.cfg.get("photo_resize", True) else url

    def u(self, path=""):
        path = path.strip("/")
        return f"{self.cfg['base_path']}/{path}/" if path else f"{self.cfg['base_path']}/"

    def asset(self, path):
        url = f"{self.cfg['base_path']}/{path.lstrip('/')}"
        if path.lstrip("/").startswith("static/") and path.endswith((".css", ".js")):
            url += "?v=" + BUILD_ID                      # new URL after every build, so browsers never keep an old stylesheet
        return url

    def canonical(self, path):
        return self.cfg["base_url"] + self.u(path)

    def write(self, path, template, indexable=True, lastmod="", sitemap=True, **ctx):
        ctx.setdefault("noindex", not indexable)
        ctx["canonical"] = self.canonical(path)
        html = self.env.get_template(template).render(**ctx)
        dest = self.out / path.strip("/") / "index.html" if path.strip("/") else self.out / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        self.pages.append((path.strip("/"), lastmod, indexable and sitemap))


def crumbs_for(site, node, last_link=False):
    items = [("Home", site.u())]
    chain = node.ancestors()[1:]
    for i, n in enumerate(chain):
        items.append((n.name, site.u(n.path) if (i < len(chain) - 1 or last_link) else None))
    return items


def breadcrumb_schema(site, items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": name,
         "item": site.cfg["base_url"] + href if href else None}
        for i, (name, href) in enumerate(items)]}


def finalize_breadcrumb_schema(schema, page_url):
    for el in schema["itemListElement"]:
        if el["item"] is None:
            el["item"] = page_url
    return schema


def faq_schema(faqs):
    return {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": f["q"],
         "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in faqs]}


PAGE_SIZE = 24


def page_path(base, n):
    return base if n == 1 else f"{base}/page/{n}"


def pagination(site, base, page, total):
    """Pager data for the template: prev/next links and a windowed list of page numbers."""
    if total <= 1:
        return None
    lo, hi = max(1, page - 2), min(total, page + 2)
    if page <= 3:
        lo, hi = 1, min(total, 5)
    elif page >= total - 2:
        lo, hi = max(1, total - 4), total
    show = sorted({n for n in [1, total, *range(lo, hi + 1)] if 1 <= n <= total})
    items, prev = [], 0
    for n in show:
        if prev and n - prev > 1:
            items.append(None)
        items.append({"n": n, "href": site.u(page_path(base, n)), "current": n == page})
        prev = n
    return {"page": page, "total": total,
            "prev": site.u(page_path(base, page - 1)) if page > 1 else "",
            "next": site.u(page_path(base, page + 1)) if page < total else "", "items": items}


def country_phrase(countries):
    names = [c.name for c in countries]
    if len(names) == 1:
        return f"in {names[0]}"
    if len(names) <= 3:
        return "in " + ", ".join(names[:-1]) + " and " + names[-1]
    return "around the world"


def absolute(site, url):
    """Full URL for structured data and og:image (local photos are stored as /base_path/photos/...)."""
    return url if url.startswith("http") else site.cfg["base_url"].rstrip("/") + url


def decorate(site, r):
    r["href"] = site.u(r["path"])
    return r



def pick(path, options):
    """Deterministic variant per page so wording differs between pages but never changes between builds."""
    return options[int(hashlib.md5(path.encode()).hexdigest(), 16) % len(options)]


INTRO_VARIANTS = {
    "area": [
        "Hungry in {place}? Browse the restaurants in the area and open any listing for the "
        "rating, cuisine, address and phone number, plus photos where available, "
        "and a tap takes you to directions. {cuisines}Use the filters to narrow by cuisine or rating.",
        "Planning a meal in {place}? Compare restaurants in the area side by side: ratings from Google Maps, cuisine "
        "type, address and phone number, with photos to help you choose. Start with the top rated places below, or "
        "filter by the food you feel like. {cuisines}",
        "Find a good place to eat in {place}. Browse cafes, casual spots and sit-down restaurants, see how diners rate "
        "them on Google Maps, and open a listing for the address, contact details and directions. {cuisines}",
    ],
    "city": [
        "Looking for the best restaurants in {place}? Start with the top rated places below, or pick your neighbourhood "
        "to see every restaurant in that area. Each listing has the Google Maps rating, cuisine, address, phone number "
        "and photos, so you can decide quickly. {cuisines}",
        "Discover where to eat in {place}. Browse area by area, compare ratings and cuisines, and open any restaurant "
        "for its address, phone number, photos and directions. The highest rated places are listed first. {cuisines}",
    ],
    "state": [
        "Find where to eat in {place}. Choose a city to explore its restaurants neighbourhood by neighbourhood, or start "
        "with the highest rated places across the state. Every listing shows the Google Maps rating, cuisine, address "
        "and contact details. {cuisines}",
        "Planning a trip or a night out in {place}? Pick a city below to browse restaurants by area, compare ratings and "
        "cuisines, and check addresses, phone numbers and photos before you go. {cuisines}",
    ],
    "country": [
        "Find the best restaurants in {place}, city by city. Choose a state or city to browse restaurants by "
        "neighbourhood, compare Google Maps ratings and cuisines, and get addresses, phone numbers, photos and "
        "directions. Start with the top rated places below. {cuisines}",
    ],
}


def place_label(node):
    return f"{node.name}, {node.parent.name}" if node.kind == "area" else node.name


def generic_intro(node):
    cuisines = cuisine_phrase([c for c, _ in node.cuisines[:3]])
    text = pick(node.path, INTRO_VARIANTS[node.kind]).format(place=place_label(node), cuisines=(cuisines + " ") if cuisines else "")
    return re.sub(r"\s+", " ", text).strip()


def generic_desc(node):
    return clip(f"Best restaurants in {place_label(node)}: browse top rated places with cuisines, "
                "photos, addresses, phone numbers and directions.", 155)




def render_place(site, node, content, report):
    c = content.get(node.path)
    kind = node.kind
    name, sname = node.name, site.cfg["site_name"]
    children = sorted(node.children.values(), key=lambda n: -n.count)
    tiles = [{"name": ch.name, "href": site.u(ch.path), "sub": ""} for ch in children]
    heading = {"country": f"Explore {name} state by state", "state": f"Explore {name} city by city",
               "city": f"Explore {name} neighbourhood by neighbourhood"}[kind]
    secondary = None
    if kind == "country":
        cities = sorted((ct for st in node.children.values() for ct in st.children.values()), key=lambda n: -n.count)[:12]
        secondary = {"heading": f"Popular cities in {name}",
                     "tiles": [{"name": ct.name, "href": site.u(ct.path), "sub": ct.parent.name} for ct in cities]}
    h1 = (c and c["h1"]) or f"Best Restaurants in {name}"
    title = (c and c["meta_title"]) or fit([f"Best Restaurants in {name}: Ratings & Photos | {sname}",
                                             f"Best Restaurants in {name} | {sname}", f"Restaurants in {name} | {sname}",
                                             f"Restaurants in {name}"], 60)
    desc = (c and c["meta_description"]) or generic_desc(node)
    # layout: short generic paragraph under the H1, long location text at the bottom
    paras = paragraphs(c["intro"]) if c else []
    if c and c["short_intro"]:
        top_intro, bottom_intro = c["short_intro"], paras
    elif paras:
        top_intro, bottom_intro = paras[0], paras[1:]
    else:
        top_intro, bottom_intro = generic_intro(node), []
    crumbs = crumbs_for(site, node)
    schema = [finalize_breadcrumb_schema(breadcrumb_schema(site, crumbs), site.canonical(node.path))]
    if c and c["faqs"]:
        schema.append(faq_schema(c["faqs"]))
    if not c:
        report["pages_without_content"].append(node.path)
    elif c["words"] < 600:
        report["short_content"].append(f"{node.path}: {c['words']} words")
    if c and len(title) > 65:
        report["long_titles"].append(f"{node.path}: {len(title)} chars")

    size = int(site.cfg.get("page_size", PAGE_SIZE))
    total = max(1, -(-len(node.ranked) // size))
    for page in range(1, total + 1):
        chunk = [decorate(site, r) for r in node.ranked[(page - 1) * size: page * size]]
        pager = pagination(site, node.path, page, total)
        if page == 1:
            site.write(node.path, "place.html", indexable=bool(c), lastmod=node.lastmod, title=title, description=desc,
                       schema=schema, crumbs=crumbs, node=node, h1=h1, top_intro=top_intro, bottom_intro=bottom_intro,
                       sections=(c and c["sections"]) or [], faqs=(c and c["faqs"]) or [], tiles=tiles,
                       tiles_heading=heading, secondary=secondary, listing=chunk, pager=pager, first_page=True,
                       top_heading=f"Top rated restaurants in {name}", og_type="website")
        else:
            pcrumbs = crumbs_for(site, node, last_link=True) + [(f"Page {page}", None)]
            ptitle = fit([f"Restaurants in {name}, Page {page} | {sname}", f"{name} Restaurants, Page {page}",
                          f"Page {page} | {name}"], 60)
            pschema = [finalize_breadcrumb_schema(breadcrumb_schema(site, pcrumbs), site.canonical(page_path(node.path, page)))]
            site.write(page_path(node.path, page), "place.html", indexable=bool(c), sitemap=False, lastmod=node.lastmod,
                       title=ptitle, description=clip(f"More restaurants in {name}, with ratings, photos and directions. Page {page} "
                                                      f"of {total}.", 155),
                       schema=pschema, crumbs=pcrumbs, node=node, h1=h1, top_intro=top_intro,
                       bottom_intro=[], sections=[], faqs=[], tiles=[], tiles_heading="", secondary=None, listing=chunk,
                       pager=pager, first_page=False, top_heading=f"Top rated restaurants in {name}", og_type="website")



def render_area(site, area, report):
    city, state = area.parent, area.parent.parent
    indexable = area.count >= site.cfg["min_area_restaurants"]
    if not indexable:
        report["thin_areas"].append(f"{area.path} ({area.count})")
    rs = sorted(area.restaurants, key=lambda r: r["score"], reverse=True)
    for r in rs:
        decorate(site, r)
    sname = site.cfg["site_name"]
    title = fit([f"Best Restaurants in {area.name}, {city.name}: Ratings & Photos | {sname}",
                 f"Best Restaurants in {area.name}, {city.name} | {sname}", f"Restaurants in {area.name}, {city.name} | {sname}",
                 f"Restaurants in {area.name}, {city.name}"], 60)
    desc = generic_desc(area)
    nearby = [a for a in city.children.values() if a is not area and a.count]
    if area.center:
        nearby = [a for a in nearby if a.center]
        nearby.sort(key=lambda a: km_between(*area.center, *a.center))
    else:
        nearby.sort(key=lambda a: -a.count)
    crumbs = crumbs_for(site, area)
    size = int(site.cfg.get("area_page_size", 100))
    total = max(1, -(-len(rs) // size))
    cuisines = [c for c, _ in area.cuisines]
    for page in range(1, total + 1):
        chunk = rs[(page - 1) * size: page * size]
        pcrumbs = crumbs if page == 1 else crumbs_for(site, area, last_link=True) + [(f"Page {page}", None)]
        schema = [finalize_breadcrumb_schema(breadcrumb_schema(site, pcrumbs), site.canonical(page_path(area.path, page)))]
        schema.append({"@context": "https://schema.org", "@type": "ItemList", "name": f"Restaurants in {area.name}, {city.name}",
                       "itemListElement": [{"@type": "ListItem", "position": (page - 1) * size + i + 1,
                                            "url": site.cfg["base_url"] + r["href"], "name": r["name"]}
                                           for i, r in enumerate(chunk[:50])]})
        site.write(page_path(area.path, page), "area.html", indexable=indexable, sitemap=(page == 1), lastmod=area.lastmod,
                   title=title if page == 1 else fit([f"Restaurants in {area.name}, {city.name}, Page {page} | {sname}",
                                                      f"Restaurants in {area.name}, Page {page}"], 60),
                   description=desc, schema=schema, crumbs=pcrumbs, node=area, city=city, state=state,
                   h1=f"Best Restaurants in {area.name}, {city.name}",
                   intro=generic_intro(area), restaurants=chunk, cuisines=cuisines,
                   pager=pagination(site, area.path, page, total),
                   nearby=[{"name": a.name, "href": site.u(a.path), "sub": ""} for a in nearby[:12]] if page == 1 else [],
                   og_type="website")


def restaurant_copy(r):
    """About paragraph and FAQs for a restaurant page, written only from the listing's own data."""
    kind = (r["cuisine_short"] + " restaurant") if r["cuisine_short"] else "restaurant"
    article = "an" if kind[0].lower() in "aeiou" else "a"
    about = [f"{r['name']} is {article} {kind} in {r['area']}, {r['city']}."]
    if r["rating"]:
        about[0] += (f" Diners rate it {r['rating']} out of 5 on Google Maps"
                     + (f" from {r['reviews']:,} reviews." if r["reviews"] else "."))
    more = []
    if r["price"]:
        more.append(f"The price level is shown as {r['price']}.")
    if r["cuisine_type"] and r["cuisine_type"].lower() != (r["cuisine_short"] or "").lower():
        more.append(f"Google lists it as: {r['cuisine_type']}.")
    more.append("Use the buttons above to call, open directions or visit the website, and scroll down for the full "
                "address and nearby restaurants.")
    about.append(" ".join(more))
    faqs = [{"q": f"Where is {r['name']} located?", "a": f"{r['name']} is at {r['address']}." if r["address"]
             else f"{r['name']} is in {r['area']}, {r['city']}. Open the Google Maps listing for the exact location."}]
    if r["cuisine_short"] or r["cuisine_type"]:
        faqs.append({"q": f"What kind of food does {r['name']} serve?",
                     "a": f"{r['name']} is listed on Google Maps as {r['cuisine_type'] or r['cuisine_short']}."})
    if r["phone"]:
        faqs.append({"q": f"What is the phone number of {r['name']}?", "a": f"You can call {r['name']} on {r['phone']}."})
    if r["rating"]:
        faqs.append({"q": f"How is {r['name']} rated?",
                     "a": f"{r['name']} has a Google Maps rating of {r['rating']} out of 5"
                          + (f" based on {r['reviews']:,} reviews." if r["reviews"] else ".")
                          + " Ratings come from Google Maps and can change over time."})
    faqs.append({"q": f"What are the opening hours of {r['name']}?",
                 "a": "Opening hours can change, especially on holidays. Open the Google Maps listing from this page for the "
                      "current hours before you visit."})
    return about, faqs


def render_restaurant(site, r, report):
    area = r["area_node"]
    city, state = area.parent, area.parent.parent
    decorate(site, r)
    kind = r["cuisine_short"] or "Restaurant"
    place = f"{r['area']}, {r['city']}"
    title = fit([f"{r['name']}, {place} | {kind}", f"{r['name']}, {place}", f"{r['name']} | {r['city']}", r["name"]], 60)
    bits = [f"{r['name']}: {r['cuisine_short'] + ' restaurant' if r['cuisine_short'] else 'restaurant'} in {place}."]
    if r["rating"]:
        bits.append(f"Rated {r['rating']} on Google Maps" + (f" from {r['reviews']:,} reviews." if r["reviews"] else "."))
    bits.append("See photos, address, phone number and directions.")
    desc = clip(" ".join(bits), 155)
    crumbs = crumbs_for(site, area, last_link=True) + [(r["name"], None)]
    related_area = [x for x in sorted(area.restaurants, key=lambda x: -x["score"]) if x is not r][:6]
    same_cuisine = []
    if r["cuisine_short"]:
        same_cuisine = [x for x in sorted(city.restaurants, key=lambda x: -x["score"])
                        if x is not r and x["cuisine_short"] == r["cuisine_short"] and x not in related_area][:6]
    for x in related_area + same_cuisine:
        decorate(site, x)
    r["directions"] = (f"https://www.google.com/maps/dir/?api=1&destination={r['lat']},{r['lon']}"
                       f"&destination_place_id={r['place_id']}") if r["lat"] is not None and r["lon"] is not None else r["google_maps_link"]
    page_url = site.canonical(r["path"])
    r["about"], r["faqs"] = restaurant_copy(r)
    schema = [finalize_breadcrumb_schema(breadcrumb_schema(site, crumbs), page_url)]
    schema.append(faq_schema(r["faqs"]))
    ld = {"@context": "https://schema.org", "@type": "Restaurant", "name": r["name"], "url": page_url,
          "address": {"@type": "PostalAddress", "streetAddress": r["address"], "addressLocality": r["city"],
                      "addressRegion": r["state"], "addressCountry": r["country"]}}
    if r["postal_code"]:
        ld["address"]["postalCode"] = r["postal_code"]
    if r["lat"] is not None and r["lon"] is not None:
        ld["geo"] = {"@type": "GeoCoordinates", "latitude": r["lat"], "longitude": r["lon"]}
    if r["phone"]:
        ld["telephone"] = r["phone"]
    if r["cuisine_short"]:
        ld["servesCuisine"] = r["cuisine_short"]
    if r["photos"]:
        ld["image"] = [absolute(site, x) for x in r["photos"][:5]]
        site.page_images[r["path"]] = [absolute(site, x) for x in r["photos"][:5]]
    if r["google_maps_link"]:
        ld["hasMap"] = r["google_maps_link"]
    if r["price"] and re.fullmatch(r"[₹$€£]{1,4}", r["price"]):
        ld["priceRange"] = r["price"]
    if r["website"]:
        ld["sameAs"] = [r["website"]]
    schema.append(ld)
    site.write(r["path"], "restaurant.html", indexable=True, lastmod=r["scraped_at"][:10], title=title,
               description=desc, schema=schema, crumbs=crumbs, r=r, area=area, city=city, state=state,
               related_area=related_area, same_cuisine=same_cuisine, og_type="place", body_class="has-mbar",
               og_image=absolute(site, thumb(r["photos"][0], 1200, 630)) if r["photos"] else "")




def node_photo(node):
    """First working photo among a place's top rated restaurants (used as the tile picture)."""
    for r in getattr(node, "top", []) or []:
        if r["photos"]:
            return r["photos"][0]
    return ""


HOME_FAQS = [
    ("Which countries does {site} cover?",
     "{site} currently lists restaurants in {countries}, and new countries and cities are added regularly. Choose a "
     "country on the home page, then a city and a neighbourhood."),
    ("How do I find a good restaurant on {site}?",
     "Pick your city, choose a neighbourhood, and open any restaurant to see its rating, photos, address, phone "
     "number and directions. Area pages also let you filter by cuisine."),
    ("Where do the ratings and photos come from?",
     "Ratings, review counts, addresses, phone numbers and photos come from public Google Maps listings. The ratings "
     "are Google's figures, not reviews written on {site}. Photos are checked automatically before they are shown."),
    ("Can I get a restaurant's phone number and directions?",
     "Yes. Every restaurant page shows the address and the phone number when one is listed on Google Maps, with "
     "one-tap buttons to call or open directions."),
    ("How do I find restaurants near me?",
     "Choose a country, then a city, then your neighbourhood, or type a restaurant, area or city into the search box. "
     "Each area page lists its restaurants from the best rated downwards, with filters for cuisine and rating."),
    ("A restaurant has closed or its details are wrong. What should I do?",
     "Tell us on the contact page with the address of the page and what needs to change. We will review it and update "
     "the listing."),
]


def interleave(groups):
    """Round robin across countries so no single country fills a list: [[a1,a2],[b1]] -> a1,b1,a2."""
    out, i = [], 0
    while any(i < len(g) for g in groups):
        out += [g[i] for g in groups if i < len(g)]
        i += 1
    return out


def render_home(site, root, all_rest):
    countries = sorted(root.children.values(), key=lambda n: -n.count)
    multi = len(countries) > 1
    by_country = [sorted((ct for st in c.children.values() for ct in st.children.values()), key=lambda n: -n.count)
                  for c in countries]
    all_cities = interleave(by_country)
    names = sorted(c.name for c in countries)
    country_list = (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1])
    top = sorted(all_rest, key=lambda r: r["score"], reverse=True)[:8]
    phrase = "around the world"
    sname = site.cfg["site_name"]
    title = fit([f"Best Restaurants {phrase}: Ratings, Photos & Directions | {sname}", f"Best Restaurants {phrase} | {sname}",
                 f"{sname}: Find Restaurants", sname], 60)
    desc = clip(f"Find the best restaurants {phrase}. Browse city by city and area by area, compare Google Maps ratings and "
                "cuisines, see photos, and get addresses, phone numbers and directions.", 155)
    faqs = [{"q": q.format(site=sname), "a": a.format(site=sname, countries=country_list)} for q, a in HOME_FAQS]
    logo = site.cfg["base_url"] + site.cfg["base_path"] + "/static/icon-512.png"
    schema = [
        {"@context": "https://schema.org", "@type": "Organization", "name": sname, "url": site.canonical(""), "logo": logo,
         "description": f"A worldwide restaurant guide organised by country, city and neighbourhood."},
        {"@context": "https://schema.org", "@type": "WebSite", "name": sname, "url": site.canonical(""),
         "potentialAction": {"@type": "SearchAction", "target": {"@type": "EntryPoint",
                             "urlTemplate": site.canonical("") + "?q={search_term_string}"},
                             "query-input": "required name=search_term_string"}},
        faq_schema(faqs),
    ]
    sub = lambda ct: f"{ct.parent.name}, {ct.parent.parent.name}"

    def best_with_photos(node, n=4):
        pool = node.ranked[:40]
        pick_ = [r for r in pool if r["photos"]][:n]
        pick_ += [r for r in pool if r not in pick_][: n - len(pick_)]
        return [decorate(site, r) for r in pick_]

    city_rows = [{"name": ct.name, "href": site.u(ct.path), "restaurants": best_with_photos(ct)} for ct in [g[0] for g in by_country if g][:4]]
    site.write("", "home.html", indexable=True, lastmod=max((r["scraped_at"][:10] for r in all_rest), default=""),
               title=title, description=desc, schema=schema, hero_title=f"Find the best restaurants {phrase}",
               country_tiles=[{"name": c.name, "href": site.u(c.path), "sub": "Explore " + c.name, "img": node_photo(c)} for c in sorted(countries, key=lambda n: n.name)],
               hero_cities=[{"name": ct.name, "href": site.u(ct.path)} for ct in all_cities[:8]],
               city_tiles=[{"name": ct.name, "href": site.u(ct.path), "sub": sub(ct), "img": node_photo(ct)} for ct in all_cities[:12]],
               city_rows=city_rows, faqs=faqs, multi=multi,
               top=[decorate(site, r) for r in top], og_type="website",
               hero_photos=interleave([[r["photos"][0] for r in sorted((x for x in all_rest if x["country"] == c.name and x["photos"]),
                                                                 key=lambda r: r["score"], reverse=True)[:4]] for c in countries])[:4],
               country_list=country_list)


STATIC_PAGES = {
    "about": ("About", "about.html", "About {site}: how this restaurant guide is built, where the data comes from and how to find the right restaurant."),
    "contact": ("Contact", "contact.html", "Contact {site} to report a correction, a closed restaurant or a listing removal, or to get in touch."),
    "privacy-policy": ("Privacy Policy", "privacy.html", "How {site} handles visitor data, cookies, third-party services and messages sent through the contact form."),
    "terms": ("Terms of Use", "terms.html", "The terms for using {site}, including a disclaimer about restaurant details, ratings and photos."),
}


def render_static_pages(site):
    for slug, (name, tpl, desc) in STATIC_PAGES.items():
        crumbs = [("Home", site.u()), (name, None)]
        schema = [finalize_breadcrumb_schema(breadcrumb_schema(site, crumbs), site.canonical(slug))]
        site.write(slug, tpl, indexable=True, title=f"{name} | {site.cfg['site_name']}",
                   description=desc.format(site=site.cfg["site_name"]), schema=schema, crumbs=crumbs, og_type="website")
    site.write("404.html".replace(".html", ""), "404.html", indexable=False, title=f"Page not found | {site.cfg['site_name']}",
               description="Page not found.", schema=[], crumbs=[], og_type="website")
    # 404 must live at /404.html for GitHub Pages / Cloudflare Pages
    src = site.out / "404" / "index.html"
    shutil.move(str(src), str(site.out / "404.html"))
    (site.out / "404").rmdir()
    site.pages = [p for p in site.pages if p[0] != "404"]


def write_sitemap_and_robots(site):
    base = site.cfg["base_url"]
    urls = []
    for path, lastmod, indexable in sorted(site.pages):
        if not indexable:
            continue
        loc = base + site.u(path)
        imgs = "".join(f"<image:image><image:loc>{xml_escape(i)}</image:loc></image:image>"
                       for i in site.page_images.get(path, []))
        urls.append(f"  <url><loc>{xml_escape(loc)}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + imgs + "</url>")
    (site.out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
        + "\n".join(urls) + "\n</urlset>\n", encoding="utf-8")
    (site.out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nDisallow: {site.cfg['base_path']}/photo-audit/\n\nSitemap: {base}{site.cfg['base_path']}/sitemap.xml\n",
                                         encoding="utf-8")
    return len(urls)


def write_search_index(site, all_rest):
    rows = [[r["name"], r["area"], r["city"], r["path"]] for r in sorted(all_rest, key=lambda r: -r["score"])]
    (site.out / "search-index.json").write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
                                                encoding="utf-8")


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("DB_SOURCE") or (
        f"gsheet:{os.environ['DB_SHEET_ID']}" if os.environ.get("DB_SHEET_ID") else ""))
    ap.add_argument("--content", default=os.environ.get("CONTENT_SOURCE") or (
        f"gsheet:{os.environ['CONTENT_SHEET_ID']}" if os.environ.get("CONTENT_SHEET_ID") else "none"))
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--include-review", action="store_true",
                    help="Also publish content rows with status Review (preview only).")
    ap.add_argument("--min-restaurants", type=int, default=int(os.environ.get("MIN_RESTAURANTS", "100")),
                    help="Abort if fewer restaurants load (protects the live site from a half-synced sheet).")
    ap.add_argument("--check-photos", type=int, default=int(os.environ.get("PHOTO_CHECK_SAMPLE", "0") or 0),
                    help="Test N random restaurants' photo links (needs internet) and report how many still load.")
    ap.add_argument("--sync-photos", action="store_true", default=os.environ.get("PHOTO_SYNC") == "1",
                    help="Download, verify (HTTP 200) and self-host restaurant photos (needs internet; Selenium refreshes dead ones).")
    ap.add_argument("--export-images", default="", help="Write the images database CSV (verified photo links and reviews for the WordPress plugin) to this path.")
    ap.add_argument("--photos-only", action="store_true", help="Only download/verify photos and refresh via Selenium, then stop (fills photo_cache/).")
    ap.add_argument("--no-selenium", action="store_true", default=os.environ.get("PHOTO_SELENIUM") == "0")
    ap.add_argument("--limit", type=int, default=0, help="Only build the first N listing rows (quick tests).")
    args = ap.parse_args()
    if not args.db:
        sys.exit("Give --db <xlsx|gsheet:ID> (or set DB_SHEET_ID).")

    cfg = load_config()
    report = {"skipped_incomplete": [], "duplicates_dropped": 0, "skipped_junk_name": 0, "name_variants": set(),
              "pages_without_content": [], "short_content": [], "thin_areas": [], "long_titles": [],
              "unmatched_content": []}

    df = data_sources.read_listings(args.db)
    if args.limit:
        df = df.head(args.limit)
    restaurants = load_restaurants(df, report, cfg.get("ignore_names", []))
    rc = ROOT / "review_cache" / "reviews.json"          # filled by review_sync.py (reviews.yml workflow)
    if rc.exists():
        try:
            cached = json.loads(rc.read_text("utf-8"))
            for r in restaurants:
                if not r["review_list"]:
                    r["review_list"] = [dict(v, rating=int(v.get("rating") or 0)) for v in cached.get(r["place_id"], {}).get("reviews", [])]
            print(f"  reviews: {sum(1 for r in restaurants if r['review_list'])} restaurants have review text")
            ex = [r for r in restaurants if r["review_list"]][:3]
            for r in ex:
                print(f"::notice title=review sample::{r['name']} | {r['city']} | {len(r['review_list'])} reviews | first: {r['review_list'][0]['text'][:140]}")
            print(f"::notice title=reviews in build::{sum(1 for r in restaurants if r['review_list'])} restaurants have review text, cache has {len(cached)} entries")
        except (ValueError, OSError):
            print("  reviews: cache unreadable, skipped")
    if len(restaurants) < args.min_restaurants and not args.limit:
        sys.exit(f"Only {len(restaurants)} restaurants loaded (minimum {args.min_restaurants}). "
                 "The sheet may be mid-sync. Nothing was built.")
    if args.sync_photos:
        rep = photo_sync.sync(restaurants, per=int(cfg.get("photos_per_restaurant", 4)),
                              export_per=int(cfg.get("export_photos", 10)) if args.export_images or cfg.get("export_photos_always") else 0,
                              min_photos=int(cfg.get("min_photos", 3)),
                              selenium=not args.no_selenium, refresh_minutes=int(cfg.get("selenium_minutes", 70)),
                              download_minutes=int(os.environ.get("PHOTO_DOWNLOAD_MINUTES", "0") or 0),
                              selenium_workers=int(cfg.get("selenium_workers", 3)))
        print("  photo http codes:", rep.get("http_codes"))
        if args.export_images:
            n_rows = photo_sync.write_images_database(restaurants, args.export_images, int(cfg.get("export_photos", 10)))
            print(f"  images database: {n_rows} restaurants written to {args.export_images}")
            print(f"::notice title=images database::{n_rows} restaurants, "
                  f"{sum(len(r.get('verified_urls', [])) for r in restaurants)} verified photo links")
        if args.photos_only:
            print("  photos-only run finished; cache saved.")
            sys.exit(0)
        report["photo_sync"] = rep
        print(f"  photos: {rep['with_photo']} of {rep['restaurants']} restaurants have a verified photo "
              f"({rep['fixed_by_selenium']} recovered via Selenium, {rep['without_photo']} without, {rep['seconds']}s)")
        prefix = cfg["base_path"]
        for r in restaurants:
            r["photos"] = [f"{prefix}/{p}" for p in r["local_photos"]]
    elif args.check_photos:
        res = photo_check.run(restaurants, args.check_photos, cfg["base_url"] + "/")
        report["photo_check"] = res
        print(f"  photo check ({res['sampled']} sampled): working {res['working_pct']}% | both links ok {res['both_ok']} | "
              f"only original link ok {res['only_original_ok']} | only resized ok {res['only_resized_ok']} | "
              f"both dead {res['both_dead']} | other {res['other']}")
        if res["only_original_ok"] > 3 * max(res["only_resized_ok"], 0) + 2 and res["only_original_ok"] > res["both_ok"]:
            cfg["photo_resize"] = False
            print("  ! resized photo links fail but originals load: serving original links (photo_resize off)")
    root, walk = build_tree(restaurants, report)
    compute_stats(root, walk, restaurants)
    content = load_content(data_sources.read_content(args.content), args.include_review, report)
    known = {n.path for n in walk(root) if n.kind in ("country", "state", "city")}
    report["unmatched_content"] = sorted(set(content) - known)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(ROOT / "static", out / "static")
    if cfg.get("custom_domain"):
        (out / "CNAME").write_text(cfg["custom_domain"].replace("https://", "").replace("http://", "").strip("/") + "\n", encoding="utf-8")
    if args.sync_photos:
        ok_n, rej_n = photo_sync.write_review_page(restaurants, out, cfg["base_path"])
        print(f"  review page /photo-audit/: {ok_n} shown, {rej_n} rejected examples")
        files, mb = photo_sync.copy_used(restaurants, out)
        print(f"  photos copied into the site: {files} files, {mb} MB (GitHub Pages limit is 1000 MB)")
        report["photo_mb"] = mb
        if mb > 900:
            print("  ! WARNING: photo folder is close to the GitHub Pages size limit; lower photos_per_restaurant in config.json")
    (out / ".nojekyll").write_text("", encoding="utf-8")

    site = Site(cfg, out)
    countries = sorted(root.children.values(), key=lambda n: -n.count)
    site.env.globals["nav_countries"] = [{
        "name": c.name, "href": site.u(c.path),
        "cities": [{"name": ct.name, "href": site.u(ct.path)} for ct in sorted(
            (ct for st in c.children.values() for ct in st.children.values()), key=lambda n: -n.count)[:10]]}
        for c in sorted(countries, key=lambda n: n.name)]
    site.env.globals["footer_countries"] = [{"name": c.name, "href": site.u(c.path)} for c in sorted(countries, key=lambda n: n.name)]
    site.env.globals["footer_cities"] = [{"name": n.name, "href": site.u(n.path)} for n in interleave([
        sorted((ct for st in c.children.values() for ct in st.children.values()), key=lambda n: -n.count) for c in countries])[:10]]
    for n in walk(root):
        if n.kind in ("country", "state", "city"):
            render_place(site, n, content, report)
        elif n.kind == "area":
            render_area(site, n, report)
    for r in restaurants:
        render_restaurant(site, r, report)
    render_home(site, root, restaurants)
    render_static_pages(site)
    indexed = write_sitemap_and_robots(site)
    write_search_index(site, restaurants)

    levels = Counter(n.kind for n in walk(root))
    print(f"Built {len(site.pages)} pages into {out}")
    print(f"  restaurants: {len(restaurants)}  areas: {levels['area']}  cities: {levels['city']}  "
          f"states: {levels['state']}  countries: {levels['country']}")
    print(f"  in sitemap (indexable): {indexed}")
    print(f"  content pages live: {len(content)}  without approved content (noindex): {len(report['pages_without_content'])}")
    for key, label in (("skipped_incomplete", "rows skipped (missing name/place/location)"),
                       ("name_variants", "place-name spellings merged"),
                       ("thin_areas", "areas below minimum (noindex)"),
                       ("short_content", "content under 600 words"),
                       ("long_titles", "meta titles over 65 chars"),
                       ("unmatched_content", "content rows that match no page path")):
        items = sorted(report[key]) if isinstance(report[key], set) else report[key]
        if items:
            print(f"  ! {label}: {len(items)}")
            for it in items[:8]:
                print(f"      {it}")
    if report["skipped_junk_name"]:
        print(f"  rows skipped, junk name {cfg.get('ignore_names')}: {report['skipped_junk_name']}")
    if report["duplicates_dropped"]:
        print(f"  duplicate place_id rows dropped: {report['duplicates_dropped']}")
    rep = {k: (sorted(v) if isinstance(v, set) else v) for k, v in report.items()}
    (ROOT / "build_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
