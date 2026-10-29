"""Tiny text utility: slugify() turns arbitrary text into a lowercase ASCII hyphen-separated slug."""

import re
import unicodedata


def slugify(text: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")