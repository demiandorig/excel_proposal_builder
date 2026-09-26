---
name: Git push authorization
description: Distinguish Replit Git Providers, the GitHub API connector, and shell Git credentials.
---

Replit's account-level Git Providers authorization, a project-level GitHub API connector, and the credentials used by shell `git push` are separate access paths. Successful repository reads through the connector do not establish that either the connector or shell credentials can write.

**Why:** In this workspace, GitHub ref reads succeeded while API writes returned 403 and shell HTTPS pushes rejected the current credential. These failures came from different routes and could not be fixed by treating them as one connection.

**How to apply:** Identify whether the failing operation is in the Git pane, a GitHub API connector, or shell Git. Reconnect the matching authorization path, and verify the exact remote ref before and after any write. For a history rewrite, keep a recovery point and use a force-with-lease tied to the observed remote SHA.