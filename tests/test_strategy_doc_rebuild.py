"""
Covers POST /api/strategy/{doc_token}/rebuild — added for Step 03's
tactic-card checkboxes (planner deselects a recommended tactic they
disagree with; the downloadable .docx should reflect that). Calls the
endpoint function directly with a real temp PROPOSALS_DIR (docx_builder
needs no DB at all, so this is a genuine, if narrow, end-to-end check —
not mocked) rather than through the full FastAPI app + auth middleware,
which isn't what changed here.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from docx import Document

from app import main


@pytest.fixture
def proposals_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROPOSALS_DIR", tmp_path)
    return tmp_path


def _seed_doc(proposals_dir, doc_token: str):
    """Mimics what /api/strategy already does on first generation — a
    real .docx + its metadata json, exactly what /rebuild expects to find."""
    doc_path = proposals_dir / f"strategy_{doc_token}.docx"
    from app.services import docx_builder
    docx_builder.build_strategy_brief_docx(
        output_path=doc_path,
        title="Acme Corp",
        client_summary="Acme sells widgets.",
        market_context="Local market context.",
        objectives_analysis="Objectives.",
        strategy_summary="Strategy.",
        recommended_tactics=[
            {"product_family": "CTV / OTT", "rationale": "r1", "data_point": "d1",
             "citation": "c1", "entravision_advantage": "a1", "suggested_budget_pct": 60},
            {"product_family": "Social", "rationale": "r2", "data_point": "d2",
             "citation": "c2", "entravision_advantage": "a2", "suggested_budget_pct": 40},
        ],
        key_insights=["insight 1"],
    )
    (proposals_dir / f"strategy_{doc_token}.json").write_text(
        json.dumps({"path": str(doc_path), "filename": "Acme_Strategy_Brief.docx"})
    )
    return doc_path


def _docx_text(path) -> str:
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def test_rebuild_overwrites_the_same_file_with_only_selected_tactics(proposals_dir):
    doc_token = "tok123"
    doc_path = _seed_doc(proposals_dir, doc_token)
    assert "CTV / OTT" in _docx_text(doc_path)
    assert "Social" in _docx_text(doc_path)

    body = main.StrategyDocRebuildRequest(
        request={"client_name": "Acme Corp"},
        client_summary="Acme sells widgets.",
        market_context="Local market context.",
        objectives_analysis="Objectives.",
        strategy_summary="Strategy.",
        # Social deselected — only CTV/OTT sent, mirroring what
        # app.js's _selectedTactics() would send.
        recommended_tactics=[
            {"product_family": "CTV / OTT", "rationale": "r1", "data_point": "d1",
             "citation": "c1", "entravision_advantage": "a1", "suggested_budget_pct": 60},
        ],
        key_insights=["insight 1"],
    )
    result = asyncio.run(main.rebuild_strategy_doc(doc_token, body))

    assert result == {"rebuilt": True}
    # SAME path, not a new file — the existing download link keeps working.
    assert doc_path.exists()
    text = _docx_text(doc_path)
    assert "CTV / OTT" in text
    assert "Social" not in text


def test_rebuild_404s_for_an_unknown_token(proposals_dir):
    with pytest.raises(Exception) as exc_info:
        asyncio.run(main.rebuild_strategy_doc("does-not-exist", main.StrategyDocRebuildRequest(request={})))
    assert getattr(exc_info.value, "status_code", None) == 404
