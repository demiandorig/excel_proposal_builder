"""
Covers the planner-signature feature: the internal (AE-facing) email now
signs off as the ACTUAL logged-in planner (name + email), not a generic
"Your Entravision Strategy Team." Mirrors the {{PROPOSAL_LINE}} pattern
already used for the Google Drive link line — the AI is instructed to
leave a literal {{PLANNER_SIGNATURE}} placeholder rather than trusted to
reproduce a real name/email verbatim, and main.py substitutes the real
value from request.state.user (set by session auth) after the fact.
There's no display-name field stored anywhere in this app (schema.sql's
users table has only email/password/is_admin) — the name is derived from
the session email's own dot-separated local part.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

from app.services.ai_enricher import _display_name_from_email, _first_name_from_requested_by


def test_derives_first_and_last_name_from_dot_separated_email():
    assert _display_name_from_email("irvin.villa@entravision.com") == "Irvin Villa"


def test_derives_first_name_only_when_no_dot_in_local_part():
    assert _display_name_from_email("irvin@entravision.com") == "Irvin"


def test_underscore_within_a_segment_becomes_a_space():
    assert _display_name_from_email("mary_jane.smith@entravision.com") == "Mary Jane Smith"


def test_blank_or_none_email_returns_empty_string_not_an_error():
    assert _display_name_from_email("") == ""
    assert _display_name_from_email(None) == ""


# --- AE greeting first-name extraction --------------------------------
# Real bug found after this session's own earlier work shipped: the AE
# greeting showed "Hi Lauren Sandford," instead of "Hi Lauren," because
# requested_by (a plain human name, not an email) has no "@"/"." for the
# old split-based logic to actually act on.

def test_plain_name_requested_by_yields_just_the_first_word():
    assert _first_name_from_requested_by("Lauren Sandford") == "Lauren"


def test_email_shaped_requested_by_still_works():
    assert _first_name_from_requested_by("lauren.sandford@entravision.com") == "Lauren"


def test_falls_back_to_salesperson_email_when_requested_by_is_blank():
    assert _first_name_from_requested_by("", "camilo.arias@entravision.com") == "Camilo"
    assert _first_name_from_requested_by(None, "camilo.arias@entravision.com") == "Camilo"


def test_underscore_separated_email_local_part():
    assert _first_name_from_requested_by("", "lauren_sandford@entravision.com") == "Lauren"


def test_both_blank_returns_empty_string():
    assert _first_name_from_requested_by("", "") == ""
    assert _first_name_from_requested_by(None, None) == ""
