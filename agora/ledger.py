"""Quota ledger: the FreeLLMAPI idea applied to agentic systems.

Persistent per-(agent, model, key, window) counters in SQLite, cooldowns with
exponential backoff on rate limits, caps learned from the first 429, and a
headroom number the allocator can rank on. A clock is injectable so tests can
move time without sleeping.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Callable

from .registry import AgentSpec, QuotaWindow

DAY = 86400


@dataclass
class AgentStats:
    successes: int
    failures: int
    avg_latency: float | None


class Ledger:
    def __init__(self, path: str = ":memory:", *, now: Callable[[], float] = time.time,
                 base_cooldown: float = 30.0, max_cooldown: float = 3600.0) -> None:
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self._now = now
        self.base_cooldown = base_cooldown
        self.max_cooldown = max_cooldown
        self._init()

    # -- schema -----------------------------------------------------------
    def _init(self) -> None:
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS usage(
            agent TEXT, model TEXT, key TEXT, window TEXT, window_start INTEGER,
            requests INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(agent, model, key, window, window_start))""")
        c.execute("""CREATE TABLE IF NOT EXISTS cooldowns(
            agent TEXT PRIMARY KEY, until REAL NOT NULL, failures INTEGER NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS learned_caps(
            agent TEXT, window TEXT, requests INTEGER, tokens INTEGER,
            PRIMARY KEY(agent, window))""")
        c.execute("""CREATE TABLE IF NOT EXISTS invocations(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, agent TEXT, role TEXT,
            requested_model TEXT, observed_model TEXT, tokens_in INTEGER, tokens_out INTEGER,
            duration REAL, ok INTEGER, outcome TEXT)""")

    def close(self) -> None:
        self.conn.close()

    def __del__(self) -> None:  # pragma: no cover - best effort
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    # -- time -------------------------------------------------------------
    def now(self) -> float:
        return float(self._now())

    def window_start(self, spec: AgentSpec, window: QuotaWindow, now: float | None = None) -> int:
        """Bucket start for a window.

        utc-midnight: buckets are aligned to UTC midnight (a 24h window resets
        at midnight, a 1h window at the top of each hour, counted from midnight).
        rolling: buckets are aligned to the epoch, so a 5h window resets every
        5h from the epoch. Both are deterministic and need no history scan.
        """
        t = self.now() if now is None else now
        if spec.reset == "utc-midnight":
            day_start = int(t // DAY) * DAY
            return day_start + int((t - day_start) // window.seconds) * window.seconds
        return int(t // window.seconds) * window.seconds

    # -- recording --------------------------------------------------------
    def record(self, spec: AgentSpec, role: str, *, key: str = "default", tokens_in: int = 0,
               tokens_out: int = 0, duration: float = 0.0, ok: bool = True, outcome: str = "ok",
               observed_model: str | None = None, rate_limited: bool = False) -> None:
        model = spec.model or ""
        tokens = int(tokens_in) + int(tokens_out)
        if rate_limited:
            # Learn caps from the requests that succeeded, before counting the rejected one.
            self.mark_rate_limited(spec, key=key)
        for w in spec.windows:
            start = self.window_start(spec, w)
            self.conn.execute(
                """INSERT INTO usage(agent, model, key, window, window_start, requests, tokens)
                   VALUES(?,?,?,?,?,1,?)
                   ON CONFLICT(agent, model, key, window, window_start)
                   DO UPDATE SET requests = requests + 1, tokens = tokens + excluded.tokens""",
                (spec.id, model, key, w.name, start, tokens))
        self.conn.execute(
            """INSERT INTO invocations(ts, agent, role, requested_model, observed_model, tokens_in,
               tokens_out, duration, ok, outcome) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (self.now(), spec.id, role, spec.model, observed_model, int(tokens_in), int(tokens_out),
             float(duration), 1 if ok else 0, outcome))
        if ok and not rate_limited:
            self.clear_cooldown(spec.id)

    def mark_rate_limited(self, spec: AgentSpec, *, key: str = "default") -> float:
        """Put the agent in cooldown with exponential backoff; learn unknown caps.

        Returns the cooldown end time.
        """
        row = self.conn.execute("SELECT failures FROM cooldowns WHERE agent=?", (spec.id,)).fetchone()
        failures = (row["failures"] if row else 0) + 1
        delay = min(self.max_cooldown, self.base_cooldown * (2 ** (failures - 1)))
        until = self.now() + delay
        self.conn.execute(
            "INSERT INTO cooldowns(agent, until, failures) VALUES(?,?,?) "
            "ON CONFLICT(agent) DO UPDATE SET until=excluded.until, failures=excluded.failures",
            (spec.id, until, failures))
        for w in spec.windows:
            used_req, used_tok = self.used(spec, w, key=key)
            learned = self.conn.execute(
                "SELECT requests, tokens FROM learned_caps WHERE agent=? AND window=?",
                (spec.id, w.name)).fetchone()
            req_cap = w.requests if w.requests is not None else (learned["requests"] if learned else None)
            tok_cap = w.tokens if w.tokens is not None else (learned["tokens"] if learned else None)
            if req_cap is None:
                req_cap = max(1, used_req)
            if tok_cap is None and used_tok > 0:
                tok_cap = used_tok
            self.conn.execute(
                "INSERT INTO learned_caps(agent, window, requests, tokens) VALUES(?,?,?,?) "
                "ON CONFLICT(agent, window) DO UPDATE SET requests=excluded.requests, tokens=excluded.tokens",
                (spec.id, w.name, req_cap, tok_cap))
        return until

    def learned(self, agent_id: str) -> dict[str, dict[str, int | None]]:
        """Caps learned from rate limits, per window."""
        rows = self.conn.execute("SELECT window, requests, tokens FROM learned_caps WHERE agent=?",
                                 (agent_id,)).fetchall()
        return {r["window"]: {"requests": r["requests"], "tokens": r["tokens"]} for r in rows}

    def clear_cooldown(self, agent_id: str) -> None:
        self.conn.execute("DELETE FROM cooldowns WHERE agent=?", (agent_id,))

    def forget(self, agent_id: str) -> None:
        """Drop learned caps and cooldown for an agent (e.g. after a misdetected 429).
        Declared caps and recorded usage are untouched."""
        self.conn.execute("DELETE FROM learned_caps WHERE agent=?", (agent_id,))
        self.conn.execute("DELETE FROM cooldowns WHERE agent=?", (agent_id,))
        self.conn.commit()

    # -- queries ----------------------------------------------------------
    def in_cooldown(self, agent_id: str) -> bool:
        return self.cooldown_remaining(agent_id) > 0

    def cooldown_remaining(self, agent_id: str) -> float:
        row = self.conn.execute("SELECT until FROM cooldowns WHERE agent=?", (agent_id,)).fetchone()
        if not row:
            return 0.0
        return max(0.0, float(row["until"]) - self.now())

    def cooldown_failures(self, agent_id: str) -> int:
        row = self.conn.execute("SELECT failures FROM cooldowns WHERE agent=?", (agent_id,)).fetchone()
        return int(row["failures"]) if row else 0

    def used(self, spec: AgentSpec, window: QuotaWindow, *, key: str = "default") -> tuple[int, int]:
        start = self.window_start(spec, window)
        row = self.conn.execute(
            "SELECT COALESCE(SUM(requests),0) AS r, COALESCE(SUM(tokens),0) AS t FROM usage "
            "WHERE agent=? AND key=? AND window=? AND window_start=?",
            (spec.id, key, window.name, start)).fetchone()
        return int(row["r"]), int(row["t"])

    def cap_for(self, spec: AgentSpec, window: QuotaWindow) -> tuple[int | None, int | None]:
        """Declared caps win; learned caps fill the unknowns."""
        learned = self.conn.execute(
            "SELECT requests, tokens FROM learned_caps WHERE agent=? AND window=?",
            (spec.id, window.name)).fetchone()
        req = window.requests if window.requests is not None else (learned["requests"] if learned else None)
        tok = window.tokens if window.tokens is not None else (learned["tokens"] if learned else None)
        return req, tok

    def headroom(self, spec: AgentSpec, *, key: str = "default") -> float:
        """min over windows and over (requests, tokens) of (cap - used) / cap.

        Unknown caps are optimistic (1.0) until a rate limit teaches them.
        """
        best = 1.0
        for w in spec.windows:
            used_req, used_tok = self.used(spec, w, key=key)
            cap_req, cap_tok = self.cap_for(spec, w)
            if cap_req is not None and cap_req > 0:
                best = min(best, max(0.0, (cap_req - used_req) / cap_req))
            elif cap_req == 0:
                best = 0.0
            if cap_tok is not None and cap_tok > 0:
                best = min(best, max(0.0, (cap_tok - used_tok) / cap_tok))
        return best

    def stats(self, agent_id: str) -> AgentStats:
        row = self.conn.execute(
            "SELECT SUM(ok) AS s, COUNT(*) - SUM(ok) AS f, AVG(CASE WHEN ok=1 THEN duration END) AS d "
            "FROM invocations WHERE agent=?", (agent_id,)).fetchone()
        if not row or row["s"] is None:
            return AgentStats(0, 0, None)
        return AgentStats(int(row["s"]), int(row["f"]), float(row["d"]) if row["d"] is not None else None)

    def total_requests(self, agent_id: str) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM invocations WHERE agent=?", (agent_id,)).fetchone()
        return int(row["n"])

    def usage_report(self, catalog: dict[str, AgentSpec]) -> list[dict]:
        out = []
        for spec in catalog.values():
            st = self.stats(spec.id)
            row = self.conn.execute(
                "SELECT COALESCE(SUM(tokens_in),0) AS ti, COALESCE(SUM(tokens_out),0) AS tout "
                "FROM invocations WHERE agent=?", (spec.id,)).fetchone()
            windows = []
            for w in spec.windows:
                used_req, used_tok = self.used(spec, w)
                cap_req, cap_tok = self.cap_for(spec, w)
                windows.append({"window": w.name, "requests": used_req, "requests_cap": cap_req,
                                "tokens": used_tok, "tokens_cap": cap_tok})
            out.append({
                "agent": spec.id, "provider": spec.provider, "tier": spec.tier,
                "requests": st.successes + st.failures, "successes": st.successes,
                "failures": st.failures, "tokens_in": int(row["ti"]), "tokens_out": int(row["tout"]),
                "headroom": round(self.headroom(spec), 3),
                "cooldown_s": round(self.cooldown_remaining(spec.id), 1),
                "windows": windows,
            })
        return out