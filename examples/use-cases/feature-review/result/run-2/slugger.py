"""Fixture: slugify is the task. The tests in test_slugger.py are the contract."""

import re
import unicodedata


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9\s_-]", "", text)
    return re.sub(r"[\s_-]+", "-", text).strip("-")
