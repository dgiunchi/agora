## Goal
Implement `slugify(text)` in `slugger.py` using only the Python standard library so all five existing tests pass. Leave tests unchanged.

## Observable acceptance criteria
- `Hello World` → `hello-world`
- `Hello, World! (2026)` → `hello-world-2026`
- `  a  --  b__c  ` → `a-b-c`
- `Crème Brûlée` → `creme-brulee`
- Empty string → empty string
- `python -m unittest -q` exits successfully and reports five passing tests.

## Concrete approach
1. Add standard-library imports `re` and `unicodedata` to `slugger.py`.
2. Replace the `NotImplementedError` in `slugify`, preserving its signature.
3. Normalize input with `unicodedata.normalize('NFKD', text)`, encode as ASCII with unsupported characters ignored, decode, and lowercase.
4. Strip punctuation except hyphens and underscores using `re.sub(r'[^a-z0-9\s_-]', '', text)`.
5. Collapse whitespace, underscores, and hyphens into one hyphen using `re.sub(r'[\s_-]+', '-', text)`, then strip leading and trailing hyphens.
6. Run verification and inspect the diff to confirm only the implementation changed.

## Key decisions and trade-offs
- Remove punctuation rather than converting all punctuation to separators; whitespace, underscores, and hyphens explicitly act as separators.
- NFKD plus ASCII encoding handles the tested accents without dependencies. It is not a comprehensive transliteration system for every script.
- Empty input naturally remains empty through the same pipeline; no special case is necessary.

## Non-goals
- Editing tests or adding dependencies.
- Comprehensive multilingual transliteration, slug uniqueness, length limits, or URL routing.
- Defining behavior for non-string inputs.

## Assumptions and sources
- `test_slugger.py` contains the five concrete acceptance examples above and is the authoritative contract, as requested by the user and reiterated in the `slugger.py` module docstring.
- Inspection confirms `slugify(text: str) -> str` currently raises `NotImplementedError`.
- Treating whitespace, underscores, and hyphens as separators follows `test_collapses_separators`; punctuation removal follows the user requirement and `test_strips_punctuation`.
- External guidance lookup through prompts-chat was attempted, but automatic approval review rejected the search because approval is required and the approval policy is `never`. No external guidance was applied.
- This planning pass inspected files only; implementation and test execution remain for the builder.

## Exact verification commands
Run from the current working directory:
```powershell
python -m unittest -q
git diff --check
git diff -- slugger.py test_slugger.py
git status --short
```
Confirm five tests pass, no whitespace errors appear, and `test_slugger.py` remains unchanged.