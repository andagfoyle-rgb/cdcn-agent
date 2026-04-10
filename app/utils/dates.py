"""
Shared date-extraction utilities for CDCN Agent.

Consolidates logic previously duplicated in indexer.py and document_parser.py.
"""
import re
from pathlib import Path

# Full month map (short + long names) — superset from document_parser
_MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    "january": "01", "february": "02", "march": "03", "april": "04",
    "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def extract_date_from_filename(filename: str) -> str | None:
    """
    Parse an ISO date (YYYY-MM-DD) from common CDCN filename patterns.

    Handles: YYYYMMDD, DD-MM-YYYY, DD.MM.YY, DD_MM_YY, ordinal text
    (e.g. "19th nov 2024"), month+year ("nov 2017"), and bare year.
    Returns None if no recognisable date is found.
    """
    s = Path(filename).stem.lower()

    # 1. Compact YYYYMMDD (e.g. board_minute_20260322)
    m = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # 2. DD sep MM sep YYYY  (sep = - . _)
    m = re.search(r"(?<!\d)(\d{1,2})[._-](\d{1,2})[._-](20\d{2})(?!\d)", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}-{mo:02d}-{d:02d}"

    # 3. DD sep MM sep YY  (2-digit year -> 20YY; only if YY >= 20)
    m = re.search(r"(?<!\d)(\d{1,2})[._-](\d{1,2})[._-](\d{2})(?!\d)", s)
    if m:
        d, mo, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31 and yy >= 20:
            return f"20{yy:02d}-{mo:02d}-{d:02d}"

    # 4. Ordinal day + text month + year: "19th nov 2024" or "19th_nov_2024"
    m = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)[\s_]+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*[\s_]+(20\d{2})",
        s,
    )
    if m:
        d = int(m.group(1))
        mon = _MONTH_MAP[m.group(2)[:3]]
        return f"{m.group(3)}-{mon}-{d:02d}"

    # 5. Text month + year: "nov 2017"
    m = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*[_\s]*(20\d{2})",
        s,
    )
    if m:
        mon = _MONTH_MAP[m.group(1)[:3]]
        return f"{m.group(2)}-{mon}-01"

    # 6. Bare 4-digit year
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", s)
    if m:
        return f"{m.group(1)}-01-01"

    return None


def extract_date_from_text(text: str) -> str | None:
    """
    Extract an ISO date (YYYY-MM-DD) from the first ~2000 chars of document text.

    Handles: ordinal + text month + year (e.g. "4th March 2025"),
    and numeric DD/MM/YYYY or DD-MM-YYYY patterns.
    """
    sample = text[:2000].lower()

    # "4th March 2025" / "19th November 2024"
    m = re.search(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
        r"apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(20\d{2})",
        sample,
    )
    if m:
        d = int(m.group(1))
        mon = _MONTH_MAP.get(m.group(2)[:3], "01")
        return f"{m.group(3)}-{mon}-{d:02d}"

    # DD/MM/YYYY or DD-MM-YYYY
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", sample)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31 and 2020 <= y <= 2030:
            return f"{y}-{mo:02d}-{d:02d}"

    return None
