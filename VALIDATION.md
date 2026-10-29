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

Quota consumed by the successful run, per gora quota: Claude Code 3 requests, Codex 4 requests (2 of them the earlier failures). Codex usage reported by its CLI: review 57k input / 581 output tokens, inspect 80k input / 641 output tokens. Claude Code does not report usage in --output-format json on this version.

Requirement discovered: on ChatGPT accounts Codex needs an explicit -m model; the example catalog now pins gpt-6-astra.

A live run must use a disposable fixture repository and an explicit model, and must note requested versus observed model, rounds used, fallbacks, and quota consumed per agent as printed by gora quota --json.

None recorded yet beyond the above. A live run must use a disposable fixture repository and an explicit model, and must note requested versus observed model, rounds used, fallbacks, and quota consumed per agent as printed by `agora quota --json`.