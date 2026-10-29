"""Fixture: parse_size is the task. The tests in test_sizes.py are the contract.

A parser is a good red-team target: every accepted shape is a chance to accept
something that should have been rejected.
"""

import re


def parse_size(text: str) -> int:
    """Parse a nonnegative size, flooring fractional bytes exactly."""
    if not isinstance(text, str):
        raise ValueError("size must be a string")

    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?) ?([A-Za-z]*)", text.strip())
    if match is None:
        raise ValueError("invalid size")

    number, unit = match.groups()
    multipliers = {
        "": 1,
        "b": 1,
        "kb": 1000,
        "mb": 1000 ** 2,
        "gb": 1000 ** 3,
        "tb": 1000 ** 4,
        "kib": 1024,
        "mib": 1024 ** 2,
        "gib": 1024 ** 3,
        "tib": 1024 ** 4,
    }
    multiplier = multipliers.get(unit.lower())
    if multiplier is None:
        raise ValueError("unknown size unit")

    whole, _, fraction = number.partition(".")
    numerator = int(whole + fraction)
    denominator = 10 ** len(fraction)
    return (numerator * multiplier) // denominator
