# Validation record

The automated suite uses fake agent processes and spends no quota. This file records what has been checked against real tools. Add a row when you run one.

## Doctor (no quota spent)

`commons agents doctor --catalog agents.example.toml` on Windows 11, Python 3.13, 2026-10-29:

| Agent | Result |
|---|---|
| claude-code | ok, Claude Code 2.1.306 |
| codex | ok, codex-cli 0.153.4 |
| gemini-cli | installed, but `gemini --version` did not answer within 20 s on this machine (probe timeout); catalog argv unverified |
| qwen-code, opencode, aider, goose, crush, kilo-code, cline | not installed here; argv taken from each tool's documentation |
| freellmapi, ollama | http entries; endpoint not running on this machine |

## Dry run (no quota spent)

`commons run "smoke" --catalog agents.example.toml --dry-run --seed 1 --allocation auto:cheap` produced a seven-role hybrid allocation plan with independence satisfied (reviewers never shared the planner's provider; the inspector never shared the builder's).

## Live loops

None recorded yet. A live run must use a disposable fixture repository and an explicit model, and must note requested versus observed model, rounds used, fallbacks, and quota consumed per agent as printed by `commons quota --json`.