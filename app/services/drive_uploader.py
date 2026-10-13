"""
Google Drive upload service — OAuth2 web client flow.

Setup:
  1. Add to .env:
       DRIVE_CLIENT_ID=<your-client-id>
       DRIVE_CLIENT_SECRET=<your-client-secret>
       DRIVE_ROOT_FOLDER_ID=1regtb_G2iuNto9PQ-pwQ7am89JVBMjxy
  2. On first use, call /api/drive/upload — it returns {needs_auth: true, auth_url: ...}.
     Open auth_url in a browser, authorize, and the callback at /api/drive/callback
     stores the token. Subsequent uploads use the stored token (auto-refreshed).

  OAuth credentials are stored in PostgreSQL so they are shared by autoscale
  instances.

Reliability (this module used to raise a bare HTTP 500 on the first Google/network hiccup):
  * EVERY Google call (token refresh, folder lookup/creation, the upload, the share) goes through
    call_with_retries(): bounded exponential backoff with jitter for the failures that are transient by nature
    (429, 5xx, rate-limit 403s, timeouts, connection resets, DNS blips, a dropped database connection) and NO retry
    for the ones that never get better by asking again (auth, permissions, quota, bad request).
  * Whatever finally goes wrong leaves upload_proposal() as a DriveError that already carries a plain-language
    message, an HTTP status and a `retryable` flag - the endpoint never has to guess, and the only exception that
    can still escape as a bare 500 is a genuine bug (which is logged with a reference).
  * The upload is resumable (chunked) and carries a pre-generated Drive file id, so retrying an upload whose
    response was lost cannot leave a duplicate copy of the proposal in Drive.
  * Folder get-or-create is serialised (per process by a lock, across Autoscale instances by a Postgres advisory
    lock), escapes the name properly, and always picks the OLDEST match, so two uploads for the same seller can't
    scatter files over duplicate folders.
  * The refreshed access token is cached in-process and refreshed under a lock, so concurrent uploads share one
    refresh instead of each hitting Google (the expiry used to be unknown, so EVERY upload refreshed).
"""
from __future__ import annotations

import contextlib
import errno
import http.client
import io
import json
import logging
import os
import random
import secrets
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

from app.db import get_connection

_logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"

# --- retry policy (see call_with_retries) -----------------------------------------------------------------------------
MAX_ATTEMPTS = 4                  # total tries per Google call: the first one plus up to 3 retries
BACKOFF_BASE_SECONDS = 1.0        # ceiling of the first wait; doubles each retry (1s, 2s, 4s ...)
BACKOFF_CAP_SECONDS = 8.0         # no single wait is ever longer than this, whatever Retry-After says
RETRY_BUDGET_SECONDS = 60.0       # a retry is not started if the call has already been at it this long
HTTP_TIMEOUT_SECONDS = 30         # per Google HTTP request - a hung connection becomes a retryable timeout
_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024   # resumable chunk size; Google requires a multiple of 256 KiB
_FOLDER_NAME_MAX = 120
_FOLDER_CACHE_TTL_SECONDS = 600   # how long a seller's folder id is trusted before it is looked up again
_FOLDER_LOCK_TIMEOUT = "20s"      # longest an upload waits for another instance's folder get-or-create

# Indirection so the tests can run the retry logic instantly and deterministically.
_sleep = time.sleep
_rand = random.random
_monotonic = time.monotonic


# ---------------------------------------------------------------------------
# Errors: what the endpoint is told, already translated
# ---------------------------------------------------------------------------

