#!/usr/bin/env python3
"""Run a bundled use case against your own agents, without touching any real repo.

    python examples/use-cases/run.py feature-review --catalog agents.toml
    python examples/use-cases/run.py red-team       --catalog agents.toml --dry-run

Each use case directory holds a tiny fixture repository (tests that fail until the
task is done) and a `usecase.json` with the task, the agora flags and the proof
command. This script copies the fixture into a fresh temporary directory, makes
it a git repository with one commit, and runs `agora run` there. Per-turn
diagnostics go next to it, never inside the checkout. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SKIP = {"usecase.json", "README.md", "__pycache__", "result"}


def available() -> list[str]:
    return sorted(p.name for p in HERE.iterdir() if (p / "usecase.json").exists())


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=agora", "-c", "user.email=agora@example.invalid", *args],
                   cwd=str(cwd), check=True, capture_output=True, text=True)


def prepare(name: str, workdir: Path | None) -> tuple[Path, dict]:
    src = HERE / name
    spec = json.loads((src / "usecase.json").read_text(encoding="utf-8"))
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix=f"agora-{name}-"))
    workdir.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in SKIP:
            continue
        if item.is_dir():
            shutil.copytree(item, workdir / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, workdir / item.name)
    if not (workdir / ".git").exists():
        git(workdir, "init", "-q")
        git(workdir, "add", ".")
        git(workdir, "commit", "-q", "-m", f"fixture: {name}")
    return workdir, spec


def agora_command() -> list[str]:
    exe = shutil.which("agora")
    return [exe] if exe else [sys.executable, "-m", "agora.cli"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", choices=available(), help="which use case to run")
    p.add_argument("--catalog", help="agents.toml to use (default: agora's own lookup)")
    p.add_argument("--ledger", help="ledger path (default: ~/.agora/ledger.sqlite)")
    p.add_argument("--workdir", help="where to put the fixture checkout (default: a fresh temp dir)")
    p.add_argument("--seed", type=int, help="seed the bandit for a reproducible allocation")
    p.add_argument("--dry-run", action="store_true", help="show who would fill each role, spend nothing")
    p.add_argument("extra", nargs="*", help="extra flags for `agora run`, after `--`")
    args = p.parse_args(argv)

    workdir, spec = prepare(args.name, Path(args.workdir) if args.workdir else None)
    artifacts = workdir.parent / f"{workdir.name}-artifacts"
    cmd = agora_command() + ["run", spec["task"], "--repo", str(workdir), "--artifacts", str(artifacts),
                             "--proof-cmd", spec["proof_cmd"], *spec.get("args", [])]
    if args.catalog:
        cmd += ["--catalog", str(Path(args.catalog).resolve())]
    if args.ledger:
        cmd += ["--ledger", args.ledger]
    if args.seed is not None:
        cmd += ["--seed", str(args.seed)]
    if args.dry_run:
        cmd.append("--dry-run")
    cmd += args.extra

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    print(f"use case : {spec['title']}")
    print(f"fixture  : {workdir}")
    print(f"artifacts: {artifacts}")
    print("command  : " + " ".join(_quote(c) for c in cmd), flush=True)
    code = subprocess.run(cmd, env=env, check=False).returncode
    if not args.dry_run:
        print(f"\nlog      : {workdir / 'AGORA-LOG.md'}")
        print(f"plan     : {workdir / 'PLAN.md'}")
    return code


def _quote(s: str) -> str:
    return f'"{s}"' if " " in s or not s else s


if __name__ == "__main__":
    sys.exit(main())
