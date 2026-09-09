---
name: Post-merge Python installs
description: Environment constraint for Python dependency setup in Replit post-merge hooks.
---

Post-merge Python dependency installation must be non-interactive and explicitly account for Nix's externally managed Python environment.

**Why:** A plain pip install can be rejected by the externally managed environment, causing an otherwise valid post-merge setup to fail before tests run.

**How to apply:** Keep hook installs non-interactive and use pip's supported override for externally managed environments, then run the project's compile and test checks.