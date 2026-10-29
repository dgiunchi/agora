## Goal
Implement `parse_size(text)` in `sizes.py` to convert valid human-readable sizes to integer bytes and raise `ValueError` for invalid input, using only the standard library.

## Observable acceptance criteria
- `python -m unittest -q` exits successfully with all existing tests passing.
- Bare numbers and `B` represent bytes; `KB`/`MB`/`GB`/`TB` use powers of 1000; `KiB`/`MiB`/`GiB`/`TiB` use powers of 1024.
- Units are case-insensitive. Surrounding whitespace and zero or one literal space between number and unit are accepted.
- Fractional results are rounded down exactly: `1.9999KiB` becomes `2047`; `0.5B` becomes `0`.
- Empty input, signs (including `-0` and `+5MB`), unknown units, malformed numbers, and non-string input raise `ValueError`.
- Existing tests remain unchanged.

## Concrete approach
1. Preserve the public signature `parse_size(text: str) -> int` and replace its `NotImplementedError` stub.
2. Check `isinstance(text, str)` before calling string methods; raise `ValueError` otherwise.
3. Strip surrounding whitespace, then use `re.fullmatch` with ASCII number grammar `[0-9]+(?:\.[0-9]+)?`, an optional single literal space, and an optional ASCII-letter unit. Reject any mismatch with `ValueError`.
4. Lowercase the captured unit and look it up in an explicit multiplier dictionary containing only the empty suffix, `b`, `kb`, `mb`, `gb`, `tb`, `kib`, `mib`, `gib`, and `tib`. Reject missing entries with `ValueError`.
5. Split the validated number at its decimal point. Convert its digits to an integer numerator and use `10 ** fractional_digit_count` as the denominator. Return `(numerator * multiplier) // denominator`.
6. Run the existing suite and focused boundary checks without modifying test files.

## Key decisions and trade-offs
- Use full-string validation rather than permissive numeric conversion, preventing exponent notation, commas, underscores, signs, embedded whitespace, and trailing garbage from slipping through.
- Use exact integer arithmetic rather than floating point or context-dependent decimal arithmetic, avoiding rounding errors near byte boundaries.
- Interpret the allowed internal separator as one ASCII space; tabs, newlines, and repeated spaces between number and unit are invalid.
- Use a small explicit unit table to make the accepted vocabulary and multipliers auditable.

## Non-goals
- Editing tests, adding dependencies, changing unrelated files, or implementing formatting back to human-readable sizes.
- Supporting additional units, signed values, scientific notation, locale-specific numbers, or permissive input coercion.
- Writing implementation changes during this read-only planning task.

## Assumptions and sources
- `sizes.py` currently contains only the documented parser stub; its module docstring identifies `test_sizes.py` as the contract.
- `test_sizes.py` supplies eight test methods covering bytes, decimal and binary units, casing, whitespace, fractional flooring, malformed inputs, signs, and non-string inputs.
- The user request additionally requires `TiB` and a maximum of one internal space, although these boundaries are not explicitly tested in the current suite.
- Fractional notation requires digits on both sides of the decimal point; `.5` and `1.` are treated as malformed. This is a conservative interpretation of the request's examples and malformed-number rejection requirement.
- Surrounding whitespace follows Python `str.strip()` semantics. No explicit numeric size limit is introduced beyond Python's runtime limits.
- Supplementary guidance search at prompts.chat (`search_skills`, query `python parsing`) was unavailable: automatic approval review rejected the call because approval is required and the policy is `never`. No external skill was applied.

## Exact verification commands
Run from the repository root. Disable bytecode writes for read-only-compatible verification:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest -q
```

Run additional checks without editing the tests:

```powershell
@'
from sizes import parse_size

valid = {
    '0': 0,
    '1TiB': 1099511627776,
    '\t1 tIb\n': 1099511627776,
    '1.9999KiB': 2047,
    '0.999999999999999999B': 0,
    '1.000000000001TB': 1000000000001,
}
for value, expected in valid.items():
    result = parse_size(value)
    assert type(result) is int and result == expected, (value, result)

for value in ('1  KB', '1\tKB', '1\nKB', '.5KB', '1.KB', '1e3B', 'NaN', 'Infinity', '1_000B', '1PB', '1KB junk', '+0', '-0', True, [], {}):
    try:
        parse_size(value)
    except ValueError:
        pass
    else:
        raise AssertionError(repr(value))
print('Boundary checks passed')
'@ | python -
```

These commands are planned verification; they were not executed during repository inspection.