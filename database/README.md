# Restaurant data collection

This folder contains the existing Google Maps collection workflow and its local data. The Python virtual environment remains in the parent project folder at `.venv`.

## Files

- `maps_scraper.py`: scraper, resume and Google Sheets sync logic
- `run_maps_scraper.bat`: review-count repair, followed by a website build on success
- `run_maps_scraper_locked.bat`: headless review-repair run that keeps Windows awake, followed by a website build on success
- `run_maps_scraper_single_area.bat`: Bandra test run, followed by a website build on success
- `India_DB.xlsx`: local area source
- `google_maps_data.xlsx`: local restaurant data consumed by the website builder
- `google_maps_status.xlsx`: resume state
- `restro-database-*.json`: private Google service-account credential

The `.before-*` and `.corrupt-*` workbooks are retained recovery copies. They are intentionally not removed. The service-account key, local workbooks, log and scraper lock are ignored by Git; do not publish or share the credentials.

Google Sheets listing data is synchronized only to the `Listings` tab. The scraper does not write listing data to any other tab; status information is synchronized separately to `Status`.
Review totals are stored as full integer counts, including when Google Maps displays abbreviated values such as `1.2K`.

## Setup

1. Keep the existing `.venv` in the project root, or create it there if this is a fresh checkout.
2. Install the scraper dependencies in that environment:

```powershell
..\.venv\Scripts\python.exe -m pip install selenium pandas openpyxl gspread google-auth
```

3. Place the service-account JSON file in this folder, matching `restro-database-*.json`.
4. Share the target Google Sheet with the service-account email as Editor.
5. Keep `India_DB.xlsx` in this folder.

## Run

Run `run_maps_scraper.bat` to repair saved review totals and build the website without starting new-area scraping. To run that repair directly, use:

```powershell
..\.venv\Scripts\python.exe database\maps_scraper.py --repair-missing-reviews-only --headless
```

The repair-only run checks blank review counts and zero placeholders from Listings row 4330 onward. It uses four visible Chrome workers with image downloads blocked and waits up to 8 seconds for the review total to appear in the loaded Maps profile. It does not start new-area scraping.

To run Python directly for the full area-scraping workflow:

```powershell
..\.venv\Scripts\python.exe maps_scraper.py
```

The scraper stores its workbook, resume data and log beside `maps_scraper.py`, regardless of the current working directory. Its output workbook is also the input for the website builder.
