import tempfile
import unittest
from pathlib import Path

from .helpers import events, log_text, make_coordinator, review

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

    def test_timeout_also_falls_back(self):
        script = {"*": {"plan": [{"sleep": 20, "json": {"plan": "late"}}, {"json": {"plan": "# ok"}}]}}
        coord, ledger = make_coordinator(self.tmp, "collaborative", script, agents=TWO, timeout=1.0)
        out = coord.run()
        self.assertEqual(out.status, "completed", out.reason)
        self.assertEqual(out.fallbacks[0].reason, "timeout")


if __name__ == "__main__":
    unittest.main()