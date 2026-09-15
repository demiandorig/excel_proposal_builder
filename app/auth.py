"""
Login for the Entravision Proposal Builder.

This is an internal tool — there's no self-service signup. An admin creates
accounts from the admin console's Users tab (or via the one-time
ADMIN_BOOTSTRAP_EMAIL/ADMIN_BOOTSTRAP_PASSWORD env-var bootstrap below, for
the very first account, since nobody can be logged in yet to create it).

WHY SERVER-SIDE SESSIONS (not a signed/stateless cookie or a JWT)
---------------------------------------------------------------------
A session here is a random opaque token, stored in the `sessions` table and
handed to the browser as an HttpOnly cookie — looked up in Postgres on every
request (see get_user_by_session). That's a deliberate choice over a
stateless signed cookie (Starlette's own SessionMiddleware) or a JWT: those
stay "valid" until they naturally expire, even if the account is disabled
or deleted a second later, unless you build separate revocation machinery.
A DB-backed session can just be deleted (see delete_sessions_for_user),
taking effect on that user's very next request — closer to what "remove
someone's access" should actually mean for an internal tool.

WHY SCRYPT (not bcrypt/argon2)
---------------------------------------------------------------------
hashlib.scrypt has been in the Python standard library since 3.6 — a
memory-hard, well-vetted KDF with zero new pip dependency. bcrypt/argon2
are also fine choices, but they're C extensions that need a matching wheel
for whatever Python/OS combination Replit's Nix environment ends up
building on; scrypt sidesteps that entirely.

PASSWORD POLICY
---------------------------------------------------------------------
Length only (MIN_PASSWORD_LENGTH), no forced mix of symbols/numbers/casing.
That's deliberate, not an oversight: a long passphrase is both easier to
remember/type AND more resistant to guessing than a short "complex"
password crammed with substitutions — the current NIST guidance (SP
800-63B) explicitly recommends length over composition rules for this
exact reason, and it matches what was actually asked for here ("long
passwords required but still very accessible").

SESSION LIFETIME
---------------------------------------------------------------------
Rolling 5-day expiry: every authenticated request pushes expires_at
forward another SESSION_LIFETIME_DAYS (see refresh_session, called from
main.py's auth middleware on every request). A planner using the tool
regularly is never interrupted; a session nobody's touched in 5 days stops
working. That's one reasonable reading of "session reset every 5 days" —
if the intent was instead an absolute cutoff regardless of activity, that's
a one-line change (stop refreshing expires_at after the initial login).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db import get_connection

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256  # sanity cap, not a security measure — scrypt's own cost dominates either way
SESSION_LIFETIME_DAYS = 5

_SCRYPT_N = 16384  # 2**14 — standard interactive-login cost factor (RFC 7914 §2)
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def validate_password_policy(password: str) -> Optional[str]:
    """Returns an error message if `password` fails the policy, else None."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    return None


