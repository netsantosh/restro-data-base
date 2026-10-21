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
from utils import (clean_image, clip, fit, km_between, paragraphs, s, slugify,
                   tel_href, thumb, to_float, to_int)

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
                     ("SITE_NAME", "site_name"), ("CONTACT_EMAIL", "contact_email")):
        if os.environ.get(env, "").strip():
            cfg[key] = os.environ[env].strip()
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
        r["price"] = r["price_level"]
        site = r["website"]
        r["website"] = site if site.lower().startswith(("http://", "https://")) else ""
        r["tel"] = tel_href(r["phone"])
        photos = []
        for c in photo_cols:
            u = clean_image(rec.get(c))
            if u and u not in photos:
                photos.append(u)
        r["photos"] = photos
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
        n.top = sorted(rs, key=lambda r: r["score"], reverse=True)[:12]
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
                "sections": sections, "faqs": faqs, "words": words,
            }
    return content


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
class Site:
    def __init__(self, cfg, out):
        self.cfg, self.out = cfg, out
        self.pages = []              # (path, lastmod, indexable)
        self.env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                               autoescape=select_autoescape(["html"]),
                               trim_blocks=True, lstrip_blocks=True)
        self.env.filters["paragraphs"] = paragraphs
        self.env.globals.update(thumb=thumb, site=cfg, u=self.u, asset=self.asset,
                                year=datetime.now().year)

    def u(self, path=""):
        path = path.strip("/")
        return f"{self.cfg['base_path']}/{path}/" if path else f"{self.cfg['base_path']}/"

    def asset(self, path):
        return f"{self.cfg['base_path']}/{path.lstrip('/')}"

    def canonical(self, path):
        return self.cfg["base_url"] + self.u(path)

    def write(self, path, template, indexable=True, lastmod="", **ctx):
        ctx.setdefault("noindex", not indexable)
        ctx["canonical"] = self.canonical(path)
        html = self.env.get_template(template).render(**ctx)
        dest = self.out / path.strip("/") / "index.html" if path.strip("/") else self.out / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        self.pages.append((path.strip("/"), lastmod, indexable))


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


def decorate(site, r):
    r["href"] = site.u(r["path"])
    return r


def place_fallback(node):
    kids = {"country": ("state", "states"), "state": ("city", "cities"), "city": ("area", "areas")}[node.kind]
    n_kids = len(node.children)
    top = ", ".join(f"{c} ({n})" for c, n in node.cuisines[:4])
    intro = (f"{node.name} has {node.count:,} restaurants listed across {n_kids} {kids[1]}."
             + (f" The average Google Maps rating is {node.avg}." if node.avg else "")
             + (f" Most common cuisines: {top}." if top else ""))
    return intro


