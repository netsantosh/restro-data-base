"""Read the restaurant database sheet ONCE and save it as an .xlsx file, so the later passes of a long
workflow do not each read Google Sheets again (Google allows about 60 reads a minute).

    python snapshot_sheet.py gsheet:<id> out.xlsx
"""
import sys

import data_sources

if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    df = data_sources.read_listings(src)
    df.to_excel(out, sheet_name="Listings", index=False)
    print(f"Saved {len(df)} rows to {out}")
