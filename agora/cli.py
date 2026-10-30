"""`agora` command line: run a loop, show roles, show quota, doctor the agents."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import __version__
from .allocator import AllocationError, Allocator
from .coordinator import ALL_ROLES, JUDGES, STRATEGIES, Coordinator, LoopConfig
from .ledger import Ledger
from .registry import CatalogError, load_catalog, load_profiles
from .runner import build_argv, resolve_launch

DEFAULT_CATALOG_NAMES = ("agents.toml",)


def _find_catalog(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SystemExit(f"catalog not found: {p}")
        return p
    for name in DEFAULT_CATALOG_NAMES:
        for base in (Path.cwd(), Path.home() / ".agora"):
            p = base / name
            if p.exists():
                return p
    raise SystemExit("no catalog found: run `agora agents init` to write agents.toml here, "
                     "or pass --catalog")


def split_command(text: str, *, windows: bool = os.name == "nt") -> list[str]:
    """Split a quoted command line into argv.

    POSIX rules on POSIX. On Windows, shlex's non-POSIX mode keeps the quote
    characters inside each token (so `python -c "import x"` would pass a string
    literal to Python and exit 0); strip them so the proof really runs.
    """
    if not windows:
        return shlex.split(text, posix=True)
    out = []
    for tok in shlex.split(text, posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def _allocator(args: argparse.Namespace, catalog, ledger, path: Path) -> Allocator:
    return Allocator(catalog, ledger, strategy=args.allocation, seed=args.seed,
                     profiles=load_profiles(path),
                     require_installed=not getattr(args, "assume_installed", False))


def _ledger_path(explicit: str | None) -> str:
    if explicit:
        return explicit
    d = Path.home() / ".agora"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "ledger.sqlite")


def cmd_run(args: argparse.Namespace) -> int:
    path = _find_catalog(args.catalog)
    catalog = load_catalog(path)
    ledger = Ledger(_ledger_path(args.ledger))
    allocator = _allocator(args, catalog, ledger, path)
    cfg = LoopConfig(task=args.task, strategy=args.strategy, allocation=args.allocation, rounds=args.rounds,
                     max_fix_rounds=args.max_fix_rounds, max_inspection_rounds=args.max_inspection_rounds,
                     quorum=args.quorum, judge=args.judge,
                     proof_cmd=split_command(args.proof_cmd) if args.proof_cmd else None,
                     repo=Path(args.repo), plan_file=args.plan, log_file=args.log, timeout=args.timeout,
                     builder=args.builder, artifacts_root=Path(args.artifacts) if args.artifacts else None,
                     fallback_roles=() if args.no_fallback else ALL_ROLES)
    coord = Coordinator(cfg, catalog, ledger, allocator)
    print(f"catalog: {path}")
    if args.dry_run:
        try:
            allocs = coord.dry_run()
        except AllocationError as exc:
            print(f"dry-run: {exc}", file=sys.stderr)
            return 2
        print(f"dry-run for strategy={cfg.strategy} allocation={cfg.allocation} (no quota spent)")
        for a in allocs:
            print("  " + a.as_line())
        for note in coord.dry_run_notes:
            print(f"  note: {note}")
        return 0
    if not cfg.repo.is_dir():
        print(f"run: --repo {cfg.repo} is not a directory", file=sys.stderr)
        return 2
    outcome = coord.run()
    print(f"status: {outcome.status}" + (f" ({outcome.reason})" if outcome.reason else ""))
    if outcome.proof_skipped:
        print("warning: no --proof-cmd was given; nothing mechanical checked the deliverable")
    print(f"plan: {coord.plan_path}  sha256: {outcome.plan_sha256}")
    print(f"rounds: {outcome.rounds_used}  inspections: {outcome.inspections}  "
          f"fallbacks: {len(outcome.fallbacks)}")
    for ev in outcome.fallbacks:
        print(f"  fallback {ev.role}: {ev.fallback_from} -> {ev.fallback_to} ({ev.reason})")
    if outcome.open_findings:
        print(f"open findings: {len(outcome.open_findings)}")
        for f in outcome.open_findings:
            print(f"  [{f['severity']}] {f['id']} {f['path']}: {f['evidence'][:100]}")
    print(f"log: {coord.log_path}")
    return 0 if outcome.ok else 1


def cmd_roles(args: argparse.Namespace) -> int:
    path = _find_catalog(args.catalog)
    catalog = load_catalog(path)
    ledger = Ledger(_ledger_path(args.ledger))
    allocator = _allocator(args, catalog, ledger, path)
    cfg = LoopConfig(task="(roles preview)", strategy=args.strategy, allocation=args.allocation,
                     quorum=args.quorum, builder=args.builder, judge=args.judge)
    coord = Coordinator(cfg, catalog, ledger, allocator)
    try:
        allocs = coord.dry_run()
    except AllocationError as exc:
        print(f"roles: {exc}", file=sys.stderr)
        return 2
    for a in allocs:
        print(a.as_line())
    for note in coord.dry_run_notes:
        print(f"note: {note}")
    return 0


def cmd_quota(args: argparse.Namespace) -> int:
    catalog = load_catalog(_find_catalog(args.catalog))
    ledger = Ledger(_ledger_path(args.ledger))
    for agent_id in args.forget or ():
        if agent_id not in catalog:
            print(f"quota: unknown agent {agent_id!r}", file=sys.stderr)
            return 2
        ledger.forget(agent_id)
        print(f"forgot learned caps and cooldown for {agent_id}")
    rows = ledger.usage_report(catalog)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(f"{'agent':<18}{'tier':<13}{'req':>5}{'fail':>6}{'headroom':>10}{'cooldown':>10}")
    for r in rows:
        print(f"{r['agent']:<18}{r['tier']:<13}{r['requests']:>5}{r['failures']:>6}{r['headroom']:>10.2f}"
              f"{r['cooldown_s']:>9.0f}s")
        for w in r["windows"]:
            rc = w['requests_cap'] if w['requests_cap'] is not None else '?'
            tc = w['tokens_cap'] if w['tokens_cap'] is not None else '?'
            print(f"{'':<18}  {w['window']:<10} {w['requests']}/{rc} req   {w['tokens']}/{tc} tok")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    if args.agents_cmd == "init":
        return _agents_init(Path(args.catalog or "agents.toml"), force=args.force)
    catalog = load_catalog(_find_catalog(args.catalog))
    if args.agents_cmd == "list":
        for s in catalog.values():
            caps = " ".join(f"{k}={v:.1f}" for k, v in sorted(s.capabilities.items()))
            print(f"{s.id:<18}{s.provider:<12}{s.kind:<5}{s.tier:<13}{s.model or 'cli-default':<24}{caps}")
        return 0
    rows = _doctor(catalog, probe_timeout=20.0)
    bad = 0
    for status, line in rows:
        bad += status != "ok"
        print(line)
    if bad:
        print(f"{bad} agent(s) cannot run here. Edit the catalog to keep only the agents you have; "
              f"the allocator skips agents whose executable is missing.")
    return 1 if bad else 0


def _agents_init(dest: Path, *, force: bool) -> int:
    """Write the bundled example catalog so a pip/pipx install has something to edit."""
    from importlib import resources
    if dest.exists() and not force:
        print(f"{dest} exists; pass --force to overwrite", file=sys.stderr)
        return 2
    text = (resources.files("agora") / "agents.example.toml").read_text(encoding="utf-8")
    dest.write_text(text, encoding="utf-8")
    print(f"wrote {dest}: now delete the agents you do not have and run `agora agents doctor`")
    return 0


def _probe(spec) -> tuple[str, str]:
    """One doctor row: (status, line). ok | WARN | MISSING. Never spends quota."""
    sid = f"{spec.id:<18}"
    if spec.kind == "http":
        has_key = bool(spec.api_key_env and os.environ.get(spec.api_key_env))
        return "ok", f"{sid} http     endpoint={spec.endpoint}  model={spec.model}  key_env={'set' if has_key else 'missing'}"
    argv = build_argv(spec, "x", role="doctor")
    try:
        launch = resolve_launch(argv)   # exactly what `agora run` would execute
    except FileNotFoundError:
        return "MISSING", f"{sid} MISSING  {argv[0]} not on PATH"
    except OSError as exc:
        return "WARN", f"{sid} WARN     {exc}"
    exe = launch[0] if len(launch) == 1 or not launch[1].lower().endswith((".js", ".cjs", ".mjs")) else " ".join(launch[:2])
    probe = launch[:2] + ["--version"] if launch[1:2] and launch[1].lower().endswith((".js", ".cjs", ".mjs")) else [launch[0], "--version"]
    try:
        out = subprocess.run(probe, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20, check=False, stdin=subprocess.DEVNULL)
        blob = (out.stdout or out.stderr).strip()
        ver = blob.splitlines()[0] if blob else "?"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "WARN", f"{sid} WARN     {exe}  --version did not answer: {exc}"
    return "ok", f"{sid} ok       {exe}  {ver}"


def _doctor(catalog, *, probe_timeout: float) -> list[tuple[str, str]]:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(_probe, catalog.values()))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agora", description="A quota-aware system of agentic systems.")
    p.add_argument("--version", action="version", version=f"agora {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--catalog", help="agents.toml path (default: ./agents.toml or ~/.agora/)")
    common.add_argument("--ledger", help="SQLite ledger path (default: ~/.agora/ledger.sqlite)")
    common.add_argument("--allocation", default="auto:smart",
                        help="auto:smart | auto:fast | auto:cheap | profile:<name> (a [profiles] entry in the catalog)")
    common.add_argument("--seed", type=int, default=None, help="seed the bandit for reproducible allocation")
    common.add_argument("--assume-installed", action="store_true",
                        help="do not skip agents whose executable is missing (dry runs on another machine)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", parents=[common], help="run one loop")
    r.add_argument("task")
    r.add_argument("--strategy", choices=STRATEGIES, default="hybrid")
    r.add_argument("--rounds", type=int, default=5)
    r.add_argument("--max-fix-rounds", type=int, default=2)
    r.add_argument("--max-inspection-rounds", type=int, default=2)
    r.add_argument("--quorum", type=int, default=1)
    r.add_argument("--judge", choices=JUDGES, default="both")
    r.add_argument("--proof-cmd",
                   help='command that proves the deliverable, one quoted string, e.g. --proof-cmd "python -m unittest -q"')
    r.add_argument("--repo", default=".")
    r.add_argument("--plan", default="PLAN.md")
    r.add_argument("--log", default="AGORA-LOG.md")
    r.add_argument("--timeout", type=float, default=600.0)
    r.add_argument("--builder", help="force a builder agent id")
    r.add_argument("--artifacts", help="directory for per-turn diagnostics (default: system temp)")
    r.add_argument("--no-fallback", action="store_true", help="disable explicit fallback for every role")
    r.add_argument("--dry-run", action="store_true", help="print the allocation plan and spend nothing")
    r.set_defaults(fn=cmd_run)

    ro = sub.add_parser("roles", parents=[common], help="show who would fill each role")
    ro.add_argument("--strategy", choices=STRATEGIES, default="hybrid")
    ro.add_argument("--quorum", type=int, default=1)
    ro.add_argument("--builder")
    ro.add_argument("--judge", choices=JUDGES, default="both")
    ro.set_defaults(fn=cmd_roles)

    q = sub.add_parser("quota", parents=[common], help="show the quota ledger")
    q.add_argument("--json", action="store_true")
    q.add_argument("--forget", action="append", metavar="AGENT",
                   help="drop learned caps and cooldown for AGENT (after a misdetected rate limit)")
    q.set_defaults(fn=cmd_quota)

    a = sub.add_parser("agents", parents=[common], help="init, list or doctor the catalog")
    a.add_argument("agents_cmd", choices=("init", "list", "doctor"),
                   help="init writes the bundled example catalog to ./agents.toml (or --catalog PATH)")
    a.add_argument("--force", action="store_true", help="init: overwrite an existing catalog")
    a.set_defaults(fn=cmd_agents)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")   # agent text on a cp1252 console must not crash the run
            except (ValueError, OSError):
                pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except CatalogError as exc:
        print(f"catalog error: {exc}", file=sys.stderr)
        return 2
    except AllocationError as exc:
        print(f"allocation error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())