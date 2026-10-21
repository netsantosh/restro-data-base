"""Load the restaurant DB and the content sheet from .xlsx files or Google Sheets.

--db / --content accept:
  path/to/file.xlsx        local workbook
  gsheet:<spreadsheet id>  Google Sheet (needs GOOGLE_SERVICE_ACCOUNT_JSON)
"""
import json
import os

import pandas as pd

CONTENT_TABS = {"country": "Country", "state": "State", "city": "City"}


def _client():
    import gspread
    from google.oauth2.service_account import Credentials

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise SystemExit("Set GOOGLE_SERVICE_ACCOUNT_JSON (file path or the JSON itself).")
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    if raw.startswith("{"):
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(raw, scopes=scopes)
    return gspread.authorize(creds)


def _find_ws(sheet, title):
    for ws in sheet.worksheets():
        if ws.title.strip().lower() == title.lower():
            return ws
    return None


def _ws_to_df(ws):
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    header = [h.strip() for h in values[0]]
    return pd.DataFrame(values[1:], columns=header)


def _xlsx_tab(path, title):
    xl = pd.ExcelFile(path)
    for name in xl.sheet_names:
        if name.strip().lower() == title.lower():
            return xl.parse(name, dtype=str)
    return None


def read_listings(source):
    if source.startswith("gsheet:"):
        sheet = _client().open_by_key(source.split(":", 1)[1])
        ws = _find_ws(sheet, "Listings")
        if ws is None:
            raise SystemExit("Tab 'Listings' not found in the restaurant database sheet.")
        return _ws_to_df(ws)
    df = _xlsx_tab(source, "Listings")
    if df is None:
        raise SystemExit(f"No 'listings' tab in {source}")
    return df


def read_content(source):
    """Return {'country': df, 'state': df, 'city': df} (empty frames if missing)."""
    out = {k: pd.DataFrame() for k in CONTENT_TABS}
    if not source or source == "none":
        return out
    if source.startswith("gsheet:"):
        sheet = _client().open_by_key(source.split(":", 1)[1])
        for key, title in CONTENT_TABS.items():
            ws = _find_ws(sheet, title)
            if ws is not None:
                out[key] = _ws_to_df(ws)
        return out
    for key, title in CONTENT_TABS.items():
        df = _xlsx_tab(source, title)
        if df is not None:
            out[key] = df
    return out
