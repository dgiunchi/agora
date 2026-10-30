import tempfile
import unittest
from pathlib import Path

from .helpers import events, log_text, make_coordinator, proof_cmd_for, review, toy_build_response

RATE_LIMITED = {"stdout": "", "stderr": "Error: 429 rate limit exceeded for this account", "exit": 1}
TWO = (("alpha", "anthropic", "free"), ("beta", "openai", "free"), ("gamma", "google", "free"))


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_rate_limit_triggers_logged_fallback(self):
        # whoever is picked for review first is rate limited once; the next eligible agent approves
        script = {"*": {"review": [RATE_LIMITED, review("APPROVED")]}}
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, agents=TWO)
        out = coord.run()
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(len(out.fallbacks), 1)
        ev = out.fallbacks[0]
        self.assertEqual(ev.role, "review")
        self.assertEqual(ev.reason, "rate limited")
        self.assertNotEqual(ev.fallback_from, ev.fallback_to)
        line = [ln for ln in log_text(coord).splitlines() if " FALLBACK " in ln][0]
        for key in ("fallback_from=", "fallback_to=", "reason="):
            self.assertIn(key, line)
        self.assertTrue(ledger.in_cooldown(ev.fallback_from))
        self.assertEqual(ledger.cooldown_failures(ev.fallback_from), 1)

    def test_fallback_disabled_for_role_fails_loudly(self):
        script = {"*": {"review": [RATE_LIMITED, review("APPROVED")]}}
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, agents=TWO,
                                         fallback_roles=("plan", "build", "inspect", "judge"))
        out = coord.run()
        self.assertEqual(out.status, "failed")
        self.assertIn("fallback not allowed", out.reason)
        self.assertEqual(out.fallbacks, [])
        self.assertEqual(events(coord, "FALLBACK"), [])

    def test_fallback_respects_independence(self):
        # planner is anthropic; the reviewer fallback must still avoid anthropic
        script = {"*": {"review": [RATE_LIMITED, review("APPROVED")]}}
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, agents=TWO)
        out = coord.run()
        self.assertEqual(out.status, "completed", out.reason)
        planner = [a for a in out.allocations if a.role == "plan"][0]
        for a in out.allocations:
            if a.role == "review":
                self.assertNotEqual(a.provider, planner.provider)

    def test_fallback_budget_exhausted(self):
        script = {"*": {"review": [RATE_LIMITED]}}  # every reviewer is rate limited
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, agents=TWO, max_fallbacks=1)
        out = coord.run()
        self.assertEqual(out.status, "failed")
        self.assertEqual(len(out.fallbacks), 1)

    def test_failed_builder_that_touched_the_tree_cannot_inspect(self):
        """A build turn that writes a file and then dies is still an author: the
        inspector must be independent of it as well as of the fallback builder."""
        half_build = {"json": {"summary": "half done"}, "write_files": {"half.py": "x = 1\n"}, "exit": 1}
        script = {"*": {"build": [half_build, toy_build_response()]}}
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, agents=TWO)
        coord.config.proof_cmd = proof_cmd_for(self.tmp / "repo")
        out = coord.run()
        self.assertEqual(out.status, "completed", out.reason)
        ev = out.fallbacks[0]
        self.assertEqual(ev.role, "build")
        self.assertIn("FALLBACK role=build", log_text(coord))
        self.assertIn("tree_changed=true", log_text(coord))
        builders = {ev.fallback_from, ev.fallback_to}
        inspect = [a for a in out.allocations if a.role == "inspect"][0]
        self.assertNotIn(inspect.agent_id, builders)
        self.assertNotIn(inspect.provider, {coord.catalog[b].provider for b in builders})
        self.assertEqual(events(coord, "INSPECT")[0]["also_touched"], [ev.fallback_from])

    def test_failed_builder_that_touched_the_tree_leaves_no_inspector_with_two_providers(self):
        two = (("alpha", "anthropic", "free"), ("beta", "openai", "free"))
        half_build = {"json": {"summary": "half done"}, "write_files": {"half.py": "x = 1\n"}, "exit": 1}
        script = {"*": {"build": [half_build, toy_build_response()]}}
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, agents=two)
        coord.config.proof_cmd = proof_cmd_for(self.tmp / "repo")
        out = coord.run()
        self.assertEqual(out.status, "failed")
        self.assertIn("inspect", out.reason)

    def test_failed_builder_that_wrote_nothing_may_still_inspect(self):
        script = {"*": {"build": [{"stdout": "", "stderr": "boom", "exit": 1}, toy_build_response()]}}
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, agents=TWO)
        coord.config.proof_cmd = proof_cmd_for(self.tmp / "repo")
        out = coord.run()
        self.assertEqual(out.status, "completed", out.reason)
        self.assertIn("tree_changed=false", log_text(coord))
        self.assertEqual(events(coord, "INSPECT")[0]["also_touched"], [])

    def test_timeout_also_falls_back(self):
        script = {"*": {"plan": [{"sleep": 20, "json": {"plan": "late"}}, {"json": {"plan": "# ok"}}]}}
        # the timeout applies to every turn; 8 s leaves slow CI runners room for the other turns
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, agents=TWO, timeout=8.0)
        out = coord.run()
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(out.fallbacks[0].reason, "timeout")


if __name__ == "__main__":
    unittest.main()