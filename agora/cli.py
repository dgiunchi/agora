"""`agora` command line: run a loop, show roles, show quota, doctor the agents."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .allocator import AllocationError, Allocator
from .coordinator import ALL_ROLES, JUDGES, STRATEGIES, Coordinator, LoopConfig
from .ledger import Ledger
from .registry import CatalogError, load_catalog
from .runner import build_argv

DEFAULT_CATALOG_NAMES = ("agents.toml", "agents.example.toml")


def _find_catalog(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for name in DEFAULT_CATALOG_NAMES:
        for base in (Path.cwd(), Path.home() / ".agora"):
            p = base / name
            if p.exists():
                return p
    raise SystemExit("no catalog found: pass --catalog or create agents.toml (see agents.example.toml)")


def _ledger_path(explicit: str | None) -> str:
    if explicit:
        return explicit
    d = Path.home() / ".agora"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "ledger.sqlite")


def cmd_run(args: argparse.Namespace) -> int:
    catalog = load_catalog(_find_catalog(args.catalog))
    ledger = Ledger(_ledger_path(args.ledger))
    allocator = Allocator(catalog, ledger, strategy=args.allocation, seed=args.seed)
    cfg = LoopConfig(task=args.task, strategy=args.strategy, allocation=args.allocation, rounds=args.rounds,
                     max_fix_rounds=args.max_fix_rounds, max_inspection_rounds=args.max_inspection_rounds,
                     quorum=args.quorum, judge=args.judge,
                     proof_cmd=shlex.split(args.proof_cmd, posix=os.name != "nt") if args.proof_cmd else None,
                     repo=Path(args.repo), plan_file=args.plan, log_file=args.log, timeout=args.timeout,
                     builder=args.builder, artifacts_root=Path(args.artifacts) if args.artifacts else None,
                     fallback_roles=() if args.no_fallback else ALL_ROLES)
    coord = Coordinator(cfg, catalog, ledger, allocator)
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
    outcome = coord.run()
    print(f"status: {outcome.status}" + (f" ({outcome.reason})" if outcome.reason else ""))
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
    catalog = load_catalog(_find_catalog(args.catalog))
    ledger = Ledger(_ledger_path(args.ledger))
    allocator = Allocator(catalog, ledger, strategy=args.allocation, seed=args.seed)
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
    catalog = load_catalog(_find_catalog(args.catalog))
    if args.agents_cmd == "list":
        for s in catalog.values():
            caps = " ".join(f"{k}={v:.1f}" for k, v in sorted(s.capabilities.items()))
            print(f"{s.id:<18}{s.provider:<12}{s.kind:<5}{s.tier:<13}{s.model or 'cli-default':<24}{caps}")
        return 0
    bad = 0
    for s in catalog.values():
        if s.kind == "http":
            has_key = bool(s.api_key_env and os.environ.get(s.api_key_env))
            print(f"{s.id:<18} http     endpoint={s.endpoint}  model={s.model}  "
                  f"key_env={'set' if has_key else 'missing'}")
            continue
        argv = build_argv(s, "x", role="doctor")
        exe = shutil.which(argv[0]) or (argv[0] if Path(argv[0]).exists() else None)
        if not exe:
            print(f"{s.id:<18} MISSING  {argv[0]} not on PATH")
            bad += 1
            continue
        via_node = bool(s.node_entry) and argv[0] != s.argv[0]
        probe = [exe, argv[1], "--version"] if via_node else [exe, "--version"]
        try:
            out = subprocess.run(probe, capture_output=True, text=True, timeout=20, check=False)
            blob = (out.stdout or out.stderr).strip()
            ver = blob.splitlines()[0] if blob else "?"
        except (OSError, subprocess.TimeoutExpired) as exc:
            ver = f"version probe failed: {exc}"
            bad += 1
        print(f"{s.id:<18} ok       {exe}  {ver}")
    return 1 if bad else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agora", description="A quota-aware system of agentic systems.")
    p.add_argument("--version", action="version", version=f"agora {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--catalog", help="agents.toml path (default: ./agents.toml or ~/.agora/)")
    common.add_argument("--ledger", help="SQLite ledger path (default: ~/.agora/ledger.sqlite)")
    common.add_argument("--allocation", default="auto:smart",
                        help="auto:smart | auto:fast | auto:cheap | profile:<name>")
    common.add_argument("--seed", type=int, default=None, help="seed the bandit for reproducible allocation")
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

    a = sub.add_parser("agents", parents=[common], help="list or doctor the catalog")
    a.add_argument("agents_cmd", choices=("list", "doctor"))
    a.set_defaults(fn=cmd_agents)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args))
    except CatalogError as exc:
        print(f"catalog error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())