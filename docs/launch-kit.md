# Launch kit

Ready-to-paste announcement drafts. Every claim below is backed by `README.md`, `VALIDATION.md` and `examples/use-cases/*/result/`; update the numbers if the record changes before posting.

## One line

Agora runs the coding agents you already have (Claude Code, Codex, Gemini CLI, Aider, ...) through a bounded plan / review / build / inspect loop where no agent approves its own work, and spends only the free tiers and subscriptions you already pay for.

## Show HN (title under 80 characters)

**Show HN: Agora – free coding agents review each other's work, on their own quotas**

I stacked two ideas: FreeLLMAPI's quota router (track every free tier, fail over on 429, pick by a bandit) and Claudex Loop's cross-provider review (one model plans, a different provider reviews, a fresh session inspects the diff). Agora treats whole agentic systems the way FreeLLMAPI treats models: a catalog with quota shapes, a SQLite ledger, cooldowns, and a Thompson-sampling allocator that fills each role with the best eligible agent that is not from the provider it has to check.

Strategies: collaborative (peer review with a quorum), adversarial (red team + judge; after the build a failing proof command stops the loop whatever the judge said), hybrid. Approvals are bound to the plan's SHA256; inspections to a fingerprint of the working tree. Fallback on a rate limit or timeout is explicit and logged, never silent.

It is stdlib Python, 135 tests on fake agents that spend no quota, CI on three OSes. Before posting this I had two independent adversarial review passes run over the code and the docs; they found 56 things, five of them release-blocking (one was a Windows quoting bug that let a quoted proof command pass vacuously). All fixed with tests, and the list of what they found is in VALIDATION.md. All three bundled use cases (a feature with tests, a red-teamed parser, and a decision memo with a structure checker, because the deliverable does not have to be code) have run live with Claude Code and Codex. Two of those runs found real bugs in Agora: a timed-out builder had already written the code and the fallback's "independent" inspector was the original author; and the rate-limit detector matched Agora's own log line when a reviewer echoed it. Both are fixed and tested, and the unedited logs are in the repo, failures included.

Honest limits: it needs headless CLIs, only two providers have been exercised live so far, and a free tier late in the day may not be the smartest model. The log always says who did what.

https://github.com/dgiunchi/agora

## Reddit (r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA)

Title: I made Claude Code and Codex review each other's work in a loop, on the subscriptions I already have, with a quota router so it never dies on a 429

Body: see the Show HN text; add the live-run table from the README and the `assets/loop.svg` image. End with the three one-line commands from "Three use cases, end to end".

## X / LinkedIn thread

1. Your coding agents all come with a free or subscription quota. Alone each one is a toy. Together they are a review board. Meet Agora.
2. One agent plans. A different provider reviews it with evidence. The builder implements. A proof command runs. A fresh session of yet another provider inspects the diff. Nobody approves their own work.
3. A quota ledger and a bandit pick who fills each role, under the caps you actually have. Rate-limited mid-turn? Explicit, logged fallback to the next eligible agent.
4. Three strategies: collaborative, adversarial (red team + judge; a failing proof after the build always stops the loop), hybrid. Approvals bound to the plan hash, every turn bracketed by a tree fingerprint.
5. Ran live with Claude Code and Codex. The first run found a bug in my own inspection rule. It is fixed, tested, and the log of the failure is in the repo.
6. Stdlib Python, zero runtime deps, 135 tests that spend no quota, two adversarial reviews before launch. Decision models (BeatAPI's free JEV, Cloudflare Clef, ...) can pick the strategy and arbitrate the red team as a cheap third provider. MIT. github.com/dgiunchi/agora

## Where to post, in order

1. Done 2026-10-30: Show and tell in FreeLLMAPI, https://github.com/tashfeenahmed/freellmapi/discussions/1776. Claudex Loop has no Discussions, and its issue tracker is not the place for an announcement.
2. Show HN, on a weekday morning US time.
3. r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA, one at a time, a day apart.
4. The awesome lists, each on its own terms (see below): awesome-claude-code only through its web issue form and not before 2026-11-12; awesome-ai-agents through its Google form, since its pull-request queue is not being merged.
5. X / LinkedIn thread with `assets/hero.svg` rendered to PNG.
6. A short screen recording of `python examples/use-cases/run.py red-team` as the pinned asset.

## Awesome-list entries

- awesome-claude-code (hesreallyhim): no pull requests and no `gh` submissions; a human fills the issue form at https://github.com/hesreallyhim/awesome-claude-code/issues/new?template=recommend-resource.yml. A project must be 14 days old with commits after day one, or have 100 stars, so not before 2026-11-12. One-line description, no pitch, no emoji: `Runs Claude Code, Codex and other headless CLIs through a plan / review / build / inspect loop where a different provider always reviews, with a quota ledger and explicit fallback.`
- awesome-ai-agents (e2b-dev): the README says pull request or form, but contributor pull requests are not being merged (checked 2026-10-30). Use the form at https://forms.gle/UXQFCogLYrPFvfoUA. Category `Coding, Multi-agent`; description: `Multi-agent review loop over the free tiers you already have: one coding agent plans, a different provider reviews, a fresh session inspects, with a quota ledger and explicit fallback.`

## Before posting

- Check CI is green on the commit you link.
- PyPI: done. v0.1.0 was published on 2026-10-30 from the GitHub release through trusted publishing (`.github/workflows/publish.yml`, environment `pypi`, no token stored); `pipx install agora-loop` is the install line in the README. Every later GitHub release publishes the same way: bump `version` in `pyproject.toml`, tag, publish the release.
- GitHub social preview: Settings > General > Social preview, upload a 1280x640 PNG of `assets/hero.svg` (render it in any browser and screenshot; GitHub does not accept SVG there).
