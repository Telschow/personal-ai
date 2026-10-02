# Career Operations Mode

This document records the operating cycle, not a personal search strategy.
No search target, sector, compensation figure, or location preference belongs
here: those live in gitignored local configuration.

## Operating Cycle

1. Discover postings from configured sources
2. Evaluate fit against the configured scoring policy
3. Produce application-ready artifacts
4. Review artifacts
5. Apply manually
6. Track applications
7. Learn from results and adjust configuration

Every step is local-first. Nothing is auto-submitted; the operator sends.

## Stop Condition for Feature Work

Feature work on this vertical is complete once:

1. Docker deployment works
2. Discovery works against configured sources
3. Fit evaluation works
4. Tailored document generation works
5. Application lifecycle tracking works
6. Reporting works
7. Real postings have been discovered end to end
8. Real application packets can be generated
9. The operator can apply manually

After that, effort goes into applying rather than into another development
sprint. New feature work requires an explicitly scoped new phase.

## Deferred

Deferred items are not committed work. They are parked until the stop condition
above is met and a new phase is opened:

- a second vertical (see `docs/FLAT_SEARCH_FUTURE.md`)
- generalized MCP / tool expansion
- large discovery-provider expansion
- any autonomous submission path (permanently out of scope, not merely parked)
- speculative AI features
- nonessential refactors

## Hard Boundaries

These are not roadmap items and are not subject to the stop condition:

- **No auto-submit.** Validation hard-blocks `auto_submit` and `auto_publish`.
- **No scraping behind a login or with cookie access.**
- **No personal data in the repository.** Profiles, company lists, reports, and
  supporting documents are gitignored.
- **No provider secrets in the repository.** Tokens come from environment
  variables or gitignored local config.
