# Instructions for SudoSync work

Before analyzing or changing this repository in a new session, read
[`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md) and
[`docs/V2_USER_FLOW.md`](docs/V2_USER_FLOW.md). Treat them as the durable record
of the user's requirements. Do not ask the user to repeat requirements already
recorded there; update the documents when the user changes or clarifies them.

At the start of work, inspect the current branch, working tree, recent commits,
and remote state. Work on `v2/rewrite-2026-10-10` unless the user explicitly
directs otherwise. Never modify `master` or `v1-stable`, merge a pull request,
or publish a release without explicit user approval. Treat `sources/` as
read-only reference material.

The implementation status in project documents can become stale. Verify it
against the current code and Git history before making claims about completion,
tests, commits, or deployment readiness.
