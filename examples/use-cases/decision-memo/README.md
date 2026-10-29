# Use case 3: write a decision memo, not code

Strategy `collaborative`. Fixture: `check_memo.py`, a proof command that fails until `DECISION.md` exists with six level-2 sections in the required order, at least three options, a criteria table with at least four criteria, a stated recommendation, 250 to 700 words and no code block. The loop is exactly the one used for code; only the task and the proof differ.

```bash
python examples/use-cases/run.py decision-memo --catalog agents.toml
```

## Live run 1 (2026-10-29), `result/run-1/`

| Role | Agent | Wall time | Result |
|---|---|---|---|
| plan | Claude Code 2.1.306 | 276 s | PLAN.md with section word budgets, invented scenario details, verification table |
| review 1 | Codex 0.153.4, gpt-6-astra, read-only | 96 s | REVISE, 2 medium + 1 low: invented pains drive the recommendation; "drift disappears" and "half the migration effort" unsupported |
| revise 1 | Claude Code | 142 s | assumptions labelled, claims qualified |
| review 2 | Codex | 76 s | REVISE, 2 medium + 1 low: outline still recommends two options conditionally; "no team boundary worth mirroring" asserted from nothing |
| revise 2 | Claude Code | 135 s | exactly one recommendation; shared ownership stated as an assumption to validate |
| review 3 | Codex | 75 s | APPROVED, 2 low: section budgets add up past the target; git status expectation |
| build | Codex, workspace-write | 77 s | wrote DECISION.md, 548 words, six sections in order |
| proof | `python check_memo.py` | 1 s | OK |
| inspect | Claude Code, fresh session | 280 s | APPROVED, 2 low: one clumsy sentence; a weighting claim the table does not back |

Outcome `completed`, 3 rounds, 0 fallbacks, 4 low findings left open. The memo is `result/run-1/DECISION.md`; every turn's verdict is in the numbered JSON files.

What this run showed: the reviewer's objections were about substance. Round 1 refused a plan that invented the team's pain points and then recommended on the basis of them; round 2 refused a recommendation that hedged between two options and a rationale asserted from nothing. The checker only guards structure and length; the cross-provider review is what guarded the argument.

It also found a bug in Agora. On an earlier attempt the reviewer read the fixture's `AGORA-LOG.md`, echoed it, and the rate-limit detector matched Agora's own `rate_limited=false` field: a clean, complete review was recorded as a 429 and a cap of nine requests was learned, so the next attempt stopped at review with `codex: headroom 0.00`. The detector now ignores clean exits, the pattern no longer matches the underscore form, and `agora quota --forget codex` drops a learned cap. Details in `VALIDATION.md`.

Writing your own non-code use case: the proof command is the whole trick. A structure checker, a schema validator, a link checker, a word-count budget or a spreadsheet formula check all turn "is this document good enough?" into an exit code the loop can act on. The reviewer and the inspector then spend their effort on the part a script cannot judge: wrong facts, weak arguments, unmet criteria.
