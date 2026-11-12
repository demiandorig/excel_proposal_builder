"""How long the app waits for an AI step.

Enrichment at 60 s was timing out on real proposals ("AI enrichment exceeded the 60.0-second time limit; the workbook was generated without optional
enrichment"), so every whole-step budget in app/main.py is 120 s. One model call (llm_utils.OPENAI_REQUEST_TIMEOUT_SECONDS) stays shorter than all of
them: a slow model then fails with its own "timed out" message instead of the generic outer one, and a step that has to ask twice (a cut-off answer is
asked again at twice the size) is still capped by the outer budget.
"""
from __future__ import annotations

import ast
from pathlib import Path

import app.main as main
from app.services import llm_utils

ROOT = Path(__file__).resolve().parents[1]
STEP_BUDGETS = {
    "_GENERATE_ENRICHMENT_TIMEOUT_SECONDS": main._GENERATE_ENRICHMENT_TIMEOUT_SECONDS,
    "_STRATEGY_REQUEST_TIMEOUT_SECONDS": main._STRATEGY_REQUEST_TIMEOUT_SECONDS,
    "_ROADBLOCKS_TOTAL_TIMEOUT_SECONDS": main._ROADBLOCKS_TOTAL_TIMEOUT_SECONDS,
    "_AI_REVISION_TIMEOUT_SECONDS": main._AI_REVISION_TIMEOUT_SECONDS,
}


def test_every_whole_ai_step_may_take_two_minutes():
    assert STEP_BUDGETS == {name: 120.0 for name in STEP_BUDGETS}


def test_one_model_call_is_shorter_than_every_whole_step_so_a_slow_model_names_itself():
    one_call = llm_utils.OPENAI_REQUEST_TIMEOUT_SECONDS
    assert one_call == 110.0
    for name, budget in STEP_BUDGETS.items():
        assert one_call + 5 <= budget, f"{name} ({budget} s) must leave room above one model call ({one_call} s)"


def test_the_enrichment_message_tells_the_planner_the_limit_that_applies():
    import asyncio
    import time

    async def slow():
        return await main._generate_enrichment_with_timeout(_request())

    original = main.ai_enricher.enrich_proposal
    main.ai_enricher.enrich_proposal = lambda *a, **k: time.sleep(0.2)
    saved, main._GENERATE_ENRICHMENT_TIMEOUT_SECONDS = main._GENERATE_ENRICHMENT_TIMEOUT_SECONDS, 0.01
    try:
        result = asyncio.run(slow())
    finally:
        main.ai_enricher.enrich_proposal = original
        main._GENERATE_ENRICHMENT_TIMEOUT_SECONDS = saved
    assert "0.01-second time limit" in result.error and result.error_category == "timeout"      # the text follows the constant, never a typed number


def _request():
    from app.services.notion_parser import ProposalRequest
    return ProposalRequest(client_name="Acme")


def test_no_openai_client_in_the_app_types_its_own_timeout():
    """Every client takes llm_utils.OPENAI_REQUEST_TIMEOUT_SECONDS (directly or through a module alias): a literal number would quietly
    bring back a shorter limit for one step."""
    offenders = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in {"OpenAI", "_OpenAI", "AsyncOpenAI"}:
                continue
            for kw in node.keywords:
                if kw.arg == "timeout" and isinstance(kw.value, ast.Constant):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} timeout={kw.value.value!r}")
    assert offenders == [], offenders
