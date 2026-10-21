# claude-website

Static restaurant directory generated from two Google Sheets.

```
Scraper -> Sheet 1 "Restro Database" (Listings tab)
                 |  read by Apps Script (facts) and by build.py (pages)
                 v
          Sheet 2 "Content CMS" (Country / State / City tabs)
                 |  Gemini writes text, you review and set status = Approved
                 v
          build.py  ->  dist/ (home, country, state, city, area, restaurant pages, sitemap, robots)
                 |  GitHub Actions
                 v
          GitHub Pages (live site)
```

Your scraper and Sheet 1 are not changed.

| Page | Content comes from | LLM? |
|---|---|---|
| Home | templates + DB stats | no |
| Country, State, City | Content CMS sheet, only rows with status **Approved** | yes |
| Area | DB rows for that area, template heading, meta and short intro, filters | no |
| Restaurant | one DB row, template H1, title, meta, Restaurant schema | no |

Country/State/City pages with no Approved row are still built (so links work) but are `noindex` and left out of the sitemap. Areas with fewer than `min_area_restaurants` (config.json, default 3) are `noindex` too.

## 1. Try it locally (no accounts needed)

```bash
cd claude-website
pip install -r requirements.txt

# make placeholder content so you can see the full layout (test only, not for publishing)
python sample/make_sample_content.py ../database/google_maps_data.xlsx sample/content_sample.xlsx

# build from the local workbook and open dist/index.html through a local server
python build.py --db ../database/google_maps_data.xlsx --content sample/content_sample.xlsx
cd dist && python -m http.server 8000      # http://localhost:8000
```

Useful flags: `--content none` (no content, all place pages noindex), `--include-review` (also show rows with status Review, for previewing before approval), `--limit 300` (quick test build).

The build prints a report: rows skipped (no `place_id`, junk names such as "Google Maps"), duplicate `place_id` rows dropped, thin areas, content under 600 words, unmatched content paths. The same report is saved to `build_report.json`.

## 2. Set up the Content CMS sheet (Sheet 2)

1. Create a new Google Sheet (name it e.g. "Restro Content CMS").
2. Extensions > Apps Script. Paste `apps_script/Code.gs` into the editor. In Project Settings tick "Show appsscript.json" and paste `apps_script/appsscript.json`. Save.
3. Reload the sheet. A **Content** menu appears. Run **Content > Set up tabs (first time)** and accept the permissions.
4. In the **Config** tab, set `DB_SHEET_ID` to the ID of Sheet 1 (the long string between `/d/` and `/edit` in its URL). Check `GEMINI_MODEL` against the models listed in Google AI Studio for your free tier.
5. Project Settings > Script properties > add:
   * `GEMINI_API_KEY`: from https://aistudio.google.com (do not paste it in a cell or share it)
   * `GITHUB_TOKEN`: GitHub > Settings > Developer settings > Fine-grained token, only this repo, permission **Actions: Read and write**
6. **Content > Test Gemini connection** to confirm the key works.

Daily use:

1. **Content > 1. Refresh pages and facts** reads Sheet 1 and adds a row for every new country/state/city (status Pending). Rows whose restaurant count moved 10%+ since generation get `refresh_flag = YES`.
2. **Content > 2. Generate content for Pending rows** writes H1, meta title and description, intro, 5 sections and 5 FAQs (about 800 words). Status becomes **Review**. The `notes` column shows the word count and lists any number in the text that is not in the data. Check those before approving.
3. Read and edit the text in the sheet. Add verified local facts in `local_notes` if you want them used (then regenerate the row). Set status to **Approved**.
4. **Content > 3. Publish site** starts the build.

Status values: `Pending` (waiting for text), `Review` (generated, hidden), `Approved` (live), `Hold` (hidden).

## 3. Put it on GitHub Pages

1. Merge this branch into `main` (the workflow must exist on the default branch).
2. Repo Settings > Pages > Source: **GitHub Actions**.
3. Repo Settings > Secrets and variables > Actions:
   * Secrets: `GOOGLE_SERVICE_ACCOUNT_JSON` (paste the whole key file contents), `DB_SHEET_ID`, `CONTENT_SHEET_ID`
   * Variables: `SITE_URL` (for example `https://yourname.github.io` or `https://yourdomain.com`), `BASE_PATH` (`/restro-data-base` for a project site, empty for a custom domain), `SITE_NAME`, `CONTACT_EMAIL`
4. Share **both** sheets with the service account email (Viewer is enough for the build).
5. Actions > claude-website > Run workflow. It also runs daily at 06:00 IST and on every change to `claude-website/`.

The build refuses to publish if fewer than `MIN_RESTAURANTS` (default 100) rows load, so a sheet caught in the middle of a scraper sync cannot replace the live site with a near-empty one. Avoid scheduling the build while the scraper is syncing.

## Before you go live

* Replace `base_url`, `site_name` and `contact_email` in `config.json` (or use the repo variables above).
* Read `templates/about.html`, `contact.html` and `privacy.html`. They are generic starting texts; adjust the privacy policy to the ads and analytics you actually use.
* Photos are hotlinked from Google's image servers. Those URLs can expire and Google's terms limit reuse of Maps content, so check that you are comfortable with that. Cards and galleries hide images that fail to load.
* Ratings and review counts are shown as "Google Maps rating" text. They are intentionally **not** in the structured data (`aggregateRating`), because Google's rich result rules do not accept ratings copied from another site.

## Files

```
build.py            builder (pages, internal links, schema, sitemap, robots)
data_sources.py     reads .xlsx or Google Sheets
utils.py            slugs, image cleaning, text helpers
config.json         site name, base URL, thresholds, names to skip
templates/          Jinja templates for every page type
static/             style.css, site.js (search box, listing filters)
apps_script/        Code.gs + appsscript.json for the Content CMS sheet
sample/             test-only content generator
../.github/workflows/claude-website.yml   build + deploy
```

`slugify()` in `utils.py` and `slugify_()` in `Code.gs` must stay identical: the sheet's `path` column is how a content row finds its page.
