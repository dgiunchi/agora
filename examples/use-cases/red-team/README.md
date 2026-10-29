# Use case 2: red-team a parser before it ships

Strategy `adversarial`. Fixture: `sizes.py` with a `parse_size()` stub and nine tests in `test_sizes.py`, fifteen of them reject-cases. Proof: `python -m unittest -q`.

```bash
python examples/use-cases/run.py red-team --catalog agents.toml
```

## Live run 1 (2026-10-29), `result/run-1/`

| Role | Agent | Wall time | Result |
|---|---|---|---|
| plan | Codex 0.153.4, gpt-6-astra | 90 s | PLAN.md: anchored ASCII grammar, explicit unit table, exact integer floor arithmetic |
| red team | Claude Code 2.1.306, read-only | 354 s | APPROVED, 4 low: `re.IGNORECASE` would case-fold Unicode letters; non-ASCII digits missing from the rejection list; Python's int digit limit; leading zeros unspecified |
| build | Codex, workspace-write | 139 s | implemented the plan; 8 test methods, 30 assertions pass |
| proof | `python -m unittest -q` | 1 s | OK |
| attack | Claude Code, fresh session | 576 s | APPROVED, 4 low: `str.strip()` accepts Unicode whitespace such as NBSP; no magnitude cap; unit table rebuilt per call; `__pycache__` untracked |

Outcome `completed`, 0 fallbacks, 8 low findings left open for a human. Codex tokens: plan 62,519 in / 1,388 out, build 138,105 in / 1,030 out (mostly cached).

Because the red team returned no material finding in round 1 the judge was never consulted. With a material finding and only two providers the log would show `JUDGE by=llm skipped=true` and the planner would have to answer it in a revision; with a third provider `--judge both` arbitrates. The four plan-stage findings are the kind of thing a red team is for: none of them is covered by the tests, and two of them (Unicode case folding, Unicode digits) are classic parser holes.
