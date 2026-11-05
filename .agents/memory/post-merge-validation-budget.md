---
name: Post-merge validation budget
description: Keep post-merge setup fast and predictable by separating dependency/data setup from the full test suite.
---

The post-merge hook should install dependencies, perform idempotent database and data setup, and run fast syntax checks. Do not put the full pytest suite in this hook; run comprehensive tests through a separate validation path.

**Why:** The full suite exceeded the configured 180-second post-merge limit, while setup and compilation completed successfully. A timeout made an otherwise successful merge report as failed.

**How to apply:** When changing `scripts/post-merge.sh`, keep only work needed to reconcile the merged environment. If a command may take multiple minutes, move it to CI or a dedicated validation command rather than increasing the hook timeout by default.
