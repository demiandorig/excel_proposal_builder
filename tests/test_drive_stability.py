"""
Stability of "Upload to Google Drive" (the intermittent HTTP 500 teammates reported, which "fixed itself after
generating again").

What is pinned here:
  * ROOT CAUSE: the workbook is written to the local disk of ONE Autoscale instance, so the upload (another request,
    maybe another instance) found no file -> FileNotFoundError -> bare 500. Now the bytes are also stored in the shared
    database at Generate time and the upload reads them from there (local disk is only the legacy fallback).
  * Every Google call retries transient failures (429 / 5xx / rate-limit 403 / timeouts / resets / a dropped database
    connection) with bounded exponential backoff + jitter, and does NOT retry auth / permission / quota / bad requests.
  * Folder get-or-create: the name is escaped properly (apostrophe, backslash, injection-shaped text), races between
    concurrent uploads create ONE folder, the oldest match wins, a lost reply doesn't duplicate the folder or the file.
  * The token is refreshed once under a lock and cached; a transient refresh failure is retried (and never mistaken
    for "re-authorize"); a revoked grant still leads to the consent flow.
  * Every failure is mapped to a status code / plain-language message / `retryable` flag; nothing leaks as a bare 500.
  * The heavy work runs off the event loop.
  * The front end (onDriveUpload) retries retryable failures with a visible "Retrying upload...", ignores a second
    click, and doesn't touch the page after a New Proposal reset (run in Node when it is installed).

Google is never called: a fake Drive v3 service (with a real parser for the `q` search syntax) stands in for it.
What a fake cannot prove is listed in the work report: real Google behaviour (see the module-level notes in
app/services/drive_uploader.py).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httplib2
import psycopg
import pytest
from google.auth import exceptions as gauth_exc
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError

from app import main
from app.services import drive_uploader as du
from app.services import proposal_files

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
SCHEMA_SQL = (ROOT / "schema.sql").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """No real waiting; every backoff the code asked for is recorded. Module caches are reset around each test."""
    recorded: list[float] = []
    monkeypatch.setattr(du, "_sleep", recorded.append)
    monkeypatch.setattr(du, "_rand", lambda: 0.5)
    monkeypatch.setattr(du, "_folder_lock_across_instances", lambda key: contextlib.nullcontext())
    for name, value in (("DRIVE_CLIENT_ID", "cid"), ("DRIVE_CLIENT_SECRET", "secret"), ("DRIVE_ROOT_FOLDER_ID", "ROOT")):
        monkeypatch.setenv(name, value)
    _reset_module_state()
    yield recorded
    _reset_module_state()


def _reset_module_state():
    du._forget_cached_credentials()
    du._folder_cache.clear()
    du._uploads_in_flight.clear()


def http_error(status: int, reason: str = "", message: str = "boom", headers: dict | None = None) -> HttpError:
    info = {"status": str(status), **(headers or {})}
    body = {"error": {"code": status, "message": message}}
    if reason:
        body["error"]["errors"] = [{"reason": reason, "message": message}]
    return HttpError(httplib2.Response(info), json.dumps(body).encode("utf-8"))


class _Req:
    """A Drive request: execute() for plain calls, next_chunk() for resumable uploads (one chunk = done)."""

    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()

    def next_chunk(self):
        return None, self._fn()


def _parse_literal(s: str, i: int):
    """Parse one single-quoted Drive query literal (backslash escapes the next character) starting at s[i]."""
    assert s[i] == "'", f"expected a quote at {i} in {s!r}"
    out, i = [], i + 1
    while i < len(s):
        c = s[i]
        if c == "\\":
            out.append(s[i + 1])
            i += 2
        elif c == "'":
            return "".join(out), i + 1
        else:
            out.append(c)
            i += 1
    raise ValueError("unterminated literal")


def parse_folder_query(q: str):
    """The exact shape _find_folder() builds: name = LIT and mimeType = 'folder' and LIT in parents and trashed = false.
    Anything Drive would reject (an unescaped quote/backslash leaves stray text) raises ValueError, like a 400."""
    assert q.startswith("name = ")
    name, i = _parse_literal(q, len("name = "))
    mid = " and mimeType = 'application/vnd.google-apps.folder' and "
    assert q[i:i + len(mid)] == mid, f"unexpected query shape: {q!r}"
    i += len(mid)
    parent, i = _parse_literal(q, i)
    assert q[i:] == " in parents and trashed = false", f"stray text after the parent literal: {q[i:]!r}"
    return name, parent


class FakeDrive:
    """Just enough of the Drive v3 client for drive_uploader, with scriptable faults."""

    def __init__(self, list_delay: float = 0.0):
        self.lock = threading.RLock()
        self.items: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.faults: dict[str, list] = {}         # op -> exceptions raised INSTEAD of doing the work
        self.after_faults: dict[str, list] = {}   # op -> exceptions raised AFTER the work happened (a lost reply)
        self.shared: list[tuple[str, dict]] = []
        self.list_delay = list_delay
        self._seq = 0

    # --- scripting
    def fail(self, op, *excs):
        self.faults.setdefault(op, []).extend(excs)

    def fail_after(self, op, *excs):
        self.after_faults.setdefault(op, []).extend(excs)

    def _before(self, op, info):
        with self.lock:
            self.calls.append((op, info))
            queue = self.faults.get(op)
            if queue:
                exc = queue.pop(0)
                if exc is not None:
                    raise exc

    def _after(self, op):
        with self.lock:
            queue = self.after_faults.get(op)
            if queue:
                exc = queue.pop(0)
                if exc is not None:
                    raise exc

    def _next(self):
        with self.lock:
            self._seq += 1
            return self._seq

    # --- Drive surface
    def files(self):
        return _Files(self)

    def permissions(self):          # the real client's name
        return _Permissions(self)

    # --- implementations
    def do_list(self, **kw):
        self._before("list", kw)
        name, parent = parse_folder_query(kw["q"])      # raises like Drive's 400 on a malformed query
        with self.lock:
            hits = [it for it in self.items.values()
                    if it["mimeType"] == "application/vnd.google-apps.folder" and it["name"] == name
                    and parent in it["parents"]]
        hits.sort(key=lambda it: it["seq"])
        result = {"files": [{"id": it["id"], "name": it["name"]} for it in hits]}
        if self.list_delay:
            time.sleep(self.list_delay)                  # the state was READ above: a slow reply, so the race is real
        self._after("list")
        return result

    def do_create_folder(self, body, **kw):
        self._before("create_folder", body)
        with self.lock:
            fid = f"folder-{self._next()}"
            self.items[fid] = {**body, "id": fid, "seq": self._seq, "parents": list(body["parents"])}
        self._after("create_folder")
        return {"id": fid}

    def do_create_file(self, body, media, **kw):
        self._before("create_file", body)
        with self.lock:
            fid = body.get("id") or f"file-{self._next()}"
            if fid in self.items:
                raise http_error(409, "alreadyExists", "The provided file ID already exists")
            self.items[fid] = {**body, "id": fid, "seq": self._seq, "parents": list(body["parents"]),
                               "content": media.getbytes(0, media.size())}
        self._after("create_file")
        sheet = body.get("mimeType") == "application/vnd.google-apps.spreadsheet"
        return {"id": fid, "name": body["name"], "mimeType": body.get("mimeType", ""),
                "webViewLink": f"https://docs.google.com/spreadsheets/d/{fid}/edit" if sheet else f"https://drive.example/{fid}"}

    def do_get(self, fileId, **kw):
        self._before("get", {"fileId": fileId})
        it = self.items.get(fileId)
        if it is None:
            raise http_error(404, "notFound", "File not found")
        return {"id": it["id"], "name": it["name"], "mimeType": it.get("mimeType", ""),
                "webViewLink": f"https://docs.google.com/spreadsheets/d/{it['id']}/edit"}

    def do_generate_ids(self, **kw):
        self._before("generate_ids", kw)
        return {"ids": [f"gen-{self._next()}"]}

    def do_permission(self, fileId, body, **kw):
        self._before("permission", body)
        with self.lock:
            self.shared.append((fileId, body))
        return {"id": "perm"}

    # --- convenience for assertions
    def folders(self):
        return [it for it in self.items.values() if it["mimeType"] == "application/vnd.google-apps.folder"]

    def files_only(self):
        return [it for it in self.items.values() if it["mimeType"] != "application/vnd.google-apps.folder"]

    def count(self, op):
        return sum(1 for o, _ in self.calls if o == op)


class _Files:
    def __init__(self, d):
        self.d = d

    def list(self, **kw):
        return _Req(lambda: self.d.do_list(**kw))

    def create(self, body, media_body=None, **kw):
        if media_body is None:
            return _Req(lambda: self.d.do_create_folder(body, **kw))
        return _Req(lambda: self.d.do_create_file(body, media_body, **kw))

    def get(self, fileId, **kw):
        return _Req(lambda: self.d.do_get(fileId, **kw))

    def generateIds(self, **kw):
        return _Req(lambda: self.d.do_generate_ids(**kw))


class _Permissions:
    def __init__(self, d):
        self.d = d

    def create(self, fileId, body, **kw):
        return _Req(lambda: self.d.do_permission(fileId, body, **kw))


@pytest.fixture
def drive(monkeypatch):
    fake = FakeDrive()
    monkeypatch.setattr(du, "_load_credentials", lambda: object())
    monkeypatch.setattr(du, "_build_service", lambda creds: fake)
    return fake


def _upload(content=b"xlsx-bytes", seller="ae@entravision.com", **kw):
    return du.upload_proposal(Path("0001_Acme_Proposal.xlsx"), seller, content=content, proposal_id="p1", **kw)


# ---------------------------------------------------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------------------------------------------------

def _flaky(*failures, then="ok"):
    state = {"calls": 0}

    def fn():
        state["calls"] += 1
        if state["calls"] <= len(failures):
            raise failures[state["calls"] - 1]
        return then

    return fn, state


def test_retries_a_503_then_succeeds(sleeps):
    fn, state = _flaky(http_error(503, "backendError"), http_error(503, "backendError"))
    assert du.call_with_retries(fn, "x") == "ok"
    assert state["calls"] == 3
    assert len(sleeps) == 2


@pytest.mark.parametrize("status,reason", [(403, "forbidden"), (403, "insufficientFilePermissions"),
                                           (403, "storageQuotaExceeded"), (404, "notFound"), (400, "invalid"),
                                           (401, "authError")])
def test_does_not_retry_auth_permission_quota_or_bad_requests(status, reason, sleeps):
    fn, state = _flaky(http_error(status, reason))
    with pytest.raises(HttpError):
        du.call_with_retries(fn, "x")
    assert state["calls"] == 1
    assert sleeps == []


@pytest.mark.parametrize("reason", ["rateLimitExceeded", "userRateLimitExceeded"])
def test_rate_limit_403_is_retried_but_other_403_is_not(reason, sleeps):
    fn, state = _flaky(http_error(403, reason))
    assert du.call_with_retries(fn, "x") == "ok"
    assert state["calls"] == 2


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(status, sleeps):
    fn, state = _flaky(http_error(status))
    assert du.call_with_retries(fn, "x") == "ok"
    assert state["calls"] == 2


@pytest.mark.parametrize("exc", [
    ConnectionResetError(), TimeoutError(), socket.timeout(), ssl.SSLError("handshake"), socket.gaierror("dns"),
    httplib2.ServerNotFoundError("Unable to find the server"), gauth_exc.TransportError("reset"),
    gauth_exc.RefreshError("token endpoint 503", retryable=True), psycopg.OperationalError("server closed"),
    psycopg.InterfaceError("connection is closed"),
])
def test_network_and_database_blips_are_retried(exc, sleeps):
    fn, state = _flaky(exc)
    assert du.call_with_retries(fn, "x") == "ok"
    assert state["calls"] == 2


@pytest.mark.parametrize("exc", [ValueError("bug"), KeyError("x"), FileNotFoundError("gone"),
                                 gauth_exc.RefreshError("invalid_grant: Bad Request",
                                                        {"error": "invalid_grant"})])
def test_other_exceptions_are_not_retried(exc, sleeps):
    fn, state = _flaky(exc)
    with pytest.raises(type(exc)):
        du.call_with_retries(fn, "x")
    assert state["calls"] == 1


@pytest.mark.parametrize("rand", [0.0, 0.5, 1.0])
def test_backoff_is_bounded_exponential_and_jittered(rand, monkeypatch, sleeps):
    monkeypatch.setattr(du, "_rand", lambda: rand)
    fn, state = _flaky(*[http_error(503)] * 10)
    with pytest.raises(HttpError):
        du.call_with_retries(fn, "x")
    assert state["calls"] == du.MAX_ATTEMPTS                    # never more than the cap on attempts
    assert len(sleeps) == du.MAX_ATTEMPTS - 1
    ceilings = [min(du.BACKOFF_CAP_SECONDS, du.BACKOFF_BASE_SECONDS * 2 ** n) for n in range(len(sleeps))]
    for wait, ceiling in zip(sleeps, ceilings):
        assert ceiling / 2 <= wait <= ceiling                   # equal jitter: between half and all of the ceiling
        assert wait <= du.BACKOFF_CAP_SECONDS
    assert sum(sleeps) <= du.BACKOFF_CAP_SECONDS * (du.MAX_ATTEMPTS - 1)


def test_retry_after_is_honoured_but_capped(sleeps):
    fn, _ = _flaky(http_error(429, headers={"retry-after": "120"}), http_error(503, headers={"retry-after": "3"}))
    du.call_with_retries(fn, "x")
    assert sleeps[0] == du.BACKOFF_CAP_SECONDS                  # 120s asked for, capped
    assert sleeps[1] == 3                                       # a small Retry-After beats the jittered 1.5-2s


def test_retries_stop_when_the_time_budget_is_spent(monkeypatch, sleeps):
    clock = iter([0.0, du.RETRY_BUDGET_SECONDS + 1, 999.0, 999.0])
    monkeypatch.setattr(du, "_monotonic", lambda: next(clock))
    fn, state = _flaky(http_error(503), http_error(503))
    with pytest.raises(HttpError):
        du.call_with_retries(fn, "x")
    assert state["calls"] == 1 and sleeps == []


def test_attempts_override(sleeps):
    fn, state = _flaky(*[http_error(503)] * 10)
    with pytest.raises(HttpError):
        du.call_with_retries(fn, "x", attempts=2)
    assert state["calls"] == 2


# ---------------------------------------------------------------------------------------------------------------------
# Query escaping + folder names
# ---------------------------------------------------------------------------------------------------------------------

def test_q_literal_escapes_backslash_before_quote():
    assert du._q_literal("o'brien@x.com") == r"'o\'brien@x.com'"
    assert du._q_literal("a\\b") == "'a\\\\b'"
    assert du._q_literal("a\\'b") == "'a\\\\\\'b'"      # backslash then quote: both escaped, in that order
    assert _parse_literal(du._q_literal("a\\'b"), 0)[0] == "a\\'b"


@pytest.mark.parametrize("email", [
    "o'brien@entravision.com", "back\\slash@x.com", "a'b\\'c@x.com", "x' or name contains '@y.com",
    "plain@entravision.com", "trailing\\", "quote'",
])
def test_folder_lookup_survives_awkward_emails(email):
    fake = FakeDrive()
    folder_id = du._find_or_create_folder(fake, "ROOT", email)
    assert [f["name"] for f in fake.folders()] == [du.normalize_folder_name(email)]     # created once, exact name
    # The query the code sent parses back to EXACTLY that name under ROOT - nothing injected, nothing lost.
    name, parent = parse_folder_query(fake.calls[0][1]["q"])
    assert (name, parent) == (du.normalize_folder_name(email), "ROOT")
    du._folder_cache.clear()
    assert du._find_or_create_folder(fake, "ROOT", email) == folder_id        # found, not re-created
    assert len(fake.folders()) == 1


def test_unescaped_query_would_have_been_a_400():
    """Sanity check of the fake: the OLD escaping (quote only) produces a query Drive rejects for a backslash."""
    old = "name = '%s' and mimeType = 'application/vnd.google-apps.folder' and 'ROOT' in parents and trashed = false" % (
        "trailing\\".replace("'", "\\'"))
    with pytest.raises((ValueError, AssertionError)):
        parse_folder_query(old)


def test_normalize_folder_name():
    assert du.normalize_folder_name("ae@entravision.com") == "ae@entravision.com"          # unchanged: folders keep matching
    assert du.normalize_folder_name("  ae@x.com \n Cc: b@x.com\t") == "ae@x.com Cc: b@x.com"
    assert du.normalize_folder_name("a\x00b​c") == "abc"
    assert du.normalize_folder_name("") == "unassigned"
    assert du.normalize_folder_name(None) == "unassigned"
    assert du.normalize_folder_name(" \n ") == "unassigned"
    assert len(du.normalize_folder_name("x" * 500)) == du._FOLDER_NAME_MAX


def test_oldest_duplicate_folder_wins():
    fake = FakeDrive()
    for seq, fid in ((2, "newer"), (1, "older")):
        fake.items[fid] = {"id": fid, "name": "ae@x.com", "mimeType": "application/vnd.google-apps.folder",
                           "parents": ["ROOT"], "seq": seq}
    assert du._find_or_create_folder(fake, "ROOT", "ae@x.com") == "older"
    assert fake.calls[0][1]["orderBy"] == "createdTime"


# ---------------------------------------------------------------------------------------------------------------------
# Concurrent get-or-create
# ---------------------------------------------------------------------------------------------------------------------

def test_concurrent_uploads_create_one_folder():
    fake = FakeDrive(list_delay=0.03)                 # widen the check-then-create race window
    results, errors = [], []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait()
            results.append(du._find_or_create_folder(fake, "ROOT", "same@seller.com"))
        except Exception as exc:                      # pragma: no cover - would fail the assertions below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(set(results)) == 1
    assert len(fake.folders()) == 1


def test_different_sellers_do_not_block_each_other_or_mix():
    fake = FakeDrive()
    a = du._find_or_create_folder(fake, "ROOT", "a@x.com")
    b = du._find_or_create_folder(fake, "ROOT", "b@x.com")
    assert a != b and len(fake.folders()) == 2


def test_retried_folder_create_with_a_lost_reply_does_not_duplicate():
    fake = FakeDrive()
    fake.fail_after("create_folder", ConnectionResetError())      # created, but the reply never arrived
    fid = du._find_or_create_folder(fake, "ROOT", "lost@x.com")
    assert len(fake.folders()) == 1 and fid == fake.folders()[0]["id"]     # the retry's lookup found it


def test_folder_cache_expires_and_is_forgotten_on_demand(monkeypatch):
    fake = FakeDrive()
    now = [1000.0]
    monkeypatch.setattr(du, "_monotonic", lambda: now[0])
    du._find_or_create_folder(fake, "ROOT", "c@x.com")
    du._find_or_create_folder(fake, "ROOT", "c@x.com")
    assert fake.count("list") == 1                                # second call served from the cache
    now[0] += du._FOLDER_CACHE_TTL_SECONDS + 1
    du._find_or_create_folder(fake, "ROOT", "c@x.com")
    assert fake.count("list") == 2                                # expired -> looked up again
    du._forget_folder("ROOT", "c@x.com")
    du._find_or_create_folder(fake, "ROOT", "c@x.com")
    assert fake.count("list") == 3


def test_cross_instance_lock_uses_an_advisory_lock_and_always_closes(monkeypatch):
    monkeypatch.undo()                                            # the autouse stub would hide the real context manager
    executed, closed = [], []

    class Conn:
        def execute(self, sql, params=()):
            executed.append((sql, params))

        def close(self):
            closed.append(True)

    monkeypatch.setattr(du, "get_connection", lambda: Conn())
    with du._folder_lock_across_instances("drive-folder:ROOT/x"):
        assert closed == []                                       # held while the body runs
    assert any("pg_advisory_xact_lock" in sql for sql, _ in executed)
    assert any("lock_timeout" in sql for sql, _ in executed)
    assert closed == [True]                                       # releasing = closing the connection


def test_cross_instance_lock_is_best_effort(monkeypatch):
    monkeypatch.undo()

    def boom():
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(du, "get_connection", boom)
    ran = []
    with du._folder_lock_across_instances("k"):
        ran.append(True)                                          # the upload goes ahead unlocked
    assert ran == [True]


# ---------------------------------------------------------------------------------------------------------------------
# The upload end to end (fake Drive)
# ---------------------------------------------------------------------------------------------------------------------

def test_upload_success_shape(drive):
    out = _upload()
    assert out["uploaded"] is True and out["link_shared"] is True
    assert out["filename"] == "0001_Acme_Proposal"
    assert out["shareable_link"].startswith("https://docs.google.com/spreadsheets/d/")
    (f,) = drive.files_only()
    assert f["content"] == b"xlsx-bytes"
    assert f["mimeType"] == "application/vnd.google-apps.spreadsheet"
    assert f["parents"] == [out["folder_id"]]
    assert f["id"].startswith("gen-")                              # created under a pre-generated id (idempotency key)
    assert drive.shared == [(out["file_id"], {"type": "anyone", "role": "writer"})]


def test_upload_retries_a_transient_503_on_the_folder_lookup(drive, sleeps):
    drive.fail("list", http_error(503, "backendError"))
    out = _upload()
    assert out["uploaded"] is True
    assert drive.count("list") == 2 and len(sleeps) == 1


def test_upload_retries_a_transient_failure_of_the_file_create_itself(drive, sleeps):
    drive.fail("create_file", http_error(500, "backendError"), http_error(503))
    assert _upload()["uploaded"] is True
    assert len(drive.files_only()) == 1 and len(sleeps) == 2


def test_lost_reply_on_file_create_does_not_leave_a_duplicate(drive):
    drive.fail_after("create_file", ConnectionResetError())        # the file landed, the reply was lost
    out = _upload()
    assert out["uploaded"] is True
    assert len(drive.files_only()) == 1                            # 409 on the retry -> fetched, not re-created
    assert out["file_id"] == drive.files_only()[0]["id"]


def test_upload_still_works_when_pre_generating_an_id_fails(drive):
    drive.fail("generate_ids", http_error(500), http_error(500), http_error(500), http_error(500))
    out = _upload()
    assert out["uploaded"] is True
    assert "id" not in drive.calls[[o for o, _ in drive.calls].index("create_file")][1]


def test_share_failure_is_not_fatal_but_is_reported(drive):
    drive.fail("permission", http_error(403, "forbidden", "sharing disabled by policy"))
    out = _upload()
    assert out["uploaded"] is True and out["link_shared"] is False
    assert drive.count("permission") == 1                          # 403 forbidden is not retried


def test_share_retries_a_transient_failure(drive):
    drive.fail("permission", http_error(503))
    assert _upload()["link_shared"] is True


def test_drive_401_means_reauthorize_not_a_500(drive):
    drive.fail("list", http_error(401, "authError", "Invalid Credentials"))
    out = _upload()
    assert out == {"uploaded": False, "needs_auth": True, "reason": "Google Drive authorization required."}


def test_no_stored_authorization_means_reauthorize(monkeypatch, drive):
    monkeypatch.setattr(du, "_load_credentials", lambda: None)
    assert _upload()["needs_auth"] is True


def test_not_configured_is_still_a_graceful_noop(monkeypatch):
    monkeypatch.delenv("DRIVE_CLIENT_ID")
    out = du.upload_proposal(Path("x.xlsx"), "a@b.com", content=b"1")
    assert out["uploaded"] is False and "not configured" in out["reason"]


def test_reads_the_local_file_when_no_content_is_given(drive, tmp_path):
    p = tmp_path / "0002_Local.xlsx"
    p.write_bytes(b"local-bytes")
    assert du.upload_proposal(p, "a@b.com", proposal_id="p")["uploaded"] is True
    assert drive.files_only()[0]["content"] == b"local-bytes"


def test_missing_local_file_is_a_410_not_a_500(drive, tmp_path):
    with pytest.raises(du.DriveError) as info:
        du.upload_proposal(tmp_path / "gone.xlsx", "a@b.com", proposal_id="p")
    assert info.value.status_code == 410 and not info.value.retryable
    assert "Generate" in info.value.message


def test_empty_seller_email_goes_to_unassigned(drive):
    _upload(seller="")
    assert [f["name"] for f in drive.folders()] == ["unassigned"]


def test_a_deleted_cached_folder_is_forgotten_after_a_404(drive):
    out = _upload()
    assert ("ROOT", "ae@entravision.com") in du._folder_cache
    drive.fail("create_file", http_error(404, "notFound", "File not found: folder"))
    with pytest.raises(du.DriveError) as info:
        _upload()
    assert info.value.status_code == 424
    assert ("ROOT", "ae@entravision.com") not in du._folder_cache       # the next try looks the folder up afresh
    lists_before = drive.count("list")
    again = _upload()
    assert again["uploaded"] is True and drive.count("list") == lists_before + 1
    assert again["folder_id"] == out["folder_id"]                        # same folder (it still exists in the fake)


# ---------------------------------------------------------------------------------------------------------------------
# The REAL google-api-python-client + httplib2 against a local server that speaks a slice of the Drive protocol
# (the fake service above proves our logic; this proves it works with the library's real resumable machinery)
# ---------------------------------------------------------------------------------------------------------------------

class _ProtocolState:
    def __init__(self):
        self.folders: list[dict] = []
        self.files: dict[str, dict] = {}
        self.log: list[tuple] = []
        self.fail_list = 0        # answer this many files.list calls with 503
        self.fail_put = 0         # answer this many chunk PUTs with 503
        self.drop_reply = 0       # store the upload, then hang up without answering (a lost reply)
        self.meta: dict | None = None


def _make_protocol_handler(state: _ProtocolState):
    from http.server import BaseHTTPRequestHandler
    from urllib.parse import parse_qs, urlparse

    def norm(raw):
        parsed = urlparse(raw)
        path, upload = parsed.path, parsed.path.startswith("/upload")
        if upload:
            path = path[len("/upload"):]
        return path.replace("/drive/v3", ""), parse_qs(parsed.query), upload

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, status, body=None, headers=None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def do_GET(self):
            path, _q, _up = norm(self.path)
            state.log.append(("GET", path))
            if path.endswith("/generateIds"):
                return self._send(200, {"ids": ["gen-1"]})
            if path == "/files":
                if state.fail_list:
                    state.fail_list -= 1
                    return self._send(503, {"error": {"code": 503, "message": "Backend Error",
                                                      "errors": [{"reason": "backendError"}]}})
                return self._send(200, {"files": [{"id": f["id"], "name": f["name"]} for f in state.folders]})
            file = state.files.get(path.rsplit("/", 1)[-1])
            return self._send(200, file) if file else self._send(404, {"error": {"code": 404, "message": "nf"}})

        def do_POST(self):
            path, q, upload = norm(self.path)
            body = self._body()
            state.log.append(("POST", path, "upload" if upload else ""))
            if path == "/files" and not upload:
                meta = json.loads(body)
                folder = {"id": f"folder-{len(state.folders) + 1}", "name": meta["name"]}
                state.folders.append(folder)
                return self._send(200, folder)
            if path == "/files" and upload and q.get("uploadType") == ["resumable"]:
                state.meta = json.loads(body)
                return self._send(200, {}, {"Location": f"http://127.0.0.1:{self.server.server_port}/session/1"})
            if path.endswith("/permissions"):
                return self._send(200, {"id": "perm"})
            self._send(404, {})

        def do_PUT(self):
            body = self._body()
            state.log.append(("PUT", self.headers.get("Content-Range"), len(body)))
            stored = state.meta and state.meta.get("id") in state.files
            if not body:                                   # the library's "how far did you get?" query
                if stored:
                    return self._send(200, {k: v for k, v in state.files[state.meta["id"]].items() if k != "content"})
                return self._send(308, None)               # resume incomplete (not a redirect - see _build_service)
            if state.fail_put:
                state.fail_put -= 1
                return self._send(503, {"error": {"code": 503, "message": "Service Unavailable"}})
            meta = state.meta
            file = {"id": meta["id"], "name": meta["name"], "mimeType": meta.get("mimeType", ""),
                    "webViewLink": f"https://docs.google.com/spreadsheets/d/{meta['id']}/edit",
                    "content": body.decode("latin1")}
            state.files[file["id"]] = file
            if state.drop_reply:
                state.drop_reply -= 1
                self.close_connection = True               # stored, but the answer never reaches the client
                return
            self._send(200, {k: v for k, v in file.items() if k != "content"})

    return Handler


@pytest.fixture
def protocol_drive(monkeypatch):
    from http.server import ThreadingHTTPServer

    from googleapiclient.discovery import build
    from googleapiclient.http import build_http

    state = _ProtocolState()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_protocol_handler(state))
    except OSError as exc:                                  # pragma: no cover - no loopback sockets here
        pytest.skip(f"cannot bind a loopback port: {exc}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    http = build_http()
    http.timeout = 1                                        # the "lost reply" case surfaces as a read timeout
    real = build("drive", "v3", http=http, cache_discovery=False,
                 client_options={"api_endpoint": f"http://127.0.0.1:{server.server_port}/"})

    class _Files:
        def __init__(self, files):
            self._files = files

        def __getattr__(self, name):
            return getattr(self._files, name)

        def create(self, **kw):
            request = self._files.create(**kw)
            # Pointed at a custom endpoint the client keeps Google's https scheme for UPLOAD urls; this local
            # server speaks plain http. (Nothing else about the request or the retry machinery is touched.)
            request.uri = request.uri.replace("https://127.0.0.1", "http://127.0.0.1")
            return request

    class _Service:
        def files(self):
            return _Files(real.files())

        def permissions(self):
            return real.permissions()

    monkeypatch.setattr(du, "_load_credentials", lambda: object())
    monkeypatch.setattr(du, "_build_service", lambda creds: _Service())
    yield state
    server.shutdown()
    server.server_close()


def test_real_client_survives_503s_on_the_lookup_and_mid_upload(protocol_drive):
    protocol_drive.fail_list = 1
    protocol_drive.fail_put = 1
    out = _upload(content=b"PK\x03\x04-workbook", seller="o'brien@x.com")
    assert out["uploaded"] is True and out["link_shared"] is True
    assert [f["name"] for f in protocol_drive.folders] == ["o'brien@x.com"]
    (stored,) = protocol_drive.files.values()
    assert stored["content"] == "PK\x03\x04-workbook" and stored["id"] == "gen-1"        # the pre-generated id
    puts = [e for e in protocol_drive.log if e[0] == "PUT"]
    assert [p[1] for p in puts] == ["bytes 0-12/13", "bytes */13", "bytes 0-12/13"]      # fail -> status query -> resume


def test_real_client_lost_reply_leaves_one_file_not_two(protocol_drive):
    protocol_drive.drop_reply = 1                       # the upload landed; the client never heard back
    out = _upload(content=b"PK-workbook")
    assert out["uploaded"] is True and out["file_id"] == "gen-1"
    assert len(protocol_drive.files) == 1
    assert [e for e in protocol_drive.log if e[0] == "POST" and e[2] == "upload"].__len__() == 1   # one session, resumed


def test_service_is_built_with_308_as_resume_incomplete_and_a_timeout():
    from google.oauth2.credentials import Credentials as GCreds

    service = du._build_service(GCreds(token="t", refresh_token="r", token_uri="u", client_id="c", client_secret="s"))
    http = service._http.http
    assert 308 not in http.redirect_codes                # else a resumable chunk's "resume incomplete" breaks httplib2
    assert http.timeout == du.HTTP_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------------------------------------------------
# Error mapping: status code / message / retryable, never a bare exception
# ---------------------------------------------------------------------------------------------------------------------

def test_transient_failures_that_outlast_the_retries_become_a_retryable_503(drive, sleeps):
    drive.fail("list", *[http_error(503, "backendError")] * 10)
    with pytest.raises(du.DriveError) as info:
        _upload()
    err = info.value
    assert (err.status_code, err.retryable, err.code) == (503, True, "drive_unavailable")
    assert "try again" in err.message.lower()
    assert drive.count("list") == du.MAX_ATTEMPTS
    assert err.payload() == {"detail": err.message, "retryable": True, "code": "drive_unavailable"}


@pytest.mark.parametrize("status,reason,needle", [
    (403, "storageQuotaExceeded", "out of storage"),
    (403, "forbidden", "refused the upload"),
    (404, "notFound", "DRIVE_ROOT_FOLDER_ID"),
    (400, "invalid", "rejected the upload"),
])
def test_permanent_google_failures_are_424_with_a_plain_reason(drive, status, reason, needle):
    drive.fail("list", http_error(status, reason, "raw google text"))
    with pytest.raises(du.DriveError) as info:
        _upload()
    err = info.value
    assert (err.status_code, err.retryable, err.code) == (424, False, "drive_rejected")
    assert needle in err.message
    assert drive.count("list") == 1                                # permanent: not retried


def test_an_unexpected_exception_is_a_500_with_a_log_reference(drive, caplog):
    drive.fail("list", KeyError("secret-looking-thing"))
    with caplog.at_level(logging.ERROR, logger=du.__name__):
        with pytest.raises(du.DriveError) as info:
            _upload()
    err = info.value
    assert (err.status_code, err.retryable, err.code) == (500, False, "unexpected")
    assert err.ref and err.ref in err.message
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert err.ref in logged and "KeyError" in logged and "proposal=p1" in logged and "find/create folder" in logged


def test_database_trouble_is_a_retryable_503_with_its_own_message(drive, monkeypatch):
    def boom():
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(du, "_load_credentials", boom)
    with pytest.raises(du.DriveError) as info:
        _upload()
    assert info.value.status_code == 503 and info.value.retryable and "database" in info.value.message


def test_logs_carry_class_status_reason_but_no_tokens(drive, caplog):
    drive.fail("list", http_error(403, "forbidden", "The caller does not have permission"))
    with caplog.at_level(logging.DEBUG, logger=du.__name__):
        with pytest.raises(du.DriveError):
            _upload()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "HttpError" in text and "403" in text and "forbidden" in text
    assert "secret" not in text.lower() and "ya29" not in text


# ---------------------------------------------------------------------------------------------------------------------
# upload_slot (double submit)
# ---------------------------------------------------------------------------------------------------------------------

def test_a_second_concurrent_upload_of_the_same_proposal_is_refused_then_allowed_again():
    with du.upload_slot("p1"):
        with pytest.raises(du.DriveError) as info:
            with du.upload_slot("p1"):
                pass
        assert info.value.status_code == 409 and info.value.code == "upload_in_progress"
        with du.upload_slot("p2"):                                  # a different proposal is unaffected
            pass
    with du.upload_slot("p1"):                                      # released afterwards (even after a failure)
        pass
    with pytest.raises(RuntimeError):
        with du.upload_slot("p3"):
            raise RuntimeError("x")
    with du.upload_slot("p3"):
        pass


# ---------------------------------------------------------------------------------------------------------------------
# Credentials: refreshed once under a lock, cached, transient != re-authorize
# ---------------------------------------------------------------------------------------------------------------------

class _TokenConn:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, _sql):
        return SimpleNamespace(fetchone=lambda: {
            "token": "stale", "refresh_token": "stored-refresh", "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "cid", "client_secret": "secret", "scopes": ["https://www.googleapis.com/auth/drive"],
        })


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def token_env(monkeypatch):
    saved, refreshes = [], []
    monkeypatch.setattr(du, "get_connection", _TokenConn)
    monkeypatch.setattr(du, "_save_credentials", saved.append)

    def fake_refresh(creds, _request):
        refreshes.append(True)
        time.sleep(0.02)
        creds.token = "fresh"
        creds.expiry = _utcnow_naive() + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    return SimpleNamespace(saved=saved, refreshes=refreshes)


def test_refreshed_token_is_cached_so_later_uploads_dont_refresh_again(token_env):
    first = du._load_credentials()
    second = du._load_credentials()
    assert first is second and first.token == "fresh"
    assert len(token_env.refreshes) == 1 and len(token_env.saved) == 1
    du._forget_cached_credentials()                                 # e.g. after a re-authorization
    du._load_credentials()
    assert len(token_env.refreshes) == 2


def test_concurrent_uploads_share_one_refresh(token_env):
    barrier = threading.Barrier(6)
    out = []

    def worker():
        barrier.wait()
        out.append(du._load_credentials())

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(token_env.refreshes) == 1
    assert len({id(c) for c in out}) == 1


def test_expired_cached_token_is_refreshed_again(token_env):
    creds = du._load_credentials()
    creds.expiry = _utcnow_naive() - timedelta(minutes=1)
    du._load_credentials()
    assert len(token_env.refreshes) == 2


def test_a_credential_with_unknown_expiry_is_never_served_from_the_cache(monkeypatch, token_env):
    def refresh_without_expiry(creds, _request):
        token_env.refreshes.append(True)
        creds.token = "fresh"

    monkeypatch.setattr(Credentials, "refresh", refresh_without_expiry)
    du._load_credentials()
    du._load_credentials()
    assert len(token_env.refreshes) == 2


def test_a_transient_refresh_failure_is_retried_and_does_not_trigger_reauthorization(monkeypatch, token_env, sleeps):
    attempts = []

    def flaky_refresh(creds, _request):
        attempts.append(True)
        if len(attempts) < 3:
            raise gauth_exc.TransportError("connection reset")
        creds.token = "fresh"
        creds.expiry = _utcnow_naive() + timedelta(hours=1)

    monkeypatch.setattr(Credentials, "refresh", flaky_refresh)
    creds = du._load_credentials()
    assert creds is not None and creds.token == "fresh" and len(attempts) == 3 and len(sleeps) == 2


def test_a_refresh_that_keeps_failing_transiently_is_raised_not_reported_as_unauthorized(monkeypatch, token_env):
    def always_down(creds, _request):
        raise gauth_exc.TransportError("down")

    monkeypatch.setattr(Credentials, "refresh", always_down)
    with pytest.raises(gauth_exc.TransportError):
        du._load_credentials()                                       # NOT None: the planner must not be sent to consent


def test_a_revoked_grant_still_leads_to_reauthorization(monkeypatch, token_env, sleeps):
    def revoked(creds, _request):
        raise gauth_exc.RefreshError("invalid_grant: Token has been expired or revoked.",
                                     {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})

    monkeypatch.setattr(Credentials, "refresh", revoked)
    assert du._load_credentials() is None
    assert sleeps == []                                              # not retried


def test_invalid_client_is_a_config_error_not_an_endless_consent_loop(monkeypatch, token_env):
    def bad_client(creds, _request):
        raise gauth_exc.RefreshError("invalid_client: Unauthorized", {"error": "invalid_client"})

    monkeypatch.setattr(Credentials, "refresh", bad_client)          # the REAL _load_credentials runs here
    monkeypatch.setattr(du, "_build_service", lambda c: FakeDrive())
    with pytest.raises(du.DriveError) as info:
        _upload()
    assert info.value.status_code == 424 and "DRIVE_CLIENT_ID" in info.value.message and not info.value.retryable


def test_a_database_outage_while_loading_the_token_is_not_reported_as_unauthorized(monkeypatch):
    def boom():
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(du, "get_connection", boom)
    with pytest.raises(psycopg.OperationalError):
        du._load_credentials()


def test_an_unusable_stored_row_still_leads_to_reauthorization(monkeypatch):
    class BadRow(_TokenConn):
        def execute(self, _sql):
            return SimpleNamespace(fetchone=lambda: {"token": None})   # KeyError building the credential

    monkeypatch.setattr(du, "get_connection", BadRow)
    assert du._load_credentials() is None


def test_saving_never_overwrites_the_stored_refresh_token_with_null(monkeypatch):
    seen = []

    class Conn(_TokenConn):
        def execute(self, sql, params=()):
            seen.append((sql, params))

    monkeypatch.setattr(du, "get_connection", Conn)
    du._save_credentials(SimpleNamespace(token="t", refresh_token=None, token_uri="u", client_id="c",
                                         client_secret="s", scopes=["a"]))
    sql, params = seen[0]
    assert "COALESCE(EXCLUDED.refresh_token, drive_tokens.refresh_token)" in sql
    assert params[1] is None


# ---------------------------------------------------------------------------------------------------------------------
# The shared copy of the workbook (the root-cause fix)
# ---------------------------------------------------------------------------------------------------------------------

class _FakeFileConn:
    def __init__(self, store, fail=None, row=None):
        self.store, self.fail, self.row = store, fail, row

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, sql, params=()):
        if self.fail:
            raise self.fail
        self.store.append((sql, params))
        return SimpleNamespace(fetchone=lambda: self.row)


def test_save_file_stores_the_bytes_keyed_by_proposal_and_kind(monkeypatch, tmp_path):
    store = []
    monkeypatch.setattr(proposal_files, "get_connection", lambda: _FakeFileConn(store))
    f = tmp_path / "0001_Acme.xlsx"
    f.write_bytes(b"PK-workbook")
    assert proposal_files.save_file("pid-1", "xlsx", f) is True
    sql, params = store[0]
    assert "INSERT INTO proposal_files" in sql and "ON CONFLICT (proposal_id, kind) DO UPDATE" in sql
    assert params == ("pid-1", "xlsx", "0001_Acme.xlsx", b"PK-workbook", 11)


def test_save_file_is_fail_safe(monkeypatch, tmp_path):
    f = tmp_path / "a.xlsx"
    f.write_bytes(b"x")

    def boom():
        raise psycopg.errors.UndefinedTable("relation proposal_files does not exist")

    monkeypatch.setattr(proposal_files, "get_connection", boom)
    assert proposal_files.save_file("p", "xlsx", f) is False             # never raises: Generate must not fail
    assert proposal_files.save_file("p", "xlsx", tmp_path / "missing.xlsx") is False
    (tmp_path / "empty.xlsx").write_bytes(b"")
    assert proposal_files.save_file("p", "xlsx", tmp_path / "empty.xlsx") is False


def test_load_bytes_distinguishes_nothing_stored_from_cannot_look(monkeypatch):
    monkeypatch.setattr(proposal_files, "get_connection", lambda: _FakeFileConn([], row=None))
    assert proposal_files.load_bytes("p", "xlsx") is None
    monkeypatch.setattr(proposal_files, "get_connection", lambda: _FakeFileConn([], row={"content": memoryview(b"abc")}))
    assert proposal_files.load_bytes("p", "xlsx") == b"abc"
    monkeypatch.setattr(proposal_files, "get_connection",
                        lambda: _FakeFileConn([], fail=psycopg.errors.UndefinedTable("no table")))
    assert proposal_files.load_bytes("p", "xlsx") is None                 # schema not applied yet: legacy fallback
    monkeypatch.setattr(proposal_files, "get_connection",
                        lambda: _FakeFileConn([], fail=psycopg.OperationalError("down")))
    with pytest.raises(psycopg.OperationalError):
        proposal_files.load_bytes("p", "xlsx")                            # the caller must see this one


def test_schema_defines_the_shared_file_table():
    assert "CREATE TABLE IF NOT EXISTS proposal_files" in SCHEMA_SQL
    block = SCHEMA_SQL[SCHEMA_SQL.index("CREATE TABLE IF NOT EXISTS proposal_files"):]
    block = block[:block.index(");")]
    for needle in ("proposal_id", "kind", "filename", "BYTEA", "PRIMARY KEY (proposal_id, kind)"):
        assert needle in block


def test_generate_stores_the_shared_copy_after_the_metadata_row():
    src = __import__("inspect").getsource(main.generate)
    assert src.index("_save_proposal_metadata(") < src.index("proposal_files_svc.save_file")
    assert "asyncio.to_thread(proposal_files_svc.save_file" in src


# ---------------------------------------------------------------------------------------------------------------------
# POST /api/drive/upload and GET /api/drive/callback (handlers called directly, like the other API tests)
# ---------------------------------------------------------------------------------------------------------------------

def _request():
    return SimpleNamespace(base_url="http://testserver/")


def _call_upload(proposal_id="pid-1", seller="ae@entravision.com"):
    return asyncio.run(main.drive_upload(main.DriveUploadRequest(proposal_id=proposal_id, seller_email=seller), _request()))


def _json(resp) -> dict:
    return json.loads(resp.body)


@pytest.fixture
def api_base(monkeypatch, tmp_path):
    """A generated proposal row, an empty proposals dir (= an instance that never generated it), a stored copy."""
    meta = {"proposal_id": "pid-1", "filename": "0001_Acme_Proposal.xlsx", "status": "generated",
            "seller_email": "row-seller@entravision.com"}
    env = SimpleNamespace(meta=meta, stored=b"stored-from-db", uploads=[], tmp=tmp_path)
    monkeypatch.setattr(main, "PROPOSALS_DIR", tmp_path)
    monkeypatch.setattr(main, "_get_proposal_metadata", lambda pid: env.meta if pid == "pid-1" else None)
    monkeypatch.setattr(main.proposal_files_svc, "load_bytes", lambda pid, kind: env.stored)
    return env


@pytest.fixture
def api(monkeypatch, api_base):
    """api_base + a stub Drive uploader that records what the endpoint handed it."""
    env = api_base

    def fake_upload(**kw):
        env.uploads.append(kw)
        return {"uploaded": True, "file_id": "f1", "shareable_link": "https://x/y", "folder_id": "d1",
                "filename": "0001_Acme_Proposal", "link_shared": True}

    monkeypatch.setattr(main.drive_uploader, "upload_proposal", fake_upload)
    return env


def test_upload_uses_the_shared_copy_when_this_instance_has_no_local_file(api):
    """THE regression: file only on another instance's disk -> used to be FileNotFoundError -> bare 500."""
    assert not (api.tmp / api.meta["filename"]).exists()
    out = _call_upload()
    assert out["uploaded"] is True
    (kw,) = api.uploads
    assert kw["content"] == b"stored-from-db"
    assert kw["local_path"].name == "0001_Acme_Proposal.xlsx" and kw["proposal_id"] == "pid-1"
    assert kw["seller_email"] == "ae@entravision.com" and kw["as_google_sheet"] is True


