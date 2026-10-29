"""Lint one or more agent catalogs. Exit 1 on any problem. Used in CI."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agora.registry import validate_catalog  # noqa: E402


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv] or [Path(__file__).resolve().parents[1] / "agents.example.toml"]
    bad = 0
    for path in paths:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"{path}: cannot parse: {exc}")
            bad += 1
            continue
        errors = validate_catalog(data)
        if errors:
            bad += 1
            print(f"{path}: {len(errors)} problem(s)")
            for e in errors:
                print(f"  - {e}")
        else:
            n = len(data["agents"])
            kinds = sorted({a.get("kind", "cli") for a in data["agents"]})
            print(f"{path}: ok ({n} agents, kinds: {', '.join(kinds)})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))