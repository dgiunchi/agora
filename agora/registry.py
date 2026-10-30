"""Agent registry: a declarative catalog of agentic systems.

Each entry describes how to invoke an agent headlessly (CLI) or over an
OpenAI-compatible HTTP endpoint, what quota shape it has, how good it is at
each role, and whether it counts as independent from the agents it checks.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROLES = ("plan", "review", "build", "inspect", "judge", "research")
KINDS = ("cli", "http", "decision")
DECISION_APIS = ("systemone", "cloudflare", "openrouter")   # request shapes, see decisions.py
TIERS = ("free", "subscription", "paid")
RESETS = ("utc-midnight", "rolling")
TRUST = ("independent", "any")
OUTPUT_FORMATS = ("json", "stream-json", "text")


class CatalogError(ValueError):
    """Raised when a catalog fails validation."""


@dataclass(frozen=True)
class QuotaWindow:
    name: str
    seconds: int
    requests: int | None = None
    tokens: int | None = None


@dataclass(frozen=True)
class AgentSpec:
    id: str
    provider: str
    kind: str = "cli"
    tier: str = "free"
    argv: tuple[str, ...] = ()
    read_only_argv: tuple[str, ...] = ()   # appended for reviewers, inspectors, judges, planners
    write_argv: tuple[str, ...] = ()       # appended for builders (e.g. codex: -s workspace-write)
    resume_argv: tuple[str, ...] = ()
    node_entry: str | None = None
    endpoint: str | None = None
    api_key_env: str | None = None
    decision_api: str = "systemone"        # kind = "decision" only
    output_format: str = "json"
    model: str | None = None
    reset: str = "utc-midnight"
    caps_known: bool = False
    windows: tuple[QuotaWindow, ...] = ()
    capabilities: dict[str, float] = field(default_factory=dict, compare=False, hash=False)
    context_window: int = 0
    tools: bool = True
    trust: str = "independent"

    def capability(self, role: str) -> float:
        return float(self.capabilities.get(role, 0.0))


def validate_catalog(data: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems; empty means valid."""
    errors: list[str] = []
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        return ["catalog must contain a non-empty [[agents]] array"]
    seen: set[str] = set()
    for i, a in enumerate(agents):
        where = f"agents[{i}]"
        if not isinstance(a, dict):
            errors.append(f"{where}: must be a table")
            continue
        aid = a.get("id")
        if not isinstance(aid, str) or not aid:
            errors.append(f"{where}: missing id")
        elif aid in seen:
            errors.append(f"{where}: duplicate id {aid!r}")
        else:
            seen.add(aid)
            where = f"agents[{aid}]"
        if not isinstance(a.get("provider"), str) or not a.get("provider"):
            errors.append(f"{where}: missing provider")
        for key, allowed in (("kind", KINDS), ("tier", TIERS), ("reset", RESETS),
                             ("trust", TRUST), ("output_format", OUTPUT_FORMATS)):
            if key in a and a[key] not in allowed:
                errors.append(f"{where}: {key} must be one of {allowed}")
        if a.get("kind", "cli") == "cli":
            argv = a.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                errors.append(f"{where}: cli agents need a non-empty argv list of strings")
            elif not any("{prompt}" in x for x in argv):
                errors.append(f"{where}: argv must contain a {{prompt}} placeholder")
        else:
            kind = a.get("kind")
            if not isinstance(a.get("endpoint"), str) or not a.get("endpoint"):
                errors.append(f"{where}: {kind} agents need an endpoint URL")
            if not isinstance(a.get("model"), str) or not a.get("model"):
                errors.append(f"{where}: {kind} agents need a model")
            if kind == "decision" and a.get("decision_api", "systemone") not in DECISION_APIS:
                errors.append(f"{where}: decision_api must be one of {DECISION_APIS}")
        for key in ("read_only_argv", "write_argv", "resume_argv"):
            if key in a and (not isinstance(a[key], list) or not all(isinstance(x, str) for x in a[key])):
                errors.append(f"{where}: {key} must be a list of strings")
        caps = a.get("capabilities", {})
        if not isinstance(caps, dict):
            errors.append(f"{where}: capabilities must be a table")
        else:
            for role, score in caps.items():
                if role not in ROLES:
                    errors.append(f"{where}: unknown role {role!r} in capabilities")
                elif not isinstance(score, (int, float)) or not 0.0 <= float(score) <= 1.0:
                    errors.append(f"{where}: capability {role} must be a number in [0, 1]")
        windows = a.get("windows", [])
        if not isinstance(windows, list):
            errors.append(f"{where}: windows must be an array of tables")
        else:
            names: set[str] = set()
            for w in windows:
                if not isinstance(w, dict):
                    errors.append(f"{where}: each window must be a table")
                    continue
                name = w.get("name")
                if not isinstance(name, str) or not name:
                    errors.append(f"{where}: window missing name")
                elif name in names:
                    errors.append(f"{where}: duplicate window {name!r}")
                else:
                    names.add(name)
                if not isinstance(w.get("seconds"), int) or w.get("seconds", 0) <= 0:
                    errors.append(f"{where}: window {name!r} needs positive integer seconds")
                for cap in ("requests", "tokens"):
                    if cap in w and w[cap] is not None and (not isinstance(w[cap], int) or w[cap] < 0):
                        errors.append(f"{where}: window {name!r} {cap} must be a non-negative integer")
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        errors.append("profiles must be a table of name = [agent ids]")
    else:
        for name, ids in profiles.items():
            if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
                errors.append(f"profiles.{name}: must be a non-empty list of agent ids")
                continue
            for x in ids:
                if x not in seen:
                    errors.append(f"profiles.{name}: unknown agent {x!r}")
    return errors


