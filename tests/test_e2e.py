"""End-to-end: three fake agents, a toy repo, a real proof command, all three strategies."""
import re
import tempfile
import unittest
from pathlib import Path

from .helpers import finding, make_coordinator, proof_cmd_for, review, toy_build_response


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _script(self):
        return {"*": {"review": [review("REVISE", [finding("F1", "high", "PLAN.md")]), review("APPROVED")],
                      "plan": [{"json": {"plan": "# Plan v1\n\nadd(a, b) returns a + b"}},
                               {"json": {"plan": "# Plan v2\n\nadd(a, b) returns a + b; F1 addressed"}}],
                      # the judge keeps F1 in round 1 (a pre-build proof failure is evidence, not a veto)
                      "judge": [{"json": {"unresolved": ["F1"]}}, {"json": {"unresolved": []}}],
                      "build": [toy_build_response()],
                      "inspect": [review("APPROVED", [finding("L1", "low", "toy.py")], coverage=["toy.py"])]}}

    def _run(self, strategy):
        coord, ledger = make_coordinator(self.tmp, strategy, self._script(), rounds=3)
        coord.config.proof_cmd = proof_cmd_for(self.tmp / "repo")
        return coord, coord.run()

    def test_each_strategy_end_to_end(self):
        for strategy in ("collaborative", "adversarial", "hybrid"):
            with self.subTest(strategy=strategy):
                self.tmp = Path(tempfile.mkdtemp())
                coord, out = self._run(strategy)
                self.assertEqual(out.status, "completed", out.reason)
                self.assertTrue((self.tmp / "repo" / "toy.py").exists())
                log = coord.log_path.read_text(encoding="utf-8")
                self.assertIn("task: make toy.add work", log)
                self.assertRegex(log, r"ALLOCATION role=plan agent=\w+ provider=\w+")
                self.assertIn("REVISION round=1", log)
                self.assertIn("APPROVAL mode=", log)
                self.assertRegex(log, r"PROOF cmd=.* passed=true")
                self.assertRegex(log, r"INSPECT round=1 agent=\w+ verdict=APPROVED")
                self.assertIn("SUMMARY status=completed", log)
                self.assertEqual([f["id"] for f in out.open_findings], ["L1"])
                # per-agent quota in the log matches the ledger
                for row in out.quota:
                    if row["requests"]:
                        self.assertIn(f"QUOTA agent={row['agent']} requests={row['requests']}", log)
                self.assertEqual(sum(r["requests"] for r in out.quota), len(out.allocations))
                self.assertEqual(out.plan_sha256, out.approval.plan_sha256)
                self.assertIn("F1 addressed", coord.plan_path.read_text(encoding="utf-8"))

    def test_log_is_append_only_across_runs(self):
        coord, out = self._run("collaborative")
        first = coord.log_path.read_text(encoding="utf-8")
        coord2, ledger2 = make_coordinator(self.tmp, "collaborative", self._script(), rounds=3)
        coord2.config.proof_cmd = proof_cmd_for(self.tmp / "repo")
        coord2.run()
        second = coord2.log_path.read_text(encoding="utf-8")
        self.assertTrue(second.startswith(first))
        self.assertEqual(len(re.findall(r"^## Run ", second, re.M)), 2)


if __name__ == "__main__":
    unittest.main()