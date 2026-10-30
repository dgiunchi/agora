# Bundled use cases

Three small, complete scenarios you can run against your own agents in a few minutes. Each directory is a fixture (a stub or a checker plus a proof that fails until the task is done) and a `usecase.json` that holds the task text, the `agora run` flags and the proof command. Two produce code, one produces a document: the loop does not care.

| Directory | Strategy | Deliverable | Proof |
|---|---|---|---|
| [`feature-review/`](feature-review/) | collaborative | `slugify()` with five failing tests | `python -m unittest -q` |
| [`red-team/`](red-team/) | adversarial | `parse_size()` with eight failing tests, three of which drive fifteen reject-cases | `python -m unittest -q` |
| [`decision-memo/`](decision-memo/) | collaborative | `DECISION.md`, a one-page memo with six required sections | `python check_memo.py` |

## Run one

```bash
agora agents init                           # writes agents.toml; keep two agents from different providers
agora agents doctor                         # on PATH? which version? what exactly would run?
python examples/use-cases/run.py feature-review --catalog agents.toml --dry-run   # who does what, free
python examples/use-cases/run.py feature-review --catalog agents.toml             # the real loop
python examples/use-cases/run.py red-team       --catalog agents.toml
python examples/use-cases/run.py decision-memo  --catalog agents.toml
```

`run.py` copies the fixture into a fresh temporary directory, makes it a git repository with one commit, and runs `agora run` there with per-turn diagnostics next to it. Your own repositories are never touched. Use `--workdir DIR` to pick the location, `--seed N` for a reproducible allocation, `--ledger PATH` to keep this quota separate, and anything after `--` goes to `agora run` (for example `-- --judge proof-cmd --rounds 2`).

When it finishes you get the loop status on stdout, `AGORA-LOG.md` and `PLAN.md` in the fixture directory, and the built code next to the tests. Run the proof command yourself in that directory if you want to see it pass with your own eyes.

## Make your own

Copy one of the directories, replace the stub and the tests, and edit `usecase.json`:

```json
{
  "title": "what the scenario is called",
  "strategy": "collaborative | adversarial | hybrid",
  "task": "the exact task text the planner receives",
  "args": ["--strategy", "hybrid", "--rounds", "3"],
  "proof_cmd": "pytest -q"
}
```

The task text should name the proof command and say that the tests are the contract. Keep the fixture small: every turn sends the planner, reviewer and inspector the whole repository.

## What was checked

All three use cases were run live to completion on 2026-10-29 with Claude Code 2.1.306 and Codex CLI 0.153.4 (gpt-6-astra). Each `result/run-1/` folder holds that run's log section, the plan, the deliverable and one numbered JSON per turn with the agent's full response and token usage where the CLI reports it, paths redacted. Two of the runs found bugs in Agora itself; both are fixed and the records were left as they were. Everything is summarised in the top-level [README](../../README.md#three-use-cases-end-to-end) and [VALIDATION.md](../../VALIDATION.md).