def _spec_from_table(a: dict[str, Any]) -> AgentSpec:
    windows = tuple(
        QuotaWindow(name=w["name"], seconds=int(w["seconds"]),
                    requests=w.get("requests"), tokens=w.get("tokens"))
        for w in a.get("windows", [])
    )
    return AgentSpec(
        id=a["id"],
        provider=a["provider"],
        kind=a.get("kind", "cli"),
        tier=a.get("tier", "free"),
        argv=tuple(a.get("argv", [])),
        read_only_argv=tuple(a.get("read_only_argv", [])),
        write_argv=tuple(a.get("write_argv", [])),
        resume_argv=tuple(a.get("resume_argv", [])),
        node_entry=a.get("node_entry"),
        endpoint=a.get("endpoint"),
        api_key_env=a.get("api_key_env"),
        decision_api=a.get("decision_api", "systemone"),
        output_format=a.get("output_format", "json"),
        model=a.get("model"),
        reset=a.get("reset", "utc-midnight"),
        caps_known=bool(a.get("caps_known", False)),
        windows=windows,
        capabilities={k: float(v) for k, v in a.get("capabilities", {}).items()},
        context_window=int(a.get("context_window", 0)),
        tools=bool(a.get("tools", True)),
        trust=a.get("trust", "independent"),
    )


def catalog_from_dict(data: dict[str, Any]) -> dict[str, AgentSpec]:
    errors = validate_catalog(data)
    if errors:
        raise CatalogError("; ".join(errors))
    return {a["id"]: _spec_from_table(a) for a in data["agents"]}


def _load_toml(path: str | Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"{path}: {exc}") from exc


def load_catalog(path: str | Path) -> dict[str, AgentSpec]:
    """Load and validate a TOML catalog. BOM-tolerant."""
    return catalog_from_dict(_load_toml(path))


def load_profiles(path: str | Path) -> dict[str, list[str]]:
    """The optional `[profiles]` table: name = [agent ids], in the order to try them."""
    data = _load_toml(path)
    errors = validate_catalog(data)
    if errors:
        raise CatalogError("; ".join(errors))
    return {k: list(v) for k, v in data.get("profiles", {}).items()}