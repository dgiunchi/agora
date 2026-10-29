import tempfile
import unittest
from pathlib import Path

from agora.coordinator import Coordinator, LoopFailure

from .helpers import events, finding, log_text, make_coordinator, proof_cmd_for, review, toy_build_response


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _run(self, strategy, script=None, **cfg):
        coord, ledger = make_coordinator(self.tmp, strategy, script, **cfg)
        return coord, coord.run()

    def test_collaborative_completes(self):
        coord, out = self._run("collaborative")
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(out.rounds_used, 1)
        self.assertEqual(out.inspections, 1)
        self.assertIsNotNone(out.approval)
        kinds = [e["kind"] for e in out.events]
        for k in ("ALLOCATION", "PLAN", "REVIEW", "APPROVAL", "BUILD", "INSPECT", "QUOTA", "SUMMARY"):
            self.assertIn(k, kinds)

    def test_adversarial_completes(self):
        coord, out = self._run("adversarial")
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual([e["mode"] for e in events(coord, "REVIEW")], ["adversarial"])

    def test_hybrid_runs_both_review_modes(self):
        coord, out = self._run("hybrid")
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual([e["mode"] for e in events(coord, "REVIEW")], ["collaborative", "adversarial"])
        self.assertEqual(len(events(coord, "APPROVAL")), 2)

    def test_reviewer_is_independent_of_planner_and_inspector_of_builder(self):
        for strategy in ("collaborative", "adversarial", "hybrid"):
            coord, out = self._run(strategy)
            self.assertEqual(out.status, "completed", out.reason)
            roles = {}
            for a in out.allocations:
                roles.setdefault(a.role, a)
            self.assertNotEqual(roles["review"].provider, roles["plan"].provider)
            self.assertNotEqual(roles["inspect"].provider, roles["build"].provider)

    def test_revise_then_approve_binds_new_sha(self):
        script = {"*": {"review": [review("REVISE", [finding("F1")]), review("APPROVED")],
                        "plan": [{"json": {"plan": "# v1"}}, {"json": {"plan": "# v2 addresses F1"}}]}}
        coord, out = self._run("collaborative", script)
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(out.rounds_used, 2)
        self.assertEqual(coord.plan_path.read_text(encoding="utf-8"), "# v2 addresses F1")
        self.assertEqual(out.approval.plan_sha256, out.plan_sha256)
        self.assertEqual(len(events(coord, "REVISION")), 1)

    def test_rounds_exhausted(self):
        script = {"*": {"review": [review("REVISE", [finding("F1")])]}}
        coord, out = self._run("collaborative", script, rounds=3)
        self.assertEqual(out.status, "rounds_exhausted")
        self.assertEqual(out.rounds_used, 3)
        self.assertIsNone(out.approval)
        self.assertNotIn("BUILD", [e["kind"] for e in out.events])

    def test_blocked_is_never_approval(self):
        script = {"*": {"review": [review("BLOCKED", [finding("F1")], summary="cannot verify")]}}
        coord, out = self._run("collaborative", script)
        self.assertEqual(out.status, "blocked")
        self.assertIsNone(out.approval)

    def test_malformed_review_blocks(self):
        script = {"*": {"review": [{"stdout": "I think it is fine!"}]}}
        coord, out = self._run("collaborative", script)
        self.assertEqual(out.status, "blocked")
        self.assertIn("not a valid review", out.reason)

    def test_duplicate_finding_ids_rejected(self):
        script = {"*": {"review": [review("REVISE", [finding("F1"), finding("F1")])]}}
        coord, out = self._run("collaborative", script)
        self.assertEqual(out.status, "blocked")
        self.assertIn("duplicate", out.reason)

    def test_approved_with_material_findings_rejected(self):
        script = {"*": {"review": [review("APPROVED", [finding("F1", "medium")])]}}
        coord, out = self._run("collaborative", script)
        self.assertEqual(out.status, "blocked")
        self.assertIn("APPROVED cannot carry", out.reason)

    def test_quorum_two_requires_two_independent_approvals(self):
        script = {"gamma": {"review": [review("REVISE", [finding("G1")]), review("APPROVED")]}}
        coord, out = self._run("collaborative", script, quorum=2)
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(out.rounds_used, 2)
        first_round = [e for e in events(coord, "REVIEW") if e["round"] == 1]
        self.assertEqual(len(first_round), 2)
        self.assertEqual(len({e["agent"] for e in first_round}), 2)

    def test_adversarial_judge_llm_clears_findings(self):
        script = {"*": {"review": [review("REVISE", [finding("R1"), finding("R2")])],
                        "judge": [{"json": {"unresolved": ["R2"]}}, {"json": {"unresolved": []}}],
                        "plan": [{"json": {"plan": "# v1"}}, {"json": {"plan": "# v2"}}]}}
        coord, out = self._run("adversarial", script, judge="llm")
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(out.rounds_used, 2)
        judged = events(coord, "JUDGE")
        self.assertEqual(judged[0]["unresolved"], ["R2"])

    def test_proof_cmd_judge_wins_when_proof_passes(self):
        repo = self.tmp / "repo"
        script = {"*": {"review": [review("REVISE", [finding("R1")])], "build": [toy_build_response()]}}
        coord, ledger = make_coordinator(self.tmp, "adversarial", script, judge="proof-cmd")
        (repo / "toy.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        coord.config.proof_cmd = proof_cmd_for(repo)
        out = coord.run()
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(out.rounds_used, 1)
        self.assertNotIn("judge", [a.role for a in out.allocations])

    def test_failed_proof_means_llm_cannot_clear(self):
        repo = self.tmp / "repo"
        script = {"*": {"review": [review("REVISE", [finding("R1")])], "judge": [{"json": {"unresolved": []}}]}}
        coord, ledger = make_coordinator(self.tmp, "adversarial", script, judge="both", rounds=2)
        coord.config.proof_cmd = proof_cmd_for(repo)  # toy.py does not exist: proof fails
        out = coord.run()
        self.assertEqual(out.status, "rounds_exhausted")

    def test_build_refuses_tampered_plan(self):
        class Tamper(Coordinator):
            def _phase_build(self):
                self.plan_path.write_text("tampered", encoding="utf-8")
                return super()._phase_build()

        coord, ledger = make_coordinator(self.tmp, "collaborative")
        coord.__class__ = Tamper
        out = coord.run()
        self.assertEqual(out.status, "failed")
        self.assertIn("not approved", out.reason)

    def test_build_fix_rounds_and_proof(self):
        repo = self.tmp / "repo"
        script = {"*": {"build": [{"json": {"summary": "forgot"}}, toy_build_response()]}}
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, max_fix_rounds=1)
        coord.config.proof_cmd = proof_cmd_for(repo)
        out = coord.run()
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(len(events(coord, "FIX")), 1)
        proofs = events(coord, "PROOF")
        self.assertEqual([p["passed"] for p in proofs], [False, True])

    def test_build_fails_after_fix_budget(self):
        repo = self.tmp / "repo"
        coord, ledger = make_coordinator(self.tmp, "collaborative", {}, max_fix_rounds=1)
        coord.config.proof_cmd = proof_cmd_for(repo)
        out = coord.run()
        self.assertEqual(out.status, "failed")
        self.assertIn("still failing", out.reason)

    def test_inspection_revise_then_approve(self):
        script = {"*": {"inspect": [review("REVISE", [finding("I1", "medium", "toy.py")]), review("APPROVED")]}}
        coord, out = self._run("collaborative", script)
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(out.inspections, 2)
        fps = [e["fingerprint"] for e in events(coord, "INSPECT") if "fingerprint" in e]
        self.assertEqual(len(fps), 2)
        self.assertEqual(len([e for e in events(coord, "FIX") if e.get("after") == "inspection"]), 1)

    def test_inspection_budget_leaves_findings_open(self):
        script = {"*": {"inspect": [review("REVISE", [finding("I1", "high", "toy.py")])]}}
        coord, out = self._run("collaborative", script, max_inspection_rounds=2)
        self.assertEqual(out.status, "completed")
        self.assertEqual([f["id"] for f in out.open_findings], ["I1"])
        self.assertIn("budget exhausted", log_text(coord))

    def test_forced_builder(self):
        coord, out = self._run("collaborative", builder="gamma")
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual([a.agent_id for a in out.allocations if a.role == "build"], ["gamma"])
        inspect = [a for a in out.allocations if a.role == "inspect"][0]
        self.assertNotEqual(inspect.provider, "google")

    def test_dry_run_spends_nothing_and_respects_independence(self):
        coord, ledger = make_coordinator(self.tmp, "hybrid", quorum=2)
        allocs = coord.dry_run()
        self.assertEqual([a.role for a in allocs], ["plan", "review", "review", "review", "judge", "build", "inspect"])
        for aid in coord.catalog:
            self.assertEqual(ledger.total_requests(aid), 0)
        self.assertFalse(coord.log_path.exists())


if __name__ == "__main__":
    unittest.main()