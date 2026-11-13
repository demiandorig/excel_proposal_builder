"""Shared pieces for the tests that run the REAL planner page (templates/index.html + static/app.js) against the REAL route functions of app/main.py.

`Backend` is the proposals table in memory plus an answer for every /api/** call the page makes: `my_proposals`, `reopen_proposal`, `save_draft`,
`duplicate_defaults`, `duplicate_proposal`, `delete_draft`, `me`, `get_catalog`, `suggest_tabs` and `preview_disclaimers` are the real route functions (called with a fake request, the way the
other route tests do); only what talks to PostgreSQL is replaced (`_get_proposal_metadata`, `_insert_draft_if_absent`, `_save_proposal_metadata`, the
list query and the connection `delete_draft` deletes through). The save keeps `duplicated_from` and drops `duplicate_notes`, the way the real upsert's CASE does.
Anything else the page asks for is a 404 the test can see in `backend.unplanned`.

    backend = Backend(source_row(...))
    backend.install(monkeypatch)
    with dom.serve({"/": INDEX}) as base:
        ...
        page.context_.route("**/api/**", backend.route)            # context level: a tab opened by a link is answered too

Playwright's sync handlers run inside a running event loop, so the async route functions are driven from a worker thread (`run_async`).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import HTTPException
from pydantic import ValidationError

from app import main
from app.services import proposal_list as proposal_list_svc
from tests import dom_support as dom

PLANNER = "planner@entravision.com"
INDEX = (dom.APP / "templates" / "index.html").read_text(encoding="utf-8")

_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def run_async(coro):
    return _POOL.submit(asyncio.run, coro).result()


def fake_request(email: str = PLANNER, *, admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user={"email": email, "is_admin": admin}), client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})


def jsonb(value):
    """What Postgres's JSONB hands back: the same data with every object's keys re-ordered. Nothing may depend on key order."""
    def order(pairs):
        return dict(sorted(pairs, key=lambda kv: (len(kv[0].encode("utf-8")), kv[0].encode("utf-8"))))
    return json.loads(json.dumps(value, default=str), object_pairs_hook=order)


def table_row(proposal_id: str, state: dict, *, client: str, title: str, owner: str = PLANNER, status: str = "draft", notion: str = "EVC-48213",
              minutes_ago: int = 60, summary: dict | None = None, seller: str = "ana@entravision.com", requested_by: str = "Ana Torres") -> dict:
    """A row of the proposals table as the real queries return it (timestamps are datetimes, the JSON columns are dicts)."""
    return {
        "proposal_id": proposal_id, "client_name": client, "seller_email": seller, "created_by_email": owner, "requested_by": requested_by, "notion_id": notion,
        "proposal_title": title, "filename": None, "email_doc_filename": None, "pptx_net_filename": None, "pptx_gross_filename": None,
        "generated_at": None if status == "draft" else datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        "requester_ip": "127.0.0.1", "requester_user_agent": "pytest", "summary": summary if summary is not None else {}, "reopen_state": jsonb(state),
        "status": status, "updated_at": datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    }


def shaped(key: str) -> str:
    """"POST /api/proposal/abc/duplicate" -> "POST /api/proposal/{id}/duplicate" (the draft save has no id in its path)."""
    return re.sub(r"^(\w+ /api/proposal/)(?!draft(?:$|/))[^/]+", r"\1{id}", key)


class _Connection:
    """The one thing delete_draft does with the database: `DELETE FROM proposals WHERE proposal_id = %s`."""

    def __init__(self, backend: "Backend"):
        self.backend = backend

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        assert sql.strip().upper().startswith("DELETE FROM PROPOSALS"), sql
        self.backend.rows.pop(params[0], None)
        return self


class Backend:
    def __init__(self, *rows: dict, email: str = PLANNER, admin: bool = False):
        self.rows: dict[str, dict] = {r["proposal_id"]: r for r in rows}
        self.email, self.admin = email, admin
        self.calls: list[tuple[str, str]] = []              # (method, path) of every request, in order
        self.bodies: dict[str, list] = {}                    # "POST /api/proposal/draft" -> [parsed bodies]
        self.unplanned: list[tuple[str, str]] = []
        self.fail: dict[str, tuple] = {}                     # "POST /api/proposal/draft" or "GET /api/proposal/{id}/reopen" -> (status, detail): answered instead of calling the route
        self.delay: dict[str, float] = {}

    # ------------------------------------------------------------------ what replaces the database
    def install(self, monkeypatch) -> "Backend":
        monkeypatch.setattr(main, "_get_proposal_metadata", self._get)
        monkeypatch.setattr(main, "_insert_draft_if_absent", self._insert)
        monkeypatch.setattr(main, "_save_proposal_metadata", self._save)
        monkeypatch.setattr(main, "_query_proposals", self._query)
        monkeypatch.setattr(main, "get_connection", lambda: _Connection(self))
        return self

    def _get(self, proposal_id):
        row = self.rows.get(proposal_id)
        return main._proposal_metadata_from_row(copy.deepcopy(row)) if row else None

    def _insert(self, **kw) -> bool:
        if kw["proposal_id"] in self.rows:
            return False
        self.rows[kw["proposal_id"]] = {
            **kw, "reopen_state": jsonb(kw["reopen_state"]), "status": "draft", "summary": {}, "filename": None, "email_doc_filename": None,
            "pptx_net_filename": None, "pptx_gross_filename": None, "generated_at": None, "updated_at": datetime.now(timezone.utc),
        }
        return True

    def _save(self, *, proposal_id, reopen_state, status="generated", summary=None, **kw) -> None:
        old = self.rows.get(proposal_id)
        state = jsonb(reopen_state)
        lineage = (old or {}).get("reopen_state", {}).get("duplicated_from")
        if lineage is not None:                              # schema.sql's upsert: the lineage survives every save, the one-shot notes do not (they are not in the new state)
            state["duplicated_from"] = lineage
        self.rows[proposal_id] = {**(old or {}), **kw, "proposal_id": proposal_id, "reopen_state": state, "status": status, "summary": summary or {},
                                  "updated_at": datetime.now(timezone.utc)}

    def _query(self, *, mine_email, search, page, page_size, status="all", sort="updated", direction=None, admin=False) -> dict:
        mine = [r for r in self.rows.values() if (r.get("created_by_email") or "").lower() == (mine_email or "").lower()]
        words = [w.lower() for w in (search or "").split()]
        hay = lambda r: " ".join(str(r.get(k) or "") for k in ("client_name", "seller_email", "requested_by", "notion_id", "proposal_title")).lower()   # noqa: E731
        found = [r for r in mine if all(w in hay(r) for w in words)]
        facets = {"all": len(found), "draft": sum(r["status"] == "draft" for r in found), "generated": sum(r["status"] != "draft" for r in found)}
        if status in ("draft", "generated"):
            found = [r for r in found if (r["status"] == "draft") == (status == "draft")]
        found.sort(key=lambda r: r["updated_at"], reverse=True)
        chunk = found[(page - 1) * page_size: page * page_size]
        out = []
        for r in chunk:
            state = r.get("reopen_state") or {}
            req = state.get("request") if isinstance(state.get("request"), dict) else {}
            tiers = state.get("tiers") if isinstance(state.get("tiers"), list) else []
            facts = {"start_raw": req.get("start_date"), "end_raw": req.get("end_date"), "line_count": sum(len(t.get("line_items") or []) for t in tiers) or len(state.get("line_items") or []),
                     "tier_count": len(tiers) or None, "time_unit_raw": state.get("time_unit"), "step_raw": state.get("wizard_step"), "dup_raw": state.get("duplicated_from"),
                     "has_state": bool(state)}
            out.append(proposal_list_svc.shape_row(main._proposal_metadata_from_row(copy.deepcopy(r)), facts, admin=admin))
        return {"proposals": out, "count": len(out), "total_count": len(found) if out else 0, "page": page, "page_size": page_size,
                "total_pages": max(1, -(-len(found) // page_size)), "facets": facets, "applied": {"status": status, "sort": sort, "dir": direction or "desc"}}

    # ------------------------------------------------------------------ what answers the page
    def request(self):
        return fake_request(self.email, admin=self.admin)

    def answer(self, method: str, url: str, body: dict | None) -> tuple[int, object]:
        parts = urlsplit(url)
        path, query = unquote(parts.path), {k: v[-1] for k, v in parse_qs(parts.query).items()}
        key = f"{method} {path}"
        self.calls.append((method, path))
        shape = shaped(key)
        self.bodies.setdefault(shape, []).append(body)
        if key in self.fail or shape in self.fail:
            status, detail = self.fail.get(key) or self.fail[shape]
            return status, {"detail": detail}
        req = self.request()
        try:
            if key == "GET /api/catalog":
                return 200, run_async(main.get_catalog())
            if key == "GET /api/me":
                return 200, run_async(main.me(req))
            if key == "GET /api/my-proposals":
                params = {k: v for k, v in query.items() if k in ("search", "status", "sort", "dir")}
                params.update({k: int(query[k]) for k in ("page", "page_size") if k in query})
                return 200, run_async(main.my_proposals(req, **params))
            if key == "POST /api/suggest-tabs":                # Step 08's two pure (no AI) questions about the current plan
                return 200, run_async(main.suggest_tabs(main.SuggestTabsRequest(**body)))
            if key == "POST /api/disclaimers/preview":
                return 200, run_async(main.preview_disclaimers(main.DisclaimerPreviewRequest(**body)))
            if key == "POST /api/proposal/draft":
                return 200, run_async(main.save_draft(main.DraftSaveRequest(**body), req))
            found = re.fullmatch(r"(GET|POST|DELETE) /api/proposal/([^/]+)(?:/(reopen|duplicate-defaults|duplicate))?", key)
            if found:
                verb, proposal_id, tail = found.groups()
                if verb == "GET" and tail == "reopen":
                    return 200, run_async(main.reopen_proposal(proposal_id))
                if verb == "GET" and tail == "duplicate-defaults":
                    return 200, run_async(main.duplicate_defaults(proposal_id, req))
                if verb == "POST" and tail == "duplicate":
                    return 200, run_async(main.duplicate_proposal(proposal_id, main.DuplicateProposalRequest(**body), req))
                if verb == "DELETE" and tail is None:
                    return 200, run_async(main.delete_draft(proposal_id, req))
        except HTTPException as exc:
            return exc.status_code, {"detail": exc.detail}
        except ValidationError as exc:                       # the 422 FastAPI answers for a body that fails its model
            return 422, {"detail": [{"loc": ["body", *err["loc"]], "msg": err["msg"], "type": err["type"]} for err in exc.errors()]}
        self.unplanned.append((method, path))
        return 404, {"detail": f"not planned: {key}"}

    def route(self, route):
        """A Playwright route handler for `**/api/**`."""
        import time
        req = route.request
        wait = next((s for k, s in self.delay.items() if k == f"{req.method} {urlsplit(req.url).path}"), 0)
        if wait:
            time.sleep(wait)
        body = None
        if req.post_data:
            try:
                body = json.loads(req.post_data)
            except ValueError:
                body = None
        status, payload = self.answer(req.method, req.url, body)
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, default=str))

    # ------------------------------------------------------------------ small readers for the tests
    def sent(self, key: str) -> list:
        """The bodies the page sent to e.g. "POST /api/proposal/draft" or "POST /api/proposal/{id}/duplicate"."""
        return list(self.bodies.get(key, []))

    def copies(self, source_id: str) -> list[dict]:
        """The rows that are copies of `source_id`."""
        return [r for r in self.rows.values() if (r["reopen_state"].get("duplicated_from") or {}).get("proposal_id") == source_id]


def new_id() -> str:
    return secrets.token_urlsafe(16)


# ---------------------------------------------------------------------------------------------------------------------------------- the page

IGNORED_CONSOLE = ("Failed to load resource",)               # the browser's own line for a 4xx / 5xx the test chose to answer
INTENDED_LOGS = ("ProposalHistory:", "DuplicateProposal:")     # one line per failed request, on purpose


def _watch(page, errors: list, dialogs: list) -> None:
    def on_console(msg):
        if msg.type == "error" and not msg.text.startswith(IGNORED_CONSOLE + INTENDED_LOGS):
            errors.append(msg.text)

    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", on_console)
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))


def open_app(browser, base: str, backend: Backend, path: str = "/", *, width: int = 1280, height: int = 900, timezone_id: str = "UTC"):
    """The real planner page with every /api/** call answered by `backend` (also in a tab the page opens: the routes live on the context).
    `page.errors` collects uncaught exceptions and console errors (of every tab), `page.dialogs` any native alert / confirm that opened."""
    context = browser.new_context(viewport={"width": width, "height": height}, timezone_id=timezone_id, locale="en-US")
    context.set_default_timeout(dom.TIMEOUT_MS)
    context.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    context.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    context.route("**/api/**", backend.route)
    errors: list[str] = []
    dialogs: list[str] = []
    context.on("page", lambda p: _watch(p, errors, dialogs))
    page = context.new_page()
    page.errors, page.dialogs, page.context_ = errors, dialogs, context      # type: ignore[attr-defined]
    page.goto(base + path)
    return page


def close_app(page) -> None:
    """Closes the context and fails when any tab threw or a native dialog opened."""
    errors, dialogs = list(page.errors), list(page.dialogs)
    page.context_.close()
    assert errors == [], errors
    assert dialogs == [], f"a native confirm()/alert() opened: {dialogs}"
