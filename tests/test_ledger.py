import unittest

from agent_commons.ledger import Ledger
from agent_commons.registry import AgentSpec, QuotaWindow

DAY = 86400


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def spec(reset="utc-midnight", windows=None, agent_id="a"):
    return AgentSpec(id=agent_id, provider="p", argv=("x", "{prompt}"), reset=reset, windows=windows or (
        QuotaWindow("minute", 60, requests=10),
        QuotaWindow("day", DAY, requests=100, tokens=1000),
    ))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(1_700_000_000.0 + 3600)  # one hour after a UTC midnight
        self.ledger = Ledger(":memory:", now=self.clock, base_cooldown=30, max_cooldown=100)
        self.spec = spec()

    def test_windows_roll_independently(self):
        for _ in range(3):
            self.ledger.record(self.spec, "plan")
        minute, day = self.spec.windows
        self.assertEqual(self.ledger.used(self.spec, minute)[0], 3)
        self.assertEqual(self.ledger.used(self.spec, day)[0], 3)
        self.clock.t += 61
        self.assertEqual(self.ledger.used(self.spec, minute)[0], 0)
        self.assertEqual(self.ledger.used(self.spec, day)[0], 3)

    def test_utc_midnight_reset(self):
        midnight = (int(self.clock.t) // DAY + 1) * DAY
        self.clock.t = midnight - 10
        for _ in range(5):
            self.ledger.record(self.spec, "plan")
        day = self.spec.windows[1]
        self.assertEqual(self.ledger.used(self.spec, day)[0], 5)
        self.clock.t = midnight + 1
        self.assertEqual(self.ledger.used(self.spec, day)[0], 0)
        self.assertEqual(self.ledger.window_start(self.spec, day), midnight)

    def test_rolling_window_is_epoch_aligned(self):
        s = spec(reset="rolling", windows=(QuotaWindow("5h", 5 * 3600, requests=50),))
        w = s.windows[0]
        self.clock.t = 7 * 5 * 3600 + 100
        self.ledger.record(s, "plan")
        self.assertEqual(self.ledger.window_start(s, w), 7 * 5 * 3600)
        self.assertEqual(self.ledger.used(s, w)[0], 1)
        self.clock.t = 8 * 5 * 3600 + 1
        self.assertEqual(self.ledger.used(s, w)[0], 0)

    def test_cooldown_backoff_and_cap(self):
        self.ledger.mark_rate_limited(self.spec)
        self.assertAlmostEqual(self.ledger.cooldown_remaining("a"), 30)
        self.ledger.mark_rate_limited(self.spec)
        self.assertAlmostEqual(self.ledger.cooldown_remaining("a"), 60)
        self.ledger.mark_rate_limited(self.spec)
        self.assertAlmostEqual(self.ledger.cooldown_remaining("a"), 100)  # capped
        self.assertTrue(self.ledger.in_cooldown("a"))
        self.clock.t += 101
        self.assertFalse(self.ledger.in_cooldown("a"))
        self.ledger.record(self.spec, "plan", ok=True)
        self.assertEqual(self.ledger.cooldown_failures("a"), 0)

    def test_rate_limited_record_enters_cooldown(self):
        self.ledger.record(self.spec, "review", ok=False, rate_limited=True)
        self.assertTrue(self.ledger.in_cooldown("a"))

    def test_learned_caps_after_429(self):
        s = spec(windows=(QuotaWindow("day", DAY),))  # cap unknown
        self.assertEqual(self.ledger.headroom(s), 1.0)  # optimistic
        for _ in range(7):
            self.ledger.record(s, "plan")
        self.assertEqual(self.ledger.headroom(s), 1.0)
        self.ledger.mark_rate_limited(s)
        self.assertEqual(self.ledger.cap_for(s, s.windows[0])[0], 7)
        self.assertEqual(self.ledger.headroom(s), 0.0)
        self.clock.t += DAY
        self.assertEqual(self.ledger.headroom(s), 1.0)  # new window, learned cap stays
        self.assertEqual(self.ledger.cap_for(s, s.windows[0])[0], 7)

    def test_declared_caps_win_over_learned(self):
        self.ledger.mark_rate_limited(self.spec)
        self.assertEqual(self.ledger.cap_for(self.spec, self.spec.windows[0])[0], 10)

    def test_headroom_math(self):
        for _ in range(4):
            self.ledger.record(self.spec, "plan")
        self.assertAlmostEqual(self.ledger.headroom(self.spec), 0.6)  # minute: (10-4)/10
        self.clock.t += 61
        self.ledger.record(self.spec, "plan", tokens_in=500, tokens_out=400)
        # day tokens: (1000-900)/1000 = 0.1 beats minute (10-1)/10 and day requests (100-5)/100
        self.assertAlmostEqual(self.ledger.headroom(self.spec), 0.1)

    def test_stats_and_report(self):
        self.ledger.record(self.spec, "plan", duration=2.0, ok=True)
        self.ledger.record(self.spec, "plan", duration=4.0, ok=True)
        self.ledger.record(self.spec, "plan", ok=False, outcome="exit 1")
        st = self.ledger.stats("a")
        self.assertEqual((st.successes, st.failures), (2, 1))
        self.assertAlmostEqual(st.avg_latency, 3.0)
        rep = self.ledger.usage_report({"a": self.spec})[0]
        self.assertEqual(rep["requests"], 3)
        self.assertEqual(rep["windows"][0]["requests_cap"], 10)

    def test_persists_to_disk(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        path = os.path.join(d, "l.sqlite")
        l1 = Ledger(path, now=self.clock)
        l1.record(self.spec, "plan")
        l1.close()
        l2 = Ledger(path, now=self.clock)
        self.assertEqual(l2.used(self.spec, self.spec.windows[1])[0], 1)
        l2.close()


if __name__ == "__main__":
    unittest.main()