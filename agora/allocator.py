"""Allocator: FreeLLMAPI routing criteria applied to roles in a loop.

Hard filters first (capability, cooldown, headroom reserve, independence),
then a Thompson-sampling bandit over reliability, speed, capability and
headroom. Fallback is explicit: `next_choice` returns the next eligible agent
and the coordinator logs the event. Nothing here spends quota.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .ledger import Ledger
from .registry import AgentSpec

STRATEGY_WEIGHTS: dict[str, dict[str, float]] = {
    "auto:smart": {"reliability": 0.2, "speed": 0.1, "capability": 0.6, "headroom": 0.1},
    "auto:fast": {"reliability": 0.2, "speed": 0.5, "capability": 0.2, "headroom": 0.1},
    "auto:cheap": {"reliability": 0.15, "speed": 0.05, "capability": 0.2, "headroom": 0.6},
}
TIER_BONUS = {"free": 1.0, "subscription": 0.5, "paid": 0.0}


class AllocationError(RuntimeError):
    """No eligible agent for a role."""


@dataclass(frozen=True)
class RoleRequest:
    role: str
    independent_of: tuple[str, ...] = ()   # agent ids, or "role:<name>" resolved inside plan()
    exclude: tuple[str, ...] = ()          # agent ids never to pick
    min_capability: float = 0.5
    fresh: bool = False                    # informational: coordinator must start a new session


@dataclass
class Allocation:
    role: str
    agent_id: str
    provider: str
    model: str | None
    reason: str
    headroom: float
    score: float
    parts: dict[str, float] = field(default_factory=dict)

    def as_line(self) -> str:
        return (f"role={self.role} agent={self.agent_id} provider={self.provider} "
                f"model={self.model or 'cli-default'} headroom={self.headroom:.2f} "
                f"score={self.score:.3f} reason={self.reason}")


class Allocator:
    def __init__(self, catalog: dict[str, AgentSpec], ledger: Ledger, *, strategy: str = "auto:smart",
                 seed: int | None = None, reserve: float = 0.05,
                 weights: dict[str, float] | None = None,
                 profiles: dict[str, list[str]] | None = None) -> None:
        self.catalog = catalog
        self.ledger = ledger
        self.strategy = strategy
        self.reserve = reserve
        self.rng = random.Random(seed)
        self.profiles = profiles or {}
        if weights is not None:
            self.weights = weights
        elif strategy in STRATEGY_WEIGHTS:
            self.weights = STRATEGY_WEIGHTS[strategy]
        elif strategy.startswith("profile:") or strategy in self.profiles:
            self.weights = STRATEGY_WEIGHTS["auto:smart"]
        else:
            raise AllocationError(f"unknown allocation strategy {strategy!r}")

    # -- filters ----------------------------------------------------------
    def rejection(self, spec: AgentSpec, req: RoleRequest, independent_of: tuple[str, ...]) -> str | None:
        if spec.id in req.exclude:
            return "excluded"
        if spec.id in independent_of:
            return "same agent as the one it must check"
        if spec.trust == "independent":
            blocked = {self.catalog[a].provider for a in independent_of if a in self.catalog}
            if spec.provider in blocked:
                return f"provider {spec.provider} already used by a checked agent"
        if spec.capability(req.role) < req.min_capability:
            return f"capability {spec.capability(req.role):.2f} < {req.min_capability:.2f} for {req.role}"
        if self.ledger.in_cooldown(spec.id):
            return f"in cooldown for {self.ledger.cooldown_remaining(spec.id):.0f}s"
        hr = self.ledger.headroom(spec)
        if hr <= self.reserve:
            return f"headroom {hr:.2f} at or below reserve {self.reserve:.2f}"
        return None

    def eligible(self, req: RoleRequest, independent_of: tuple[str, ...] | None = None
                 ) -> tuple[list[AgentSpec], dict[str, str]]:
        ind = tuple(independent_of) if independent_of is not None else req.independent_of
        ok: list[AgentSpec] = []
        rejected: dict[str, str] = {}
        pool = self._profile_pool()
        for spec in pool:
            why = self.rejection(spec, req, ind)
            if why is None:
                ok.append(spec)
            else:
                rejected[spec.id] = why
        return ok, rejected

    def _profile_pool(self) -> list[AgentSpec]:
        name = self.strategy[len("profile:"):] if self.strategy.startswith("profile:") else self.strategy
        if name in self.profiles:
            return [self.catalog[a] for a in self.profiles[name] if a in self.catalog]
        return list(self.catalog.values())

    # -- scoring ----------------------------------------------------------
    def score(self, spec: AgentSpec, req: RoleRequest) -> tuple[float, dict[str, float]]:
        st = self.ledger.stats(spec.id)
        reliability = self.rng.betavariate(st.successes + 1, st.failures + 1)
        speed = 0.5 if st.avg_latency is None else 1.0 / (1.0 + st.avg_latency / 30.0)
        capability = spec.capability(req.role)
        headroom = self.ledger.headroom(spec)
        if self.strategy == "auto:cheap":
            headroom = 0.5 * headroom + 0.5 * TIER_BONUS.get(spec.tier, 0.0)
        parts = {"reliability": reliability, "speed": speed, "capability": capability, "headroom": headroom}
        total = sum(self.weights[k] * v for k, v in parts.items())
        return total, parts

    # -- choosing ---------------------------------------------------------
    def choose(self, req: RoleRequest, independent_of: tuple[str, ...] | None = None) -> Allocation:
        ok, rejected = self.eligible(req, independent_of)
        if not ok:
            detail = "; ".join(f"{k}: {v}" for k, v in rejected.items()) or "catalog is empty"
            raise AllocationError(f"no eligible agent for role {req.role!r}: {detail}")
        if self._profile_pool() is not list(self.catalog.values()) and self.strategy not in STRATEGY_WEIGHTS:
            spec = ok[0]  # named profile: first eligible in declared order
            total, parts = self.score(spec, req)
            reason = f"first eligible in profile {self.strategy}"
        else:
            scored = [(self.score(s, req), s) for s in ok]
            scored.sort(key=lambda x: x[0][0], reverse=True)
            (total, parts), spec = scored[0]
            top = ", ".join(f"{k}={v:.2f}" for k, v in parts.items())
            reason = f"{self.strategy} bandit: {top}"
        return Allocation(role=req.role, agent_id=spec.id, provider=spec.provider, model=spec.model,
                          reason=reason, headroom=self.ledger.headroom(spec), score=total, parts=parts)

    def next_choice(self, req: RoleRequest, failed_agent_id: str,
                    independent_of: tuple[str, ...] | None = None) -> Allocation:
        """Explicit fallback: the next eligible agent after a failure."""
        req2 = RoleRequest(role=req.role, independent_of=req.independent_of,
                           exclude=tuple(req.exclude) + (failed_agent_id,),
                           min_capability=req.min_capability, fresh=req.fresh)
        return self.choose(req2, independent_of)

    def plan(self, reqs: list[RoleRequest]) -> list[Allocation]:
        """Dry-run allocation for a whole loop. `role:<name>` references resolve
        to the agent allocated for that role earlier in the list."""
        out: list[Allocation] = []
        by_role: dict[str, str] = {}
        for req in reqs:
            ind = tuple(by_role.get(x[5:], "") if x.startswith("role:") else x for x in req.independent_of)
            ind = tuple(x for x in ind if x)
            alloc = self.choose(req, ind)
            out.append(alloc)
            by_role.setdefault(req.role, alloc.agent_id)
        return out