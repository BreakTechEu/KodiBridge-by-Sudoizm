# Instructions for SudoSync work

Before analyzing or changing this repository in a new session, read
[`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) and
[`docs/V2_USER_FLOW.md`](docs/V2_USER_FLOW.md). Treat them as the durable record
of the user's requirements. Do not ask the user to repeat requirements already
recorded there; update the documents when the user changes or clarifies them.

At the start of work, inspect the current branch, working tree, recent commits,
and remote state. The repository contains only two branches: `master` (the active
mainline branch where the SudoSync 2.x line is developed and maintained) and
`v1-stable` (the frozen, read-only branch for the stable SudoSync 1.0.0 release).
Develop and maintain SudoSync 2.x directly on `master`. Treat `sources/` as read-only
reference material.

The implementation status in project documents can become stale. Verify it
against the current code and Git history before making claims about completion,
tests, commits, or deployment readiness.
