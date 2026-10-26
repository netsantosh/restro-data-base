"""Generate the brand images in static/: favicon.svg, icon-512.png and og-default.png (run once, files are committed)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "static"
SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
GOLD = (201, 169, 106)

(OUT / "favicon.svg").write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#000"/>'
    '<text x="32" y="46" text-anchor="middle" font-family="Georgia,serif" font-size="40" font-weight="700" fill="#fff">V</text>'
    '<rect x="22" y="52" width="20" height="2" fill="#c9a96a"/></svg>', encoding="utf-8")

icon = Image.new("RGB", (512, 512), "black")
d = ImageDraw.Draw(icon)
f = ImageFont.truetype(SERIF, 330)
w = d.textlength("V", font=f)
d.text(((512 - w) / 2, 60), "V", font=f, fill="white")
d.rectangle((196, 430, 316, 438), fill=GOLD)
icon.save(OUT / "icon-512.png", optimize=True)

og = Image.new("RGB", (1200, 630), "black")
d = ImageDraw.Draw(og)
title = ImageFont.truetype(SERIF, 120)
text = "V A N M E Y A"
d.text(((1200 - d.textlength(text, font=title)) / 2, 215), text, font=title, fill="white")
d.rectangle((540, 380, 660, 384), fill=GOLD)
sub = ImageFont.truetype(SANS, 38)
tag = "Find where to eat, city by city"
d.text(((1200 - d.textlength(tag, font=sub)) / 2, 420), tag, font=sub, fill=(220, 220, 220))
og.save(OUT / "og-default.png", optimize=True)
print("brand images written")