def render_place(site, node, content, report):
    c = content.get(node.path)
    kind = node.kind
    child_kind = {"country": "state", "state": "city", "city": "area"}[kind]
    children = sorted(node.children.values(), key=lambda n: -n.count)
    child_links = [{"name": ch.name, "href": site.u(ch.path), "count": ch.count} for ch in children]
    if kind == "country":
        h1 = (c and c["h1"]) or f"Restaurants in {node.name}"
        top_heading = f"Top rated restaurants in {node.name}"
        children_heading = f"Browse restaurants by state in {node.name}"
        cities = sorted((ct for st in node.children.values() for ct in st.children.values()),
                        key=lambda n: -n.count)[:12]
        secondary = {"heading": f"Popular cities in {node.name}",
                     "links": [{"name": ct.name, "href": site.u(ct.path), "count": ct.count} for ct in cities]}
        title_c = [f"Restaurants in {node.name}: Cities, Areas & Top Places | {site.cfg['site_name']}",
                   f"Restaurants in {node.name} | {site.cfg['site_name']}", f"Restaurants in {node.name}"]
    elif kind == "state":
        h1 = (c and c["h1"]) or f"Restaurants in {node.name}"
        top_heading = f"Top rated restaurants in {node.name}"
        children_heading = f"Browse restaurants by city in {node.name}"
        secondary = None
        title_c = [f"Restaurants in {node.name}: Top Cities & Places to Eat | {site.cfg['site_name']}",
                   f"Restaurants in {node.name} | {site.cfg['site_name']}", f"Restaurants in {node.name}"]
    else:
        h1 = (c and c["h1"]) or f"Restaurants in {node.name}"
        top_heading = f"Top rated restaurants in {node.name}"
        children_heading = f"Browse restaurants by area in {node.name}"
        secondary = None
        title_c = [f"Restaurants in {node.name}: Top Areas & Places to Eat | {site.cfg['site_name']}",
                   f"Restaurants in {node.name} | {site.cfg['site_name']}", f"Restaurants in {node.name}"]
    title = (c and c["meta_title"]) or fit(title_c, 60)
    desc = (c and c["meta_description"]) or clip(place_fallback(node), 155)
    crumbs = crumbs_for(site, node)
    schema = [finalize_breadcrumb_schema(breadcrumb_schema(site, crumbs), site.canonical(node.path))]
    if c and c["faqs"]:
        schema.append(faq_schema(c["faqs"]))
    indexable = bool(c)
    if not c:
        report["pages_without_content"].append(node.path)
    elif c["words"] < 600:
        report["short_content"].append(f"{node.path}: {c['words']} words")
    if c and len(title) > 65:
        report["long_titles"].append(f"{node.path}: {len(title)} chars")
    site.write(node.path, "place.html", indexable=indexable, lastmod=node.lastmod,
               title=title, description=desc, schema=schema, crumbs=crumbs, node=node, h1=h1,
               intro=(c and c["intro"]) or place_fallback(node), sections=(c and c["sections"]) or [],
               faqs=(c and c["faqs"]) or [], children=child_links, children_heading=children_heading,
               child_kind=child_kind, top=[decorate(site, r) for r in node.top], top_heading=top_heading,
               secondary=secondary, cuisines=node.cuisines, og_type="website")


def render_area(site, area, report):
    city, state = area.parent, area.parent.parent
    indexable = area.count >= site.cfg["min_area_restaurants"]
    if not indexable:
        report["thin_areas"].append(f"{area.path} ({area.count})")
    rs = sorted(area.restaurants, key=lambda r: r["score"], reverse=True)
    for r in rs:
        decorate(site, r)
    cuis = ", ".join(f"{c} ({n})" for c, n in area.cuisines[:4])
    intro = (f"{area.name} in {city.name}, {state.name} has {area.count} restaurants listed on this guide"
             + (f", with an average Google Maps rating of {area.avg}" if area.avg else "")
             + "." + (f" The most common cuisines here are {cuis}." if cuis else "")
             + f" Use the filters to narrow by cuisine or rating.")
    cands = [f"Restaurants in {area.name}, {city.name} ({area.count}) | {site.cfg['site_name']}",
             f"Restaurants in {area.name}, {city.name} | {site.cfg['site_name']}",
             f"Restaurants in {area.name}, {city.name}"]
    title = fit(cands, 60)
    desc = clip(f"Find {area.count} restaurants in {area.name}, {city.name}"
                + (f" rated {area.avg} on average" if area.avg else "")
                + (f", including {', '.join(r['name'] for r in rs[:3])}" if rs else "")
                + ". Browse cuisines, ratings, addresses and photos.", 155)
    nearby = [a for a in city.children.values() if a is not area and a.count]
    if area.center:
        nearby = [a for a in nearby if a.center]
        nearby.sort(key=lambda a: km_between(*area.center, *a.center))
    else:
        nearby.sort(key=lambda a: -a.count)
    nearby = nearby[:10]
    crumbs = crumbs_for(site, area)
    schema = [finalize_breadcrumb_schema(breadcrumb_schema(site, crumbs), site.canonical(area.path))]
    schema.append({"@context": "https://schema.org", "@type": "ItemList", "name": f"Restaurants in {area.name}, {city.name}",
                   "numberOfItems": len(rs), "itemListElement": [
                       {"@type": "ListItem", "position": i + 1, "url": site.cfg["base_url"] + r["href"], "name": r["name"]}
                       for i, r in enumerate(rs[:50])]})
    site.write(area.path, "area.html", indexable=indexable, lastmod=area.lastmod, title=title, description=desc,
               schema=schema, crumbs=crumbs, node=area, city=city, state=state, h1=f"Restaurants in {area.name}, {city.name}",
               intro=intro, restaurants=rs, cuisines=[c for c, _ in area.cuisines],
               nearby=[{"name": a.name, "href": site.u(a.path), "count": a.count} for a in nearby], og_type="website")


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
    bits.append("See address, phone, photos and map link.")
    desc = clip(" ".join(bits), 155)
    crumbs = crumbs_for(site, area, last_link=True) + [(r["name"], None)]
    related_area = [x for x in sorted(area.restaurants, key=lambda x: -x["score"]) if x is not r][:6]
    same_cuisine = []
    if r["cuisine_short"]:
        same_cuisine = [x for x in sorted(city.restaurants, key=lambda x: -x["score"])
                        if x is not r and x["cuisine_short"] == r["cuisine_short"] and x not in related_area][:6]
    for x in related_area + same_cuisine:
        decorate(site, x)
    page_url = site.canonical(r["path"])
    schema = [finalize_breadcrumb_schema(breadcrumb_schema(site, crumbs), page_url)]
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
        ld["image"] = r["photos"][:3]
    if r["price"] and re.fullmatch(r"[₹$€£]{1,4}", r["price"]):
        ld["priceRange"] = r["price"]
    if r["website"]:
        ld["sameAs"] = [r["website"]]
    schema.append(ld)
    site.write(r["path"], "restaurant.html", indexable=True, lastmod=r["scraped_at"][:10], title=title,
               description=desc, schema=schema, crumbs=crumbs, r=r, area=area, city=city, state=state,
               related_area=related_area, same_cuisine=same_cuisine, og_type="place",
               og_image=thumb(r["photos"][0], 1200, 630) if r["photos"] else "")


