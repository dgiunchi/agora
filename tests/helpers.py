"""Shared fixtures: fake agents, scripts, disposable git repos. No quota is ever spent."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agent_commons.allocator import Allocator
from agent_commons.coordinator import Coordinator, LoopConfig
from agent_commons.ledger import Ledger
from agent_commons.registry import AgentSpec, QuotaWindow

FAKE = Path(__file__).with_name("fake_agent.py").resolve()
THREE = (("alpha", "anthropic", "free"), ("beta", "openai", "subscription"), ("gamma", "google", "free"))
ALL_CAPS = {"plan": 0.9, "review": 0.9, "build": 0.9, "inspect": 0.9, "judge": 0.9, "research": 0.5}


def fake_spec(agent_id: str, provider: str, tier: str = "free", caps: dict | None = None,
              windows: tuple[QuotaWindow, ...] | None = None, **kw) -> AgentSpec:
    return AgentSpec(
        id=agent_id, provider=provider, kind="cli", tier=tier,
        argv=(sys.executable, str(FAKE), "--agent", agent_id, "--role", "{role}", "--prompt", "{prompt}"),
        read_only_argv=("--read-only",), model=kw.pop("model", f"{agent_id}-model"),
        reset=kw.pop("reset", "utc-midnight"),
        windows=windows if windows is not None else (QuotaWindow("day", 86400, requests=1000),),
        capabilities=dict(caps or ALL_CAPS), **kw)


def fake_catalog(agents=THREE, caps: dict | None = None) -> dict[str, AgentSpec]:
    return {a: fake_spec(a, p, t, caps) for a, p, t in agents}


def write_script(tmp: Path, script: dict) -> Path:
    path = Path(tmp) / "script.json"
    for stale in Path(tmp).glob("script.json.*.n"):
        stale.unlink()
    path.write_text(json.dumps(script), encoding="utf-8")
    return path


def review(verdict: str = "APPROVED", findings=(), coverage=("PLAN.md",), summary="ok") -> dict:
    return {"json": {"verdict": verdict, "summary": summary, "findings": list(findings),
                     "coverage": list(coverage), "limitations": []}}


def finding(fid: str, severity: str = "high", path: str = "PLAN.md") -> dict:
    return {"id": fid, "severity": severity, "path": path, "evidence": f"evidence for {fid}", "fix": "fix it"}


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(repo),
                         capture_output=True, text=True, check=True)
    return out.stdout


def make_repo(tmp: Path) -> Path:
    repo = Path(tmp) / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("toy\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def make_coordinator(tmp: Path, strategy: str, script: dict | None = None, *, agents=THREE,
                     allocation: str = "auto:smart", seed: int = 7, **cfg) -> tuple[Coordinator, Ledger]:
    tmp = Path(tmp)
    repo = make_repo(tmp) if not (tmp / "repo").exists() else tmp / "repo"
    script_path = write_script(tmp, script or {})
    catalog = fake_catalog(agents)
    ledger = Ledger(":memory:")
    allocator = Allocator(catalog, ledger, strategy=allocation, seed=seed)
    config = LoopConfig(task="make toy.add work", strategy=strategy, allocation=allocation, repo=repo,
                        artifacts_root=tmp / "artifacts", timeout=cfg.pop("timeout", 60.0),
                        env={"FAKE_SCRIPT": str(script_path)}, **cfg)
    return Coordinator(config, catalog, ledger, allocator), ledger


def log_text(coord: Coordinator) -> str:
    return coord.log_path.read_text(encoding="utf-8")


def events(coord: Coordinator, kind: str) -> list[dict]:
    return [e for e in coord.outcome.events if e["kind"] == kind]


def proof_cmd_for(repo: Path) -> list[str]:
    return [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import toy; assert toy.add(1, 2) == 3"]


def toy_build_response() -> dict:
    return {"json": {"summary": "wrote toy.py", "files_changed": ["toy.py"]},
            "write_files": {"toy.py": "def add(a, b):\n    return a + b\n"}}