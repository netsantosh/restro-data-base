#!/usr/bin/env python3
"""Make a *test-only* content workbook from the DB so you can preview the full layout locally.

The text is plain placeholder built from the data. It is NOT meant for publishing.
Real content comes from the Content CMS Google Sheet (see apps_script/).

    python sample/make_sample_content.py <google_maps_data.xlsx> sample/content_sample.xlsx
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402
import data_sources  # noqa: E402

HEAD = ["path", "name", "level", "status", "h1", "meta_title", "meta_description", "short_intro", "intro"]
for i in range(1, 6):
    HEAD += [f"s{i}_h2", f"s{i}_body"]
for i in range(1, 6):
    HEAD += [f"faq{i}_q", f"faq{i}_a"]


def row(node, level):
    cu = ", ".join(n for n, c in node.cuisines[:5]) or "a mix of cuisines"
    top = ", ".join(r["name"] for r in node.top[:5])
    r = {"path": node.path, "name": node.name, "level": level, "status": "Approved",
         "h1": f"Best Restaurants in {node.name}",
         "meta_title": f"Restaurants in {node.name}: Top Places to Eat | Restro Guide"[:60],
         "meta_description": f"Find the best restaurants in {node.name}. Browse top rated places, cuisines, ratings and areas to explore."[:155],
         "short_intro": f"[SAMPLE] Looking for the best restaurants in {node.name}? Browse top rated places to eat, compare ratings and cuisines, and open any listing for the address, phone number and directions.",
         "intro": f"[SAMPLE TEXT FOR LAYOUT TESTING] Eating out in {node.name} covers {cu}.\n\nSecond placeholder paragraph."}
    heads = ["What the dining scene looks like", "Popular cuisines", "Top rated places", "How to choose", "Planning your visit"]
    for i, h in enumerate(heads, 1):
        r[f"s{i}_h2"] = h
        r[f"s{i}_body"] = f"[SAMPLE] {h} in {node.name}. Highly rated places include {top}.\n\nSecond placeholder paragraph."
    for i in range(1, 6):
        r[f"faq{i}_q"] = f"Sample question {i} about restaurants in {node.name}?"
        r[f"faq{i}_a"] = f"Sample answer {i}."
    return r


def main(db, out):
    df = data_sources.read_listings(db)
    report = {"skipped_incomplete": [], "duplicates_dropped": 0, "skipped_junk_name": 0, "name_variants": set()}
    rest = build.load_restaurants(df, report, build.load_config().get("ignore_names", []))
    root, walk = build.build_tree(rest, report)
    build.compute_stats(root, walk, rest)
    tabs = {"Country": [], "State": [], "City": []}
    for n in walk(root):
        if n.kind in ("country", "state", "city"):
            tabs[n.kind.capitalize()].append(row(n, n.kind))
    with pd.ExcelWriter(out) as xw:
        for name, rows in tabs.items():
            pd.DataFrame(rows, columns=HEAD).to_excel(xw, sheet_name=name, index=False)
    print({k: len(v) for k, v in tabs.items()}, "->", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
