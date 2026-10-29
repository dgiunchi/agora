# Contributing

Thanks for helping. The bar is the same for human and AI-assisted contributions: a test, a green suite, and an honest description.

## Dev loop

```text
git clone https://github.com/dgiunchi/agent-commons
cd agent-commons
python scripts/validate.py agents.example.toml
python -m unittest discover -s tests -t . -v
pip install -e .
commons --version
```

Python 3.11+ and git are the only requirements. There are no runtime dependencies; please keep it that way unless a dependency removes a lot of code.

## What a PR needs

- A test. Strategy and fallback behaviour is tested with the fake agent in `tests/fake_agent.py`; look at `tests/helpers.py` for the fixtures.
- The suite green on your OS. CI covers Ubuntu, Windows and macOS.
- No quota spent by default tests. Live runs are opt-in and go in `VALIDATION.md`.
- A `[[agents]]` entry change must pass `scripts/validate.py`.

## Adding an agent

1. Add a table to `agents.example.toml` with its headless argv (`{prompt}` and optionally `{role}` placeholders), the read-only flags if it has them, the documented quota windows, and capability priors.
2. If it emits JSON, set `output_format = "json"` or `"stream-json"`; the runner unwraps `{"result": ...}` style wrappers.
3. Run `commons agents doctor` and a `--dry-run`. If you run a real loop, add the result to `VALIDATION.md`.

## Style

Standard library, type hints, dataclasses, short functions. Argv lists, never shell strings. Every failure mode must surface in the log and in `LoopOutcome.reason`.