def hash_password(password: str) -> tuple[str, str]:
    """Returns (password_hash_hex, password_salt_hex)."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return derived.hex(), salt.hex()


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    try:
        salt = bytes.fromhex(password_salt)
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
        )
        return hmac.compare_digest(derived.hex(), password_hash)
    except (ValueError, TypeError):
        return False  # malformed stored hash/salt — treat as "doesn't match", never raise into a login attempt


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _public_user(row: dict) -> dict:
    """Strips password_hash/password_salt before a user record ever leaves this module for a route handler."""
    return {
        "id": row["id"],
        "email": row["email"],
        "is_admin": row["is_admin"],
        "disabled": row["disabled"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def list_users() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, email, is_admin, disabled, created_at FROM users ORDER BY email"
        ).fetchall()
    return [_public_user(r) for r in rows]


def get_user_by_email(email: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, password_salt, is_admin, disabled, created_at "
            "FROM users WHERE lower(email) = lower(%s)",
            (email,),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, email, is_admin, disabled, created_at FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
    return _public_user(row) if row else None


def count_users() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT count(*) AS n FROM users").fetchone()
    return row["n"] if row else 0


def create_user(email: str, password: str, is_admin: bool = False) -> dict:
    """Raises ValueError with a user-facing message on any problem (blank
    email, weak password, duplicate email) — callers (the admin API, the
    bootstrap step below) surface that message directly."""
    email = (email or "").strip()
    if not email or "@" not in email:
        raise ValueError("A valid email is required.")
    policy_error = validate_password_policy(password)
    if policy_error:
        raise ValueError(policy_error)
    if get_user_by_email(email) is not None:
        raise ValueError(f"An account for '{email}' already exists.")

    user_id = secrets.token_urlsafe(16)  # same id style as proposal_id elsewhere in this app
    password_hash, password_salt = hash_password(password)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, password_salt, is_admin)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (user_id, email, password_hash, password_salt, is_admin),
        )
    return get_user_by_id(user_id)


def set_user_password(user_id: str, new_password: str) -> None:
    policy_error = validate_password_policy(new_password)
    if policy_error:
        raise ValueError(policy_error)
    password_hash, password_salt = hash_password(new_password)
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET password_hash = %s, password_salt = %s, updated_at = now() WHERE id = %s",
            (password_hash, password_salt, user_id),
        )
    # A password change invalidates every existing session for this user —
    # otherwise a stolen/shared session would survive the very fix meant to
    # shut it out.
    delete_sessions_for_user(user_id)


def set_user_admin(user_id: str, is_admin: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_admin = %s, updated_at = now() WHERE id = %s",
            (is_admin, user_id),
        )


def set_user_disabled(user_id: str, disabled: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET disabled = %s, updated_at = now() WHERE id = %s",
            (disabled, user_id),
        )
    if disabled:
        delete_sessions_for_user(user_id)  # takes effect immediately, not on next natural expiry


def delete_user(user_id: str) -> bool:
    with get_connection() as conn:
        result = conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
        deleted = result.rowcount > 0
    return deleted  # sessions cascade-delete via the FK (ON DELETE CASCADE)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (token, user_id, expires_at),
        )
    return token


def get_user_by_session(token: str) -> Optional[dict]:
    """Returns the user dict for a valid, unexpired session on an
    enabled account — None for anything else (missing/expired session,
    disabled/deleted user), so a caller never has to check those separately."""
    if not token:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.email, u.is_admin, u.disabled, u.created_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = %s AND s.expires_at > now()
            """,
            (token,),
        ).fetchone()
    if row is None or row["disabled"]:
        return None
    return _public_user(row)


def refresh_session(token: str) -> None:
    """Pushes a valid session's expiry another SESSION_LIFETIME_DAYS
    forward — called on every authenticated request (see main.py's auth
    middleware) so an active user's session effectively never lapses."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)
    with get_connection() as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = %s WHERE token = %s AND expires_at > now()",
            (expires_at, token),
        )


def delete_session(token: str) -> None:
    """Logout — deletes just this one session/device."""
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token = %s", (token,))


def delete_sessions_for_user(user_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def purge_expired_sessions() -> int:
    """Housekeeping — safe to call occasionally (e.g. at startup); expired
    sessions are already functionally invalid via get_user_by_session's own
    `expires_at > now()` check, this just keeps the table from growing
    forever. Returns the number of rows removed."""
    with get_connection() as conn:
        result = conn.execute("DELETE FROM sessions WHERE expires_at <= now()")
        return result.rowcount


# ---------------------------------------------------------------------------
# First-run bootstrap
# ---------------------------------------------------------------------------

def bootstrap_admin_from_env() -> None:
    """
    If NO users exist yet AND both ADMIN_BOOTSTRAP_EMAIL/
    ADMIN_BOOTSTRAP_PASSWORD are set, create that one admin account —
    otherwise a brand-new deployment has a login screen nobody can ever get
    past (creating a user requires being logged in as an admin already).
    Idempotent/safe to call on every startup: once a single user exists,
    this is a permanent no-op regardless of whether the env vars are still
    set, so they can be left in place or removed after first login either way.
    """
    if count_users() > 0:
        return
    email = os.environ.get("ADMIN_BOOTSTRAP_EMAIL")
    password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD")
    if not email or not password:
        return
    try:
        create_user(email, password, is_admin=True)
    except ValueError:
        pass  # e.g. password too short — don't crash app startup over a bad bootstrap value
