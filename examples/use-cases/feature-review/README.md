# Use case 1: ship a feature nobody self-approved

Strategy `collaborative`. Fixture: `slugger.py` with a `slugify()` stub and five tests in `test_slugger.py` that fail until it is implemented. Proof: `python -m unittest -q`.

```bash
python examples/use-cases/run.py feature-review --catalog agents.toml
```

## Live run 1 (2026-10-29), `result/run-1/`

| Role | Agent | Wall time | Result |
|---|---|---|---|
| plan | Claude Code 2.1.306 | 309 s | PLAN.md with acceptance criteria and a verification table |
| review | Codex 0.153.4, gpt-6-astra, read-only | 116 s | APPROVED, 1 low (R1: expected `git status` omitted untracked files) |
| build | Claude Code | 600 s | timed out, process tree killed; `slugger.py` was already written |
| build (fallback) | Codex, workspace-write | 59 s | verified the existing implementation, changed nothing |
| proof | `python -m unittest -q` | 1 s | Ran 5 tests, OK |
| inspect | Claude Code, fresh session | 208 s | APPROVED, 2 low (F1 NFKD drops ß/æ/ø/ł; F2 `__pycache__` untracked) |

Outcome `completed`, 1 fallback, 3 low findings left open. Codex tokens: review 89,910 in / 672 out, build 91,990 in / 362 out (mostly cached).

What this run exposed: the inspector was chosen to be independent of the recorded builder (Codex), but the code had been written by the timed-out Claude turn, so Claude inspected its own work. The loop now records any agent whose failed write turn changed the working tree as an author (`FALLBACK ... tree_changed=true`, `INSPECT ... also_touched=[...]`) and the inspector must be independent of all of them. Under that rule, with two providers, this run would have stopped at inspection with `no eligible agent for role 'inspect'`. Three tests in `tests/test_fallback.py` pin the behaviour. A second live run under the fixed rule got a clean plan (158 s) and a zero-finding approval from Codex (44 s) and was then killed by the host's memory reaper during the build turn; rerun the command above to produce a complete one.

Why the Claude turns were slow: the host's Claude Code had plugin hooks that blocked or delayed its read tools inside headless turns; the planner and the inspector both reported it under `limitations` (see `result/run-1/*.json`). The `.claude-flow/` directory that appears untracked in the log comes from one of those plugins, not from Agora.

## Run 2 (2026-10-30, after the hardening)

Same fixture, same two agents, through the new launcher (npm shims resolved to `node <entry.js>`). Codex planned; Claude approved with 2 low findings in 297 s; Claude's build turn timed out at 900 s without touching the tree; Codex built on fallback in 65 s; the proof passed 5/5; a fresh Claude session inspected and approved with 3 low findings in 339 s. One fallback, zero tree-touching failures, the inspector independent of every author. Everything is in [`result/run-2/`](result/run-2/). The attempt before it was voided by the new read-only rule on a false positive (a host plugin writing `.claude-flow/mods/session.json` during a clean review); see `VALIDATION.md`.
