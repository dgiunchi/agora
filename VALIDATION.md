# Validation record

The automated suite uses fake agent processes and spends no quota. This file records what has been checked against real tools. Add a row when you run one.

## Doctor (no quota spent)

`agora agents doctor --catalog agents.example.toml` on Windows 11, Python 3.13, 2026-10-29:

| Agent | Result |
|---|---|
| claude-code | ok, Claude Code 2.1.306 |
| codex | ok, codex-cli 0.153.4 |
| gemini-cli | installed, but `gemini --version` did not answer within 20 s on this machine (probe timeout); catalog argv unverified |
| qwen-code, opencode, aider, goose, crush, kilo-code, cline | not installed here; argv taken from each tool's documentation |
| freellmapi, ollama | http entries; endpoint not running on this machine |

## Dry run (no quota spent)

`agora run "smoke" --catalog agents.example.toml --dry-run --seed 1 --allocation auto:cheap` produced a seven-role hybrid allocation plan with independence satisfied (reviewers never shared the planner's provider; the inspector never shared the builder's).

## Live loops

| Date | Strategy | Agents | Outcome | Record |
|---|---|---|---|---|
| 2026-10-29 | collaborative | Claude Code 2.1.306 (plan, build), Codex 0.153.4 gpt-6-astra (review, inspect) | completed: APPROVED round 1, proof 5/5, inspect APPROVED, 0 fallbacks, 7 turns, ~6.5 min wall | [examples/live-run-2026-10-29](examples/live-run-2026-10-29/) |
| 2026-10-29 | collaborative | same, Codex on its CLI default model | failed as designed: Codex rejected gpt-6.1-sol on a ChatGPT account, loop fell back to Claude for planning (logged), then refused to let Claude review its own plan | log excerpt in the example README |
| 2026-10-29 | collaborative (use case 1, run 1) | Claude Code (plan, build attempt, inspect), Codex (review, fallback build) | completed: review APPROVED 1 low; Claude build timed out at 600 s after writing the file; fallback to Codex (workspace-write) verified it; proof 5/5; inspect APPROVED 2 low. Exposed the author gap described below | [examples/use-cases/feature-review/result/run-1](examples/use-cases/feature-review/result/run-1/) |
| 2026-10-29 | collaborative (use case 3, decision memo) | Claude Code (plan), Codex (review, interrupted) | not completed: plan written in 259 s, the Codex review turn was running when the host ran out of memory and the run was stopped. No deliverable was produced; the checker and dry run are verified | fixture only |
| 2026-10-29 | adversarial (use case 2) | Codex (plan, build), Claude Code (red team, red-team inspect) | completed: red team APPROVED round 1 with 4 low findings, build, proof 8 tests OK, attack APPROVED 4 low, 0 fallbacks, judge not consulted (no material finding) | [examples/use-cases/red-team/result/run-1](examples/use-cases/red-team/result/run-1/) |

Quota consumed by the successful run, per gora quota: Claude Code 3 requests, Codex 4 requests (2 of them the earlier failures). Codex usage reported by its CLI: review 57k input / 581 output tokens, inspect 80k input / 641 output tokens. Claude Code does not report usage in --output-format json on this version.

Requirement discovered: on ChatGPT accounts Codex needs an explicit -m model; the example catalog now pins gpt-6-astra.

Gap found by use case 1, run 1: a build turn that times out is killed, but the edits it already made stay in the working tree. The fallback builder (Codex) found the finished file and verified it, and the inspector was allocated to be independent of the recorded builder only, so Claude inspected code Claude had written. Fix: any agent whose failed write turn changed the working-tree fingerprint is recorded as an author (`FALLBACK ... tree_changed=true`, `INSPECT ... also_touched=[...]`) and the inspector must be independent of every author. With two providers that run would now end with `no eligible agent for role 'inspect'`. Covered by three tests in `tests/test_fallback.py`.

Requirement discovered: Codex `exec` runs read-only unless told otherwise, so the catalog now has `write_argv = ["-s", "workspace-write"]` for builders next to `read_only_argv = ["-s", "read-only"]`. Verified by the Codex build turns in use cases 1 and 2.

Environment note: on this host Claude Code had plugin hooks installed (ECC GateGuard and others) that blocked or delayed its read tools inside headless `claude -p` turns. Claude's planner and reviewers reported it under `limitations`; it explains the 5 to 10 minute Claude turns and the one timeout. Headless children inherit the host's hooks; this is not something Agora can or should switch off.

A live run must use a disposable fixture repository and an explicit model, and must note requested versus observed model, rounds used, fallbacks, and quota consumed per agent as printed by gora quota --json.

A live run must use a disposable fixture repository and an explicit model, and must note requested versus observed model, rounds used, fallbacks, and quota consumed per agent as printed by `agora quota --json`. The bundled use cases (`python examples/use-cases/run.py <name>`) are the easiest way to produce one.