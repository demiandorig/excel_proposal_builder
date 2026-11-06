"""Progress and duplicate-request safeguards for the planner's AI actions."""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")


def _function(name: str) -> str:
    match = re.search(rf"^(?:async )?function {re.escape(name)}\(", APP_JS, re.M)
    assert match, f"function {name} not found"
    return APP_JS[match.start():APP_JS.index("\n}\n", match.start()) + 2]


def test_long_ai_actions_have_accessible_progress_messages():
    for element_id in ("strategy-loading", "roadblocks-loading", "generate-loading"):
        markup = re.search(rf'<div id="{element_id}"[^>]*>', INDEX_HTML)
        assert markup, f"missing progress element {element_id}"
        assert 'role="status"' in markup.group()
        assert 'aria-live="polite"' in markup.group()

    for element_id in ("email-revision-status", "gamma-reprompt-status"):
        markup = re.search(rf'<(?:div|p)[^>]*id="{element_id}"[^>]*>', INDEX_HTML)
        assert markup and 'role="status"' in markup.group()
        assert 'aria-live="polite"' in markup.group()


def test_each_expensive_ai_action_has_a_same_proposal_request_guard():
    assert "_strategyInFlightEpoch === epoch" in _function("onStrategyGenerate")
    assert "_generateInFlightEpoch === epoch" in _function("onGenerate")
    assert "_emailRevisionInFlightEpoch === epoch" in _function("_submitEmailReprompt")
    assert "_gammaRevisionInFlightEpoch === epoch" in _function("onGammaOutlineReprompt")

    assert "if (_rbBusy() || !state.parsed) return;" in _function("onRoadblocksGenerate")
    assert "if (samePick && (_rbBusy() || settled)) return;" in _function("onRvPick")


def test_strategy_revisions_show_progress_and_ignore_duplicate_clicks():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")

    source = _function("onStrategyGenerate") + "\n" + _function("_setStrategyRequestBusy")
    script = f"""
let _resetEpoch = 4, _strategyRunToken = 0, _strategyInFlightEpoch = null;
let state = {{ parsed: {{client_name: "Acme"}}, strategyBrief: null, timeUnit: "month" }};
const els = {{}};
function el(id) {{
  return els[id] || (els[id] = {{
    textContent: "", disabled: false, style: {{}}, hidden: true,
    classList: {{
      add(c) {{ if (c === "hidden") this.owner.hidden = true; }},
      remove(c) {{ if (c === "hidden") this.owner.hidden = false; }},
    }},
  }});
}}
const document = {{ getElementById: el }};
["strategy-loading", "strategy-loading-text", "strategy-regenerate-btn", "strategy-new-mix-btn", "reprompt-submit-btn"].forEach(el);
// Give the fake classList access to its owning element.
Object.values(els).forEach(item => {{ item.classList.owner = item; }});
function _resetStrategyUI() {{ el("strategy-loading").classList.add("hidden"); }}
function _adPresenceToCarry() {{ return null; }}
function _readJsonResponse(response) {{ return response.json(); }}
function renderStrategyBrief(brief) {{ state.strategyBrief = brief; }}
function _showStrategyError() {{}}
let calls = 0, finish;
function fetch() {{
  calls++;
  return new Promise(resolve => {{ finish = () => resolve({{json: () => Promise.resolve({{strategy_summary: "done"}})}}); }});
}}
{source}
(async () => {{
  const first = onStrategyGenerate("add a local angle");
  const duplicate = onStrategyGenerate("duplicate request");
  const whileRunning = {{
    calls,
    progressVisible: !el("strategy-loading").hidden,
    progressText: el("strategy-loading-text").textContent,
    retryDisabled: el("strategy-regenerate-btn").disabled,
  }};
  finish();
  await Promise.all([first, duplicate]);
  console.log(JSON.stringify({{
    whileRunning,
    callsAfter: calls,
    progressHiddenAfter: el("strategy-loading").hidden,
    retryEnabledAfter: !el("strategy-regenerate-btn").disabled,
    rendered: state.strategyBrief && state.strategyBrief.strategy_summary,
  }}));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scenario.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout.strip())
    assert observed["whileRunning"]["calls"] == 1
    assert observed["whileRunning"]["progressVisible"] is True
    assert "Revising the strategy for Acme" in observed["whileRunning"]["progressText"]
    assert observed["whileRunning"]["retryDisabled"] is True
    assert observed["progressHiddenAfter"] is True
    assert observed["retryEnabledAfter"] is True
    assert observed["rendered"] == "done"


def test_email_revision_disables_both_paths_and_keeps_failure_retry_visible():
    submit = _function("_submitEmailReprompt")
    busy_helper = _function("_setEmailRevisionBusy")
    assert '["email-reprompt-submit-btn", "email-refresh-figures-btn"]' in busy_helper
    assert "Revising ${scope" in submit
    assert "You can try again." in submit
    assert "failureMessage" in submit
    assert "if (_emailRevisionInFlightEpoch === epoch) return;" in submit

    gamma = _function("onGammaOutlineReprompt")
    assert "Revising the Gamma outline…" in gamma
    assert "You can try again." in gamma
    assert "if (_gammaRevisionInFlightEpoch === epoch) return;" in gamma