class DriveError(Exception):
    """A Drive upload failure translated into what the caller should say and send: a plain-language `message`, the
    HTTP `status_code` to answer with, whether trying again can help (`retryable`) and a short machine-readable
    `code`. `ref` is set for unexpected failures so a user report can be matched to the log line."""

    def __init__(self, message: str, *, status_code: int, retryable: bool, code: str, ref: str | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.code = code
        self.ref = ref

    def payload(self) -> dict:
        out = {"detail": self.message, "retryable": self.retryable, "code": self.code}
        if self.ref:
            out["ref"] = self.ref
        return out


class DriveAuthRequired(DriveError):
    """Google no longer accepts the stored authorization (revoked, expired or never granted): the user must go
    through the consent popup again. upload_proposal() answers this with the {needs_auth: True} contract."""

    def __init__(self, message: str = "Google Drive authorization required."):
        # Deliberately NOT 401: the page's session guard (session-guard.js) redirects to /login on any 401, which
        # would read a Drive re-authorization as an expired app login.
        super().__init__(message, status_code=403, retryable=False, code="auth_required")


# ---------------------------------------------------------------------------
# Failure classification + bounded retries
# ---------------------------------------------------------------------------

TRANSIENT = "transient"     # asking again soon can work
AUTH = "auth"               # the stored authorization was rejected
PERMANENT = "permanent"     # asking again can't help (permissions, quota, bad request, ...)
UNKNOWN = "unknown"         # not a Google/network/database error at all - a bug

_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
# Google's 403 flavours that just mean "slow down": the same request succeeds a moment later.
_RATE_LIMIT_REASONS = frozenset({"ratelimitexceeded", "userratelimitexceeded", "sharingratelimitexceeded"})
_TRANSIENT_ERRNO_NAMES = (
    "ECONNRESET", "ECONNABORTED", "ECONNREFUSED", "EPIPE", "ETIMEDOUT", "ENETUNREACH", "ENETDOWN", "EHOSTUNREACH",
    "EHOSTDOWN", "WSAETIMEDOUT",
)
_TRANSIENT_ERRNOS = frozenset(getattr(errno, n) for n in _TRANSIENT_ERRNO_NAMES if hasattr(errno, n))


@dataclass(frozen=True)
class Failure:
    kind: str
    status: int | None = None        # HTTP status when Google answered
    reason: str = ""                 # Google's machine reason ("rateLimitExceeded", "storageQuotaExceeded", ...)
    retry_after: float | None = None  # seconds, from a Retry-After header
    detail: str = ""                 # Google's message for the log (never tokens - Google doesn't echo them)


def _http_error_reason(exc) -> str:
    """The machine-readable reason inside a Google API error body, '' when there isn't one."""
    try:
        err = json.loads(exc.content.decode("utf-8"))["error"]
        errors = err.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict) and errors[0].get("reason"):
            return str(errors[0]["reason"])
        return str(err.get("status") or "")
    except Exception:  # noqa: BLE001 - any shape other than Google's JSON error is just "no reason"
        return ""


def _retry_after_seconds(exc) -> float | None:
    try:
        raw = exc.resp.get("retry-after")
        return float(raw) if raw is not None and str(raw).strip().replace(".", "", 1).isdigit() else None
    except Exception:  # noqa: BLE001
        return None


def classify(exc: BaseException) -> Failure:
    """Sort an exception from a Drive / token / database call into transient, auth, permanent or unknown."""
    try:
        from googleapiclient.errors import HttpError
    except ImportError:  # pragma: no cover - the google libs are an optional install
        HttpError = ()   # isinstance(x, ()) is simply False

    if isinstance(exc, DriveAuthRequired):
        return Failure(AUTH)
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", None)
        try:
            status = int(status)
        except (TypeError, ValueError):
            status = None
        reason = _http_error_reason(exc)
        detail = getattr(exc, "reason", "") or ""
        if status in _TRANSIENT_STATUSES or (status is not None and status >= 500):
            return Failure(TRANSIENT, status, reason, _retry_after_seconds(exc), detail)
        if status == 403 and reason.lower() in _RATE_LIMIT_REASONS:
            return Failure(TRANSIENT, status, reason, _retry_after_seconds(exc), detail)
        if status == 401:
            return Failure(AUTH, status, reason, None, detail)
        return Failure(PERMANENT, status, reason, None, detail)

    try:
        from google.auth import exceptions as gauth_exc
    except ImportError:  # pragma: no cover - the google libs are an optional install
        gauth_exc = None
    if gauth_exc is not None:
        if isinstance(exc, gauth_exc.RefreshError):
            # A refresh the token endpoint answered with 5xx / "temporarily_unavailable" is flagged retryable by
            # google-auth itself; "invalid_grant" (revoked / expired / password changed) is the user's cue to
            # re-authorize, and "invalid_client" is a deployment problem no consent popup can fix.
            if getattr(exc, "retryable", False):
                return Failure(TRANSIENT, reason="refresh_unavailable")
            payload = exc.args[1] if len(exc.args) > 1 and isinstance(exc.args[1], dict) else {}
            code = str(payload.get("error") or "")
            if code == "invalid_client":
                return Failure(PERMANENT, reason="invalid_client")
            return Failure(AUTH, reason=code or "refresh_rejected")
        if isinstance(exc, (gauth_exc.TransportError, gauth_exc.TimeoutError)):
            return Failure(TRANSIENT, reason="transport_error")

    # Network level: the connection never produced an HTTP answer.
    if isinstance(exc, (TimeoutError, ConnectionError, ssl.SSLError, socket.gaierror, http.client.HTTPException)):
        return Failure(TRANSIENT, reason=type(exc).__name__)
    try:
        import httplib2
        if isinstance(exc, httplib2.HttpLib2Error):
            return Failure(TRANSIENT, reason=type(exc).__name__)
    except ImportError:  # pragma: no cover
        pass
    if isinstance(exc, OSError) and exc.errno in _TRANSIENT_ERRNOS:
        return Failure(TRANSIENT, reason=type(exc).__name__)

    # The shared database (token row, proposal row, stored file) dropping or timing out is just as transient.
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return Failure(TRANSIENT, reason="database_unavailable")
    return Failure(UNKNOWN)


