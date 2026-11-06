---
name: Git pane sync conflicts
description: Resolving conflicts created by Replit's Git pane Sync Changes action.
---

When Sync Changes starts a rebase, check the current rebase state rather than assuming the panel's error is stale. In this workspace, the rebase replayed commits onto the unchanged `origin/main` and stopped on an overlapping `app/main.py` change. The correct resolution kept both the incoming roadblocks timeout helpers and the existing Constanza registration call.

**Why:** The source conflict was resolvable without dropping either feature, but shell Git staging was denied by a runtime permission check. Do not bypass that check with a dangerous override.

**How to apply:** Inspect the rebase todo and conflict, merge both intended code paths, validate the file, then use the Git pane to mark it resolved and complete the rebase. Never skip a substantive commit merely to clear the panel.
