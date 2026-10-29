"""Approval binding and inspection fingerprints.

An approval is bound to the exact plan bytes (SHA256). Change one byte and the
approval is void. An inspection is bound to a fingerprint of the working tree
(tracked diff plus every untracked file), so later edits need a new inspection.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_sha256(plan_path: str | Path) -> str:
    return sha256_bytes(Path(plan_path).read_bytes())


@dataclass(frozen=True)
class Approval:
    plan_path: str
    plan_sha256: str
    agent_id: str
    round: int
    result_path: str

    def to_dict(self) -> dict:
        return asdict(self)


def bind_approval(plan_path: str | Path, agent_id: str, round_no: int, result_path: str) -> Approval:
    return Approval(plan_path=str(plan_path), plan_sha256=plan_sha256(plan_path), agent_id=agent_id,
                    round=round_no, result_path=result_path)


def check_approval(plan_path: str | Path, approval: Approval | None) -> bool:
    if approval is None:
        return False
    if str(plan_path) != approval.plan_path:
        return False
    return plan_sha256(plan_path) == approval.plan_sha256


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                             encoding="utf-8", errors="replace", check=False)
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def current_commit(repo: str | Path) -> str | None:
    out = _git(Path(repo), "rev-parse", "HEAD")
    return out.strip() if out else None


def inspection_fingerprint(repo: str | Path) -> str:
    """SHA256 over tracked changes (staged and unstaged) plus untracked file contents."""
    repo = Path(repo)
    h = hashlib.sha256()
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if status is None:
        for p in sorted(x for x in repo.rglob("*") if x.is_file()):
            h.update(str(p.relative_to(repo)).encode())
            h.update(p.read_bytes())
        return h.hexdigest()
    h.update(status.encode())
    h.update((_git(repo, "diff", "HEAD") or "").encode())
    for line in status.splitlines():
        if line.startswith("??"):
            rel = line[3:].strip()
            p = repo / rel
            if p.is_file():
                h.update(rel.encode())
                h.update(p.read_bytes())
    return h.hexdigest()