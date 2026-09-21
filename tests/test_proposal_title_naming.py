"""
Covers a naming-convention fix: build_proposal_title()'s campaign-name
segment used to be JUST the AI's "order description" (e.g. campaign_name
alone), with no structural place for the client name — which meant the
AI's own campaign_name routinely embedded the client name itself (its
prompt's own example, 'Bill Luke July Awareness', modeled exactly that),
and since proposal_generator.py's C11 meta cell ALSO prints
"Media Proposal: {client_name}" alongside "Order Description: {campaign_name}",
the client name visibly repeated. Fixed two ways: (1) the AI prompt now
explicitly forbids campaign_name from containing the client's name, (2)
build_proposal_title() itself now takes client_name and builds
"{Client Name} - {Order Description}" as the ONE place the client name
appears, with a defensive check against a caller passing a
campaign_name/override that already starts with the client's name
(campaign_name_override is planner-typed free text with no AI
instruction backing it — a real, not hypothetical, way to still hit this).
"""
from app.services.ai_enricher import build_proposal_title


def test_combines_client_name_and_order_description_with_a_dash():
    title = build_proposal_title(
        "0042", "July Awareness Push", "New Business Request",
        ref_date="2026-06-15", client_name="Texmex Curios",
    )
    assert title == "0042 | Texmex Curios - July Awareness Push | Entravision | Jun26 | Digital Media Proposal"


def test_no_client_name_passed_keeps_old_behavior_exactly():
    # Backward compatibility — every caller that existed before this
    # param was added must see byte-identical output.
    title = build_proposal_title("0042", "July Awareness Push", "New Business Request", ref_date="2026-06-15")
    assert title == "0042 | July Awareness Push | Entravision | Jun26 | Digital Media Proposal"
    assert "Texmex" not in title


def test_order_description_already_equal_to_client_name_does_not_duplicate():
    # The _fallback_campaign_name() case — campaign_name IS client_name
    # verbatim (AI enrichment never ran, e.g. Step 03's early-wizard doc
    # title) — must not become "Texmex Curios - Texmex Curios".
    title = build_proposal_title(
        "0042", "Texmex Curios", "New Business Request",
        ref_date="2026-06-15", client_name="Texmex Curios",
    )
    assert title == "0042 | Texmex Curios | Entravision | Jun26 | Digital Media Proposal"
    assert title.count("Texmex Curios") == 1


def test_order_description_case_insensitively_starting_with_client_name_does_not_duplicate():
    # A planner's manual campaign_name_override (free text, no AI
    # instruction behind it) typing the client's name themselves, e.g.
    # "texmex curios fall push" — still shouldn't double up.
    title = build_proposal_title(
        "0042", "texmex curios fall push", "New Business Request",
        ref_date="2026-06-15", client_name="Texmex Curios",
    )
    assert title.count("texmex curios") + title.lower().count("texmex curios") >= 1
    assert "Texmex Curios - texmex curios" not in title and "Texmex Curios - Texmex Curios" not in title


def test_blank_client_name_falls_back_to_order_description_alone():
    title = build_proposal_title(
        "0042", "July Awareness Push", "New Business Request",
        ref_date="2026-06-15", client_name="",
    )
    assert title == "0042 | July Awareness Push | Entravision | Jun26 | Digital Media Proposal"
