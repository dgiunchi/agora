"""Decision models: validation, failover, cache, and how the loop uses them. Fake server only."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agora.cli import main
from agora.decisions import (Decider, DecisionError, DecisionRateLimited, ask, choice, noul, score,
                             validate_answers)
from agora.ledger import Ledger
from agora.registry import AgentSpec, CatalogError, QuotaWindow, catalog_from_dict

from .fake_decisions import FakeDecisionServer
from .helpers import THREE, events, finding, make_coordinator, proof_cmd_for, review, toy_build_response

TWO = (("alpha", "anthropic", "free"), ("beta", "openai", "free"))


def dspec(url: str, *, agent_id: str = "fake-dec", provider: str = "fakedec", model: str = "fake-1",
          api: str = "systemone", key_env: str | None = None, requests: int | None = 100) -> AgentSpec:
    return AgentSpec(id=agent_id, provider=provider, kind="decision", tier="free", endpoint=url, model=model,
                     decision_api=api, api_key_env=key_env, windows=(QuotaWindow("minute", 60, requests=requests),))


QS = {"strategy": choice("Which?", {"collaborative": "a", "adversarial": "b", "hybrid": "c"}),
      "specific": noul("Specific?"), "severity": score("How bad?", ["cosmetic", "low", "medium", "high"])}


class AskTests(unittest.TestCase):
    def test_typed_answers_are_parsed_for_every_shape(self):
        for api in ("systemone", "cloudflare", "openrouter"):
            srv = FakeDecisionServer(choices={"strategy": "adversarial"}, api=api).start()
            try:
                d = ask(dspec(srv.url, api=api), "state", QS, timeout=5, env={})
            finally:
                srv.stop()
            self.assertEqual(d.answers["strategy"].value, "adversarial")
            self.assertAlmostEqual(d.answers["strategy"].confidence, 0.9)
            self.assertAlmostEqual(d.answers["specific"].value, 0.9)
            self.assertEqual(d.answers["severity"].value, 3)
            self.assertEqual(d.usage["input_tokens"], 10)

    def test_bearer_key_is_sent_only_when_configured(self):
        srv = FakeDecisionServer().start()
        try:
            ask(dspec(srv.url), "s", QS, timeout=5, env={})
            ask(dspec(srv.url, key_env="FAKE_KEY"), "s", QS, timeout=5, env={"FAKE_KEY": "k-1"})
        finally:
            srv.stop()
        self.assertIsNone(srv.requests[0]["auth"])
        self.assertEqual(srv.requests[1]["auth"], "Bearer k-1")
        self.assertEqual(srv.requests[1]["body"]["model"], "fake-1")

    def test_invalid_answers_are_errors(self):
        for mode in ("wrong_model", "malformed", "invalid_choice", "500"):
            srv = FakeDecisionServer(mode=mode).start()
            try:
                with self.assertRaises(DecisionError, msg=mode):
                    ask(dspec(srv.url), "s", QS, timeout=5, env={})
            finally:
                srv.stop()

    def test_429_carries_retry_after(self):
        srv = FakeDecisionServer(mode="429").start()
        try:
            with self.assertRaises(DecisionRateLimited) as ctx:
                ask(dspec(srv.url), "s", QS, timeout=5, env={})
        finally:
            srv.stop()
        self.assertEqual(ctx.exception.retry_after, 7.0)

    def test_validation_rules(self):
        q = {"c": choice("?", {"a": "", "b": ""}), "n": noul("?"), "s": score("?", ["x", "y"])}
        good = {"c": {"type": "choice", "choice": "a", "confidence": 0.7},
                "n": {"type": "noul", "noul": 0.2}, "s": {"type": "score", "score": 1}}
        out = validate_answers(good, q, "p")
        self.assertAlmostEqual(out["n"].confidence, 0.8)
        for bad in ({**good, "c": {"type": "choice", "choice": "z"}},
                    {**good, "n": {"type": "noul", "noul": 1.5}},
                    {**good, "s": {"type": "score", "score": 5}},
                    {k: v for k, v in good.items() if k != "s"},
                    {**good, "c": {"type": "noul", "noul": 0.1}}):
            with self.assertRaises(DecisionError):
                validate_answers(bad, q, "p")


class DeciderTests(unittest.TestCase):
    def test_declared_order_failover_and_cooldown(self):
        limited = FakeDecisionServer(mode="429").start()
        ok = FakeDecisionServer(choices={"strategy": "hybrid"}).start()
        try:
            catalog = {"first": dspec(limited.url, agent_id="first", provider="p1"),
                       "second": dspec(ok.url, agent_id="second", provider="p2")}
            ledger = Ledger(":memory:")
            d, reason = Decider(catalog, ledger, env={}).decide("s", QS)
        finally:
            limited.stop(); ok.stop()
        self.assertEqual(reason, "ok")
        self.assertEqual(d.agent_id, "second")
        self.assertTrue(ledger.in_cooldown("first"))
        self.assertEqual(ledger.total_requests("second"), 1)

    def test_cache_prevents_a_second_request(self):
        srv = FakeDecisionServer().start()
        try:
            ledger = Ledger(":memory:")
            dec = Decider({"d": dspec(srv.url)}, ledger, env={})
            d1, _ = dec.decide("same state", QS)
            d2, _ = dec.decide("same state", QS)
            d3, _ = dec.decide("other state", QS)
        finally:
            srv.stop()
        self.assertFalse(d1.cached); self.assertTrue(d2.cached); self.assertFalse(d3.cached)
        self.assertEqual(len(srv.requests), 2)

    def test_missing_key_and_exclusion_are_reported_not_raised(self):
        srv = FakeDecisionServer().start()
        try:
            dec = Decider({"d": dspec(srv.url, key_env="NOPE_KEY")}, Ledger(":memory:"), env={})
            d, reason = dec.decide("s", QS)
            self.assertIsNone(d); self.assertIn("NOPE_KEY not set", reason)
            dec2 = Decider({"d": dspec(srv.url, provider="anthropic")}, Ledger(":memory:"), env={})
            d, reason = dec2.decide("s", QS, exclude_providers=("anthropic",))
            self.assertIsNone(d); self.assertIn("independence", reason)
        finally:
            srv.stop()
        self.assertEqual(srv.requests, [])

    def test_no_decision_model_in_catalog(self):
        d, reason = Decider({}, Ledger(":memory:"), env={}).decide("s", QS)
        self.assertIsNone(d); self.assertIn("no decision model", reason)


class LoopDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _attach(self, coord, ledger, srv, provider="fakedec"):
        coord.decider = Decider({"d": dspec(srv.url, provider=provider)}, ledger, env={}, min_confidence=0.5)

    def test_strategy_auto_follows_a_confident_decision(self):
        srv = FakeDecisionServer(choices={"strategy": "adversarial"}).start()
        try:
            coord, ledger = make_coordinator(self.tmp, "auto", {"*": {"build": [toy_build_response()]}}, agents=THREE)
            coord.config.proof_cmd = proof_cmd_for(self.tmp / "repo")
            self._attach(coord, ledger, srv)
            out = coord.run()
        finally:
            srv.stop()
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(coord.config.strategy, "adversarial")
        first = events(coord, "CHOICE")[0]
        self.assertEqual((first["decision"], first["value"], first["source"]), ("strategy", "adversarial", "decision"))
        self.assertEqual([e["mode"] for e in events(coord, "REVIEW")], ["adversarial"])
        self.assertEqual(events(coord, "DECISION")[0]["answers"]["strategy"]["value"], "adversarial")

    def test_strategy_auto_defaults_to_hybrid_without_confidence_or_decisions(self):
        srv = FakeDecisionServer(choices={"strategy": "adversarial"}, confidence=0.2).start()
        try:
            coord, ledger = make_coordinator(self.tmp, "auto", {"*": {"build": [toy_build_response()]}}, agents=THREE)
            coord.config.proof_cmd = proof_cmd_for(self.tmp / "repo")
            self._attach(coord, ledger, srv)
            out = coord.run()
        finally:
            srv.stop()
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(coord.config.strategy, "hybrid")
        self.assertEqual(events(coord, "CHOICE")[0]["source"], "default")
        tmp2 = Path(tempfile.mkdtemp())
        coord2, _ = make_coordinator(tmp2, "auto", {"*": {"build": [toy_build_response()]}}, agents=THREE)
        coord2.config.proof_cmd = proof_cmd_for(tmp2 / "repo")
        out2 = coord2.run()    # no decider at all
        self.assertEqual(out2.status, "completed", out2.reason)
        self.assertEqual(events(coord2, "DECISION")[0]["fallback"], "decisions off")
        self.assertEqual(coord2.config.strategy, "hybrid")

    def test_allocation_auto_switches_the_preset(self):
        srv = FakeDecisionServer(choices={"allocation": "auto:cheap"}).start()
        try:
            coord, ledger = make_coordinator(self.tmp, "collaborative", {"*": {"build": [toy_build_response()]}},
                                             agents=THREE, allocation="auto")
            coord.config.proof_cmd = proof_cmd_for(self.tmp / "repo")
            self._attach(coord, ledger, srv)
            out = coord.run()
        finally:
            srv.stop()
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(coord.allocator.strategy, "auto:cheap")
        self.assertIn("auto:cheap bandit", events(coord, "ALLOCATION")[0]["reason"])

    def test_decision_judge_clears_only_confident_addressed(self):
        for verdict, expect_revision in (("addressed", False), ("out_of_scope", False), ("stands", True)):
            tmp = Path(tempfile.mkdtemp())
            srv = FakeDecisionServer(choices={"status": verdict}).start()
            try:
                script = {"*": {"review": [review("REVISE", [finding("F1")]), review()],
                                "build": [toy_build_response()]}}
                coord, ledger = make_coordinator(tmp, "adversarial", script, agents=TWO, judge="decision")
                coord.config.proof_cmd = proof_cmd_for(tmp / "repo")
                self._attach(coord, ledger, srv)
                out = coord.run()
            finally:
                srv.stop()
            self.assertEqual(out.status, "completed", (verdict, out.reason))
            judge = [e for e in events(coord, "JUDGE") if e["by"] == "decision"][0]
            self.assertEqual(judge["unresolved"], ["F1"] if expect_revision else [], verdict)
            self.assertEqual(len(events(coord, "REVISION")), 1 if expect_revision else 0, verdict)
            self.assertTrue(any(e["decision"] == "triage" for e in events(coord, "DECISION")), verdict)

    def test_decision_judge_needs_a_third_provider_and_a_passing_proof(self):
        # same provider as the planner or the red team: skipped, finding stands
        srv = FakeDecisionServer(choices={"status": "addressed"}).start()
        try:
            script = {"*": {"review": [review("REVISE", [finding("F1")]), review()], "build": [toy_build_response()]}}
            coord, ledger = make_coordinator(self.tmp, "adversarial", script, agents=TWO, judge="decision")
            coord.config.proof_cmd = proof_cmd_for(self.tmp / "repo")
            self._attach(coord, ledger, srv, provider="anthropic")
            out = coord.run()
        finally:
            srv.stop()
        self.assertEqual(out.status, "completed", out.reason)
        judge_dec = [e for e in events(coord, "DECISION") if e["decision"] == "judge"][0]
        self.assertIn("independence", judge_dec["fallback"])
        self.assertEqual(len(events(coord, "REVISION")), 1)
        self.assertTrue(all("status" not in r["body"]["questions"] for r in srv.requests))   # triage only
        # before the build the proof is evidence, not a veto: "addressed" still clears
        tmp2 = Path(tempfile.mkdtemp())
        srv2 = FakeDecisionServer(choices={"status": "addressed"}).start()
        try:
            script = {"*": {"review": [review("REVISE", [finding("F1")]), review()], "build": [toy_build_response()]}}
            coord2, ledger2 = make_coordinator(tmp2, "adversarial", script, agents=TWO, judge="decision")
            coord2.config.proof_cmd = proof_cmd_for(tmp2 / "repo")   # fails before the build: toy.py missing
            self._attach(coord2, ledger2, srv2)
            out2 = coord2.run()
        finally:
            srv2.stop()
        self.assertEqual(out2.status, "completed", out2.reason)
        judge = [e for e in events(coord2, "JUDGE") if e["by"] == "decision"][0]
        self.assertEqual(judge["unresolved"], [])
        self.assertIn("before the build", judge["proof"])

    def test_dry_run_notes_say_what_decisions_do(self):
        srv = FakeDecisionServer().start()
        try:
            coord, ledger = make_coordinator(self.tmp, "auto", agents=THREE, judge="decision")
            self._attach(coord, ledger, srv)
            allocs = coord.dry_run()
        finally:
            srv.stop()
        self.assertEqual([a.role for a in allocs], ["plan", "review", "review", "build", "inspect"])   # hybrid shape, no judge agent
        self.assertTrue(any("ready; used for strategy, judge" in n for n in coord.dry_run_notes), coord.dry_run_notes)
        coord2, _ = make_coordinator(Path(tempfile.mkdtemp()), "auto", agents=THREE, judge="decision")
        coord2.dry_run()
        self.assertTrue(any("decisions are off" in n for n in coord2.dry_run_notes), coord2.dry_run_notes)


class CatalogAndCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_decision_entries_are_validated(self):
        base = {"id": "d", "provider": "p", "kind": "decision", "endpoint": "http://x", "model": "m"}
        catalog_from_dict({"agents": [base]})
        for bad in ({**base, "endpoint": ""}, {**base, "model": ""}, {**base, "decision_api": "soap"}):
            with self.assertRaises(CatalogError):
                catalog_from_dict({"agents": [bad]})

    def test_cli_decide_and_doctor(self):
        srv = FakeDecisionServer(choices={"strategy": "adversarial"}).start()
        try:
            catalog = self.tmp / "agents.toml"
            catalog.write_text('[[agents]]\nid = "fake-dec"\nprovider = "fakedec"\nkind = "decision"\n'
                               f'endpoint = "{srv.url}"\nmodel = "fake-1"\n[[agents.windows]]\nname = "minute"\n'
                               'seconds = 60\nrequests = 5\n', encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["decide", "--catalog", str(catalog), "--ledger", str(self.tmp / "l.sqlite"),
                             "--state", "harden parse_size against bad input",
                             "--choice", "strategy=collaborative,adversarial,hybrid", "--noul", "risky"])
            self.assertEqual(code, 0, err.getvalue())
            self.assertIn("strategy: choice=adversarial confidence=0.90", out.getvalue())
            self.assertIn("risky: noul=0.9", out.getvalue())
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["agents", "doctor", "--catalog", str(catalog)])
            self.assertEqual(code, 0, err.getvalue())
            self.assertIn("decision endpoint=", out.getvalue())
            with redirect_stdout(out), redirect_stderr(err):
                code = main(["decide", "--catalog", str(catalog), "--ledger", str(self.tmp / "l.sqlite"),
                             "--state", "x", "--choice", "only=one"])
            self.assertEqual(code, 2)
        finally:
            srv.stop()
        self.assertEqual(len(srv.requests), 1)


if __name__ == "__main__":
    unittest.main()
