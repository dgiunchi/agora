"""Fixture: slugify is the task. The tests in test_slugger.py are the contract."""

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s-]")
_SEPARATORS = re.compile(r"[\s_-]+")


def slugify(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = _PUNCT.sub("", text)
    return _SEPARATORS.sub("-", text).strip("-")
