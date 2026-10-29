# Plan: implement `slugify(text)` in slugger.py

## Status of repository inspection
Every read-only tool (Read, Glob, Grep, PowerShell, Agent) was blocked by PreToolUse hooks that time out because Git Bash is not installed on this Windows host. I could not open slugger.py or test_slugger.py. This plan is therefore derived from the task statement only, and the implementer MUST read test_slugger.py before coding to settle the two ambiguities called out under Key decisions.

## Goal
Make `python -m unittest -q` pass by implementing `slugify(text) -> str` in slugger.py using only the Python standard library, without editing test_slugger.py.

## Observable acceptance criteria
1. `python -m unittest -q` run from the repo root exits 0 and reports `OK` with 5 tests run, 0 failures, 0 errors.
2. Output is lowercase ASCII consisting only of `[a-z0-9]` and single `-` separators, with no leading or trailing hyphen.
3. Punctuation is stripped (does not appear in output).
4. Runs of whitespace / underscores / hyphens collapse to a single `-`.
5. Accented Latin characters are transliterated to their ASCII base letter (e.g. `Crème Brûlée` -> `creme-brulee`).
6. `slugify('')` returns `''` (and, defensively, so does `slugify(None)`).
7. No third-party imports (no `unidecode`, no `python-slugify`); only `re` and `unicodedata`.

## Concrete approach
Implement a single pure function in slugger.py:

```python
import re
import unicodedata

_PUNCT = re.compile(r'[^\w\s-]')        # anything that is not word char, whitespace or hyphen
_SEPARATORS = re.compile(r'[\s_-]+')     # runs of whitespace, underscore, hyphen


def slugify(text):
    if not text:
        return ''
    # 1. Transliterate accents: decompose (NFKD) and drop the combining marks
    #    by round-tripping through ASCII with errors='ignore'.
    text = unicodedata.normalize('NFKD', str(text))
    text = text.encode('ascii', 'ignore').decode('ascii')
    # 2. Lowercase.
    text = text.lower()
    # 3. Strip punctuation (remove, do not turn into separators).
    text = _PUNCT.sub('', text)
    # 4. Collapse runs of separators to a single hyphen and trim the ends.
    return _SEPARATORS.sub('-', text).strip('-')
```

Steps for the implementer:
1. Read test_slugger.py and note the five exact input/expected pairs.
2. Replace the stub body of `slugify` in slugger.py with the implementation above (keep any existing module docstring / signature / `__all__`).
3. Run the verification commands below. If any test fails, compare against the Key decisions section and switch to the alternative noted there.

## Key decisions and trade-offs
- **Transliteration via NFKD + ASCII-ignore (chosen) vs. a hand-written map.** NFKD decomposes `é` into `e` + combining acute, and the ASCII encode drops the mark. It is standard-library only and covers all Latin diacritics. Trade-off: characters with no decomposition (`ß`, `æ`, `ø`, `ł`, `œ`) are silently dropped rather than mapped to `ss`, `ae`, `o`, `l`, `oe`. Only add a small pre-NFKD `str.translate` map for those if a test demands it.
- **Punctuation stripped vs. punctuation treated as a separator.** The task says punctuation is *stripped* and separators are *collapsed*, which matches the Django-style two-step above (`don't` -> `dont`, `hello, world` -> `hello-world`). The alternative single regex `re.sub(r'[^a-z0-9]+', '-', s)` would yield `don-t`. The two approaches differ only when punctuation touches letters with no adjacent whitespace. The tests decide this; check them first.
- **Order of operations.** Normalize before lowercasing so `str.lower()` sees plain ASCII; strip punctuation before collapsing separators so `a - b` becomes `a-b` rather than `a---b`.
- **Underscore handling.** `\w` keeps `_` in step 3, and step 4 treats it as a separator, so `déjà_vu` -> `deja-vu`. If a test expects underscores preserved, drop `_` from `_SEPARATORS`.
- **`if not text: return ''`** also handles `None` cheaply; harmless if the tests only pass `''`.
- **Precompiled module-level regexes** keep the function cheap to call repeatedly; no behavioural difference.

## Non-goals
- Do not edit test_slugger.py.
- No new dependencies, no requirements file, no packaging changes.
- No max-length truncation, no uniqueness suffixing, no non-Latin script transliteration (Cyrillic, CJK, etc.).
- No CLI, logging, or type-checking tooling.
- Do not touch AGORA-LOG.md or the hook configuration.

## Assumptions (with sources)
- Five tests exist in test_slugger.py covering: lowercase, hyphen separation, punctuation stripping, separator collapsing, accent transliteration, empty input. Source: task statement.
- The test module imports `slugify` from a top-level module named `slugger` (`from slugger import slugify`). Source: task statement names the file slugger.py and the function slugify; could not verify because file reads were blocked.
- `python -m unittest -q` with no explicit module relies on default discovery (pattern `test*.py` from the current directory), so it must be run from the repo root. Source: Python unittest docs for discovery defaults.
- Separator characters are ASCII hyphen, whitespace and underscore. Source: task statement says hyphen-separated; underscore inclusion is the common convention and is an inference, not verified.
- Python 3.x is on PATH as `python`. Source: environment is Windows 11 with PowerShell; not verified because shell calls were blocked.

## Exact verification commands
Run from the repo root in PowerShell:

```powershell
python -m unittest -q
```
Expected: last lines contain `Ran 5 tests` and `OK`, exit code 0.

```powershell
python -m unittest -v test_slugger
```
Expected: each of the five test names printed with `ok`.

```powershell
python -c "from slugger import slugify; print(repr(slugify('  Crème Brûlée!!  Déjà_vu -- Hello, World  ')), repr(slugify('')))"
```
Expected: `'creme-brulee-deja-vu-hello-world' ''`

```powershell
git status --porcelain
```
Expected: only ` M slugger.py` (plus the pre-existing untracked `?? AGORA-LOG.md`); test_slugger.py must not appear as modified.

```powershell
python -c "import ast,sys; t=ast.parse(open('slugger.py',encoding='utf-8').read()); print(sorted({(n.names[0].name if isinstance(n,ast.Import) else n.module) for n in ast.walk(t) if isinstance(n,(ast.Import,ast.ImportFrom))}))"
```
Expected: `['re', 'unicodedata']` (standard library only).