def test_shared_copy_beats_a_stale_same_named_local_file(api):
    (api.tmp / api.meta["filename"]).write_bytes(b"STALE-from-an-earlier-generation")
    _call_upload()
    assert api.uploads[0]["content"] == b"stored-from-db"


def test_local_file_is_the_fallback_for_proposals_generated_before_the_shared_copy_existed(api):
    api.stored = None
    (api.tmp / api.meta["filename"]).write_bytes(b"legacy-local")
    _call_upload()
    assert api.uploads[0]["content"] == b"legacy-local"


def test_file_gone_everywhere_is_a_410_with_an_actionable_message(api):
    api.stored = None
    resp = _call_upload()
    assert resp.status_code == 410
    body = _json(resp)
    assert body["retryable"] is False and body["code"] == "file_unavailable"
    assert "Generate Proposal again" in body["detail"]
    assert api.uploads == []


def test_a_draft_or_unknown_proposal_is_a_clean_4xx(api):
    api.meta["filename"] = None
    api.meta["status"] = "draft"
    resp = _call_upload()
    assert resp.status_code == 409 and _json(resp)["code"] == "not_generated"      # used to be KeyError('path') -> 500
    resp = _call_upload(proposal_id="nope")
    assert resp.status_code == 404 and _json(resp)["code"] == "proposal_not_found"
    resp = _call_upload(proposal_id="   ")
    assert resp.status_code == 404