def _backoff_delay(attempt: int, retry_after: float | None = None) -> float:
    """Seconds to wait before retry number `attempt` (1 = the first retry): exponential, capped, with "equal
    jitter" - uniformly between half and all of the ceiling, so concurrent clients don't retry in lockstep. A
    Retry-After from Google raises the wait, but never above BACKOFF_CAP_SECONDS."""
    ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    delay = ceiling / 2 + _rand() * ceiling / 2
    if retry_after:
        delay = max(delay, min(float(retry_after), BACKOFF_CAP_SECONDS))
    return min(delay, BACKOFF_CAP_SECONDS)


def call_with_retries(fn, what: str, *, attempts: int | None = None):
    """Run fn(); when it fails in a way that is transient by nature (see classify), wait (bounded exponential
    backoff + jitter) and run it again, at most `attempts` times in total and never past RETRY_BUDGET_SECONDS.
    Anything else - and the last transient failure - is raised unchanged for the caller to translate.

    `fn` must be safe to run again: every call site either re-issues an idempotent read, upserts, or carries a
    client-chosen Drive file id."""
    attempts = attempts or MAX_ATTEMPTS
    started = _monotonic()
    attempt = 1
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classified right below; non-transient failures are re-raised
            failure = classify(exc)
            if failure.kind != TRANSIENT or attempt >= attempts:
                raise
            delay = _backoff_delay(attempt, failure.retry_after)
            if _monotonic() - started + delay > RETRY_BUDGET_SECONDS:
                raise
            _logger.warning(
                "Drive %s failed (attempt %d/%d: %s status=%s reason=%s) - retrying in %.1fs",
                what, attempt, attempts, type(exc).__name__, failure.status, failure.reason or "-", delay,
            )
            _sleep(delay)
            attempt += 1


def to_drive_error(exc: BaseException, *, stage: str = "", proposal_id: str = "") -> DriveError:
    """Translate any exception that escaped the upload into a DriveError and log it with the detail needed to
    diagnose it later (exception class, HTTP status, Google reason, stage, proposal id - never tokens)."""
    if isinstance(exc, DriveError):
        return exc
    failure = classify(exc)
    where = f"proposal={proposal_id or '-'} stage={stage or '-'}"
    if failure.kind == TRANSIENT:
        _logger.warning(
            "Drive upload gave up on a transient failure: %s %s exc=%s status=%s reason=%s",
            where, "(database)" if failure.reason == "database_unavailable" else "", type(exc).__name__,
            failure.status, failure.reason or "-",
        )
        if failure.reason == "database_unavailable":
            message = "The app couldn't reach its database for a moment. Nothing was lost - please try again."
        else:
            message = ("Google Drive is temporarily unavailable or didn't answer in time. "
                       "Nothing was lost - please try again in a moment.")
        return DriveError(message, status_code=503, retryable=True, code="drive_unavailable")
    if failure.kind == AUTH:
        return DriveAuthRequired()
    if failure.kind == PERMANENT:
        _logger.error(
            "Drive upload rejected by Google: %s exc=%s status=%s reason=%s detail=%s",
            where, type(exc).__name__, failure.status, failure.reason or "-", failure.detail or "-",
        )
        return DriveError(_permanent_message(failure), status_code=424, retryable=False, code="drive_rejected")
    ref = secrets.token_hex(3)
    _logger.error("Drive upload crashed unexpectedly: ref=%s %s exc=%s", ref, where, type(exc).__name__, exc_info=exc)
    return DriveError(
        f"The Drive upload failed unexpectedly (reference {ref}). Please try again; if it keeps happening, "
        f"send that reference to the app admin.",
        status_code=500, retryable=False, code="unexpected", ref=ref,
    )


