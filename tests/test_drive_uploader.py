from types import SimpleNamespace

from google.oauth2.credentials import Credentials

from app.services import drive_uploader


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql):
        return SimpleNamespace(
            fetchone=lambda: {
                "token": "stale-access-token",
                "refresh_token": "stored-refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "scopes": ["https://www.googleapis.com/auth/drive"],
            }
        )


def test_load_credentials_refreshes_when_expiry_was_not_persisted(monkeypatch):
    refresh_calls = []
    saved_credentials = []

    def fake_refresh(creds, _request):
        refresh_calls.append(True)
        creds.token = "fresh-access-token"
        # Google's refresh response often has no replacement refresh token.
        creds._refresh_token = None

    monkeypatch.setattr(drive_uploader, "get_connection", _FakeConnection)
    monkeypatch.setattr(Credentials, "refresh", fake_refresh)
    monkeypatch.setattr(
        drive_uploader, "_save_credentials", saved_credentials.append
    )

    creds = drive_uploader._load_credentials()

    assert refresh_calls == [True]
    assert creds is not None
    assert creds.token == "fresh-access-token"
    assert creds.refresh_token == "stored-refresh-token"
    assert saved_credentials == [creds]