def test_seller_email_falls_back_to_the_proposal_row(api):
    _call_upload(seller="")
    assert api.uploads[0]["seller_email"] == "row-seller@entravision.com"
    api.meta["seller_email"] = None
    _call_upload(seller="")
    assert api.uploads[1]["seller_email"] == ""                                      # -> "unassigned" downstream


def test_database_blips_while_loading_the_proposal_are_retried(api, monkeypatch, sleeps):
    calls = []

    def flaky(pid):
        calls.append(1)
        if len(calls) < 3:
            raise psycopg.OperationalError("SSL connection has been closed unexpectedly")
        return api.meta

    monkeypatch.setattr(main, "_get_proposal_metadata", flaky)
    assert _call_upload()["uploaded"] is True and len(calls) == 3 and len(sleeps) == 2


def test_a_database_that_stays_down_is_a_retryable_503(api, monkeypatch):
    def down(pid):
        raise psycopg.OperationalError("down")

    monkeypatch.setattr(main, "_get_proposal_metadata", down)
    resp = _call_upload()
    assert resp.status_code == 503 and _json(resp)["retryable"] is True
    assert resp.headers["retry-after"]


def test_needs_auth_keeps_the_existing_contract_with_an_auth_url(api, monkeypatch):
    monkeypatch.setattr(main.drive_uploader, "upload_proposal", lambda **kw: {"uploaded": False, "needs_auth": True})
    monkeypatch.setattr(main.drive_uploader, "get_auth_url", lambda redirect: f"https://accounts.example/auth?r={redirect}")
    out = _call_upload()
    assert out["needs_auth"] is True and out["auth_url"] == "https://accounts.example/auth?r=http://testserver/api/drive/callback"


