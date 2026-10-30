"""Coordinator: runs one bounded loop under a strategy, with explicit fallback.

Strategies are small state machines over roles and rounds:

  collaborative : plan -> peer review (quorum) -> build -> independent inspect
  adversarial   : plan -> red team + judge      -> build -> adversarial inspect
  hybrid        : plan -> peer review -> red team + judge -> build -> inspect

Every allocation, fallback, verdict, approval and proof result is appended to
the log. Approvals bind to the plan SHA256; inspections bind to a working-tree
fingerprint. Nothing is ever converted into approval by accident.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import runner as _runner
from .allocator import Allocation, AllocationError, Allocator, RoleRequest
from .approval import (Approval, bind_approval, check_approval, current_commit,
                       inspection_fingerprint, plan_sha256)
from .decisions import Decider, Decision, choice, noul, score
from .ledger import Ledger
from .prompts import (BUILD_PROMPT, FIX_FINDINGS_PROMPT, FIX_PROMPT, INSPECT_PROMPT,
                      INSPECT_REDTEAM_PROMPT, JUDGE_PROMPT, PLAN_PROMPT, REDTEAM_PROMPT,
                      REVIEW_PROMPT, REVISE_PROMPT)
from .registry import AgentSpec
from .review import Review, ReviewError, extract_json, parse_review

STRATEGIES = ("collaborative", "adversarial", "hybrid")
STRATEGY_CHOICES = STRATEGIES + ("auto",)         # auto: a decision model picks per task
JUDGES = ("llm", "proof-cmd", "both", "decision")  # decision: a decision model from a third provider
ALLOCATION_CHOICES = ("auto:smart", "auto:fast", "auto:cheap")
ALL_ROLES = ("plan", "review", "build", "inspect", "judge")


class LoopFailure(RuntimeError):
    """The loop cannot continue; the reason is the message."""


@dataclass
class LoopConfig:
    task: str
    strategy: str = "hybrid"
    allocation: str = "auto:smart"
    rounds: int = 5
    max_fix_rounds: int = 2
    max_inspection_rounds: int = 2
    quorum: int = 1
    judge: str = "both"
    fallback_roles: tuple[str, ...] = ALL_ROLES
    max_fallbacks: int = 2
    proof_cmd: list[str] | None = None
    repo: Path = field(default_factory=lambda: Path("."))
    plan_file: str = "PLAN.md"
    log_file: str = "AGORA-LOG.md"
    artifacts_root: Path | None = None
    timeout: float = 600.0
    builder: str | None = None
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGY_CHOICES:
            raise ValueError(f"strategy must be one of {STRATEGY_CHOICES}")
        if self.judge not in JUDGES:
            raise ValueError(f"judge must be one of {JUDGES}")
        if self.quorum < 1 or self.rounds < 1:
            raise ValueError("quorum and rounds must be >= 1")
        self.repo = Path(self.repo)


@dataclass
class FallbackEvent:
    role: str
    fallback_from: str
    fallback_to: str
    reason: str


@dataclass
class LoopOutcome:
    status: str                      # completed | blocked | failed | rounds_exhausted
    reason: str = ""
    plan_sha256: str | None = None
    approval: Approval | None = None
    rounds_used: int = 0
    inspections: int = 0
    open_findings: list[dict] = field(default_factory=list)
    fallbacks: list[FallbackEvent] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    quota: list[dict] = field(default_factory=list)
    allocations: list[Allocation] = field(default_factory=list)
    proof_skipped: bool = False      # no --proof-cmd: nothing mechanical checked the deliverable

    @property
    def ok(self) -> bool:
        return self.status == "completed"


def role_requests_for(strategy: str, quorum: int = 1, builder: str | None = None,
                      judge: str = "both") -> list[RoleRequest]:
    """The dry-run shape of a strategy: which roles, in order, with independence.
    A proof-command-only judge needs no agent, so no judge role is requested."""
    reqs = [RoleRequest("plan")]
    if strategy in ("collaborative", "hybrid"):
        for _ in range(quorum):
            # peers are independent of the planner and of each other: a quorum of one provider is none
            reqs.append(RoleRequest("review", independent_of=("role:plan", "role:review")))
    if strategy in ("adversarial", "hybrid"):
        reqs.append(RoleRequest("review", independent_of=("role:plan",)))
        if judge in ("llm", "both"):      # proof-cmd and decision judges need no agent role
            reqs.append(RoleRequest("judge", independent_of=("role:plan", "last:review")))
    reqs.append(RoleRequest("build"))
    reqs.append(RoleRequest("inspect", independent_of=("role:build",), fresh=True))
    return reqs


class Coordinator:
    def __init__(self, config: LoopConfig, catalog: dict[str, AgentSpec], ledger: Ledger,
                 allocator: Allocator, *, run_turn: Callable[..., _runner.TurnResult] = _runner.run_turn,
                 now: Callable[[], float] = time.time, decider: Decider | None = None) -> None:
        self.config = config
        self.catalog = catalog
        self.ledger = ledger
        self.allocator = allocator
        self.decider = decider
        self._run_turn = run_turn
        self._now = now
        self.outcome = LoopOutcome(status="failed")
        self._approval: Approval | None = None
        self._planner: str | None = None
        self._plan_authors: list[str] = []    # everyone who wrote or revised the plan
        self._builder: str | None = None
        self._reviewers: list[str] = []
        self._pre_build_commit: str | None = None
        self._last_proof_output = ""
        self.dry_run_notes: list[str] = []
        self._tree_touchers: list[str] = []   # agents whose failed write turn still changed the tree

    # -- paths & logging --------------------------------------------------
    @property
    def plan_path(self) -> Path:
        return self.config.repo / self.config.plan_file

    @property
    def log_path(self) -> Path:
        return self.config.repo / self.config.log_file

    def log(self, kind: str, **fields: Any) -> None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._now()))
        event = {"ts": ts, "kind": kind, **fields}
        self.outcome.events.append(event)
        # Agent text (errors, evidence) never gets to forge a log line: no control characters.
        body = " ".join(f"{k}={_clean(v) if isinstance(v, str) else json.dumps(v)}" for k, v in fields.items())
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"- [{ts}] {kind} {body}\n")

    def _start_log(self) -> None:
        c = self.config
        header = (f"\n## Run {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(self._now()))} UTC\n\n"
                  f"task: {c.task}\nstrategy: {c.strategy}  allocation: {c.allocation}  "
                  f"rounds: {c.rounds}  quorum: {c.quorum}  judge: {c.judge}  "
                  f"fallback_roles: {','.join(c.fallback_roles) or 'none'}\n\n")
        new = not self.log_path.exists()
        with self.log_path.open("a", encoding="utf-8") as fh:
            if new:
                fh.write("# AGORA-LOG\n\nAppend-only record of allocations, fallbacks, verdicts, "
                         "approvals and proof.\n")
            fh.write(header)

    # -- turns with explicit fallback ------------------------------------
    def _turn(self, req: RoleRequest, prompt: str, *, read_only: bool,
              independent_of: tuple[str, ...] | None = None) -> tuple[Allocation, _runner.TurnResult]:
        try:
            alloc = self.allocator.choose(req, independent_of)
        except AllocationError as exc:
            raise LoopFailure(str(exc)) from exc
        attempts = 0
        while True:
            self.log("ALLOCATION", **_alloc_fields(alloc))
            self.outcome.allocations.append(alloc)
            spec = self.catalog[alloc.agent_id]
            # Every turn is bracketed by a working-tree fingerprint. A read-only role that
            # changes the tree has failed, whatever its CLI flags promised; a failed write
            # turn may still have changed files (a timeout kills the process, not its edits).
            fp_before = inspection_fingerprint(self.config.repo)
            result = self._run_turn(spec, req.role, prompt, artifacts_root=self.config.artifacts_root,
                                    timeout=self.config.timeout, read_only=read_only,
                                    cwd=self.config.repo, env=self.config.env)
            tree_changed = inspection_fingerprint(self.config.repo) != fp_before
            if read_only and tree_changed and result.ok:
                result.ok = False
                result.error = "read-only turn changed the working tree"
            self.ledger.record(spec, req.role, duration=result.duration, ok=result.ok,
                               tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                               outcome="ok" if result.ok else (result.error or f"exit {result.exit_code}"),
                               observed_model=result.observed_model, rate_limited=result.rate_limited)
            self.log("TURN", role=req.role, agent=alloc.agent_id, ok=result.ok, exit_code=result.exit_code,
                     rate_limited=result.rate_limited, timed_out=result.timed_out, read_only=read_only,
                     tree_changed=tree_changed, tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                     requested_model=spec.model or "cli-default",
                     observed_model=result.observed_model or "unknown", artifacts=result.artifact_dir)
            if result.rate_limited:
                self.log("LEARNED_CAP", agent=alloc.agent_id, caps=self.ledger.learned(alloc.agent_id),
                         note="drop with: agora quota --forget " + alloc.agent_id)
            if result.ok:
                return alloc, result
            reason = ("rate limited" if result.rate_limited else
                      "timeout" if result.timed_out else (result.error or f"exit code {result.exit_code}"))
            # Whoever changed the tree in a failed attempt is an author and may never inspect it.
            if tree_changed and alloc.agent_id not in self._tree_touchers:
                self._tree_touchers.append(alloc.agent_id)
            if req.role not in self.config.fallback_roles:
                raise LoopFailure(f"{req.role} turn by {alloc.agent_id} failed ({reason}); "
                                  f"fallback not allowed for this role")
            if attempts >= self.config.max_fallbacks:
                raise LoopFailure(f"{req.role} turn by {alloc.agent_id} failed ({reason}); "
                                  f"fallback budget of {self.config.max_fallbacks} exhausted")
            try:
                nxt = self.allocator.next_choice(req, alloc.agent_id, independent_of)
            except AllocationError as exc:
                raise LoopFailure(f"{req.role} turn by {alloc.agent_id} failed ({reason}); "
                                  f"no fallback available: {exc}") from exc
            ev = FallbackEvent(role=req.role, fallback_from=alloc.agent_id, fallback_to=nxt.agent_id,
                               reason=reason)
            self.outcome.fallbacks.append(ev)
            self.log("FALLBACK", role=ev.role, fallback_from=ev.fallback_from, fallback_to=ev.fallback_to,
                     reason=ev.reason, tree_changed=tree_changed)
            alloc = nxt
            attempts += 1

    # -- decisions --------------------------------------------------------
    def _decide(self, kind: str, state: Any, questions: dict, *, exclude_providers: tuple[str, ...] = (),
                **extra: Any) -> Decision | None:
        """Ask the decision model and log the answer or the fallback. Never raises."""
        if self.decider is None:
            self.log("DECISION", decision=kind, fallback="decisions off", **extra)
            return None
        d, reason = self.decider.decide(state, questions, exclude_providers=exclude_providers)
        if d is None:
            self.log("DECISION", decision=kind, fallback=reason, **extra)
            return None
        answers = {k: {"value": a.value, "confidence": round(a.confidence, 3)} for k, a in d.answers.items()}
        self.log("DECISION", decision=kind, model=d.model, provider=d.provider, cached=d.cached, answers=answers, **extra)
        return d

    def _confident(self, d: Decision | None, qid: str) -> bool:
        return d is not None and self.decider is not None and d.answers[qid].confidence >= self.decider.min_confidence

    def _choose(self, kind: str, state: Any, question: Any, default: str) -> str:
        d = self._decide(kind, state, {kind: question})
        if self._confident(d, kind):
            value = str(d.answers[kind].value)
            self.log("CHOICE", decision=kind, value=value, source="decision")
            return value
        self.log("CHOICE", decision=kind, value=default, source="default",
                 reason="no confident decision" if d is not None else "no decision")
        return default

    def _choose_strategy(self) -> str:
        q = choice("Which review strategy fits this task best?", {
            "collaborative": "features, refactors, migrations, documents: peers review the plan for correctness",
            "adversarial": "parsers, input validation, security, anything where the question is what input breaks it: a red team attacks the plan",
            "hybrid": "anything that will be shipped: peers first, then a red team"})
        return self._choose("strategy", self.config.task[:1600], q, "hybrid")

    def _choose_allocation(self) -> str:
        q = choice("Which allocation preset fits this task best?", {
            "auto:smart": "hard or subtle work: prefer the most capable agents",
            "auto:fast": "small or urgent work: prefer the fastest agents",
            "auto:cheap": "routine work: prefer free tiers and the most remaining quota"})
        value = self._choose("allocation", self.config.task[:1600], q, "auto:smart")
        self.allocator.set_strategy(value)
        return value

    def _triage(self, findings: list[dict]) -> None:
        """Evidence only: how specific and how serious each material finding looks."""
        for f in findings:
            q = {"specific": noul("Does the finding cite a concrete line, input, fact or test that can be checked?"),
                 "severity": score("How serious is this finding for the deliverable?",
                                   ["cosmetic", "low", "medium", "high"])}
            self._decide("triage", {"finding": f}, q, finding=f.get("id"))

    def _judge_by_decision(self, red_id: str, plan_text: str, remaining: list[dict], round_no: int,
                           proof_ran: bool, proof_ok: bool) -> list[dict]:
        """A decision model from a provider independent of the planner and the red team decides,
        per finding, whether it still stands. Only a confident `addressed` or `out_of_scope`
        clears a finding; a failing proof clears nothing."""
        blocked = tuple({self.catalog[a].provider for a in [*self._plan_authors, red_id] if a in self.catalog})
        proof_state = ("passed" if proof_ok else
                       "failed before the build (expected while the deliverable does not exist)"
                       if proof_ran else "not run")
        kept: list[dict] = []
        for f in remaining:
            q = {"status": choice("Does this red-team finding still stand against the plan?", {
                "stands": "the plan does not handle it; the planner must revise",
                "addressed": "the plan as written already handles it",
                "out_of_scope": "it is not part of this task's deliverable"})}
            state = {"task": self.config.task, "plan": plan_text, "finding": f, "proof": proof_state}
            d = self._decide("judge", state, q, exclude_providers=blocked, finding=f.get("id"))
            if d is None or d.answers["status"].value == "stands" or not self._confident(d, "status"):
                kept.append(f)
        self.log("JUDGE", by="decision", round=round_no, unresolved=[f["id"] for f in kept],
                 cleared=[f["id"] for f in remaining if f not in kept], proof=proof_state)
        return kept

    # -- phases -----------------------------------------------------------
    def _phase_plan(self) -> None:
        req = RoleRequest("plan")
        prompt = PLAN_PROMPT.format(task=self.config.task)
        alloc, result = self._turn(req, prompt, read_only=True)
        self._planner = alloc.agent_id
        self._plan_authors.append(alloc.agent_id)
        self._write_plan(_plan_text(result))
        self.log("PLAN", agent=alloc.agent_id, plan=str(self.plan_path), plan_sha256=self.outcome.plan_sha256)

    def _write_plan(self, text: str) -> None:
        self.plan_path.write_text(text, encoding="utf-8")
        self._approval = None  # any plan change voids approval
        self.outcome.plan_sha256 = plan_sha256(self.plan_path)

    def _phase_review(self, mode: str) -> bool:
        """Return True on approval. Sets outcome.status otherwise."""
        plan_text = self.plan_path.read_text(encoding="utf-8")
        for round_no in range(1, self.config.rounds + 1):
            self.outcome.rounds_used += 1
            reviews: list[tuple[Allocation, Review, _runner.TurnResult]] = []
            chosen: list[str] = []
            n = self.config.quorum if mode == "collaborative" else 1
            for _ in range(n):
                # independent of every plan author (the reviser too) and of the peers already chosen
                req = RoleRequest("review", independent_of=(*self._plan_authors, *chosen), exclude=tuple(chosen))
                tmpl = REVIEW_PROMPT if mode == "collaborative" else REDTEAM_PROMPT
                prompt = tmpl.format(task=self.config.task, plan=plan_text, round=round_no)
                alloc, result = self._turn(req, prompt, read_only=True)
                chosen.append(alloc.agent_id)
                try:
                    review = parse_review(result.text())
                except ReviewError as exc:
                    self.log("REVIEW", mode=mode, round=round_no, agent=alloc.agent_id, verdict="INVALID",
                             error=str(exc), artifacts=result.artifact_dir)
                    self.outcome.status = "blocked"
                    self.outcome.reason = f"{mode} review by {alloc.agent_id} was not a valid review: {exc}"
                    return False
                self.log("REVIEW", mode=mode, round=round_no, agent=alloc.agent_id, verdict=review.verdict,
                         findings=len(review.findings), material=len(review.material_findings),
                         coverage=len(review.coverage), artifacts=result.artifact_dir)
                reviews.append((alloc, review, result))
            self._reviewers = chosen
            if any(r.verdict == "BLOCKED" for _, r, _ in reviews):
                who = ", ".join(a.agent_id for a, r, _ in reviews if r.verdict == "BLOCKED")
                self.outcome.status = "blocked"
                self.outcome.reason = f"{mode} review BLOCKED by {who}"
                self.outcome.open_findings = [f.__dict__ for _, r, _ in reviews for f in r.findings]
                return False
            unresolved: list[dict] = [f.__dict__ for _, r, _ in reviews for f in r.material_findings]
            if unresolved and self.decider is not None:
                self._triage(unresolved)
            if mode == "adversarial" and unresolved:
                unresolved = self._judge(reviews[0][0].agent_id, plan_text, unresolved, round_no)
            if mode == "collaborative":
                approved = all(r.verdict == "APPROVED" for _, r, _ in reviews)
            else:
                approved = not unresolved
            if approved:
                alloc, review, result = reviews[-1]
                self._approval = bind_approval(self.plan_path, alloc.agent_id, round_no, result.artifact_dir)
                self.outcome.approval = self._approval
                self.outcome.open_findings = [f.__dict__ for _, r, _ in reviews for f in r.findings
                                              if not f.material]
                self.log("APPROVAL", mode=mode, round=round_no, agent=alloc.agent_id,
                         plan_sha256=self._approval.plan_sha256, low_findings=len(self.outcome.open_findings))
                return True
            all_findings = [f.__dict__ for _, r, _ in reviews for f in r.findings]
            dispositions = json.dumps(unresolved or all_findings, indent=2)
            req = RoleRequest("plan", exclude=tuple(chosen))
            prompt = REVISE_PROMPT.format(task=self.config.task, plan=plan_text, findings=dispositions)
            alloc, result = self._turn(req, prompt, read_only=True)
            if alloc.agent_id not in self._plan_authors:
                self._plan_authors.append(alloc.agent_id)
            self._write_plan(_plan_text(result))
            plan_text = self.plan_path.read_text(encoding="utf-8")
            self.log("REVISION", round=round_no, agent=alloc.agent_id, plan_sha256=self.outcome.plan_sha256,
                     addressed=len(unresolved or all_findings))
        self.outcome.status = "rounds_exhausted"
        self.outcome.reason = f"{mode} review did not converge in {self.config.rounds} rounds"
        return False

    def _judge(self, red_id: str, plan_text: str, findings: list[dict], round_no: int) -> list[dict]:
        """Decide which red-team findings stand. Proof commands win on conflict."""
        remaining = list(findings)
        proof_ran = proof_ok = False
        if self.config.judge in ("proof-cmd", "both", "decision") and self.config.proof_cmd:
            proof_ran, proof_ok = True, self._run_proof()
            cleared = [f["id"] for f in remaining] if proof_ok and self.config.judge == "proof-cmd" else []
            self.log("JUDGE", by="proof-cmd", round=round_no, passed=proof_ok, cleared=cleared)
            if proof_ok and self.config.judge == "proof-cmd":
                return []
        if self.config.judge == "decision":
            return self._judge_by_decision(red_id, plan_text, remaining, round_no, proof_ran, proof_ok)
        if self.config.judge in ("llm", "both"):
            req = RoleRequest("judge", independent_of=(*self._plan_authors, red_id))
            eligible, rejected = self.allocator.eligible(req)
            if not eligible and self.config.judge == "both":
                # No independent LLM judge (typically only two providers). Degrade
                # conservatively and visibly: nothing is cleared, the planner must
                # answer every red-team finding. `--judge llm` still fails loudly.
                why = "; ".join(f"{k}: {v}" for k, v in rejected.items()) or "catalog is empty"
                self.log("JUDGE", by="llm", round=round_no, skipped=True,
                         reason=f"no independent judge available ({why})",
                         unresolved=[f["id"] for f in remaining])
                return remaining
            # Before the build the proof is evidence only: it is expected to fail while the
            # deliverable does not exist yet. After the build a failing proof stops the loop
            # on its own (see _phase_build and _phase_inspect), whatever any judge said.
            proof_state = ("passed" if proof_ok else
                           "failed before the build (expected while the deliverable does not exist)"
                           if proof_ran else "not run")
            prompt = JUDGE_PROMPT.format(plan=plan_text, findings=json.dumps(remaining, indent=2),
                                         proof=proof_state)
            alloc, result = self._turn(req, prompt, read_only=True)
            try:
                data = extract_json(result.text())
                ids = data.get("unresolved") if isinstance(data, dict) else None
                if not isinstance(ids, list):
                    raise ReviewError("judge must return {\"unresolved\": [ids]}")
            except ReviewError as exc:
                self.log("JUDGE", by="llm", round=round_no, agent=alloc.agent_id, error=str(exc))
                return remaining  # an invalid judge never clears findings
            wanted = {str(i) for i in ids}
            llm_remaining = [f for f in remaining if f["id"] in wanted]
            self.log("JUDGE", by="llm", round=round_no, agent=alloc.agent_id,
                     unresolved=[f["id"] for f in llm_remaining], proof=proof_state)
            return llm_remaining
        return remaining

    def _run_proof(self) -> bool:
        cmd = self.config.proof_cmd
        if not cmd:
            self.outcome.proof_skipped = True
            self.log("PROOF", skipped=True, reason="no --proof-cmd; nothing mechanical checked the deliverable")
            return True
        try:
            proc = subprocess.Popen(cmd, cwd=str(self.config.repo), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace")
        except OSError as exc:
            self.log("PROOF", cmd=cmd, passed=False, error=str(exc))
            self._last_proof_output = str(exc)
            return False
        try:
            stdout, stderr = proc.communicate(timeout=self.config.timeout)
        except subprocess.TimeoutExpired:
            _runner._kill_tree(proc)
            self.log("PROOF", cmd=cmd, passed=False, error=f"timeout after {self.config.timeout}s; process tree killed")
            self._last_proof_output = "proof command timed out"
            return False
        except BaseException:
            _runner._kill_tree(proc)
            raise
        self.log("PROOF", cmd=cmd, passed=proc.returncode == 0, exit_code=proc.returncode,
                 stdout_tail=stdout[-400:], stderr_tail=stderr[-400:])
        self._last_proof_output = (stdout + "\n" + stderr)[-4000:]
        return proc.returncode == 0

    def _assert_plan_intact(self, agent_id: str, stage: str) -> None:
        """The approved plan is the contract: a builder may not rewrite it after approval."""
        if check_approval(self.plan_path, self._approval):
            return
        self.log("PLAN_TAMPERED", stage=stage, agent=agent_id, plan_sha256=plan_sha256(self.plan_path),
                 approved_sha256=self._approval.plan_sha256 if self._approval else None)
        raise LoopFailure(f"{self.config.plan_file} changed after approval during the {stage} turn "
                          f"by {agent_id}; refusing to continue")

    def _builder_request(self) -> RoleRequest:
        if self.config.builder:
            if self.config.builder not in self.catalog:
                raise LoopFailure(f"builder {self.config.builder!r} not in catalog")
            others = tuple(a for a in self.catalog if a != self.config.builder)
            return RoleRequest("build", exclude=others, min_capability=0.0)
        return RoleRequest("build")

    def _phase_build(self) -> None:
        if not check_approval(self.plan_path, self._approval):
            raise LoopFailure("plan is not approved (or changed after approval); refusing to build")
        self._pre_build_commit = current_commit(self.config.repo)
        alloc, _ = self._turn(self._builder_request(), self._build_prompt(), read_only=False)
        self._builder = alloc.agent_id
        self._assert_plan_intact(alloc.agent_id, "build")
        self.log("BUILD", agent=alloc.agent_id, pre_build_commit=self._pre_build_commit or "none",
                 plan_sha256=self._approval.plan_sha256 if self._approval else None)
        for fix in range(self.config.max_fix_rounds + 1):
            if self._run_proof():
                return
            if fix == self.config.max_fix_rounds:
                raise LoopFailure(f"proof command still failing after {self.config.max_fix_rounds} fix rounds")
            req = RoleRequest("build", exclude=tuple(a for a in self.catalog if a != self._builder),
                              min_capability=0.0)
            prompt = FIX_PROMPT.format(plan=self.plan_path.read_text(encoding="utf-8"),
                                       output=self._last_proof_output)
            alloc, _ = self._turn(req, prompt, read_only=False)
            self._assert_plan_intact(alloc.agent_id, "fix")
            self.log("FIX", round=fix + 1, agent=alloc.agent_id)

    def _build_prompt(self) -> str:
        return BUILD_PROMPT.format(task=self.config.task, plan=self.plan_path.read_text(encoding="utf-8"),
                                   proof=" ".join(self.config.proof_cmd or []) or "none")

    def _phase_inspect(self, mode: str) -> None:
        for round_no in range(1, self.config.max_inspection_rounds + 1):
            self._assert_plan_intact(self._builder or "?", "build")
            self.outcome.inspections += 1
            fp = inspection_fingerprint(self.config.repo)
            authors = tuple(dict.fromkeys([self._builder or "", *self._tree_touchers]))
            req = RoleRequest("inspect", independent_of=authors, fresh=True)
            tmpl = INSPECT_PROMPT if mode == "collaborative" else INSPECT_REDTEAM_PROMPT
            prompt = tmpl.format(task=self.config.task, plan=self.plan_path.read_text(encoding="utf-8"),
                                 pre_build_commit=self._pre_build_commit or "none", builder=self._builder)
            alloc, result = self._turn(req, prompt, read_only=True)
            try:
                review = parse_review(result.text())
            except ReviewError as exc:
                self.log("INSPECT", round=round_no, agent=alloc.agent_id, verdict="INVALID", error=str(exc),
                         fingerprint=fp)
                raise LoopFailure(f"inspection by {alloc.agent_id} was not a valid review: {exc}") from exc
            self.log("INSPECT", round=round_no, agent=alloc.agent_id, verdict=review.verdict,
                     findings=len(review.findings), material=len(review.material_findings),
                     fingerprint=fp, pre_build_commit=self._pre_build_commit or "none", builder=self._builder,
                     also_touched=self._tree_touchers)
            if review.verdict == "BLOCKED":
                self.outcome.status = "blocked"
                self.outcome.reason = f"inspection BLOCKED by {alloc.agent_id}"
                self.outcome.open_findings = [f.__dict__ for f in review.findings]
                return
            if review.verdict == "APPROVED":
                self.outcome.open_findings += [f.__dict__ for f in review.findings]
                return
            self.outcome.open_findings = [f.__dict__ for f in review.findings]
            if round_no == self.config.max_inspection_rounds:
                self.log("INSPECT", round=round_no, note="inspection budget exhausted; findings left open")
                return
            fix_req = RoleRequest("build", exclude=tuple(a for a in self.catalog if a != self._builder),
                                  min_capability=0.0)
            prompt = FIX_FINDINGS_PROMPT.format(findings=json.dumps(self.outcome.open_findings, indent=2))
            alloc2, _ = self._turn(fix_req, prompt, read_only=False)
            self._assert_plan_intact(alloc2.agent_id, "fix")
            self.log("FIX", round=round_no, agent=alloc2.agent_id, after="inspection")
            if not self._run_proof():
                raise LoopFailure("proof command failed after inspection fixes")

    # -- entry points -----------------------------------------------------
    def dry_run(self) -> list[Allocation]:
        """Allocate every role without spending quota. With `--judge both` and no
        third provider the judge role is dropped and a note explains why."""
        self.dry_run_notes = []
        try:
            allocs = self.allocator.plan(self._dry_run_requests(self.config.judge))
        except AllocationError as exc:
            if self.config.judge != "both" or exc.role != "judge":
                raise
            self.dry_run_notes.append(
                f"judge: {exc}. The loop will log JUDGE skipped=true and send every red-team "
                f"finding back to the planner; add an agent from a third provider for an LLM judge.")
            allocs = self.allocator.plan(self._dry_run_requests("proof-cmd"))
        for a in allocs:
            spec = self.catalog.get(a.agent_id)
            if spec and spec.kind == "cli" and a.role != "build" and not spec.read_only_argv:
                self.dry_run_notes.append(
                    f"{a.agent_id}: no read_only_argv in the catalog; its {a.role} turn runs with the "
                    f"CLI's default permissions and fails if it changes the working tree.")
        if not self.config.proof_cmd:
            self.dry_run_notes.append("no --proof-cmd: nothing mechanical will check the deliverable.")
        uses = [x for x, on in (("strategy", self.config.strategy == "auto"),
                                ("allocation", self.config.allocation == "auto"),
                                ("judge", self.config.judge == "decision")) if on]
        if self.decider is not None:
            ready = self.decider.ready()
            if ready:
                self.dry_run_notes.append("decisions: " + ", ".join(f"{s.id} ({s.model})" for s in ready)
                                          + " ready; used for " + (", ".join(uses + ["finding triage"])))
            else:
                why = "; ".join(f"{s.id}: {w}" for s, w in self.decider.availability())
                self.dry_run_notes.append(f"decisions: no decision model ready ({why or 'none declared'}); "
                                          f"defaults apply and are logged" + (f" for {', '.join(uses)}" if uses else ""))
        elif uses:
            self.dry_run_notes.append(f"decisions are off but {', '.join(uses)} asked for one: defaults apply "
                                      f"(hybrid, auto:smart, every red-team finding back to the planner)")
        return allocs

    def _dry_run_requests(self, judge: str) -> list[RoleRequest]:
        # `auto` is decided at run time from the task; plan the fullest shape for the dry run
        strategy = "hybrid" if self.config.strategy == "auto" else self.config.strategy
        reqs = role_requests_for(strategy, self.config.quorum, self.config.builder, judge)
        if self.config.builder:
            others = tuple(a for a in self.catalog if a != self.config.builder)
            reqs = [RoleRequest("build", exclude=others, min_capability=0.0) if r.role == "build" else r
                    for r in reqs]
        return reqs

    def run(self) -> LoopOutcome:
        if not self.config.repo.is_dir():
            raise ValueError(f"repo does not exist: {self.config.repo}")
        self._start_log()
        try:
            if self.config.strategy == "auto":
                self.config.strategy = self._choose_strategy()
            if self.config.allocation == "auto":
                self.config.allocation = self._choose_allocation()
            self._phase_plan()
            s = self.config.strategy
            if s == "collaborative":
                approved = self._phase_review("collaborative")
            elif s == "adversarial":
                approved = self._phase_review("adversarial")
            else:
                approved = self._phase_review("collaborative")
                peer_sha = self.outcome.plan_sha256
                approved = approved and self._phase_review("adversarial")
                if approved and self.outcome.plan_sha256 != peer_sha:
                    # the red team forced a revision: the peers approved an older plan
                    self.log("REREVIEW", reason="plan revised after peer approval; peers review it again",
                             plan_sha256=self.outcome.plan_sha256)
                    approved = self._phase_review("collaborative")
            if not approved:
                return self._finish()
            self._phase_build()
            self._phase_inspect("adversarial" if s == "adversarial" else "collaborative")
            if self.outcome.status != "blocked":
                self.outcome.status = "completed"
        except LoopFailure as exc:
            self.outcome.status = "failed"
            self.outcome.reason = str(exc)
            self.log("FAILURE", reason=str(exc))
        return self._finish()

    def _finish(self) -> LoopOutcome:
        self.outcome.quota = self.ledger.usage_report(self.catalog)
        for row in self.outcome.quota:
            if row["requests"]:
                self.log("QUOTA", agent=row["agent"], requests=row["requests"], failures=row["failures"],
                         headroom=row["headroom"], cooldown_s=row["cooldown_s"])
        self.log("SUMMARY", status=self.outcome.status, reason=self.outcome.reason or "-",
                 rounds=self.outcome.rounds_used, inspections=self.outcome.inspections,
                 fallbacks=len(self.outcome.fallbacks), open_findings=len(self.outcome.open_findings),
                 plan_sha256=self.outcome.plan_sha256)
        return self.outcome


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def _clean(text: str) -> str:
    return _CONTROL.sub(" ", text)


def _alloc_fields(a: Allocation) -> dict[str, Any]:
    return {"role": a.role, "agent": a.agent_id, "provider": a.provider, "model": a.model or "cli-default",
            "headroom": round(a.headroom, 3), "score": round(a.score, 3), "reason": a.reason}


def _plan_text(result: _runner.TurnResult) -> str:
    if isinstance(result.parsed, dict) and isinstance(result.parsed.get("plan"), str):
        return result.parsed["plan"]
    text = result.text()
    try:
        data = extract_json(text)
        if isinstance(data, dict) and isinstance(data.get("plan"), str):
            return data["plan"]
    except ReviewError:
        pass
    return text