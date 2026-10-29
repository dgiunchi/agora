import random
import unittest

from agent_commons.allocator import AllocationError, Allocator, RoleRequest
from agent_commons.ledger import Ledger
from agent_commons.registry import AgentSpec, QuotaWindow

from .helpers import fake_catalog, fake_spec


class Clock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        return self.t


class AllocatorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.ledger = Ledger(":memory:", now=self.clock)
        self.catalog = fake_catalog((("alpha", "anthropic", "free"), ("beta", "openai", "subscription"),
                                     ("gamma", "google", "free"), ("delta", "anthropic", "paid")))

    def test_hard_filters_never_violated(self):
        rng = random.Random(1)
        ids = list(self.catalog)
        alloc = Allocator(self.catalog, self.ledger, strategy="auto:smart", seed=3)
        for _ in range(1000):
            for aid in ids:
                self.ledger.clear_cooldown(aid)
            cooled = {aid for aid in ids if rng.random() < 0.3}
            for aid in cooled:
                self.ledger.mark_rate_limited(self.catalog[aid])
            independent = tuple(rng.sample(ids, rng.randint(0, 2)))
            min_cap = rng.choice([0.0, 0.5, 0.95])
            req = RoleRequest(rng.choice(["plan", "review", "build", "inspect", "judge"]),
                              independent_of=independent, min_capability=min_cap)
            try:
                a = alloc.choose(req)
            except AllocationError:
                ok, _ = alloc.eligible(req)
                self.assertEqual(ok, [])
                continue
            spec = self.catalog[a.agent_id]
            self.assertNotIn(a.agent_id, cooled)
            self.assertNotIn(a.agent_id, independent)
            blocked = {self.catalog[x].provider for x in independent}
            self.assertNotIn(spec.provider, blocked)
            self.assertGreaterEqual(spec.capability(req.role), min_cap)

    def test_cheap_prefers_free_tier(self):
        cat = {"free": fake_spec("free", "p1", "free"), "paid": fake_spec("paid", "p2", "paid")}
        for seed in range(50):
            a = Allocator(cat, self.ledger, strategy="auto:cheap", seed=seed).choose(RoleRequest("build"))
            self.assertEqual(a.agent_id, "free")

    def test_smart_prefers_capability(self):
        cat = {"weak": fake_spec("weak", "p1", "free", caps={"build": 0.6}),
               "strong": fake_spec("strong", "p2", "paid", caps={"build": 1.0})}
        hits = sum(Allocator(cat, self.ledger, strategy="auto:smart", seed=s).choose(RoleRequest("build")).agent_id
                   == "strong" for s in range(50))
        self.assertGreaterEqual(hits, 48)

    def test_seeded_bandit_is_deterministic(self):
        req = RoleRequest("review")
        a1 = Allocator(self.catalog, self.ledger, seed=42)
        a2 = Allocator(self.catalog, self.ledger, seed=42)
        seq1 = [a1.choose(req).agent_id for _ in range(20)]
        seq2 = [a2.choose(req).agent_id for _ in range(20)]
        self.assertEqual(seq1, seq2)

    def test_dry_run_spends_nothing(self):
        alloc = Allocator(self.catalog, self.ledger, seed=1)
        reqs = [RoleRequest("plan"), RoleRequest("review", independent_of=("role:plan",)),
                RoleRequest("build"), RoleRequest("inspect", independent_of=("role:build",))]
        plan = alloc.plan(reqs)
        self.assertEqual(len(plan), 4)
        for aid in self.catalog:
            self.assertEqual(self.ledger.total_requests(aid), 0)
            self.assertEqual(self.ledger.used(self.catalog[aid], self.catalog[aid].windows[0])[0], 0)

    def test_plan_resolves_role_references_for_independence(self):
        alloc = Allocator(self.catalog, self.ledger, seed=5)
        for _ in range(30):
            plan = alloc.plan([RoleRequest("plan"), RoleRequest("review", independent_of=("role:plan",)),
                               RoleRequest("build"), RoleRequest("inspect", independent_of=("role:build",))])
            by_role = {a.role: a for a in plan}
            self.assertNotEqual(by_role["review"].provider, by_role["plan"].provider)
            self.assertNotEqual(by_role["inspect"].provider, by_role["build"].provider)

    def test_headroom_reserve_filters(self):
        s = fake_spec("tiny", "p", windows=(QuotaWindow("day", 86400, requests=10),))
        cat = {"tiny": s}
        alloc = Allocator(cat, self.ledger, seed=1, reserve=0.05)
        for _ in range(10):
            self.ledger.record(s, "plan")
        with self.assertRaisesRegex(AllocationError, "headroom"):
            alloc.choose(RoleRequest("plan"))

    def test_error_lists_reasons(self):
        alloc = Allocator(self.catalog, self.ledger, seed=1)
        for aid in self.catalog:
            self.ledger.mark_rate_limited(self.catalog[aid])
        with self.assertRaisesRegex(AllocationError, "cooldown"):
            alloc.choose(RoleRequest("plan"))

    def test_next_choice_excludes_failed_agent(self):
        alloc = Allocator(self.catalog, self.ledger, seed=1)
        first = alloc.choose(RoleRequest("plan"))
        nxt = alloc.next_choice(RoleRequest("plan"), first.agent_id)
        self.assertNotEqual(nxt.agent_id, first.agent_id)

    def test_named_profile_takes_declared_order(self):
        alloc = Allocator(self.catalog, self.ledger, strategy="profile:coding", seed=1,
                          profiles={"coding": ["gamma", "alpha"]})
        self.assertEqual(alloc.choose(RoleRequest("build")).agent_id, "gamma")
        self.ledger.mark_rate_limited(self.catalog["gamma"])
        self.assertEqual(alloc.choose(RoleRequest("build")).agent_id, "alpha")

    def test_unknown_strategy_rejected(self):
        with self.assertRaises(AllocationError):
            Allocator(self.catalog, self.ledger, strategy="auto:magic")


if __name__ == "__main__":
    unittest.main()