def test_drive_reauthorization_is_never_an_http_401(api, monkeypatch):
    """session-guard.js bounces the browser to /login on ANY 401, so a Drive re-auth must stay a 200 {needs_auth}."""
    assert du.DriveAuthRequired().status_code != 401

    def revoked(**kw):
        raise du.DriveAuthRequired()

    monkeypatch.setattr(main.drive_uploader, "upload_proposal", revoked)
    monkeypatch.setattr(main.drive_uploader, "get_auth_url", lambda redirect: "https://accounts.example/auth")
    out = _call_upload()
    assert out == {"uploaded": False, "needs_auth": True, "reason": "Google Drive authorization required.",
                   "auth_url": "https://accounts.example/auth"}


def test_no_failure_the_endpoint_can_return_is_a_401(api, monkeypatch):
    for exc in (du.DriveError("x", status_code=503, retryable=True, code="c"), RuntimeError("bug"),
                psycopg.OperationalError("db"), http_error(401, "authError"), http_error(403, "forbidden")):
        def boom(**kw):
            raise exc

        monkeypatch.setattr(main.drive_uploader, "upload_proposal", boom)
        resp = _call_upload()
        assert getattr(resp, "status_code", 200) not in (401,)


def test_driveerrors_from_the_uploader_become_json_with_the_right_status(api, monkeypatch):
    def fail(**kw):
        raise du.DriveError("Google Drive is temporarily unavailable.", status_code=503, retryable=True, code="drive_unavailable")

    monkeypatch.setattr(main.drive_uploader, "upload_proposal", fail)
    resp = _call_upload()
    assert (resp.status_code, _json(resp)) == (503, {"detail": "Google Drive is temporarily unavailable.",
                                                      "retryable": True, "code": "drive_unavailable"})
    assert resp.headers["retry-after"]


