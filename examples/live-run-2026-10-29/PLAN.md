# Implementation plan: `slugify(text)` in `slugger.py`

## Goal
Replace the `raise NotImplementedError` stub at `slugger.py:4-5` with a working `slugify(text: str) -> str` so that `python -m unittest -q`, run from the repository root, passes all five tests in `test_slugger.py`. Use only the Python standard library. Do not modify `test_slugger.py`.

## Observable acceptance criteria
The test file is the only contract in the repo (`slugger.py:1` docstring: "see test_slugger.py for the contract"; `README.md` adds only the run command). Each row is one test method:

| Test | Input | Expected output | Behaviour exercised |
|---|---|---|---|
| `test_lowercase_and_hyphens` | `"Hello World"` | `"hello-world"` | lowercase; space becomes hyphen |
| `test_strips_punctuation` | `"Hello, World! (2026)"` | `"hello-world-2026"` | punctuation dropped, digits kept, no trailing hyphen |
| `test_collapses_separators` | `"  a  --  b__c  "` | `"a-b-c"` | leading/trailing whitespace trimmed; runs of spaces, hyphens and underscores collapse to a single hyphen |
| `test_transliterates_accents` | `"Crème Brûlée"` | `"creme-brulee"` | precomposed Latin accents folded to ASCII base letters |
| `test_empty` | `""` | `""` | empty in, empty out, no exception |

Pass condition: `python -m unittest -q` exits 0 and prints `Ran 5 tests` followed by `OK`.

## Concrete approach
One function, four steps, two stdlib modules (`unicodedata`, `re`). Keep the existing signature and update the module docstring so it no longer says the function is unimplemented.

1. **Fold accents to ASCII.** `unicodedata.normalize("NFKD", text)` decomposes each accented letter into its base letter plus a combining mark. Then `.encode("ascii", "ignore").decode("ascii")` drops the combining marks and any other non-ASCII code point. `Crème Brûlée` becomes `Creme Brulee`.
2. **Lowercase.** `.lower()` on the now-ASCII string.
3. **Collapse every run of non-alphanumerics into one hyphen.** `re.sub(r"[^a-z0-9]+", "-", s)`. Because `-` and `_` are outside the class, the `--` and `__` in the separator test are treated as separators and merged in the same pass. Punctuation (`,`, `!`, `(`, `)`) is folded into separators too, so `Hello, World! (2026)` becomes `-hello-world-2026-` at this point.
4. **Trim edge hyphens.** `.strip("-")` removes hyphens produced by leading/trailing whitespace or punctuation. Empty input flows through every step unchanged and yields `""`.

Target code for `slugger.py` (entire file):

```python
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
```

Hand-trace against the five inputs confirms each expected output above.

## Key decisions and trade-offs
- **NFKD + ASCII-ignore instead of a hand-written accent table.** One line covers every precomposed Latin accent and needs no maintenance. Trade-off: letters with no canonical decomposition (`ß`, `ø`, `đ`, CJK, Cyrillic) are silently dropped rather than transliterated. The tests only require French accents, so this is acceptable and keeps the code minimal. NFKD is preferred over NFD so compatibility characters (ligatures such as `ﬁ`, full-width digits) also fold to ASCII.
- **A single regex for punctuation, whitespace, hyphens and underscores.** Treating every non-`[a-z0-9]` run as one separator satisfies both the punctuation test and the collapse test in one substitution, instead of separate strip and collapse passes. Trade-off: underscores become hyphens rather than being preserved. `a__c -> a-c` in the test explicitly requires this.
- **Lowercase after ASCII folding.** Order does not change results for the tested inputs, but lowercasing an already-ASCII string keeps the regex class simple (`a-z` only) and avoids Unicode case-folding edge cases.
- **No special case for empty input.** Every step is a no-op on `""`, so `test_empty` passes without a guard.
- **No input coercion, no extra parameters.** The contract covers `str` input only. Adding `str(text)`, a `separator` argument, or a max length would widen behaviour beyond the tests.

## Non-goals
- Do not modify `test_slugger.py` (explicit task constraint).
- No third-party packages (`unidecode`, `python-slugify`, `text-unidecode`).
- No transliteration of non-Latin scripts or special-case mappings such as `ß -> ss` or `ø -> o`.
- No length truncation, custom separator, stop-word removal, or CLI entry point.
- No changes to `README.md`, `.gitignore`, `AGORA-LOG.md`, or anything under `.claude-flow/`.
- Do not delete or commit `__pycache__`; it is already gitignored.
- Committing is not required by the task; if the loop decides to commit, only `slugger.py` should be staged.

## Assumptions (with sources)
- **`test_slugger.py` is the complete contract.** Source: `slugger.py:1` docstring; `README.md:3` only gives the run command.
- **Python 3 with `unicodedata` and `re` available.** Source: `__pycache__/slugger.cpython-313.pyc` and `__pycache__/test_slugger.cpython-313.pyc` show the suite has been run under CPython 3.13; both modules ship with every CPython build.
- **Tests are discovered from the repo root.** Source: `test_slugger.py:3` does a top-level `from slugger import slugify`, and the README command `python -m unittest -q` has no path argument, so default discovery of `test*.py` in the current directory applies.
- **Only `slugger.py` needs to change.** Source: git status shows a clean tracked tree (untracked `PLAN.md`, `AGORA-LOG.md`, `.claude-flow/` are orchestration artefacts), and the stub signature already matches the test import.
- **`python` on PATH resolves to the CPython 3 used for the earlier run.** Source: environment block (Windows 11, PowerShell). The commands below are shell-neutral.

## Exact verification commands
Run all from the repository root (`...\scratchpad\live-fixture`).

Primary gate:
```
python -m unittest -q
```
Expected: final lines `Ran 5 tests in ...s` then `OK`; exit code 0.

Per-test confirmation:
```
python -m unittest -v test_slugger
```
Expected: five lines ending in `ok` (one per test method listed above), then `OK`.

Stdlib-only check (import list must contain only `re` and `unicodedata`):
```
python -c "import ast; t=ast.parse(open('slugger.py', encoding='utf-8').read()); print(sorted({a.name for n in ast.walk(t) if isinstance(n, ast.Import) for a in n.names} | {n.module for n in ast.walk(t) if isinstance(n, ast.ImportFrom)}))"
```
Expected: `['re', 'unicodedata']`.

Test file untouched:
```
git status --porcelain test_slugger.py
```
Expected: empty output.

Optional spot check beyond the suite (edge trimming plus accent folding plus underscore collapse together):
```
python -c "from slugger import slugify; print(repr(slugify('  Crème, Brûlée!! __2026__  ')))"
```
Expected: `'creme-brulee-2026'`.