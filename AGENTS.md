# Instructions for SudoSync work

Before analyzing or changing this repository in a new session, read
[`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) and
[`docs/V2_USER_FLOW.md`](docs/V2_USER_FLOW.md). Treat them as the durable record
of the user's requirements. Do not ask the user to repeat requirements already
recorded there; update the documents when the user changes or clarifies them.

At the start of work, inspect the current branch, working tree, recent commits,
and remote state. Develop on `v2/rewrite-2026-10-10` unless the user explicitly
directs otherwise. `master` is the intended v2 mainline/destination;
`v1-stable` is the stable SudoSync 1.0.0 line and must remain read-only. Integrate v2 into
`master` through the agreed review path, and do not merge a pull request or
publish a release without explicit user approval. Treat `sources/` as read-only
reference material.

The implementation status in project documents can become stale. Verify it
against the current code and Git history before making claims about completion,
tests, commits, or deployment readiness.
