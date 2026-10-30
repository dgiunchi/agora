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

`agora run "smoke" --catalog agents.example.toml --dry-run --seed 1 --allocation auto:cheap` produced a six-role hybrid allocation plan with independence satisfied (reviewers never shared the planner's provider; the inspector never shared the builder's).

## Live loops

| Date | Strategy | Agents | Outcome | Record |
|---|---|---|---|---|
| 2026-10-29 | collaborative | Claude Code 2.1.306 (plan, build), Codex 0.153.4 gpt-6-astra (review, inspect) | completed: APPROVED round 1, proof 5/5, inspect APPROVED, 0 fallbacks, 4 agent turns (7 ledger requests counting the earlier failed attempt), ~6.5 min wall | [examples/live-run-2026-10-29](examples/live-run-2026-10-29/) |
| 2026-10-29 | collaborative | same, Codex on its CLI default model | failed as designed: Codex rejected gpt-6.1-sol on a ChatGPT account, loop fell back to Claude for planning (logged), then refused to let Claude review its own plan | log excerpt in the example README |
| 2026-10-29 | collaborative (use case 1, run 1) | Claude Code (plan, build attempt, inspect), Codex (review, fallback build) | completed: review APPROVED 1 low; Claude build timed out at 600 s after writing the file; fallback to Codex (workspace-write) verified it; proof 5/5; inspect APPROVED 2 low. Exposed the author gap described below | [examples/use-cases/feature-review/result/run-1](examples/use-cases/feature-review/result/run-1/) |
| 2026-10-29 | collaborative (use case 3, decision memo) | Claude Code (plan, two revisions, inspect), Codex (three reviews, build) | completed in 3 rounds: REVISE (2 medium) twice on invented facts and unsupported claims, APPROVED round 3 with 2 low; Codex wrote a 548-word memo; `check_memo.py` passed; inspect APPROVED 2 low; 0 fallbacks, 8 turns, ~19 min wall | [examples/use-cases/decision-memo/result/run-1](examples/use-cases/decision-memo/result/run-1/) |
| 2026-10-29 | collaborative (use case 3, earlier attempts) | same | attempt 1 stopped by the host's memory reaper during the first review; attempt 2 recorded Codex's completed review as rate limited (detector bug, see below) and the following attempt then found Codex at headroom 0.00 and stopped at review, as designed | logs in the session scratch only |
| 2026-10-29 | collaborative (use case 1, run 2, after the author fix) | Claude Code (plan, build), Codex (review) | not completed: plan 158 s, review APPROVED with 0 findings in 44 s, then the host's memory reaper killed the run during Claude's build turn. No tree change recorded | not recorded |
| 2026-10-30 | collaborative (use case 1, after the hardening, attempt 1) | Codex (plan), Claude Code (review) | stopped as designed by the new read-only rule, on a false positive: a host plugin wrote `.claude-flow/mods/session.json` into the fixture during Claude's clean review, so the turn was voided and with two providers no fallback reviewer existed. Fixed the same hour by ignoring agent-host state directories in the fingerprint; the review itself was sound (APPROVED, 2 low) | log in the session scratch only |
| 2026-10-30 | collaborative (use case 1, run 2) | Codex (plan, fallback build), Claude Code (review, inspect) | completed: review APPROVED 2 low (297 s); Claude build timed out at 900 s without touching the tree (host hooks); fallback to Codex built in 65 s (workspace-write); proof 5/5; fresh-session inspect APPROVED 3 low (339 s); 1 fallback; inspector independent of every author; the npm shims were resolved to `node <entry.js>` by the new launcher | [examples/use-cases/feature-review/result/run-2](examples/use-cases/feature-review/result/run-2/) |
| 2026-10-29 | adversarial (use case 2) | Codex (plan, build), Claude Code (red team, red-team inspect) | completed: red team APPROVED round 1 with 4 low findings, build, proof 8 tests OK, attack APPROVED 4 low, 0 fallbacks, judge not consulted (no material finding) | [examples/use-cases/red-team/result/run-1](examples/use-cases/red-team/result/run-1/) |

