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
| 2026-10-29 | collaborative (use case 3, decision memo) | Claude Code (plan, two revisions, inspect), Codex (three reviews, build) | completed in 3 rounds: REVISE (2 medium) twice on invented facts and unsupported claims, APPROVED round 3 with 2 low; Codex wrote a 548-word memo; `check_memo.py` passed; inspect APPROVED 2 low; 0 fallbacks, 8 turns, ~19 min wall | [examples/use-cases/decision-memo/result/run-1](examples/use-cases/decision-memo/result/run-1/) |
| 2026-10-29 | collaborative (use case 3, earlier attempts) | same | attempt 1 stopped by the host's memory reaper during the first review; attempt 2 recorded Codex's completed review as rate limited (detector bug, see below) and the following attempt then found Codex at headroom 0.00 and stopped at review, as designed | logs in the session scratch only |
| 2026-10-29 | collaborative (use case 1, run 2, after the author fix) | Claude Code (plan, build), Codex (review) | not completed: plan 158 s, review APPROVED with 0 findings in 44 s, then the host's memory reaper killed the run during Claude's build turn. No tree change recorded | not recorded |
| 2026-10-29 | adversarial (use case 2) | Codex (plan, build), Claude Code (red team, red-team inspect) | completed: red team APPROVED round 1 with 4 low findings, build, proof 8 tests OK, attack APPROVED 4 low, 0 fallbacks, judge not consulted (no material finding) | [examples/use-cases/red-team/result/run-1](examples/use-cases/red-team/result/run-1/) |

Quota consumed by the successful run, per gora quota: Claude Code 3 requests, Codex 4 requests (2 of them the earlier failures). Codex usage reported by its CLI: review 57k input / 581 output tokens, inspect 80k input / 641 output tokens. Claude Code does not report usage in --output-format json on this version.

Requirement discovered: on ChatGPT accounts Codex needs an explicit -m model; the example catalog now pins gpt-6-astra.

Gap found by use case 1, run 1: a build turn that times out is killed, but the edits it already made stay in the working tree. The fallback builder (Codex) found the finished file and verified it, and the inspector was allocated to be independent of the recorded builder only, so Claude inspected code Claude had written. Fix: any agent whose failed write turn changed the working-tree fingerprint is recorded as an author (`FALLBACK ... tree_changed=true`, `INSPECT ... also_touched=[...]`) and the inspector must be independent of every author. With two providers that run would now end with `no eligible agent for role 'inspect'`. Covered by three tests in `tests/test_fallback.py`.

Gap found by use case 3, first attempt: Codex's review turn completed with a full verdict and exit code 0, but Agora recorded it as rate limited. The reviewer had read the fixture's `AGORA-LOG.md` and echoed it in its stream, and the keyword detector's `rate.?limit` pattern matched Agora's own `rate_limited=false` field. The ledger then learned a cap of nine requests for the 5-hour window, and the next run stopped at review with `codex: headroom 0.00`. Fix: a turn that exits 0 is never classified as rate limited by keyword (only a structured error from the CLI can say so), the pattern no longer matches the underscore form, and `agora quota --forget <agent>` drops a learned cap and cooldown. Covered by tests in `tests/test_runner.py`, `tests/test_ledger.py` and `tests/test_validate_and_cli.py`. The same turn's stderr also shows an auth error from a Figma MCP server configured in the host's Codex; it is noise from the host configuration and did not affect the verdict.

Requirement discovered: Codex `exec` runs read-only unless told otherwise, so the catalog now has `write_argv = ["-s", "workspace-write"]` for builders next to `read_only_argv = ["-s", "read-only"]`. Verified by the Codex build turns in use cases 1 and 2.

Environment note: on this host Claude Code had plugin hooks installed (ECC GateGuard and others) that blocked or delayed its read tools inside headless `claude -p` turns. Claude's planner and reviewers reported it under `limitations`; it explains the 5 to 10 minute Claude turns and the one timeout. Headless children inherit the host's hooks; this is not something Agora can or should switch off.

A live run must use a disposable fixture repository and an explicit model, and must note requested versus observed model, rounds used, fallbacks, and quota consumed per agent as printed by gora quota --json.

A live run must use a disposable fixture repository and an explicit model, and must note requested versus observed model, rounds used, fallbacks, and quota consumed per agent as printed by `agora quota --json`. The bundled use cases (`python examples/use-cases/run.py <name>`) are the easiest way to produce one.