"""
Shared content-extraction utilities for CDCN Agent.

Consolidates attendee extraction previously duplicated in indexer.py
and document_parser.py.
"""
import re


def extract_attendees(elements_or_text) -> list[str]:
    """
    Extract attendee names from a 'Present:' or 'Attendees:' section.

    Accepts either:
      - A string (full document text) — used by document_parser
      - A list of element objects with a .text attribute — used by indexer

    Returns a list of name strings (may be empty, max 20).
    """
    if isinstance(elements_or_text, str):
        sample = elements_or_text[:3000]
    elif isinstance(elements_or_text, list):
        # List of elements with .text attribute
        sample = "\n".join(
            el.text for el in elements_or_text[:20]
            if hasattr(el, "text")
        )
    else:
        return []

    m = re.search(
        r"(?:present|attendees?|attendance|in\s+attendance)\s*[:\s]+([^\n]{10,600})",
        sample,
        re.IGNORECASE,
    )
    if not m:
        return []

    raw = m.group(1).strip()
    # Remove common suffixes like "(Chair)", "*"
    raw = re.sub(r"\([^)]*\)", "", raw)
    raw = re.sub(r"\*", "", raw)
    names = [n.strip() for n in re.split(r"[,;\n]+", raw) if 2 < len(n.strip()) < 60]
    return names[:20]
