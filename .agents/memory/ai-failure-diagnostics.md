---
name: Safe AI failure diagnostics
description: Shared error categories and non-sensitive provider failure logs for AI features.
---

Every AI feature that calls OpenAI should classify provider failures through the shared safe categories and show category-specific, non-sensitive guidance. Diagnostic logs may include the provider request ID, feature, stage, elapsed time, and category, but never exception text, prompts, model responses, or credentials.

**Why:** SDK exception messages and parse failures can contain provider response details or other request context; raw errors are not safe or consistent user guidance.

**How to apply:** Use the shared classifier and message mapping in `llm_utils` for new OpenAI paths. Keep tests deterministic by using synthetic provider exceptions and never make real model calls in automated tests.
