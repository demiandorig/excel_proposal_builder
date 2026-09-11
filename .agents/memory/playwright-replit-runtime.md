---
name: Playwright browser runtime
description: Runtime requirements for browser automation in this Replit project.
---

Browser automation should prefer Replit's managed Chromium executable instead of the downloaded Playwright headless shell.

**Why:** The downloaded browser can lack access to Nix-provided shared libraries such as Chromium's GBM and NSS dependencies, while Replit's managed wrapper includes the correct runtime setup.

**How to apply:** When launching Playwright Chromium in the app, use the managed executable when available and pass through the workspace's Nix library paths; keep the downloaded browser as a fallback.