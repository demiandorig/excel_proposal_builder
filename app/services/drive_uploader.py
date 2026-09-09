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
"""
from __future__ import annotations

import os

from app.db import get_connection

SCOPES = ["https://www.googleapis.com/auth/drive"]


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


def _save_credentials(creds) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO drive_tokens
                (id, token, refresh_token, token_uri, client_id, client_secret, scopes)
            VALUES ('default', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                token = EXCLUDED.token,
                refresh_token = EXCLUDED.refresh_token,
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


def _load_credentials():
    """Load stored OAuth2 credentials, refreshing if expired. Returns None if not authorized."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GRequest
        with get_connection() as conn:
            data = conn.execute(
                """
                SELECT token, refresh_token, token_uri, client_id, client_secret, scopes
                FROM drive_tokens
                WHERE id = 'default'
                """
            ).fetchone()
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
        if creds.expired and creds.refresh_token:
            creds.refresh(GRequest())
            _save_credentials(creds)
        return creds
    except Exception:
        return None


def upload_proposal(
    local_path: Path,
    seller_email: str,
    *,
    as_google_sheet: bool = True,
    redirect_uri: str = "",
) -> dict:
    """
    Upload `local_path` to Drive under a folder named after `seller_email`.

    Returns:
        {uploaded: True, file_id, shareable_link, folder_id, filename}
        {uploaded: False, needs_auth: True}  — caller should redirect to auth_url
        {uploaded: False, reason: str}       — not configured or import error
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
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return {
            "uploaded": False,
            "reason": "google-api-python-client not installed. "
                      "pip install google-api-python-client google-auth",
            "local_path": str(local_path),
        }

    creds = _load_credentials()
    if creds is None:
        return {
            "uploaded": False,
            "needs_auth": True,
            "reason": "Google Drive authorization required.",
        }

    root_folder_id = os.environ["DRIVE_ROOT_FOLDER_ID"]
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    folder_id = _find_or_create_folder(service, root_folder_id, seller_email or "unassigned")

    file_metadata: dict = {
        "name": local_path.stem,
        "parents": [folder_id],
    }
    if as_google_sheet:
        file_metadata["mimeType"] = "application/vnd.google-apps.spreadsheet"

    media = MediaFileUpload(
        str(local_path),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=False,
    )

    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, webViewLink",
        supportsAllDrives=True,
    ).execute()

    try:
        service.permissions().create(
            fileId=uploaded["id"],
            body={"type": "anyone", "role": "writer"},
            supportsAllDrives=True,
        ).execute()
    except Exception:
        pass

    return {
        "uploaded": True,
        "file_id": uploaded["id"],
        "shareable_link": uploaded.get("webViewLink"),
        "folder_id": folder_id,
        "filename": uploaded.get("name"),
    }


def _find_or_create_folder(service, parent_id: str, name: str) -> str:
    """Find a sub-folder by name under parent_id, or create one."""
    safe_name = name.replace("'", "\\'")
    query = (
        f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed = false"
    )
    result = service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = result.get("files", [])
    if files:
        return files[0]["id"]

    folder_metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(
        body=folder_metadata,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return folder["id"]
