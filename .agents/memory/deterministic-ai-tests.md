---
name: Deterministic AI tests
description: Keep automated tests independent from configured AI credentials and live model responses.
---

Tests for deterministic classification or transformation must stub model clients and avoid inheriting configured provider credentials.

**Why:** An ambient API key caused a supposedly deterministic audience test to call a live model, making a rejected ambiguous input produce a supported category and an inconsistent test result.

**How to apply:** In unit tests, patch provider constructors and response helpers; clear provider-key environment variables unless the test explicitly covers an integration path.
