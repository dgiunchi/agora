# Use case 3: write a decision memo, not code

Strategy `collaborative`. Fixture: `check_memo.py`, a proof command that fails until `DECISION.md` exists with six level-2 sections in the required order, at least three options, a criteria table with at least four criteria, a stated recommendation, 250 to 700 words and no code block. The loop is exactly the one used for code; only the task and the proof differ.

```bash
python examples/use-cases/run.py decision-memo --catalog agents.toml
```

Verified so far: the checker rejects a missing memo and accepts a well-formed synthetic one (`python check_memo.py` in a directory with and without `DECISION.md`), and the dry run allocates plan, review, build and inspect across two providers. The live run on 2026-10-29 wrote the plan (Claude Code, 259 s) and was in the Codex review when the host machine ran out of memory and the run was stopped. No deliverable was produced. When a run completes, collect it into `result/` and add a row to `VALIDATION.md`.

Writing your own non-code use case: the proof command is the whole trick. A structure checker, a schema validator, a link checker, a word-count budget or a spreadsheet formula check all turn "is this document good enough?" into an exit code the loop can act on. The reviewer and the inspector then spend their effort on the part a script cannot judge: wrong facts, weak arguments, unmet criteria.
