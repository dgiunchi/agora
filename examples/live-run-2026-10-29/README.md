# Live run, 2026-10-29

A real collaborative loop on a throwaway fixture: a one-function repo whose five tests fail until slugify is implemented.

| Role | Agent | Model | Wall time | Result |
|---|---|---|---|---|
| plan | Claude Code 2.1.306 | CLI default | 102 s | wrote PLAN.md (sha bd1331b2...) |
| review | Codex CLI 0.153.4 | gpt-6-astra | 38 s | APPROVED, 1 low finding, 4 coverage items |
| build | Claude Code 2.1.306 | CLI default | 197 s | implemented slugify, 1 tracked file changed |
| proof | python -m unittest -q | | 2 s | Ran 5 tests, OK |
| inspect | Codex CLI 0.153.4 (fresh session) | gpt-6-astra | 43 s | APPROVED, 0 findings, 5 coverage items |

Command:

```text
agora run "Implement slugify(text) in slugger.py so that 'python -m unittest -q' passes ..." \
  --strategy collaborative --builder claude-code --proof-cmd "python -m unittest -q" --rounds 3 --seed 1
```

Files here: the log section for this run, the plan Claude wrote, the implementation it built, the test contract, and both Codex verdicts with token usage. Paths are redacted.

What the run showed:
- The reviewer and inspector ran in Codex's read-only sandbox and still executed the tests in memory to check the plan, then verified the diff against the pre-build commit by hand.
- Codex caught a wrong intermediate value in the plan's prose (finding R1, low) without blocking the approval.
- Earlier the same day, a first attempt failed because the Codex CLI default model was rejected by a ChatGPT account; the loop logged the fallback to Claude for planning, then refused to let Claude review its own plan and stopped with the reason. Pinning -m gpt-6-astra fixed it. That failed run is in the catalog notes, not a success story.
- Six untracked .claude-flow files appeared in the fixture during the build: they come from a plugin installed in the host's Claude Code, not from Agora. The inspector read them and reported them under limitations.
