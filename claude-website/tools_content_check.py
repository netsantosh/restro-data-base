"""Checks the pages in content_repo/*.json: structure, word count, banned characters.
Usage: python tools_content_check.py [--pages pages.json]   (pages.json = list of expected page paths)"""
import json, re, sys
from pathlib import Path

d = Path(__file__).resolve().parent / "content_repo"
problems, total, seen = [], 0, set()
for f in sorted(d.glob("*.json")):
    for path, r in json.loads(f.read_text(encoding="utf-8")).items():
        total += 1; seen.add(path)
        def bad(m): problems.append(f"{f.name}:{path}: {m}")
        secs, faqs = r.get("sections", []), r.get("faqs", [])
        words = len((r.get("intro") or "").split()) + sum(len(x["body"].split()) for x in secs)
        if not 330 <= words <= 950: bad(f"{words} words (want 330 to 950)")
        if len(secs) != 5: bad(f"{len(secs)} sections")
        if len(faqs) != 5: bad(f"{len(faqs)} faqs")
        if not (r.get("h1") and r.get("short_intro")): bad("missing h1 or short_intro")
        if not 25 <= len(r.get("meta_title", "")) <= 62: bad(f"meta_title length {len(r.get('meta_title',''))}")
        if not 100 <= len(r.get("meta_description", "")) <= 160: bad(f"meta_description length {len(r.get('meta_description',''))}")
        blob = json.dumps(r, ensure_ascii=False)
        if "—" in blob or "–" in blob: bad("dash character")
        if re.search(r"\b\d[\d,]*\s+(restaurants|eateries|listings|places to eat)\b", blob, re.I): bad("restaurant count")
        if "<" in blob or "**" in blob: bad("markup in text")
print(f"{total} pages checked")
if len(sys.argv) > 2 and sys.argv[1] == "--pages":
    want = {p["path"] for p in json.load(open(sys.argv[2]))}
    print("missing:", len(want - seen), sorted(want - seen)[:20]); print("not in list:", sorted(seen - want)[:20])
print("\n".join(problems) if problems else "no problems")