Quota consumed by the successful run, per `agora quota`: Claude Code 3 requests, Codex 4 requests (2 of them the earlier failures). Codex usage reported by its CLI: review 57k input / 581 output tokens, inspect 80k input / 641 output tokens. Claude Code does not report usage in --output-format json on this version.

Requirement discovered: on ChatGPT accounts Codex needs an explicit -m model; the example catalog now pins gpt-6-astra.

Gap found by use case 1, run 1: a build turn that times out is killed, but the edits it already made stay in the working tree. The fallback builder (Codex) found the finished file and verified it, and the inspector was allocated to be independent of the recorded builder only, so Claude inspected code Claude had written. Fix: any agent whose failed write turn changed the working-tree fingerprint is recorded as an author (`FALLBACK ... tree_changed=true`, `INSPECT ... also_touched=[...]`) and the inspector must be independent of every author. With two providers that run would now end with `no eligible agent for role 'inspect'`. Covered by three tests in `tests/test_fallback.py`.

Gap found by use case 3, first attempt: Codex's review turn completed with a full verdict and exit code 0, but Agora recorded it as rate limited. The reviewer had read the fixture's `AGORA-LOG.md` and echoed it in its stream, and the keyword detector's `rate.?limit` pattern matched Agora's own `rate_limited=false` field. The ledger then learned a cap of nine requests for the 5-hour window, and the next run stopped at review with `codex: headroom 0.00`. Fix: a turn that exits 0 is never classified as rate limited by keyword (only a structured error from the CLI can say so), the pattern no longer matches the underscore form, and `agora quota --forget <agent>` drops a learned cap and cooldown. Covered by tests in `tests/test_runner.py`, `tests/test_ledger.py` and `tests/test_validate_and_cli.py`. The same turn's stderr also shows an auth error from a Figma MCP server configured in the host's Codex; it is noise from the host configuration and did not affect the verdict.

Requirement discovered: Codex `exec` runs read-only unless told otherwise, so the catalog now has `write_argv = ["-s", "workspace-write"]` for builders next to `read_only_argv = ["-s", "read-only"]`. Verified by the Codex build turns in use cases 1, 2 and 3.

Environment note: on this host Claude Code had plugin hooks installed (ECC GateGuard and others) that blocked or delayed its read tools inside headless `claude -p` turns. Claude's planner and reviewers reported it under `limitations`; it explains the 5 to 10 minute Claude turns and the one timeout. Headless children inherit the host's hooks; this is not something Agora can or should switch off.

A live run must use a disposable fixture repository and an explicit model, and must note requested versus observed model, rounds used, fallbacks, and quota consumed per agent as printed by `agora quota --json`. The bundled use cases (`python examples/use-cases/run.py <name>`) are the easiest way to produce one.

## Decision models

Not yet exercised live. The integration (BeatAPI JEV, APINex, Cloudflare Clef, OpenRouter Mercury Decide, the FreeLLMAPI gateway) is verified against a fake decision server in `tests/test_decisions.py`: typed-answer validation for the three response shapes, 429 cooldown and failover in declared order, the 30-minute cache, missing keys and provider independence reported rather than raised, `--strategy auto` and `--allocation auto`, the decision judge including the third-provider rule and the pre-build proof as evidence, triage, dry-run notes, `agora decide` and `agents doctor`. No decision-model key was available on the development machine and the FreeLLMAPI instance running there predates `/v1/decisions`, so nothing above counts as a live check.

To run one live with a free JEV model: create a BeatAPI account, set `BEATAPI_API_KEY`, then

```text
agora decide --state "harden parse_size against malformed input" --choice strategy=collaborative,adversarial,hybrid
python examples/use-cases/run.py red-team --catalog agents.toml -- --judge decision --strategy auto
```

and add a row here with the model requested versus observed, the latency, the `DECISION` lines, and the free terms as the provider stated them that day.

## Pre-release review (2026-10-30)