def render_home(site, root, all_rest):
    countries = sorted(root.children.values(), key=lambda n: -n.count)
    cities = sorted((ct for c in countries for st in c.children.values() for ct in st.children.values()),
                    key=lambda n: -n.count)[:12]
    n_areas = sum(len(ct.children) for c in countries for st in c.children.values() for ct in st.children.values())
    top = sorted(all_rest, key=lambda r: r["score"], reverse=True)[:12]
    names = " and ".join(c.name for c in countries[:2])
    title = fit([f"Find Restaurants in {names} | {site.cfg['site_name']}", f"{site.cfg['site_name']}: Find Restaurants"], 60)
    desc = clip(f"Browse {len(all_rest):,} restaurants across {n_areas} areas in {names}. "
                "Filter by city, area, cuisine and Google Maps rating, with addresses, phone numbers and photos.", 155)
    schema = [{"@context": "https://schema.org", "@type": "WebSite", "name": site.cfg["site_name"],
               "url": site.canonical("")}]
    site.write("", "home.html", indexable=True, lastmod=max((r["scraped_at"][:10] for r in all_rest), default=""),
               title=title, description=desc, schema=schema, countries=[
                   {"name": c.name, "href": site.u(c.path), "count": c.count} for c in countries],
               cities=[{"name": ct.name, "href": site.u(ct.path), "count": ct.count, "state": ct.parent.name} for ct in cities],
               top=[decorate(site, r) for r in top], total=len(all_rest), n_areas=n_areas, og_type="website")


STATIC_PAGES = {
    "about": ("About", "about.html", "About {site}: how this restaurant guide is compiled."),
    "contact": ("Contact", "contact.html", "Contact {site} to report a correction or suggest a restaurant."),
    "privacy-policy": ("Privacy Policy", "privacy.html", "How {site} handles visitor data, cookies and advertising."),
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
        urls.append(f"  <url><loc>{xml_escape(loc)}</loc>" + (f"<lastmod>{lastmod}</lastmod>" if lastmod else "") + "</url>")
    (site.out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n</urlset>\n", encoding="utf-8")
    (site.out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {base}{site.cfg['base_path']}/sitemap.xml\n",
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
    if len(restaurants) < args.min_restaurants and not args.limit:
        sys.exit(f"Only {len(restaurants)} restaurants loaded (minimum {args.min_restaurants}). "
                 "The sheet may be mid-sync. Nothing was built.")
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
    (out / ".nojekyll").write_text("", encoding="utf-8")

    site = Site(cfg, out)
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