def _permanent_message(failure: Failure) -> str:
    reason = (failure.reason or "").lower()
    if failure.reason == "invalid_client":
        return ("Google rejected this app's Drive credentials (DRIVE_CLIENT_ID / DRIVE_CLIENT_SECRET). "
                "An admin needs to check them.")
    if reason == "generatedidnotsupported":
        return ("Google Drive rejected the pre-generated file ID for this Google Sheets upload. "
                "An app admin needs to check the upload configuration.")
    if reason in {"storagequotaexceeded", "dailylimitexceeded", "quotaexceeded"}:
        return ("The Google Drive account this app uploads with is out of storage or quota. "
                "An admin needs to free up space or wait for the quota to reset.")
    if failure.status == 403:
        return ("Google Drive refused the upload (permission problem on the shared Drive folder or account). "
                "An admin needs to check the app's Drive access.")
    if failure.status == 404:
        return ("The Drive destination folder (DRIVE_ROOT_FOLDER_ID) could not be found. "
                "An admin needs to check it still exists and is shared with the app's Google account.")
    suffix = f" ({failure.detail})" if failure.detail else ""
    return f"Google Drive rejected the upload{suffix}. Please try again, or ask an admin if it keeps happening."


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def _is_configured() -> bool:
    return bool(
        os.environ.get("DRIVE_CLIENT_ID")
        and os.environ.get("DRIVE_CLIENT_SECRET")
        and os.environ.get("DRIVE_ROOT_FOLDER_ID")
    )