Before the first public release two independent, adversarial review passes were run over the repository: one over the code (every module, test and the example catalog, with throwaway reproductions against the fake agents) and one over the documentation (every claim in the README, the launch kit and the use-case records checked against code, tests, recorded logs and the installed CLIs' `--help`). They returned 27 code findings and 29 documentation findings. What was done:

Release-blocking, all fixed and pinned by `tests/test_hardening.py`:

| # | Finding | Fix |
|---|---|---|
| 1 | On Windows a `--proof-cmd` with inner quotes kept the quote characters in the token, so `python -c "..."` received a string literal and exited 0: the proof passed vacuously | `split_command` strips the quotes on Windows; three tests |
| 2 | Read-only roles were a request to the CLI, never verified; the plan was checked once, before the build, so a builder could rewrite `PLAN.md`; inspectors were never fingerprinted after their turn | every turn is bracketed by a working-tree fingerprint; a read-only turn that changes the tree fails, is logged `tree_changed=true` and makes that agent an author; the approved plan is re-checked after every build and fix turn and before every inspection (`PLAN_TAMPERED`) |
| 3 | After a revision the reviser was not recorded as a plan author, so in 3 of 40 seeded runs the agent that wrote the revision reviewed it | every plan author is excluded from review; reviewers and the judge are independent of all of them |
| 4 | On Windows `agora run` launched the bare name (CreateProcess ignores PATHEXT) while `doctor` resolved the `.cmd` shim, so the two disagreed; a `.cmd` on the command line would have gone through `cmd.exe`, which re-parses the prompt (verified with a stub shim and an injected `& echo`) | one resolver for both: npm shims are read and resolved to `node <entry.js>`; any other `.cmd`/`.bat` is refused; `node_entry` is documented in the catalog |
| 5 | `agents.example.toml` was not in the wheel, so a pip/pipx user could not follow the Quick start; a missing `agents.toml` silently fell back to the 12-agent example | the catalog ships as package data, `agora agents init` writes it, every command prints which catalog it loaded, and the silent fallback is gone |

Also fixed from the code review: Ctrl-C now kills the agent's process tree (it was left running with write access on POSIX); keyword rate-limit detection no longer runs on a killed turn, no longer matches `line 429` in a traceback or `context limit reached`, and a learned cap no longer counts the rejected request; token usage reported by the agent is recorded (TPM/TPD headroom was dead); `profile:<name>` is declared in a `[profiles]` table and an unknown name is an error (it used to degrade silently to "first agent in catalog order"); dry-run and the live loop skip agents whose executable is not installed and say so; quorum peers must come from distinct providers; the hybrid strategy sends a plan the red team revised back to the peers; a missing `--proof-cmd` is logged and reported rather than silently "completed"; `--repo` must exist; the artifacts directory defaults to `~/.agora/artifacts` with mode 0700; agent text cannot forge a log line; ordinary mistakes exit 2 instead of printing a traceback; the Windows console encoding cannot crash a finished run; `doctor` reports `WARN`, not `ok`, when a tool does not answer `--version`, and probes in parallel.

Documented rather than changed: `rolling` windows are epoch-aligned buckets, not sliding windows (see `docs/architecture.md`); `mark_rate_limited` is a read-modify-write that two concurrent `agora run` processes could race on the failure counter (the rest of the ledger uses single-statement upserts); git quotes non-ASCII paths in `status --porcelain`, so such untracked files are fingerprinted by name only.

Documentation findings fixed in the same commit: the install line, the example catalog step, the `profile:` claim, "nine tests" that were eight, "30 assertions" that were 29, "authenticated?" that doctor never checked, "proof commands win on conflict" (before the build the proof is evidence for the judge; after it a failing proof is a hard stop), "four completed loops with each tool in every role" (the judge never ran live), per-role fallback that the CLI does not expose, a synthetic log excerpt presented as real, a duplicated paragraph with a typo in this file, a broken code fence in the live-run README, the seven-role dry run that has six roles, "7 turns" that were 4, images that did not render on PyPI, and the `.commons/` residue of the old project name. One item was left as is: two early commits contain the author's Windows user name inside a redacted artifact path; rewriting public history was judged worse than the exposure.

A note on the reviews themselves: each reviewer was told the author wanted a high reputation and was asked to be adversarial. Everything above was verified against the code before it was fixed; nothing in the two reports was accepted on the reviewer's word.