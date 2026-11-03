from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from app import main


def test_slow_optional_enrichment_falls_back_without_blocking_generation(monkeypatch, caplog):
    monkeypatch.setattr(main, "_GENERATE_ENRICHMENT_TIMEOUT_SECONDS", 0.01)

    def slow_enrichment(request, *args, **kwargs):
        time.sleep(0.05)
        return main.ai_enricher.ProposalEnrichment(campaign_name="Late model result")

    monkeypatch.setattr(main.ai_enricher, "enrich_proposal", slow_enrichment)
    request = SimpleNamespace(client_name="Acme")

    result = asyncio.run(main._generate_enrichment_with_timeout(request, [], "0001"))

    assert result.campaign_name == "Acme"
    assert result.internal_email_body == ""
    assert "time limit" in result.error
    assert "stage=enrichment timed_out=true" in caplog.text
