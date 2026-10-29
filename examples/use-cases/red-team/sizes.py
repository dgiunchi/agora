"""Fixture: parse_size is the task. The tests in test_sizes.py are the contract.

A parser is a good red-team target: every accepted shape is a chance to accept
something that should have been rejected.
"""


def parse_size(text: str) -> int:
    raise NotImplementedError("the builder implements this")
