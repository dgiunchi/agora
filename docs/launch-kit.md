# Launch kit

Ready-to-paste announcement drafts. Every claim below is backed by `README.md`, `VALIDATION.md` and `examples/use-cases/*/result/`; update the numbers if the record changes before posting.

## One line

Agora runs the coding agents you already have (Claude Code, Codex, Gemini CLI, Aider, ...) through a bounded plan / review / build / inspect loop where no agent approves its own work, and spends only the free tiers and subscriptions you already pay for.

## Show HN (title under 80 characters)

**Show HN: Agora – free coding agents review each other's work, on their own quotas**

I stacked two ideas: FreeLLMAPI's quota router (track every free tier, fail over on 429, pick by a bandit) and Claudex Loop's cross-provider review (one model plans, a different provider reviews, a fresh session inspects the diff). Agora treats whole agentic systems the way FreeLLMAPI treats models: a catalog with quota shapes, a SQLite ledger, cooldowns, and a Thompson-sampling allocator that fills each role with the best eligible agent that is not from the provider it has to check.

Strategies: collaborative (peer review with a quorum), adversarial (red team + judge, proof commands win every conflict), hybrid. Approvals are bound to the plan's SHA256; inspections to a fingerprint of the working tree. Fallback on a rate limit or timeout is explicit and logged, never silent.

It is stdlib Python, 90-odd tests on fake agents that spend no quota, CI on three OSes. Two of the three bundled use cases have run live with Claude Code and Codex, and the first live run found a real gap: a timed-out builder had already written the code, and the fallback builder's "independent" inspector turned out to be the original author. That is fixed and tested now, and the unedited logs are in the repo, including the failure.

Honest limits: it needs headless CLIs, only two providers have been exercised live so far, and a free tier late in the day may not be the smartest model. The log always says who did what.

https://github.com/dgiunchi/agora

## Reddit (r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA)

Title: I made Claude Code and Codex review each other's work in a loop, on the subscriptions I already have, with a quota router so it never dies on a 429

Body: see the Show HN text; add the live-run table from the README and the `assets/loop.svg` image. End with the three one-line commands from "Three use cases, end to end".

## X / LinkedIn thread

1. Your coding agents all come with a free or subscription quota. Alone each one is a toy. Together they are a review board. Meet Agora.
2. One agent plans. A different provider reviews it with evidence. The builder implements. A proof command runs. A fresh session of yet another provider inspects the diff. Nobody approves their own work.
3. A quota ledger and a bandit pick who fills each role, under the caps you actually have. Rate-limited mid-turn? Explicit, logged fallback to the next eligible agent.
4. Three strategies: collaborative, adversarial (red team + judge, proof wins), hybrid. Approvals bound to the plan hash.
5. Ran live with Claude Code and Codex. The first run found a bug in my own inspection rule. It is fixed, tested, and the log of the failure is in the repo.
6. Stdlib Python, zero runtime deps, 90-odd tests that spend no quota. MIT. github.com/dgiunchi/agora

## Where to post, in order

1. GitHub Discussions or issues of the two parent projects, as a thank-you with a link (they are credited in the README).
2. Show HN, on a weekday morning US time.
3. r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA, one at a time, a day apart.
4. The awesome lists for Claude Code and for Codex (one PR each, following their format).
5. X / LinkedIn thread with `assets/hero.svg` rendered to PNG.
6. A short screen recording of `python examples/use-cases/run.py red-team` as the pinned asset.

## Before posting

- Finish the pending live run of use case 3 and record it, or keep the README's honest "pending" note.
- Check CI is green on the commit you link.
- Publish to PyPI (`pip install agora-loop` or similar; `agora` itself is likely taken) so the install line is one command.
