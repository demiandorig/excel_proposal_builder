---
name: Deterministic AI tests
description: Keep automated tests independent from configured AI credentials and live model responses.
---

Tests for deterministic classification or transformation must stub model clients and avoid inheriting configured provider credentials.

**Why:** An ambient API key caused a supposedly deterministic audience test to call a live model, making a rejected ambiguous input produce a supported category and an inconsistent test result.

**How to apply:** In unit tests, patch provider constructors and response helpers; clear provider-key environment variables unless the test explicitly covers an integration path.

For operational diagnosis, distinguish a broken credential/API connection from a slow full research request. A small synthetic request can check provider connectivity; then a synthetic end-to-end service call can measure the real feature path. Do not put live calls in the automated test suite.

**Why:** The OpenAI Responses API and web-search tool were reachable, while complete research prompts exceeded short per-request limits.

**How to apply:** Use non-sensitive synthetic inputs for a one-off live check, record only success/error and duration, and keep regular tests fully stubbed.

Every OpenAI client should set the shared request timeout and retry policy explicitly, and each route's overall deadline should leave room for the provider call and post-processing.

**Why:** Different AI steps had unrelated short deadlines, while revision clients silently inherited the SDK's much longer default.

**How to apply:** When adding an AI path, audit both its client configuration and the endpoint wrapper; keep the shared constants and regression tests aligned.
