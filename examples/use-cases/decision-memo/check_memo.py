"""Proof command for the decision-memo use case: mechanical checks on DECISION.md.

Exit 0 when every check passes, 1 otherwise, printing each failure. Stdlib only.
"""
import re
import sys
from pathlib import Path

REQUIRED = ["Context", "Options", "Criteria", "Recommendation", "Risks", "Next steps"]
MIN_WORDS, MAX_WORDS = 250, 700


def main() -> int:
    path = Path("DECISION.md")
    problems = []
    if not path.exists():
        print("FAIL: DECISION.md does not exist")
        return 1
    text = path.read_text(encoding="utf-8")
    words = len(re.findall(r"[A-Za-z0-9'-]+", text))
    if not MIN_WORDS <= words <= MAX_WORDS:
        problems.append(f"word count {words} outside {MIN_WORDS}-{MAX_WORDS}")
    headings = [m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$", text, re.M)]
    lowered = [h.lower() for h in headings]
    order = [lowered.index(r.lower()) for r in REQUIRED if r.lower() in lowered]
    missing = [r for r in REQUIRED if r.lower() not in lowered]
    if missing:
        problems.append(f"missing level-2 sections: {missing}")
    elif order != sorted(order):
        problems.append(f"sections out of order: {headings}")
    section = {}
    for i, h in enumerate(headings):
        start = text.index("## " + h) + len(h) + 3
        end = text.index("## " + headings[i + 1]) if i + 1 < len(headings) else len(text)
        section[h.lower()] = text[start:end]
    options = section.get("options", "")
    n_options = len(re.findall(r"^(?:[-*]|\d+\.|###)\s+", options, re.M))
    if n_options < 3:
        problems.append(f"Options lists {n_options} items, need at least 3")
    criteria = section.get("criteria", "")
    rows = [ln for ln in criteria.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 3 or not any(re.match(r"^\|\s*:?-{3,}", r.strip()) for r in rows):
        problems.append("Criteria has no markdown table with a header, a separator and at least one row")
    else:
        header_cells = [c.strip() for c in rows[0].strip().strip("|").split("|")]
        if len(header_cells) < 5:
            problems.append(f"Criteria table has {len(header_cells) - 1} criteria columns, need at least 4")
    if "recommend" not in section.get("recommendation", "").lower():
        problems.append("Recommendation does not state a recommendation")
    if re.search(r"^```", text, re.M):
        problems.append("memo contains a code block; no source code allowed")
    for p in problems:
        print("FAIL:", p)
    if not problems:
        print(f"OK: DECISION.md, {words} words, sections {headings}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