def test_the_endpoint_never_raises_a_bare_500_even_for_a_bug(api, monkeypatch, caplog):
    def bug(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(main.drive_uploader, "upload_proposal", bug)
    with caplog.at_level(logging.ERROR):
        resp = _call_upload()
    body = _json(resp)
    assert resp.status_code == 500 and body["code"] == "unexpected" and body["ref"] in body["detail"]
    assert body["retryable"] is False
    assert any(body["ref"] in r.getMessage() for r in caplog.records)


def test_end_to_end_through_the_real_uploader_with_the_fake_drive(api_base, drive):
    """Endpoint -> shared copy -> real upload_proposal -> fake Drive, with a transient 503 on the way."""
    drive.fail("create_file", http_error(503))
    out = _call_upload(seller="o'brien@entravision.com")
    assert out["uploaded"] is True
    assert drive.files_only()[0]["content"] == b"stored-from-db"
    assert [f["name"] for f in drive.folders()] == ["o'brien@entravision.com"]


def test_the_upload_does_not_block_the_event_loop(api, monkeypatch):
    def slow_upload(**kw):
        time.sleep(0.3)                                  # stands in for refresh + folder lookup + upload + share
        return {"uploaded": True}

    monkeypatch.setattr(main.drive_uploader, "upload_proposal", slow_upload)

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(heartbeat())
        result = await main.drive_upload(main.DriveUploadRequest(proposal_id="pid-1", seller_email="a@b.com"), _request())
        task.cancel()
        return result, ticks

    result, ticks = asyncio.run(scenario())
    assert result["uploaded"] is True
    assert ticks >= 10, f"the event loop only ticked {ticks} times during a 0.3s upload - it was blocked"


def test_a_double_submit_for_the_same_proposal_is_refused_with_409(api, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(**kw):
        entered.set()
        release.wait(5)
        return {"uploaded": True}

    monkeypatch.setattr(main.drive_uploader, "upload_proposal", slow)

    async def scenario():
        first = asyncio.create_task(main.drive_upload(main.DriveUploadRequest(proposal_id="pid-1", seller_email="a"), _request()))
        while not entered.is_set():
            await asyncio.sleep(0.01)
        second = await main.drive_upload(main.DriveUploadRequest(proposal_id="pid-1", seller_email="a"), _request())
        release.set()
        return await first, second

    first, second = asyncio.run(scenario())
    assert first["uploaded"] is True
    assert second.status_code == 409 and _json(second)["code"] == "upload_in_progress"


def test_callback_without_a_code_is_a_plain_page_not_a_422():
    resp = asyncio.run(main.drive_callback(_request(), code=None, error="access_denied"))
    assert resp.status_code == 200 and b"not authorized" in resp.body and b"access_denied" in resp.body


def test_callback_escapes_the_error_it_shows(monkeypatch):
    def boom(code, redirect):
        raise RuntimeError("<script>alert(1)</script>")

    monkeypatch.setattr(main.drive_uploader, "exchange_code", boom)
    resp = asyncio.run(main.drive_callback(_request(), code="abc", error=None))
    assert b"<script>alert(1)</script>" not in resp.body and b"&lt;script&gt;" in resp.body


def test_callback_success_is_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(main.drive_uploader, "exchange_code", lambda code, redirect: calls.append((code, redirect)))
    resp = asyncio.run(main.drive_callback(_request(), code="abc", error=None))
    assert calls == [("abc", "http://testserver/api/drive/callback")] and b"Google Drive authorized" in resp.body


# ---------------------------------------------------------------------------------------------------------------------
# Front end: onDriveUpload (extracted from app.js and run in Node when it is installed)
# ---------------------------------------------------------------------------------------------------------------------

def _drive_js_block() -> str:
    start = APP_JS.index("const DRIVE_MAX_ATTEMPTS")
    fn = APP_JS.index("async function onDriveUpload()")
    end = APP_JS.index("\n}\n", fn) + 3
    return APP_JS[start:end]


def test_front_end_source_contracts():
    block = _drive_js_block()
    assert "Retrying upload" in block
    assert "if (!deliverableId || _driveUploading) return;" in block          # double-click guard
    assert block.count("epoch !== _resetEpoch") >= 2                           # reset guards around the awaits
    assert "btn.disabled = true" in block and "btn.disabled = false" in block
    assert "data.retryable" in block
    # a New Proposal must free the button, and the auth popup must not retry for a different proposal
    reset = APP_JS[APP_JS.index("function _clearPerProposalState()"):]
    reset = reset[:reset.index("\n}\n")]
    assert "_driveUploading = false" in reset and "drive-upload-btn" in reset
    popup = APP_JS[APP_JS.index("function openDriveAuthPopup("):]
    popup = popup[:popup.index("\n}\n")]
    assert "epoch !== _resetEpoch" in popup


_NODE_PRELUDE = r"""
var __sleeps = [];
var _resetEpoch = 0, _driveUploading = false;
var state = { generatedProposalId: "p1", proposalId: "p1", parsed: { salesperson_email: "ae@x.com" } };
function makeEl() {
  var cls = {}, html = [], text = "";
  return {
    disabled: false, history: html,
    classList: { add: function (c) { cls[c] = true; }, remove: function () { for (var i = 0; i < arguments.length; i++) delete cls[arguments[i]]; },
                 has: function (c) { return !!cls[c]; } },
    set innerHTML(v) { html.push(String(v)); text = String(v); }, get innerHTML() { return text; },
    set textContent(v) { html.push(String(v)); text = String(v); }, get textContent() { return text; },
    addEventListener: function () {}
  };
}
var els = {};
var document = { getElementById: function (id) { return els[id] || (els[id] = makeEl()); } };
var window = { open: function () { return null; } };
var fetchCalls = [], plan = [];
function fetch(url, opts) {
  fetchCalls.push(JSON.parse(opts.body));
  var step = plan.shift();
  if (step.net) return Promise.reject(new TypeError("Failed to fetch"));
  var p = Promise.resolve(step.gate);
  return p.then(function () {
    return { ok: step.status < 400, status: step.status,
             json: function () { return step.json === undefined ? Promise.reject(new SyntaxError("Unexpected token <")) : Promise.resolve(step.json); } };
  });
}
function _driveSleep(ms) { __sleeps.push(Math.round(ms)); return Promise.resolve(); }
function openDriveAuthPopup() {}
function reset() { __sleeps = []; fetchCalls = []; plan = []; els = {}; _driveUploading = false; _resetEpoch = 0;
                   state.parsed = { salesperson_email: "ae@x.com" }; }
function snap(extra) {
  var s = els["drive-status"], b = els["drive-upload-btn"];
  var o = { text: s.textContent, ok: s.classList.has("ok"), warn: s.classList.has("warn"), fetches: fetchCalls.length,
            sleeps: __sleeps.slice(), btnDisabled: b.disabled, uploading: _driveUploading, history: s.history.slice() };
  for (var k in extra) o[k] = extra[k];
  return o;
}
"""


def _run_node(scenarios_js: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    escape_html = APP_JS[APP_JS.index("function escapeHtml(s)"):]
    escape_html = escape_html[:escape_html.index("\n}\n") + 3]
    script = "\n".join([_NODE_PRELUDE, escape_html, _drive_js_block(),
                        "function _driveSleep(ms) { __sleeps.push(Math.round(ms)); return Promise.resolve(); }",
                        scenarios_js])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "drive_ui.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def ui():
    scenarios = r"""
(async function () {
  var out = {};
  var OK = { status: 200, json: { uploaded: true, shareable_link: "https://docs.google.com/spreadsheets/d/abc/edit", link_shared: true } };
  var T503 = { status: 503, json: { detail: "Google Drive is temporarily unavailable. Nothing was lost - please try again in a moment.", retryable: true, code: "drive_unavailable" } };

  reset(); plan = [OK]; await onDriveUpload(); out.success = snap();

  reset(); plan = [T503, T503, OK]; await onDriveUpload(); out.retry_then_ok = snap();

  reset(); plan = [T503, T503, T503]; await onDriveUpload(); out.exhausted = snap();

  reset(); plan = [{ status: 410, json: { detail: "This proposal's .xlsx is no longer on the server. Click Generate Proposal again, then upload.", retryable: false, code: "file_unavailable" } }];
  await onDriveUpload(); out.permanent = snap();

  reset(); plan = [{ status: 500 }, { status: 502 }, OK]; await onDriveUpload(); out.bare_500_502 = snap();

  reset(); plan = [{ net: true }, OK]; await onDriveUpload(); out.network_error = snap();

  reset(); plan = [{ status: 500, json: { detail: "The Drive upload failed unexpectedly (reference ab12cd).", retryable: false, code: "unexpected", ref: "ab12cd" } }];
  await onDriveUpload(); out.unexpected_not_retried = snap();

  reset(); plan = [{ status: 200, json: { uploaded: false, needs_auth: true, auth_url: "https://accounts.google.com/o/oauth2/auth?x=1" } }];
  await onDriveUpload(); out.needs_auth = snap();

  reset(); plan = [{ status: 200, json: { uploaded: false, reason: "Google Drive not configured." } }];
  await onDriveUpload(); out.not_configured = snap();

  // double click: the second call is ignored while the first is in flight
  reset(); var gate = new Promise(function (r) { window.__release = r; });
  plan = [{ status: 200, gate: gate, json: { uploaded: true, shareable_link: "https://x/y" } }];
  var first = onDriveUpload(); var second = onDriveUpload();
  await Promise.resolve(); var mid = snap();
  window.__release(); await first; await second; out.double_click = { mid: mid, end: snap() };

  // New Proposal while the request is in flight: the late answer must not touch the page, and the reset frees the button
  reset(); var gate2 = new Promise(function (r) { window.__release2 = r; });
  plan = [{ status: 503, gate: gate2, json: { detail: "later", retryable: true } }, OK];
  var inflight = onDriveUpload(); await Promise.resolve();
  _resetEpoch++; _driveUploading = false; els["drive-upload-btn"].disabled = false;       // what _clearPerProposalState does
  var before = els["drive-status"].history.length;
  window.__release2(); await inflight;
  out.reset_mid_upload = { fetches: fetchCalls.length, statusWritesAfterReset: els["drive-status"].history.length - before,
                           uploading: _driveUploading, btnDisabled: els["drive-upload-btn"].disabled };

  // reset during the retry back-off: no further request is made
  reset(); plan = [T503, OK];
  var origSleep = _driveSleep; _driveSleep = function (ms) { _resetEpoch++; return origSleep(ms); };
  await onDriveUpload(); _driveSleep = origSleep; out.reset_during_backoff = snap();

  // parsed is null (a reset cleared it) - the payload still builds
  reset(); state.parsed = null; plan = [OK]; await onDriveUpload(); out.null_parsed = { fetchBody: fetchCalls[0], status: snap().ok };

  // retries are the SAME request (same proposal, same seller)
  reset(); plan = [T503, OK]; await onDriveUpload(); out.same_payload = fetchCalls;

  console.log(JSON.stringify(out));
})();
"""
    return _run_node(scenarios)


def test_ui_success_first_try(ui):
    s = ui["success"]
    assert s["ok"] and s["fetches"] == 1 and "Open in Drive" in s["text"] and not s["btnDisabled"] and not s["uploading"]


def test_ui_retries_twice_with_a_visible_retrying_state_then_succeeds(ui):
    s = ui["retry_then_ok"]
    assert s["ok"] and s["fetches"] == 3 and len(s["sleeps"]) == 2
    assert any("Retrying upload" in h and "attempt 2 of 3" in h for h in s["history"])
    assert any("Retrying upload" in h and "attempt 3 of 3" in h for h in s["history"])
    assert 1500 <= s["sleeps"][0] <= 2000 and 3500 <= s["sleeps"][1] <= 4000          # short, bounded backoff
    assert not s["btnDisabled"]


def test_ui_shows_the_servers_reason_after_the_retries_are_spent_and_keeps_the_button_usable(ui):
    s = ui["exhausted"]
    assert s["fetches"] == 3 and s["warn"] and not s["ok"]
    assert "temporarily unavailable" in s["text"] and "tried 3 times" in s["text"]
    assert not s["btnDisabled"] and not s["uploading"]


def test_ui_does_not_retry_permanent_failures(ui):
    s = ui["permanent"]
    assert s["fetches"] == 1 and s["sleeps"] == [] and "Generate Proposal again" in s["text"] and s["warn"]
    assert not s["btnDisabled"]
    u = ui["unexpected_not_retried"]
    assert u["fetches"] == 1 and "ab12cd" in u["text"]


def test_ui_retries_bare_gateway_errors_and_dropped_connections(ui):
    assert ui["bare_500_502"]["fetches"] == 3 and ui["bare_500_502"]["ok"]
    assert ui["network_error"]["fetches"] == 2 and ui["network_error"]["ok"]


def test_ui_keeps_the_auth_popup_flow_and_the_not_configured_message(ui):
    a = ui["needs_auth"]
    assert a["fetches"] == 1 and a["warn"] and "authorize Google Drive" in a["text"] and not a["btnDisabled"]
    n = ui["not_configured"]
    assert n["warn"] and "not configured" in n["text"] and n["fetches"] == 1


def test_ui_ignores_a_second_click_while_uploading(ui):
    d = ui["double_click"]
    assert d["mid"]["fetches"] == 1 and d["mid"]["btnDisabled"] and d["mid"]["uploading"]
    assert d["end"]["fetches"] == 1 and d["end"]["ok"] and not d["end"]["btnDisabled"]


def test_ui_leaves_the_page_alone_after_a_new_proposal_reset(ui):
    r = ui["reset_mid_upload"]
    assert r["fetches"] == 1                       # no retry for a proposal that is no longer on screen
    assert r["statusWritesAfterReset"] == 0        # the late answer wrote nothing
    assert r["uploading"] is False and r["btnDisabled"] is False
    assert ui["reset_during_backoff"]["fetches"] == 1


def test_ui_payload_is_stable_across_retries_and_null_safe(ui):
    assert ui["same_payload"] == [{"proposal_id": "p1", "seller_email": "ae@x.com"}] * 2
    assert ui["null_parsed"]["fetchBody"] == {"proposal_id": "p1", "seller_email": ""} and ui["null_parsed"]["status"]
