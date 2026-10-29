# Decision memo: move our three services into one monorepo?

Date: 2026-10-29 | Audience: six-person engineering team | Status: proposed

## Context

The given facts are six engineers and three services. The following are working assumptions to confirm before deciding: each service currently has its own repository; an API, background worker, and web front end share a client library and schema; most feature work spans services, requiring coordinated pull requests; duplicated lint, CI, and tooling configurations drift; and client versions become skewed. We also assume all six engineers share ownership and contribute across all services, with identical repository access. If coupling or duplication is low, or ownership and access differ, the case weakens and the decision must be reopened.

## Options

- **A. Keep three repos:** Add a shared tooling package and a cross-repo release checklist.
- **B. Full monorepo:** Combine all three services with one CI pipeline and path-based test selection.
- **C. Hybrid:** Merge the assumed tightly coupled API and worker; keep the front end separate.

## Criteria

Scores are the author's judgement under these assumptions, not measurements: 1-5, with 5 best, including lower cost, effort, time, and risk. Coordination and drift carry the most weight.

| Option | Cross-service change cost | CI and build time | Migration effort | Ownership and onboarding clarity | Tooling drift risk |
|---|---|---|---|---|---|
| A | 2 | 5 | 5 | 3 | 2 |
| B | 5 | 3 | 2 | 4 | 5 |
| C | 4 | 4 | 3 | 3 | 3 |

## Recommendation

We recommend Option B, conditional on confirming that most feature work spans services and that tooling and CI configuration are duplicated. One reviewable change set should reduce recurring coordination costs; shared configuration should reduce drift. Migration requires upfront effort, while path filters and caching may contain CI time. Under the shared ownership and access assumption, separate repositories do not reflect ownership boundaries; if that assumption fails, this reason does not hold.

A addresses drift but keeps cross-service reviews split. C helps the API-worker pair but leaves front-end coordination across repositories. A monorepo simplifies coordinated code changes and reviews; it does not itself coordinate deployments. Services will deploy separately and must remain compatible across versions during rollouts.

If validation does not support these assumptions, we will withdraw this recommendation, rescore using measured data, and reissue the memo.

## Risks

- Slower or flaky CI: use path triggers, caching, and monitor runtime.
- Lost history or incorrect permissions: trial history-preserving imports and review access.
- Broken main branch affects more work: require checks and prompt reverts.
- Tooling learning curve and lock-in: retain existing tools initially.
- Shared dependencies break consumers despite passing local checks: validate consumer test coverage before cutover.
- Cutover disruption: announce a one-day freeze in advance.

## Next steps

1. Team lead, by Nov 3: validate assumptions using last quarter's pull requests, compare CI and lint duplication, and check shared ownership and access restrictions.
2. Team, by Nov 5: confirm Option B only if assumptions hold; otherwise rescore and reissue this memo.
3. Migration owner, by Nov 12: trial importing all repositories with history preserved.
4. CI owner, by Nov 19: validate path triggers, ensuring shared library and schema changes run every consumer's tests.
5. Release owner, following week: announce the one-day freeze and cut over only after CI validation passes.
6. Team lead: review coordination costs and CI performance 30 days after cutover.
