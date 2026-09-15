"""
Tests for app/auth.py against an in-memory fake Postgres layer (no real
DATABASE_URL needed) — mirrors the mocking pattern already used elsewhere
in this test suite (see test_postgres_persistence.py's own docstring for
why a fake/throwaway backing store, not a live DB, is the right call here).
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import auth


class _FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, users, sessions):
        self.users = users
        self.sessions = sessions

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        users, sessions = self.users, self.sessions

        if s.startswith("INSERT INTO users"):
            uid, email, ph, ps, is_admin = params
            users[uid] = {"id": uid, "email": email, "password_hash": ph, "password_salt": ps,
                          "is_admin": is_admin, "disabled": False, "created_at": datetime.now(timezone.utc)}
            return _FakeResult(rowcount=1)
        if s.startswith("SELECT id, email, password_hash, password_salt, is_admin, disabled, created_at FROM users WHERE lower(email)"):
            (email,) = params
            for u in users.values():
                if u["email"].lower() == email.lower():
                    return _FakeResult([dict(u)])
            return _FakeResult([])
        if s.startswith("SELECT id, email, is_admin, disabled, created_at FROM users WHERE id"):
            (uid,) = params
            u = users.get(uid)
            return _FakeResult([dict(u)] if u else [])
        if s.startswith("SELECT id, email, is_admin, disabled, created_at FROM users ORDER BY email"):
            return _FakeResult([dict(r) for r in sorted(users.values(), key=lambda u: u["email"])])
        if s.startswith("SELECT count(*) AS n FROM users"):
            return _FakeResult([{"n": len(users)}])
        if s.startswith("UPDATE users SET password_hash"):
            ph, ps, uid = params
            if uid in users:
                users[uid]["password_hash"], users[uid]["password_salt"] = ph, ps
            return _FakeResult(rowcount=1)
        if s.startswith("UPDATE users SET is_admin"):
            is_admin, uid = params
            if uid in users:
                users[uid]["is_admin"] = is_admin
            return _FakeResult(rowcount=1)
        if s.startswith("UPDATE users SET disabled"):
            disabled, uid = params
            if uid in users:
                users[uid]["disabled"] = disabled
            return _FakeResult(rowcount=1)
        if s.startswith("DELETE FROM users WHERE id"):
            (uid,) = params
            existed = uid in users
            users.pop(uid, None)
            for tok in [t for t, sess in sessions.items() if sess["user_id"] == uid]:
                sessions.pop(tok, None)
            return _FakeResult(rowcount=1 if existed else 0)
        if s.startswith("INSERT INTO sessions"):
            token, uid, expires_at = params
            sessions[token] = {"token": token, "user_id": uid, "expires_at": expires_at}
            return _FakeResult(rowcount=1)
        if s.startswith("SELECT u.id, u.email, u.is_admin, u.disabled, u.created_at FROM sessions"):
            (token,) = params
            sess = sessions.get(token)
            if not sess or sess["expires_at"] <= datetime.now(timezone.utc):
                return _FakeResult([])
            u = users.get(sess["user_id"])
            return _FakeResult([dict(u)] if u else [])
        if s.startswith("UPDATE sessions SET expires_at"):
            expires_at, token = params
            sess = sessions.get(token)
            if sess and sess["expires_at"] > datetime.now(timezone.utc):
                sess["expires_at"] = expires_at
            return _FakeResult(rowcount=1)
        if s.startswith("DELETE FROM sessions WHERE token"):
            (token,) = params
            existed = token in sessions
            sessions.pop(token, None)
            return _FakeResult(rowcount=1 if existed else 0)
        if s.startswith("DELETE FROM sessions WHERE user_id"):
            (uid,) = params
            for tok in [t for t, sess in sessions.items() if sess["user_id"] == uid]:
                sessions.pop(tok, None)
            return _FakeResult()
        if s.startswith("DELETE FROM sessions WHERE expires_at"):
            expired = [t for t, sess in sessions.items() if sess["expires_at"] <= datetime.now(timezone.utc)]
            for t in expired:
                sessions.pop(t)
            return _FakeResult(rowcount=len(expired))
        raise AssertionError(f"Unhandled fake SQL in test_auth.py: {s!r}")


@pytest.fixture
def fake_db(monkeypatch):
    """Patches app.auth.get_connection with an in-memory fake, fresh per test."""
    users: dict = {}
    sessions: dict = {}
    monkeypatch.setattr(auth, "get_connection", lambda: _FakeConn(users, sessions))
    return users, sessions


def test_password_hash_and_verify_roundtrip():
    h, s = auth.hash_password("correct horse battery staple 42")
    assert auth.verify_password("correct horse battery staple 42", h, s) is True
    assert auth.verify_password("wrong password entirely", h, s) is False


def test_password_policy_enforces_minimum_length():
    assert auth.validate_password_policy("short") is not None
    assert auth.validate_password_policy("this is definitely long enough") is None


def test_bootstrap_creates_first_admin_and_is_idempotent(fake_db, monkeypatch):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_EMAIL", "admin@entravision.com")
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", "bootstrap password long enough")

    auth.bootstrap_admin_from_env()
    assert auth.count_users() == 1
    admin = auth.get_user_by_email("admin@entravision.com")
    assert admin["is_admin"] is True

    auth.bootstrap_admin_from_env()  # second call: no-op, doesn't raise or duplicate
    assert auth.count_users() == 1


def test_create_user_rejects_duplicate_email_and_weak_password(fake_db):
    auth.create_user("planner@entravision.com", "a long enough password")
    with pytest.raises(ValueError):
        auth.create_user("planner@entravision.com", "another long enough password")
    with pytest.raises(ValueError):
        auth.create_user("someone@entravision.com", "short")


def test_session_lifecycle(fake_db):
    user = auth.create_user("planner@entravision.com", "a long enough password")
    token = auth.create_session(user["id"])

    assert auth.get_user_by_session(token)["email"] == "planner@entravision.com"

    auth.refresh_session(token)
    assert auth.get_user_by_session(token) is not None

    auth.delete_session(token)
    assert auth.get_user_by_session(token) is None


def test_disabling_a_user_immediately_kills_their_session(fake_db):
    user = auth.create_user("planner@entravision.com", "a long enough password")
    token = auth.create_session(user["id"])
    assert auth.get_user_by_session(token) is not None

    auth.set_user_disabled(user["id"], True)
    assert auth.get_user_by_session(token) is None

    auth.set_user_disabled(user["id"], False)
    token2 = auth.create_session(user["id"])
    assert auth.get_user_by_session(token2) is not None


def test_changing_password_invalidates_existing_sessions(fake_db):
    user = auth.create_user("planner@entravision.com", "a long enough password")
    token = auth.create_session(user["id"])
    auth.set_user_password(user["id"], "brand new long enough password")
    assert auth.get_user_by_session(token) is None


def test_delete_user_cascades_to_sessions(fake_db):
    user = auth.create_user("planner@entravision.com", "a long enough password")
    token = auth.create_session(user["id"])
    assert auth.delete_user(user["id"]) is True
    assert auth.get_user_by_session(token) is None
    assert auth.get_user_by_id(user["id"]) is None


def test_expired_session_is_rejected(fake_db):
    user = auth.create_user("planner@entravision.com", "a long enough password")
    token = auth.create_session(user["id"])
    # Manually force it into the past, bypassing create_session's own
    # SESSION_LIFETIME_DAYS — this is what "5 days of inactivity" looks
    # like once time has actually passed.
    _, sessions = fake_db
    sessions[token]["expires_at"] = datetime.now(timezone.utc) - timedelta(days=1)
    assert auth.get_user_by_session(token) is None