def _client_config() -> dict:
    return {
        "web": {
            "client_id": os.environ.get("DRIVE_CLIENT_ID", ""),
            "client_secret": os.environ.get("DRIVE_CLIENT_SECRET", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [],
        }
    }


def get_auth_url(redirect_uri: str) -> str:
    """Return the Google OAuth2 authorization URL for the user to visit."""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        # google-auth-oauthlib >=1.0 auto-generates a PKCE code_verifier
        # here by default and folds its code_challenge into the auth URL.
        # That verifier only ever lives on THIS Flow object, in THIS
        # request — but /api/drive/callback handles the redirect in a
        # separate request and builds a brand-new Flow to exchange the
        # code, with no way to recover the original verifier. Google's
        # token endpoint then rejects the exchange with exactly the error
        # reported: "(invalid_grant) Missing code verifier." PKCE exists to
        # protect *public* clients that can't hold a secret; this is a
        # confidential "web" client (it already authenticates with
        # DRIVE_CLIENT_SECRET), so turning it off is correct here, not a
        # workaround — must match the same flag in exchange_code() below.
        autogenerate_code_verifier=False,
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url


def exchange_code(code: str, redirect_uri: str) -> None:
    """Exchange OAuth2 authorization code for tokens and persist them."""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        autogenerate_code_verifier=False,  # must match get_auth_url() — see comment there
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_credentials(creds)
    _forget_cached_credentials()   # the next upload must start from the freshly authorized token, not a cached one


def _save_credentials(creds) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO drive_tokens
                (id, token, refresh_token, token_uri, client_id, client_secret, scopes)
            VALUES ('default', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                token = EXCLUDED.token,
                -- never overwrite a stored refresh token with NULL: a response that omits one (Google normally
                -- only issues it at consent time) would otherwise lock every later upload out of the account.
                refresh_token = COALESCE(EXCLUDED.refresh_token, drive_tokens.refresh_token),
                token_uri = EXCLUDED.token_uri,
                client_id = EXCLUDED.client_id,
                client_secret = EXCLUDED.client_secret,
                scopes = EXCLUDED.scopes,
                updated_at = now()
            """,
            (
                creds.token,
                creds.refresh_token,
                creds.token_uri,
                creds.client_id,
                creds.client_secret,
                list(creds.scopes) if creds.scopes else [],
            ),
        )


# One shared Drive identity serves every planner, so every upload needs the same access token. It is cached in
# THIS process after a refresh and refreshed under a lock, so a burst of uploads costs one token-endpoint call.
# (Other Autoscale instances keep their own cache; two instances refreshing at once is harmless - Google hands
# each a valid, independent access token and the refresh token never changes.)
_creds_lock = threading.Lock()
_cached_creds = None


def _forget_cached_credentials() -> None:
    global _cached_creds
    with _creds_lock:
        _cached_creds = None


def _token_request():
    """The google-auth HTTP transport, with a sane per-request timeout (its default is two minutes)."""
    from google.auth.transport.requests import Request as GRequest

    class _BoundedRequest(GRequest):
        def __call__(self, url, method="GET", body=None, headers=None, timeout=HTTP_TIMEOUT_SECONDS, **kwargs):
            return super().__call__(url, method=method, body=body, headers=headers, timeout=timeout, **kwargs)

    return _BoundedRequest()


def _load_credentials():
    """Load stored OAuth2 credentials, refreshing if expired. Returns None if not authorized (nothing stored, the
    refresh token was revoked/expired, or the stored row is unusable) - the caller then runs the consent flow.

    Raises a transient error (see classify) when the database or Google's token endpoint can't be reached even
    after retries: that is NOT a reason to send the planner through a re-authorization they don't need."""
    global _cached_creds
    with _creds_lock:
        cached = _cached_creds
        if cached is not None and cached.valid:
            return cached
        creds = _load_credentials_locked()
        # Only a credential with a KNOWN expiry may be reused later (expiry None reads as "never expires").
        _cached_creds = creds if creds is not None and creds.expiry is not None else None
        return creds


def _load_credentials_locked():
    try:
        from google.oauth2.credentials import Credentials

        def read_row():
            with get_connection() as conn:
                return conn.execute(
                    """
                    SELECT token, refresh_token, token_uri, client_id, client_secret, scopes
                    FROM drive_tokens
                    WHERE id = 'default'
                    """
                ).fetchone()

        data = call_with_retries(read_row, "load stored token")
        if not data:
            return None
        creds = Credentials(
            token=data["token"],
            refresh_token=data.get("refresh_token"),
            token_uri=data["token_uri"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            scopes=data.get("scopes"),
        )
        # Expiry is not stored in drive_tokens, so Credentials is reconstructed
        # with expiry=None. google-auth reports that as expired=False, even when
        # the saved access token is long expired. Refresh whenever expiry is
        # unknown (or known to have passed) before making Drive API calls.
        if creds.expiry is None or creds.expired:
            if not creds.refresh_token:
                return None
            refresh_token = creds.refresh_token
            try:
                call_with_retries(lambda: creds.refresh(_token_request()), "refresh token")
            except Exception as exc:  # noqa: BLE001
                failure = classify(exc)
                if failure.kind == TRANSIENT:
                    raise
                _logger.warning(
                    "Drive token refresh rejected (%s reason=%s)", type(exc).__name__, failure.reason or "-"
                )
                if failure.kind == PERMANENT:
                    raise
                return None   # invalid_grant & co: the stored authorization is dead - re-authorize
            # Google normally does not issue a replacement refresh token.
            # Preserve the stored token if the refresh response omits one.
            if not creds.refresh_token:
                creds = Credentials(
                    token=creds.token,
                    refresh_token=refresh_token,
                    token_uri=creds.token_uri,
                    client_id=creds.client_id,
                    client_secret=creds.client_secret,
                    scopes=creds.scopes,
                    expiry=creds.expiry,
                )
            try:
                call_with_retries(lambda: _save_credentials(creds), "save refreshed token")
            except Exception as exc:  # noqa: BLE001 - the refreshed token still works for this upload
                _logger.warning("Refreshed Drive token could not be saved (%s)", type(exc).__name__)
        return creds
    except DriveError:
        raise
    except Exception as e:  # noqa: BLE001
        if classify(e).kind in (TRANSIENT, PERMANENT):
            raise   # a failure the caller can name precisely (503 / "check credentials") - not an auth problem
        # Anything unrecognised (e.g. a corrupt stored row) leads the caller to the normal reauthorization
        # flow, without logging token material or HTTP bodies.
        _logger.warning("Drive credential load/refresh failed (%s)", type(e).__name__)
        return None


# ---------------------------------------------------------------------------
# Folder get-or-create
# ---------------------------------------------------------------------------

def _q_literal(value: str) -> str:
    """A string as a Drive search-query literal: wrapped in single quotes with backslash and single quote escaped
    (the backslash first - Drive treats \\ as the escape character, so an unescaped one breaks the query)."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def normalize_folder_name(raw) -> str:
    """A seller email (or whatever came in its place) as a safe Drive folder name: whitespace/newlines collapsed,
    control and zero-width characters dropped, length capped; 'unassigned' when nothing usable is left. Ordinary
    email addresses pass through unchanged, so existing folders keep matching."""
    name = " ".join(str(raw or "").split())
    name = "".join(ch for ch in name if ch.isprintable())
    return name[:_FOLDER_NAME_MAX].strip() or "unassigned"


_folder_cache: dict[tuple[str, str], tuple[str, float]] = {}
_folder_locks: dict[tuple[str, str], threading.Lock] = {}
_folder_registry_lock = threading.Lock()


def _folder_lock_for(key: tuple[str, str]) -> threading.Lock:
    with _folder_registry_lock:
        lock = _folder_locks.get(key)
        if lock is None:
            lock = _folder_locks[key] = threading.Lock()
        return lock


def _forget_folder(parent_id: str, name: str) -> None:
    with _folder_registry_lock:
        _folder_cache.pop((parent_id, normalize_folder_name(name)), None)


@contextlib.contextmanager
def _folder_lock_across_instances(key_text: str):
    """Hold a Postgres advisory lock for this (parent, name) while a folder is looked up / created, so two
    Autoscale instances handling uploads for the same seller at the same moment can't both conclude "no such
    folder yet" and each create one. Best effort by design: if the database can't be used (or the wait times out)
    the upload goes ahead unlocked - the per-process lock still holds, and the worst case is the duplicate-folder
    outcome this module had before, never a failed upload. Closing the connection releases the lock."""
    conn = None
    try:
        conn = get_connection()
        conn.execute(f"SET LOCAL lock_timeout = '{_FOLDER_LOCK_TIMEOUT}'")
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key_text,))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Drive folder lock unavailable (%s) - continuing without it", type(exc).__name__)
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
        conn = None
    try:
        yield
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


def _find_folder(service, parent_id: str, name: str) -> str | None:
    query = (
        f"name = {_q_literal(name)} and mimeType = '{_FOLDER_MIME}' "
        f"and {_q_literal(parent_id)} in parents and trashed = false"
    )
    result = service.files().list(
        q=query,
        fields="files(id, name, createdTime)",
        orderBy="createdTime",      # several matches (an old race) -> always the OLDEST, so every upload agrees
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = result.get("files", [])
    return files[0]["id"] if files else None


def _find_or_create_folder(service, parent_id: str, name: str) -> str:
    """Find a sub-folder by name under parent_id, or create one - at most one per (parent, name) however many
    uploads ask at once."""
    name = normalize_folder_name(name)
    key = (parent_id, name)
    with _folder_lock_for(key):
        cached = _folder_cache.get(key)
        if cached and _monotonic() - cached[1] < _FOLDER_CACHE_TTL_SECONDS:
            return cached[0]
        with _folder_lock_across_instances(f"drive-folder:{parent_id}/{name}"):
            def find_then_create() -> str:
                # Look first on EVERY attempt: if an earlier attempt's create actually went through but its reply
                # was lost, this finds that folder instead of creating a second one.
                existing = _find_folder(service, parent_id, name)
                if existing:
                    return existing
                folder = service.files().create(
                    body={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]},
                    fields="id",
                    supportsAllDrives=True,
                ).execute()
                return folder["id"]

            folder_id = call_with_retries(find_then_create, "find/create folder")
        _folder_cache[key] = (folder_id, _monotonic())
        return folder_id


# ---------------------------------------------------------------------------
# The upload itself
# ---------------------------------------------------------------------------

def _build_service(creds):
    """The Drive v3 client, with a per-request timeout so a hung connection becomes a retryable error instead of
    holding the upload open for as long as the OS allows."""
    import google_auth_httplib2
    from googleapiclient.discovery import build
    from googleapiclient.http import build_http

    # build_http() (not a bare httplib2.Http) matters: it stops httplib2 treating HTTP 308 as a redirect, which for
    # Drive resumable uploads means "resume incomplete" - the answer to the status query after a failed chunk.
    base = build_http()
    base.timeout = HTTP_TIMEOUT_SECONDS
    http = google_auth_httplib2.AuthorizedHttp(creds, http=base)
    return build("drive", "v3", http=http, cache_discovery=False)


def _new_file_id(service) -> str | None:
    """A Drive-issued file id to create the file under. Creating with a client-chosen id makes the upload
    idempotent: if a reply is lost and the upload is retried, Drive answers 409 "already exists" instead of
    making a second copy. Best effort - without an id the upload still works, just not idempotently."""
    try:
        resp = call_with_retries(
            lambda: service.files().generateIds(count=1, space="drive").execute(), "generate file id"
        )
        ids = resp.get("ids") or []
        return ids[0] if ids else None
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Drive file id not pre-generated (%s) - uploading without an idempotency key", type(exc).__name__)
        return None


def _create_file(service, *, name: str, folder_id: str, content: bytes, as_google_sheet: bool) -> dict:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseUpload

    # Drive does not accept pre-generated IDs for Docs Editors formats such as converted Google Sheets.
    file_id = None if as_google_sheet else _new_file_id(service)
    body: dict = {"name": name, "parents": [folder_id]}
    if file_id:
        body["id"] = file_id
    if as_google_sheet:
        body["mimeType"] = _SHEET_MIME
    media = MediaIoBaseUpload(
        io.BytesIO(content), mimetype=_XLSX_MIME, chunksize=_UPLOAD_CHUNK_BYTES, resumable=True,
    )
    request = service.files().create(
        body=body, media_body=media, fields="id, name, webViewLink, mimeType", supportsAllDrives=True,
    )
    try:
        response = None
        while response is None:
            # Resumable: the request object remembers its session, so a retried chunk carries on where the last
            # one stopped instead of re-sending (or re-creating) everything.
            _status, response = call_with_retries(request.next_chunk, "upload file")
        return response
    except HttpError as exc:
        # 409 with OUR id = an earlier attempt of this very upload did land (the reply was what got lost).
        if file_id and getattr(exc.resp, "status", None) == 409:
            return call_with_retries(
                lambda: service.files().get(
                    fileId=file_id, fields="id, name, webViewLink, mimeType", supportsAllDrives=True,
                ).execute(),
                "fetch already-uploaded file",
            )
        raise


def _share_with_link(service, file_id: str) -> bool:
    """Anyone-with-the-link can edit (what the planners expect to paste into the email). Not fatal: an org policy
    that forbids it, or Google being slow, must not fail an upload that already succeeded - but the caller is told."""
    try:
        call_with_retries(
            lambda: service.permissions().create(
                fileId=file_id, body={"type": "anyone", "role": "writer"}, supportsAllDrives=True,
            ).execute(),
            "share file",
        )
        return True
    except Exception as exc:  # noqa: BLE001
        failure = classify(exc)
        _logger.warning(
            "Drive file shared-link not set (%s status=%s reason=%s) file=%s",
            type(exc).__name__, failure.status, failure.reason or "-", file_id,
        )
        return False


def _web_link(created: dict) -> str | None:
    link = created.get("webViewLink")
    if link or not created.get("id"):
        return link
    if created.get("mimeType") == _SHEET_MIME:
        return f"https://docs.google.com/spreadsheets/d/{created['id']}/edit"
    return f"https://drive.google.com/file/d/{created['id']}/view"


_uploads_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


@contextlib.contextmanager
def upload_slot(proposal_id: str):
    """One upload per proposal at a time (per process): a double click / second tab would otherwise put two copies
    of the proposal in Drive. The second caller is told to wait instead."""
    if not proposal_id:
        yield
        return
    with _in_flight_lock:
        if proposal_id in _uploads_in_flight:
            busy = True
        else:
            busy = False
            _uploads_in_flight.add(proposal_id)
    if busy:
        raise DriveError(
            "This proposal is already being uploaded - give it a few seconds.",
            status_code=409, retryable=False, code="upload_in_progress",
        )
    try:
        yield
    finally:
        with _in_flight_lock:
            _uploads_in_flight.discard(proposal_id)


def upload_proposal(
    local_path: Path,
    seller_email: str,
    *,
    as_google_sheet: bool = True,
    redirect_uri: str = "",
    content: bytes | None = None,
    proposal_id: str = "",
) -> dict:
    """
    Upload a proposal workbook to Drive under a folder named after `seller_email`. The bytes come from `content`
    when given (the shared copy in the database - see proposal_files.py), otherwise from `local_path`; the file is
    named after `local_path`.

    Returns:
        {uploaded: True, file_id, shareable_link, folder_id, filename, link_shared}
        {uploaded: False, needs_auth: True}  — caller should redirect to auth_url
        {uploaded: False, reason: str}       — not configured or import error
    Raises DriveError for every real failure (see its docstring) - never a bare exception.
    """
    if not _is_configured():
        return {
            "uploaded": False,
            "reason": "Google Drive not configured. Set DRIVE_CLIENT_ID, DRIVE_CLIENT_SECRET, "
                      "and DRIVE_ROOT_FOLDER_ID in .env.",
            "local_path": str(local_path),
            "filename": local_path.name,
        }

    try:
        import googleapiclient.discovery  # noqa: F401 - presence check only
        import googleapiclient.http  # noqa: F401
    except ImportError:
        return {
            "uploaded": False,
            "reason": "google-api-python-client not installed. "
                      "pip install google-api-python-client google-auth",
            "local_path": str(local_path),
        }

    stage = "load credentials"
    try:
        creds = _load_credentials()
        if creds is None:
            return {
                "uploaded": False,
                "needs_auth": True,
                "reason": "Google Drive authorization required.",
            }

        stage = "read file"
        if content is None:
            try:
                content = Path(local_path).read_bytes()
            except FileNotFoundError:
                raise DriveError(
                    "The generated .xlsx is no longer available on the server. "
                    "Click Generate Proposal again, then upload.",
                    status_code=410, retryable=False, code="file_unavailable",
                ) from None

        root_folder_id = os.environ["DRIVE_ROOT_FOLDER_ID"]
        service = _build_service(creds)

        stage = "find/create folder"
        folder_name = normalize_folder_name(seller_email)
        folder_id = _find_or_create_folder(service, root_folder_id, folder_name)

        stage = "upload file"
        try:
            created = _create_file(
                service, name=Path(local_path).stem, folder_id=folder_id, content=content,
                as_google_sheet=as_google_sheet,
            )
        except Exception as exc:  # noqa: BLE001
            # A cached folder id that was deleted since: forget it and make the planner's retry look it up afresh.
            if getattr(getattr(exc, "resp", None), "status", None) == 404:
                _forget_folder(root_folder_id, folder_name)
            raise

        stage = "share file"
        link_shared = _share_with_link(service, created["id"])

        return {
            "uploaded": True,
            "file_id": created["id"],
            "shareable_link": _web_link(created),
            "folder_id": folder_id,
            "filename": created.get("name"),
            "link_shared": link_shared,
        }
    except DriveAuthRequired:
        _forget_cached_credentials()
        return {"uploaded": False, "needs_auth": True, "reason": "Google Drive authorization required."}
    except DriveError:
        raise
    except Exception as exc:  # noqa: BLE001 - translated, logged, and raised as a DriveError
        translated = to_drive_error(exc, stage=stage, proposal_id=proposal_id)
        if isinstance(translated, DriveAuthRequired):
            _forget_cached_credentials()   # the token Google just rejected must not be served from the cache again
            return {"uploaded": False, "needs_auth": True, "reason": "Google Drive authorization required."}
        raise translated from exc
