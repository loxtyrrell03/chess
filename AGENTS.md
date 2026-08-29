# Repository guidance

## Milestone documentation

- Agents must update this `AGENTS.md` after every meaningful, verified milestone and include that update in the same milestone commit.
- Record concise, durable context: important behavior or architecture changes, decisions and their rationale, relevant tests or verification, deployment or runtime state, and material limitations or follow-up work.
- Update or replace stale guidance instead of accumulating contradictory history; keep notes factual and useful to future agents.
- Do not record secrets, credentials, personal data, raw transcripts, routine command logs, or transient debugging noise.


## Scope

These instructions apply to the entire repository unless a more specific `AGENTS.md` exists deeper in the tree.

## Working practices

- Read the README and existing build or test configuration before changing behavior.
- Keep changes focused and preserve unrelated user or agent work.
- Do not commit credentials, local machine configuration, generated caches, build outputs, or large runtime data unless the repository explicitly tracks them.
- Prefer maintainable source changes over edits to generated artifacts.

## Verification

- Run the smallest relevant tests, checks, or build for each change and report anything that could not be verified.
- Keep durable architecture, workflow, and deployment decisions in this file when they will help future work.

## Git milestones

- At each meaningful working milestone, inspect the diff, stage only relevant files, commit with a clear message, and push to the configured remote.
- Do not rewrite shared history or force-push unless the user explicitly requests it.
