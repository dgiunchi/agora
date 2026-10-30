"""Decision models: typed questions about a piece of state, answered with probabilities.

A decision model does not generate text. It answers `choice` (one of N options),
`noul` (a yes/no probability) or `score` (a level on an ordered rubric) questions
about `state`, each with a confidence. This is the "System One" request shape that
FreeLLMAPI's `/v1/decisions`, BeatAPI's `/v1/systemone` (the JEV models) and APINex's
`/v1/decisions` speak; Cloudflare Workers AI (Clef) and OpenRouter (Mercury Decide)
wrap the same questions differently.

Agora uses decisions only where it would otherwise choose by rule or heuristic:
which strategy fits a task, which allocation preset, whether a red-team finding
still stands, how specific a finding is. A decision is never an approval. Every
decision is logged with the model that answered, the answer, the confidence and
the fallback reason when the default was used instead.

Decision models are catalog entries with `kind = "decision"`. They are ranked in
declared order (put the free ones first), skipped when their key is missing, in
cooldown or out of headroom, and tried in turn. Answers are cached for 30 minutes
by the SHA256 of the question set and the state, so a retried turn never spends a
one-request-per-minute free quota twice.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .ledger import Ledger
from .registry import AgentSpec

QUESTION_TYPES = ("choice", "noul", "score")
DECISION_APIS = ("systemone", "cloudflare", "openrouter")
CACHE_TTL = 1800.0
STATE_LIMIT = 8000   # characters of state sent to a decision model


class DecisionError(RuntimeError):
    """The provider did not return a valid typed answer."""


class DecisionRateLimited(DecisionError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class Question:
    type: str
    instructions: str
    criteria: Any = None   # choice: {option id: description}; score: [level, ...] lowest first

    def to_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.criteria is not None:
            d["criteria"] = self.criteria
        return d


def choice(instructions: str, options: dict[str, str]) -> Question:
    return Question("choice", instructions, dict(options))


def noul(instructions: str) -> Question:
    return Question("noul", instructions)


def score(instructions: str, levels: list[str]) -> Question:
    return Question("score", instructions, list(levels))


@dataclass
class Answer:
    type: str
    value: Any             # choice: option id; noul: probability of yes; score: level index
    confidence: float      # in [0, 1]; for noul this is max(p, 1 - p)
    probabilities: Any = None


@dataclass
class Decision:
    agent_id: str
    provider: str
    model: str               # the model that answered
    answers: dict[str, Answer]
    usage: dict[str, Any]
    cached: bool = False
    latency: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "provider": self.provider, "model": self.model,
                "answers": {k: asdict(v) for k, v in self.answers.items()}, "usage": self.usage,
                "latency": self.latency}

    @classmethod
    def from_json(cls, d: dict[str, Any], *, cached: bool) -> "Decision":
        return cls(agent_id=d["agent_id"], provider=d["provider"], model=d["model"],
                   answers={k: Answer(**v) for k, v in d["answers"].items()}, usage=d.get("usage") or {},
                   cached=cached, latency=float(d.get("latency", 0.0)))


def _number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_answers(raw: Any, questions: dict[str, Question], platform: str) -> dict[str, Answer]:
    """Every question answered, with its own type, inside its own criteria. Strict on
    purpose: a silently malformed answer would steer the loop."""
    if not isinstance(raw, dict):
        raise DecisionError(f"{platform} returned no answers")
    out: dict[str, Answer] = {}
    for qid, q in questions.items():
        a = raw.get(qid)
        if not isinstance(a, dict) or a.get("type") != q.type:
            raise DecisionError(f"{platform} omitted or mistyped the answer to {qid!r}")
        probs = a.get("probabilities")
        conf = a.get("confidence")
        if q.type == "choice":
            c = a.get("choice")
            if not isinstance(c, str) or c not in (q.criteria or {}):
                raise DecisionError(f"{platform} chose {c!r}, not one of {sorted(q.criteria or {})} for {qid!r}")
            confidence = float(conf) if _number(conf) else (
                float(probs[c]) if isinstance(probs, dict) and _number(probs.get(c)) else 0.0)
            out[qid] = Answer("choice", c, confidence, probs)
        elif q.type == "noul":
            p = a.get("noul")
            if not _number(p) or not 0.0 <= float(p) <= 1.0:
                raise DecisionError(f"{platform} returned an invalid probability for {qid!r}")
            out[qid] = Answer("noul", float(p), max(float(p), 1.0 - float(p)), probs)
        else:
            s = a.get("score")
            levels = q.criteria or []
            if not _number(s) or not 0 <= float(s) <= max(len(levels) - 1, 0):
                raise DecisionError(f"{platform} returned an invalid score for {qid!r}")
            idx = int(round(float(s)))
            confidence = float(conf) if _number(conf) else (
                float(probs[idx]) if isinstance(probs, list) and idx < len(probs) and _number(probs[idx]) else 0.0)
            out[qid] = Answer("score", idx, confidence, probs)
        if not 0.0 <= out[qid].confidence <= 1.0:
            raise DecisionError(f"{platform} returned a confidence outside [0, 1] for {qid!r}")
    return out


def _retry_after(headers: Any) -> float | None:
    v = headers.get("Retry-After") if headers else None
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float, platform: str) -> Any:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-configured URL
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if exc.code == 429:
            raise DecisionRateLimited(f"{platform} 429: {text[:200]}", _retry_after(exc.headers)) from exc
        raise DecisionError(f"{platform} {exc.code}: {text[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DecisionError(f"{platform} request failed: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DecisionError(f"{platform} returned non-JSON: {raw[:120]!r}") from exc


def ask(spec: AgentSpec, state: Any, questions: dict[str, Question], *, timeout: float,
        env: dict[str, str]) -> Decision:
    """One request to one decision model. Raises DecisionError (or DecisionRateLimited)."""
    if spec.kind != "decision" or not spec.endpoint or not spec.model:
        raise DecisionError(f"{spec.id} is not a decision model")
    key = env.get(spec.api_key_env, "") if spec.api_key_env else ""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    qjson = {qid: q.to_json() for qid, q in questions.items()}
    state_text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    state_text = state_text[:STATE_LIMIT]
    platform = spec.provider
    start = time.monotonic()
    api = spec.decision_api
    if api == "cloudflare":
        # POST .../accounts/<id>/ai/run/@cf/cloudflare/clef-flash with the short selector inside.
        selector = spec.model.rsplit("/", 1)[-1]
        data = _post(spec.endpoint, headers, {"model": selector, "state": state_text, "questions": qjson},
                     timeout, platform)
        if not isinstance(data, dict) or not data.get("success") or not isinstance(data.get("result"), dict):
            msg = (data.get("errors") or [{}])[0].get("message", "no result") if isinstance(data, dict) else "no result"
            raise DecisionError(f"{platform}: {msg}")
        result = data["result"]
        answers, usage, served = result.get("answers"), result.get("usage"), spec.model
    elif api == "openrouter":
        data = _post(spec.endpoint, headers, {"model": spec.model, "state": state_text, "questions": qjson},
                     timeout, platform)
        if not isinstance(data, dict) or data.get("error"):
            msg = data.get("error", {}).get("message", "error") if isinstance(data, dict) else "error"
            raise DecisionError(f"{platform}: {msg}")
        answers, usage, served = data.get("answers"), data.get("usage"), data.get("model") or spec.model
    else:
        data = _post(spec.endpoint, headers, {"model": spec.model, "state": state_text, "questions": qjson},
                     timeout, platform)
        if not isinstance(data, dict) or data.get("error"):
            msg = data.get("error") if isinstance(data, dict) else "error"
            raise DecisionError(f"{platform}: {msg if isinstance(msg, str) else json.dumps(msg)[:200]}")
        served = data.get("model")
        if not isinstance(served, str) or (spec.model != "auto" and served != spec.model):
            raise DecisionError(f"{platform} answered with model {served!r}, not {spec.model!r}")
        answers, usage = data.get("answers"), data.get("usage")
    parsed = validate_answers(answers, questions, platform)
    usage = usage if isinstance(usage, dict) else {}
    return Decision(agent_id=spec.id, provider=spec.provider, model=str(served), answers=parsed, usage=usage,
                    latency=time.monotonic() - start)


def cache_key(state: Any, questions: dict[str, Question]) -> str:
    state_text = state if isinstance(state, str) else json.dumps(state, sort_keys=True, ensure_ascii=False)
    blob = json.dumps({"q": {k: q.to_json() for k, q in questions.items()}, "s": state_text[:STATE_LIMIT]},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class Decider:
    """Pick a ready decision model in declared order, ask it, record quota, cache the answer."""

    def __init__(self, catalog: dict[str, AgentSpec], ledger: Ledger, *, env: dict[str, str] | None = None,
                 timeout: float = 5.0, min_confidence: float = 0.5, reserve: float = 0.0,
                 ask_fn: Callable[..., Decision] = ask) -> None:
        self.specs = [s for s in catalog.values() if s.kind == "decision"]
        self.ledger = ledger
        self.env = dict(os.environ) if env is None else env
        self.timeout = timeout
        self.min_confidence = min_confidence
        self.reserve = reserve
        self._ask = ask_fn

    def availability(self) -> list[tuple[AgentSpec, str | None]]:
        """Each decision model with the reason it cannot be used right now, or None."""
        out = []
        for s in self.specs:
            why = None
            if s.api_key_env and not self.env.get(s.api_key_env):
                why = f"{s.api_key_env} not set"
            elif self.ledger.in_cooldown(s.id):
                why = f"in cooldown for {self.ledger.cooldown_remaining(s.id):.0f}s"
            elif self.ledger.headroom(s) <= self.reserve:
                why = f"headroom {self.ledger.headroom(s):.2f} at or below reserve"
            out.append((s, why))
        return out

    def ready(self, exclude_providers: tuple[str, ...] = ()) -> list[AgentSpec]:
        return [s for s, why in self.availability() if why is None and s.provider not in exclude_providers]

    def decide(self, state: Any, questions: dict[str, Question], *,
               exclude_providers: tuple[str, ...] = ()) -> tuple[Decision | None, str]:
        """(decision, "ok") or (None, reason). Never raises; the caller falls back to its default."""
        if not self.specs:
            return None, "no decision model in the catalog"
        key = cache_key(state, questions)
        cached = self.ledger.decision_cache_get(key, CACHE_TTL)
        if cached is not None:
            d = Decision.from_json(cached, cached=True)
            if d.provider not in exclude_providers:
                return d, "ok"
        candidates = self.ready(exclude_providers)
        if not candidates:
            why = "; ".join(f"{s.id}: {w or 'provider excluded for independence'}" for s, w in self.availability())
            return None, f"no decision model ready ({why or 'none declared'})"
        last = "no attempt"
        for spec in candidates:
            try:
                d = self._ask(spec, state, questions, timeout=self.timeout, env=self.env)
            except DecisionRateLimited as exc:
                self.ledger.record(spec, "decision", ok=False, outcome="429", rate_limited=True)
                last = f"{spec.id}: {exc}"
                continue
            except DecisionError as exc:
                self.ledger.record(spec, "decision", ok=False, outcome=str(exc)[:120])
                last = f"{spec.id}: {exc}"
                continue
            usage = d.usage if isinstance(d.usage, dict) else {}
            self.ledger.record(spec, "decision", duration=d.latency, ok=True,
                               tokens_in=int(usage.get("input_tokens") or 0),
                               tokens_out=int(usage.get("output_tokens") or 0), observed_model=d.model)
            self.ledger.decision_cache_put(key, d.to_json())
            return d, "ok"
        return None, f"every decision model failed (last: {last})"
