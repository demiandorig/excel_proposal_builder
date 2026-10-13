"""
Gamma — "Create in Gamma": the Step 08 outline is pushed to Gamma's public API, which builds the presentation in
the planner's Gamma workspace; the app then just hands back the link and the planner finishes the deck in Gamma
by hand (Gamma has no way to edit a deck from outside, and most decks need edits in the platform anyway).

Same "the app runs fine without it" convention as Drive / Notion / OpenAI: without GAMMA_API_KEY nothing here
is called and the button falls back to copying the outline for pasting into Gamma.

Env (the key is a Replit Secret / .env value, never pasted into chat or code):
  GAMMA_API_KEY            required. Needs a Gamma plan with API access (Pro, Ultra, Teams or Business).
  GAMMA_THEME_ID           optional. A brand theme to apply (from Gamma's theme list).
  GAMMA_FOLDER_ID          optional. A Gamma folder to put the decks in.
  GAMMA_WORKSPACE_ACCESS   optional, default "edit". What the rest of the workspace can do with a new deck
                           (edit | comment | view | noAccess | fullAccess, any capitalisation) — the key
                           belongs to one person, so without this the planners who didn't create it couldn't
                           open the deck. A value that isn't one of those is logged and treated as "view"
                           (never silently more access than was asked for).

API (https://developers.gamma.app): POST {base}/generations -> {generationId}; GET {base}/generations/{id}
-> {status: pending|completed|failed, gammaUrl, credits, error}. Each deck spends Gamma credits.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import requests

log = logging.getLogger(__name__)

_API_BASE = "https://public-api.gamma.app/v1.0"
_TIMEOUT = 20  # seconds — a planner is waiting on the button
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,100}$")
# gamma.app or a workspace subdomain of it, https only, no port / userinfo, and nothing a browser could read as a
# different host (a backslash, whitespace) — the match is on the WHOLE string, not on what a URL parser makes of it.
_GAMMA_URL = re.compile(r"https://(?:[a-z0-9-]+\.)*gamma\.app(?:/[^\s\\]*)?", re.IGNORECASE)
_ACCESS_LEVELS = {name.lower(): name for name in ("edit", "comment", "view", "noAccess", "fullAccess")}
_DEFAULT_ACCESS = "edit"

# Gamma writes the slides from the outline; these keep it from inventing the parts a client will read as fact.
_INSTRUCTIONS = (
    "This is an Entravision media-plan presentation for a prospective advertiser. Keep every dollar amount, "
    "option total, flight date, geography, target and product name exactly as written in the outline; do not "
    "invent statistics, results, testimonials or client details that are not in it. Suggested flow: title, client "
    "and campaign, strategy, the media plan (one slide per option when there are several), add-ons, "
    "considerations, next steps."
)


class GammaError(Exception):
    """A Gamma call that failed, with a message a planner can act on."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _api_key() -> str:
    return os.environ.get("GAMMA_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(_api_key())


def valid_generation_id(generation_id: str) -> bool:
    return bool(_ID_PATTERN.match(generation_id or ""))


def _safe_gamma_url(url) -> Optional[str]:
    """The deck link only if it really is a gamma.app page — it is handed to the browser to open."""
    text = url if isinstance(url, str) else ""
    return text if _GAMMA_URL.fullmatch(text) else None


def _headers() -> dict:
    return {"X-API-KEY": _api_key(), "Content-Type": "application/json", "Accept": "application/json"}


def _explain(resp: "requests.Response") -> GammaError:
    detail = ""
    try:
        body = resp.json()
        detail = body.get("message") or (body.get("error") or {}).get("message") or ""
    except Exception:  # noqa: BLE001
        detail = (resp.text or "")[:200]
    if resp.status_code in (401, 403):
        return GammaError("Gamma rejected the API key — check GAMMA_API_KEY and that the Gamma plan includes API access.", 502)
    if resp.status_code == 402:
        return GammaError("Gamma says the workspace is out of credits." + (f" ({detail})" if detail else ""), 502)
    if resp.status_code == 429:
        return GammaError("Gamma is rate-limiting requests — wait a minute and try again.", 429)
    return GammaError(f"Gamma returned {resp.status_code}." + (f" {detail}" if detail else ""), 502)


def _request_payload(outline: str, title: Optional[str]) -> dict:
    wanted = os.environ.get("GAMMA_WORKSPACE_ACCESS", "").strip()
    access = _ACCESS_LEVELS.get(wanted.lower()) if wanted else _DEFAULT_ACCESS
    if access is None:
        log.warning("GAMMA_WORKSPACE_ACCESS=%r isn't one of %s — using 'view'", wanted, sorted(_ACCESS_LEVELS.values()))
        access = "view"
    payload: dict = {
        "inputText": outline,
        "textMode": "generate",
        "format": "presentation",
        "additionalInstructions": _INSTRUCTIONS,
        "sharingOptions": {"workspaceAccess": access},
    }
    if title and title.strip():
        payload["title"] = title.strip()[:500]
    if os.environ.get("GAMMA_THEME_ID", "").strip():
        payload["themeId"] = os.environ["GAMMA_THEME_ID"].strip()
    if os.environ.get("GAMMA_FOLDER_ID", "").strip():
        payload["folderId"] = os.environ["GAMMA_FOLDER_ID"].strip()
    return payload


def start_generation(outline: str, title: Optional[str] = None) -> str:
    """Ask Gamma to build a presentation from `outline`; returns the generation id to poll. Raises GammaError."""
    try:
        resp = requests.post(f"{_API_BASE}/generations", headers=_headers(), json=_request_payload(outline, title), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise GammaError(f"Couldn't reach Gamma ({exc.__class__.__name__}). Try again in a moment.") from exc
    if resp.status_code >= 400:
        raise _explain(resp)
    try:
        body = resp.json()
    except ValueError:
        body = None
    generation_id = body.get("generationId") if isinstance(body, dict) else None
    if not generation_id or not valid_generation_id(str(generation_id)):
        raise GammaError("Gamma accepted the request but didn't return a generation id.")
    return str(generation_id)


def get_generation(generation_id: str) -> dict:
    """{"status": "pending"|"completed"|"failed", "gamma_url": str|None, "error": str|None}. Raises GammaError."""
    if not valid_generation_id(generation_id):
        raise GammaError("That isn't a Gamma generation id.", 400)
    try:
        resp = requests.get(f"{_API_BASE}/generations/{generation_id}", headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise GammaError(f"Couldn't reach Gamma ({exc.__class__.__name__}). Try again in a moment.") from exc
    if resp.status_code >= 400:
        raise _explain(resp)
    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise GammaError("Gamma returned something unexpected.")
    status = str(body.get("status") or "pending").lower()
    error = body.get("error")
    if isinstance(error, dict):
        error = error.get("message") or "Gamma couldn't build the deck."
    if status == "completed":
        url = _safe_gamma_url(body.get("gammaUrl"))
        if url is None:
            # Built, but with no link we'd hand to a browser: say so rather than polling a finished deck forever.
            return {"status": "failed", "gamma_url": None,
                    "error": "Gamma finished the deck but didn't return a link this app can open — look for it in your Gamma workspace."}
        return {"status": "completed", "gamma_url": url, "error": None}
    return {
        "status": "failed" if status == "failed" else "pending",
        "gamma_url": None,
        "error": (str(error) if error else "Gamma couldn't build the deck.") if status == "failed" else None,
    }
