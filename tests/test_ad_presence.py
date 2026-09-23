import asyncio
import re
import stat
import sys
import threading
import urllib.parse

import pytest

from app.services import ad_presence


# ---------------------------------------------------------------------------
# Fixtures: real rendered page text (innerText) captured from the public ad
# libraries in Sep 2026, trimmed to a handful of cards (emoji dropped from ad
# copy). The ​ lines are the invisible padding Meta really renders.
# ---------------------------------------------------------------------------

META_HEADER = """Meta Ad Library
Ad Library
Ad Library Report
Ad Library API
Branded Content
​
United States
​
​
​
All ads
​
Search by keyword or advertiser
​
​
Log in
Ad Library
Ad Library Report
Ad Library API
Branded Content
System status
Subscribe to email updates
FAQ
About ads and data use
Privacy
Terms
Cookies
"""

META_FOOTER = """System status
Ad Library APIAbout ads and data usePrivacyTermsCookies
English (US)
Turn off ad blocker
Close
​
Meta's advertising tools might not work as expected when an ad blocker is enabled in a web browser. Turn off the ad blocker or add this web page's URL as an exception so you can create ads without any problems. After you turn off the ad blocker, you'll need to refresh your screen.
OK"""

# q=Nike, country=US — captured with images/media blocked, so videos render
# as the player's "Sorry, we're having trouble..." + "Learn more" pair.
META_NIKE_PAGE = META_HEADER + """~24,000 results
These results include ads that match your keyword search.
Filters
Sort
​
Sort by
Active status: Active ads
Remove
​
​
Active
Library ID: 925321173274919
Started running on Aug 3, 2026
Platforms
​
​
​
2 ads use this creative and text
Open Dropdown
​
See summary details
Nordstrom Rack
Sponsored
Buy online and pick up in store for free! Get up to 70% off Nike, Vince, Madewell, adidas and more.
Sorry, we're having trouble playing this video.
Learn more
​
Active
Library ID: 2087470841874320
Started running on Jul 22, 2026
Platforms
​
​
​
​
This ad has multiple versions
​
Open Dropdown
​
See ad details
Foot Locker
Sponsored
Refresh your wardrobe with new styles from Nike, New Balance, adidas, and more.
FOOTLOCKER.COM
Shop New Styles
Shop the latest selection of New Arrivals at Foot Locker. Find the hottest sneaker drops from brands like Jordan, Nike, Under Armour, New Balance, and a bunch more. Free shipping for FLX members.
Shop Now
​
Active
Library ID: 1550534860016399
Started running on May 19, 2026
Platforms
​
​
​
​
​
EU transparency
​
Open Dropdown
​
See ad details
Perfect themes
Sponsored
Themepack is a FREE and personalized APP various styles of Theme, Weather, Photo, Clock, Calendar to pick any widget to dress up your home screen!
PLAY.GOOGLE.COM
Super hot widgets app !
Install now
​
Active
Library ID: 1016451784037642
Started running on Jun 20, 2025
Platforms
​
​
This ad has multiple versions
​
Open Dropdown
​
See ad details
Nike
Sponsored
Get the gear that goes hard on and off the field.
NIKE.COM
Nike Air Monarch IV
Shop Now
​
Active
Library ID: 1007351262282618
Started running on Aug 3, 2026
Platforms
​
​
​
​
​
This ad has multiple versions
​
2 ads use this creative and text
Open Dropdown
​
See summary details
JD Sports US
Sponsored
Liquid-inspired cushioning built for movement that never slows down. Introducing the Nike Air Liquid Max — now at JD. Tap in. Available now.
Sorry, we're having trouble playing this video.
Learn more
Liquid Max Lands At JD
Shop Now
​
Active
Library ID: 1107386451952079
Started running on Sep 1, 2026
Platforms
​
​
This ad has multiple versions
​
Open Dropdown
​
See ad details
Nike
Sponsored
Run through summer with gear that can take the heat.
NIKE.COM
Nike Air Monarch IV
Inspiring the world's athletes, Nike delivers innovative products, experiences and services.
Shop Now
​
Active
Library ID: 530594316492410
Started running on Oct 29, 2024
Platforms
​
​
​
​
Open Dropdown
​
See ad details
MNTowing.com & SellAJunker.com
Sponsored
No car title? Don't visit the DMV! Don't stress! Call MN Towing & Sell a Junker today! We buy all junkers!!
SELLAJUNKER.COM
No Title? Not the DMV!!
Book Now

SELLAJUNKER.COM
How did you like the DMV?
Book Now

​
Active
Library ID: 1301132848794012
Started running on May 20, 2026
Platforms
​
​
​
​
​
EU transparency
​
Open Dropdown
​
See ad details
IControl: Easy Widgets Themes
Sponsored
PLAY.GOOGLE.COM
iControl: Easy Widgets Themes
Install now
See more
""" + META_FOOTER

# q=Barringer Law Firm — captured WITH media, so videos show "0:00 / 1:19".
META_BARRINGER_PAGE = META_HEADER + """~8 results
These results include ads that match your keyword search.
Filters
Sort

Sort by
Active status: Active ads
Remove


Active
Library ID: 1773887543756078
Started running on Sep 1, 2026
Platforms






Open Dropdown

See ad details
Barringer Law Firm
Sponsored
A criminal charge can quietly threaten your immigration status, and most attorneys only handle one side of that.

Barringer Law Firm handles immigration and criminal defense under one roof, so nothing falls through the cracks.

Protect both cases at once. Call Barringer Law Firm: (720) 806-4575
0:00 / 1:19
BARRINGERLAWFIRM.COM
Crimmigration Defense
One firm. Both sides of your case.
Learn more

Active
Library ID: 2226030858130372
Started running on Sep 10, 2026
Platforms


Open Dropdown

See ad details
Barringer Law Firm
Sponsored
Before you bring your case to Barringer Law Firm, here's what actually helps: your documents, your timeline, your full story, whether it's an immigration matter or a criminal charge.

One team handles both, so nothing gets missed. If you or a loved one has been arrested or detained, don't wait.

Call (720) 806-4575 to get started.
Know This
Call Now

Start Now
Call Now

U.S. Residency
Call Now


Active
Library ID: 2917953925211889
Started running on Sep 21, 2026
Platforms


Open Dropdown

See ad details
Barringer Law Firm
Sponsored
Desde 2022, esta clienta confió en Matt Barringer con su caso de inmigración, y también con el de su esposo. Años de trabajo juntos, de principio a fin.

Gracias por confiar en Barringer Law Firm durante tantos años. Llámenos hoy al (720) 806-4575.
0:00 / 0:29
WWW.INSTAGRAM.COM
Barringer Law Firm
Visit Instagram profile
""" + META_FOOTER

# q=Barringerlawfirm (the domain-token variant) — overlaps the page above.
META_BARRINGER_DOMAIN_PAGE = META_HEADER + """~3 results
These results include ads that match your keyword search.
Active
Library ID: 1773887543756078
Started running on Sep 1, 2026
Platforms
Open Dropdown
See ad details
Barringer Law Firm
Sponsored
A criminal charge can quietly threaten your immigration status, and most attorneys only handle one side of that.
0:00 / 1:19
BARRINGERLAWFIRM.COM
Crimmigration Defense
One firm. Both sides of your case.
Learn more
""" + META_FOOTER

# q=Barringer Law Firm LLC — Meta's explicit empty state.
META_EMPTY_PAGE = META_HEADER + """Filters
Sort

Sort by
Active status: Active ads
Remove

No ads match your search criteria
Remove or adjust any filters you've applied to get different results.
Clear filters
View search tips.

System status
Ad Library APIAbout ads and data usePrivacyTermsCookies
English (US)"""

GOOGLE_NIKE_PAGE = """Ads Transparency Center
Sign in
FAQ
All topics
Political ads
search
Find the ads you've seen by searching by advertiser name or website
check
Ads In United States
arrow_drop_down
nike.com
This domain includes results for multiple advertiser accounts with ads pointing to this domain. You can filter by individual advertiser below.
~9K ads
calendar_today
Any time
arrow_drop_down
All platforms
arrow_drop_down
All formats
arrow_drop_down
chevron_right
Nike Retail BV
Verified
videocam
Nike, Inc.
Verified
Nike, Inc.
Verified
Nike Retail BV
Verified
See all ads
Discover more on related sites
PrivacyTermsAds PoliciesFAQ
PrinciplesAds Blog"""

GOOGLE_BARRINGER_PAGE = """Ads Transparency Center
Sign in
FAQ
All topics
Political ads
search
Find the ads you've seen by searching by advertiser name or website
check
Ads In United States
arrow_drop_down
barringerlawfirm.com
This domain includes results for multiple advertiser accounts with ads pointing to this domain. You can filter by individual advertiser below.
8 ads
calendar_today
Any time
arrow_drop_down
All platforms
arrow_drop_down
All formats
arrow_drop_down
chevron_right
Barringer Law Firm
Verified
Barringer Law Firm
Verified
Barringer Law Firm
Verified
Barringer Law Firm
Verified
See all ads
Discover more on related sites
PrivacyTermsAds PoliciesFAQ
PrinciplesAds Blog"""

# domain=www.barringerlawfirm.com — the false negative the "www." strip fixes.
GOOGLE_WWW_EMPTY_PAGE = """Ads Transparency Center
Sign in
FAQ
Ads In United States
arrow_drop_down
www.barringerlawfirm.com
This domain includes results for multiple advertiser accounts with ads pointing to this domain. You can filter by individual advertiser below.
0 ads
calendar_today
Any time
arrow_drop_down
No ads found
Sorry, we couldn't find anything for your search. Try changing your filters or search for another advertiser or website.
Discover more on related sites
PrivacyTermsAds PoliciesFAQ
PrinciplesAds Blog"""

# region=GB&adv_name=Nike&query_type=1 — note the comma count.
TIKTOK_GB_NIKE_PAGE = """Commercial Content Library
Ad Library
All ads report
Other commercial content
FAQs
Find ads on TikTok
Ad target country
United Kingdom
Last shown date
6/25/2026—9/23/2026
Advertiser name or keyword
Search
Search results
Total ads:
5,000
Filters
Last shown date: Newest to oldest
Ad
THE SOLE SUPPLIER LIMITED
First shown:08/30/2026
Last shown:08/30/2026
Unique users seen:0-1K
Ad
THE SOLE SUPPLIER LIMITED
First shown:08/26/2026
Last shown:08/28/2026
Unique users seen:0-1K
Ad
Unisport A/S
First shown:07/02/2026
Last shown:08/11/2026
Unique users seen:10K-100K
View more
English (US)
Help Center
Terms of Service
Privacy Policy
© 2026 TikTok"""

# region=US&adv_name=Nike&query_type=1 — the library has no US coverage:
# empty target country, "Total ads: 0", every time.
TIKTOK_US_PAGE = """Commercial Content Library
Ad Library
All ads report
Other commercial content
FAQs
Find ads on TikTok
Ad target country
Last shown date
6/25/2026—9/23/2026
Advertiser name or keyword
Search
Search results
Total ads:
0
Filters
Last shown date: Newest to oldest
No ads found
Why might that be?
This advertiser isn't running ads in the selected country
This advertiser doesn't advertise with TikTok
No ads match your query criteria
We don't support political or election ads
Results may be delayed. It can occasionally take up to 48 hours to process updates.
English (US)
Help Center
Terms of Service
Privacy Policy
© 2026 TikTok"""

TIKTOK_UNSUPPORTED_US_NOTE = (
    "TikTok's public ad library only lists ads shown in the EU/EEA, UK and Switzerland, so US campaigns "
    "can't be verified automatically — open the library to search by hand if needed."
)

COMMON_KEYS = {"platform", "status", "checked", "active", "note", "search_urls", "library_url", "languages"}
PLATFORM_KEYS = {
    "meta": COMMON_KEYS | {"queries_tried", "high_confidence_count", "result_count_estimate", "sample_ads"},
    "google": COMMON_KEYS | {"ad_count_estimate_display", "advertisers", "search_url"},
    "tiktok": COMMON_KEYS | {"queries_tried", "sample_ads"},
}
META_SAMPLE_AD_KEYS = {
    "library_id", "page_name", "started_running_on", "body", "headline", "cta", "destination_domain",
    "language", "confidence", "matched_query", "library_url", "has_versions",
}
TOP_LEVEL_KEYS = {"meta", "google", "tiktok", "summary", "elapsed_seconds", "country"}


def _param(url: str, name: str):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get(name, [None])[0]


def _fixture_for(url: str) -> str:
    """Routes a library URL to the captured page a real browser got for it."""
    if "facebook.com" in url:
        return {
            "Nike": META_NIKE_PAGE,
            "Barringer Law Firm": META_BARRINGER_PAGE,
            "Barringerlawfirm": META_BARRINGER_DOMAIN_PAGE,
        }.get(_param(url, "q"), META_EMPTY_PAGE)
    if "adstransparency.google.com" in url:
        return {
            "nike.com": GOOGLE_NIKE_PAGE,
            "barringerlawfirm.com": GOOGLE_BARRINGER_PAGE,
        }.get(_param(url, "domain"), GOOGLE_WWW_EMPTY_PAGE)
    if "library.tiktok.com" in url:
        return TIKTOK_US_PAGE if _param(url, "region") == "US" else TIKTOK_GB_NIKE_PAGE
    raise AssertionError(f"unexpected URL {url}")


def _recording_fetch(calls: list):
    async def fake_fetch(url: str):
        calls.append(url)
        return _fixture_for(url), None
    return fake_fetch


def _run(*args, **kwargs):
    return asyncio.run(ad_presence.check_ad_presence(*args, **kwargs))


def _assert_contract(result: dict) -> None:
    assert set(result) == TOP_LEVEL_KEYS
    assert isinstance(result["summary"], str) and result["summary"]
    assert isinstance(result["elapsed_seconds"], float)
    for platform, keys in PLATFORM_KEYS.items():
        r = result[platform]
        missing = keys - set(r)
        assert not missing, f"{platform} is missing {missing}"
        assert r["platform"] == platform
        assert r["status"] in ad_presence.STATUSES
        assert r["active"] is (r["status"] == "active")
        assert isinstance(r["checked"], bool)
        if r["status"] in ("error", "not_checked", "unsupported"):
            assert r["checked"] is False
        if r["status"] in ("active", "not_found"):
            assert r["checked"] is True
        assert isinstance(r["note"], str) and r["note"].strip()
        assert isinstance(r["search_urls"], list) and all(isinstance(u, str) for u in r["search_urls"])
        assert isinstance(r["library_url"], str) and r["library_url"].startswith("https://")
        assert isinstance(r["languages"], dict)
    meta = result["meta"]
    assert isinstance(meta["high_confidence_count"], int)
    assert meta["result_count_estimate"] is None or isinstance(meta["result_count_estimate"], str)
    assert len(meta["sample_ads"]) <= 12
    for ad in meta["sample_ads"]:
        assert set(ad) == META_SAMPLE_AD_KEYS
        assert ad["confidence"] in ("high", "medium", "low")
        assert ad["library_url"] == f"https://www.facebook.com/ads/library/?id={ad['library_id']}"
        assert isinstance(ad["has_versions"], bool)
        assert len(ad["body"]) <= 600
    google = result["google"]
    assert isinstance(google["advertisers"], list) and len(google["advertisers"]) <= 10
    assert isinstance(google["search_url"], str)
    assert isinstance(result["tiktok"]["sample_ads"], list)


# ---------------------------------------------------------------------------
# Original tests (kept; adjusted to the new statuses)
# ---------------------------------------------------------------------------

def test_ad_presence_uses_async_browser_path(monkeypatch):
    # _fetch_rendered_text returns (text, fail_reason) — fail_reason is
    # None on success (see its own docstring for the "no_browser" vs
    # "navigation" distinction this fake doesn't need to exercise).
    calls = []

    async def fake_fetch(url: str):
        calls.append(url)
        if "facebook.com" in url:
            return "2 results\nLibrary ID: 123\nBarringer Law\nEnglish legal services", None
        if "adstransparency.google.com" in url:
            return "Barringer Law\nVerified\n~3 ads", None
        return "Total ads:\n0", None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch)
    result = asyncio.run(ad_presence.check_ad_presence("Barringer Law", "barringerlawfirm.com"))

    assert result["meta"]["checked"] is True
    assert result["meta"]["status"] == "active"
    assert result["meta"]["sample_ads"][0]["page_name"] == "Barringer Law"
    assert result["google"]["checked"] is True
    assert result["google"]["status"] == "active"
    # US TikTok is structurally unverifiable now — never loaded, never "checked".
    assert result["tiktok"]["status"] == "unsupported"
    assert result["tiktok"]["checked"] is False
    assert not [u for u in calls if "tiktok" in u]


def test_ad_presence_distinguishes_missing_browser_from_navigation_failure(monkeypatch):
    # A "no_browser" failure (Chromium never installed on this server)
    # must produce a distinctly different, actionable note from a generic
    # "navigation" failure (timeout/network) — this is the exact ambiguity
    # that used to hide a real missing-browser-binary setup gap.
    async def fake_fetch_no_browser(url: str):
        return None, "no_browser"

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch_no_browser)
    result = asyncio.run(ad_presence.check_ad_presence("Barringer Law", "barringerlawfirm.com"))

    assert result["meta"]["checked"] is False
    assert result["meta"]["status"] == "error"
    assert result["meta"]["search_urls"] == []  # nothing was actually requested
    assert "no chromium browser is installed" in result["meta"]["note"].lower()
    assert "playwright install chromium" in result["meta"]["note"]
    assert result["google"]["status"] == "error"

    async def fake_fetch_navigation(url: str):
        return None, "navigation"

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch_navigation)
    result2 = asyncio.run(ad_presence.check_ad_presence("Barringer Law", "barringerlawfirm.com"))

    assert result2["meta"]["checked"] is False
    assert result2["meta"]["status"] == "error"
    assert "chromium" not in result2["meta"]["note"].lower()
    assert "timeout or network error" in result2["meta"]["note"].lower()


def test_chromium_executable_prefers_explicit_deployment_path(monkeypatch, tmp_path):
    executable = tmp_path / "chromium"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CHROMIUM_EXECUTABLE_PATH", str(executable))
    monkeypatch.setattr(ad_presence.shutil, "which", lambda _name: None)

    assert ad_presence._chromium_executable() == str(executable)


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

def test_tiktok_us_is_unsupported_and_never_loads_a_page(monkeypatch):
    calls = []
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch(calls))

    result = _run("Nike", "https://www.nike.com/", "US")
    tiktok = result["tiktok"]

    assert tiktok["status"] == "unsupported"
    assert tiktok["checked"] is False and tiktok["active"] is False
    assert tiktok["library_url"] == "https://library.tiktok.com/ads"
    assert tiktok["note"] == TIKTOK_UNSUPPORTED_US_NOTE
    assert tiktok["search_urls"] == [] and tiktok["queries_tried"] == [] and tiktok["sample_ads"] == []
    assert not [u for u in calls if "tiktok" in u], "TikTok must not be loaded for a US lookup"
    # ...and it reads as "can't verify", not as a negative, in the LLM summary.
    assert "TikTok: not verifiable for US campaigns (public library covers EU/UK only)." in result["summary"]
    assert "TikTok: no confirmed" not in result["summary"]


def test_tiktok_non_us_uses_advertiser_search_and_parses_comma_counts(monkeypatch):
    calls = []
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch(calls))

    parsed = ad_presence._parse_tiktok_page(TIKTOK_GB_NIKE_PAGE)
    assert parsed["total"] == 5000  # the old (\d+) read "5,000" as 5
    assert parsed["region_label"] == "United Kingdom"
    assert [c["advertiser"] for c in parsed["cards"]] == [
        "THE SOLE SUPPLIER LIMITED", "THE SOLE SUPPLIER LIMITED", "Unisport A/S"]
    assert parsed["cards"][2]["unique_users_seen"] == "10K-100K"

    result = _run("Nike", "nike.com", "GB")
    tiktok = result["tiktok"]
    tiktok_urls = [u for u in calls if "library.tiktok.com" in u]
    assert len(tiktok_urls) == 1  # "Nike" is the only distinct name variant
    url = tiktok_urls[0]
    assert _param(url, "region") == "GB"
    assert _param(url, "adv_name") == "Nike" and _param(url, "query_type") == "1"
    assert "query" not in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert tiktok["search_urls"] == tiktok_urls
    assert tiktok["checked"] is True
    assert tiktok["result_count_estimate"] == "5,000 ads"
    # Resellers mentioning Nike are not Nike advertising.
    assert tiktok["status"] == "not_found"
    assert "5,000" in tiktok["note"]
    assert {ad["confidence"] for ad in tiktok["sample_ads"]} == {"low"}
    assert result["country"] == "GB"


def test_tiktok_non_us_is_active_when_the_advertiser_matches(monkeypatch):
    page = TIKTOK_GB_NIKE_PAGE.replace(
        "View more",
        "Ad\nNike\nFirst shown:09/01/2026\nLast shown:09/20/2026\nUnique users seen:1M-10M\nView more")

    async def fake_fetch(url: str):
        return (page if "tiktok" in url else _fixture_for(url)), None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch)
    tiktok = _run("Nike", "nike.com", "GB")["tiktok"]

    assert tiktok["status"] == "active"
    assert tiktok["sample_ads"][0]["advertiser"] == "Nike"
    assert tiktok["sample_ads"][0]["confidence"] == "high"
    assert tiktok["languages"] == {}


def test_tiktok_region_the_library_doesnt_cover_is_unsupported(monkeypatch):
    calls = []
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch(calls))

    # A market outside EU/EEA/UK/CH: known-unsupported, zero page loads.
    mx = _run("Nike", "nike.com", "MX")["tiktok"]
    assert mx["status"] == "unsupported" and "MX campaigns" in mx["note"]
    assert not [u for u in calls if "tiktok" in u]

    # And if a covered region ever comes back with no target country (the
    # page's own "not covered" signal), that's unsupported too, not "0 ads".
    async def fake_fetch(url: str):
        return (TIKTOK_US_PAGE if "tiktok" in url else _fixture_for(url)), None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch)
    de = _run("Nike", "nike.com", "DE")["tiktok"]
    assert de["status"] == "unsupported" and de["checked"] is False


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

def test_meta_multiple_versions_card_keeps_page_name_and_high_confidence(monkeypatch):
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch([]))
    meta = _run("Nike", "nike.com")["meta"]

    assert meta["status"] == "active" and meta["active"] is True and meta["checked"] is True
    assert meta["high_confidence_count"] == 2
    assert meta["result_count_estimate"] == "~24,000 results"
    assert meta["queries_tried"] == ["Nike"]
    assert meta["search_urls"] == [
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&q=Nike"]
    assert meta["library_url"] == meta["search_urls"][0]
    assert meta["languages"] == {"en": 1.0}

    by_id = {ad["library_id"]: ad for ad in meta["sample_ads"]}
    nike = by_id["1016451784037642"]  # a "This ad has multiple versions" card
    assert nike["page_name"] == "Nike"
    assert nike["confidence"] == "high"
    assert nike["has_versions"] is True
    assert nike["destination_domain"] == "NIKE.COM"
    assert nike["headline"] == "Nike Air Monarch IV"
    assert nike["cta"] == "Shop Now"
    assert nike["body"] == "Get the gear that goes hard on and off the field."
    assert nike["language"] == "en"
    assert nike["matched_query"] == "Nike"
    assert nike["library_url"] == "https://www.facebook.com/ads/library/?id=1016451784037642"

    # Junk lines never masquerade as the Page name any more.
    names = {ad["page_name"] for ad in meta["sample_ads"]}
    assert not names & {"This ad has multiple versions", "EU transparency", "Sponsored", "0:00 / 0:15"}
    assert by_id["1550534860016399"]["page_name"] == "Perfect themes"  # EU-transparency card
    assert by_id["1550534860016399"]["confidence"] == "low"
    assert by_id["2087470841874320"]["page_name"] == "Foot Locker"
    assert by_id["2087470841874320"]["confidence"] == "medium"  # mentions Nike, isn't Nike
    assert by_id["2087470841874320"]["headline"] == "Shop New Styles"
    # A video card: the player's own "Learn more" is not the ad's CTA...
    assert by_id["925321173274919"]["page_name"] == "Nordstrom Rack"
    assert by_id["925321173274919"]["cta"] is None
    # ...and the link section after it is still read.
    assert by_id["1007351262282618"]["headline"] == "Liquid Max Lands At JD"
    assert by_id["1007351262282618"]["cta"] == "Shop Now"
    # Carousel: first card's domain/headline/CTA.
    assert by_id["530594316492410"]["destination_domain"] == "SELLAJUNKER.COM"
    assert by_id["530594316492410"]["headline"] == "No Title? Not the DMV!!"
    assert by_id["530594316492410"]["cta"] == "Book Now"
    # No body copy at all.
    assert by_id["1301132848794012"]["body"] == ""
    assert by_id["1301132848794012"]["cta"] == "Install now"

    # high -> medium -> low, then most recent first.
    assert [ad["library_id"] for ad in meta["sample_ads"]] == [
        "1107386451952079", "1016451784037642",                    # high: Sep 1 2026, Jun 20 2025
        "925321173274919", "1007351262282618", "2087470841874320",  # medium: Aug 3, Aug 3, Jul 22
        "1301132848794012", "1550534860016399", "530594316492410",  # low: May 20, May 19 2026, Oct 29 2024
    ]


def test_meta_start_dates_are_parsed_per_card():
    # Drop one card's date: with dates matched to cards by list position (the
    # old approach) every later card would inherit its neighbour's date.
    page = META_NIKE_PAGE.replace("Started running on Jul 22, 2026\n", "")
    cards = {c["library_id"]: c for c in ad_presence._parse_meta_page(page)["cards"]}

    assert cards["925321173274919"]["started_running_on"] == "Aug 3, 2026"
    assert cards["2087470841874320"]["started_running_on"] is None
    assert cards["1550534860016399"]["started_running_on"] == "May 19, 2026"
    assert cards["1016451784037642"]["started_running_on"] == "Jun 20, 2025"
    assert cards["1107386451952079"]["started_running_on"] == "Sep 1, 2026"
    assert cards["1301132848794012"]["started_running_on"] == "May 20, 2026"


def test_meta_destination_domain_match_alone_is_high_confidence(monkeypatch):
    # An agency-run Page whose name shares nothing with the client, but whose
    # ad points at the client's own website.
    page = META_NIKE_PAGE.replace(
        "See ad details\nNike\nSponsored\nGet the gear that goes hard",
        "See ad details\nSummit Sports Marketing\nSponsored\nGet the gear that goes hard")

    async def fake_fetch(url: str):
        return (page if "facebook.com" in url else _fixture_for(url)), None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch)
    meta = _run("Nike", "https://www.nike.com/")["meta"]
    ad = next(a for a in meta["sample_ads"] if a["library_id"] == "1016451784037642")

    assert ad["page_name"] == "Summit Sports Marketing"
    assert ad["confidence"] == "high"


def test_meta_variants_dedupe_and_detect_both_languages(monkeypatch):
    calls = []
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch(calls))
    meta = _run("Barringer Law Firm LLC", "https://www.barringerlawfirm.com/")["meta"]

    assert meta["queries_tried"] == ["Barringer Law Firm LLC", "Barringer Law Firm", "Barringerlawfirm"]
    assert len(meta["search_urls"]) == 3
    assert meta["status"] == "active"
    # 1773887543756078 comes back for two variants — counted once, credited
    # to the first variant that found it.
    assert meta["high_confidence_count"] == 3
    ids = [ad["library_id"] for ad in meta["sample_ads"]]
    assert len(ids) == len(set(ids)) == 3
    first = next(ad for ad in meta["sample_ads"] if ad["library_id"] == "1773887543756078")
    assert first["matched_query"] == "Barringer Law Firm"
    assert first["destination_domain"] == "BARRINGERLAWFIRM.COM" and first["headline"] == "Crimmigration Defense"
    assert "0:00" not in first["body"]  # video timestamp isn't ad copy
    carousel = next(ad for ad in meta["sample_ads"] if ad["library_id"] == "2226030858130372")
    assert carousel["headline"] == "Know This" and carousel["cta"] == "Call Now"
    assert carousel["body"].endswith("Call (720) 806-4575 to get started.")
    spanish = next(ad for ad in meta["sample_ads"] if ad["library_id"] == "2917953925211889")
    assert spanish["language"] == "es" and spanish["cta"] == "Visit Instagram profile"
    assert meta["languages"] == {"en": 0.67, "es": 0.33}
    # The link a human should open is the search that actually found ads,
    # not the empty "...LLC" one.
    assert _param(meta["library_url"], "q") == "Barringer Law Firm"
    assert meta["result_count_estimate"] == "~8 results"


def test_meta_page_that_never_rendered_results_is_an_error_not_a_negative(monkeypatch):
    async def fake_fetch(url: str):
        if "facebook.com" in url:
            return "Log in to Facebook\nEmail or phone number\nPassword\nLog in\nForgot password?", None
        return _fixture_for(url), None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch)
    meta = _run("Nike", "nike.com")["meta"]

    assert meta["status"] == "error" and meta["checked"] is False
    assert "never appeared" in meta["note"]
    assert meta["search_urls"]  # it was requested, it just didn't show results


def test_meta_generic_words_alone_dont_make_a_high_confidence_match():
    # "Smith Law Firm, LLC" shares only "law"/"firm"/"llc" with the client.
    ref = ad_presence._reference_tokens("Barringer Law Firm LLC", "barringerlawfirm.com")
    assert ref == {"barringer", "barringerlawfirm"}
    assert ad_presence._match_confidence("Smith Law Firm, LLC", "", ref) == "low"
    assert ad_presence._match_confidence("Barringer Law Firm", "", ref) == "high"


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------

def test_google_strips_www_and_returns_the_verified_advertisers(monkeypatch):
    calls = []
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch(calls))
    google = _run("Barringer Law Firm LLC", "https://www.barringerlawfirm.com/")["google"]

    expected_url = "https://adstransparency.google.com/?region=US&domain=barringerlawfirm.com"
    assert [u for u in calls if "adstransparency" in u] == [expected_url]
    assert google["search_url"] == expected_url
    assert google["library_url"] == expected_url
    assert google["search_urls"] == [expected_url]
    assert google["status"] == "active" and google["checked"] is True
    assert google["advertisers"] == ["Barringer Law Firm"]
    assert google["ad_count_estimate_display"] == "8"
    assert google["languages"] == {}

    nike = _run("Nike", "WWW.Nike.com:443/us/?ref=x")["google"]
    assert nike["search_url"] == "https://adstransparency.google.com/?region=US&domain=nike.com"
    assert nike["advertisers"] == ["Nike Retail BV", "Nike, Inc."]
    assert nike["ad_count_estimate_display"] == "9K"


def test_google_empty_domain_page_is_not_found():
    parsed = ad_presence._parse_google_page(GOOGLE_WWW_EMPTY_PAGE)
    assert parsed["recognized"] and parsed["empty"]
    assert parsed["count_value"] == 0 and parsed["advertisers"] == []


def test_domain_normalization():
    norm = ad_presence._normalize_domain
    assert norm("https://www.nike.com/") == "nike.com"
    assert norm("www.barringerlawfirm.com") == "barringerlawfirm.com"
    assert norm("http://user@WWW2.Example.co.uk:8080/a?b#c") == "example.co.uk"
    assert norm("wwwexample.com") == "wwwexample.com"
    assert norm("") == "" and norm("n/a") == "" and norm("nike") == ""
    # A social page isn't the client's own domain.
    assert ad_presence._client_domain("https://www.facebook.com/barringerlawfirm") == ""
    assert ad_presence._domain_brand_token("https://www.facebook.com/barringerlawfirm") == ""
    assert ad_presence._domain_brand_token("https://www.nike.com/") == "Nike"


def test_social_page_as_website_skips_google(monkeypatch):
    calls = []
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch(calls))
    result = _run("Nike", "https://www.facebook.com/nike")

    assert result["google"]["status"] == "not_checked"
    assert "facebook.com" in result["google"]["note"]
    assert not [u for u in calls if "adstransparency" in u]
    assert result["meta"]["queries_tried"] == ["Nike"]  # no "Facebook" variant


# ---------------------------------------------------------------------------
# Contract, fail-soft, orchestration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["success_us", "success_gb", "no_input", "no_browser", "google_raises"])
def test_every_platform_result_has_every_contract_key(monkeypatch, scenario):
    async def fake_fetch(url: str):
        if scenario == "no_browser":
            return None, "no_browser"
        if scenario == "google_raises" and "adstransparency" in url:
            raise RuntimeError("boom")
        return _fixture_for(url), None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch)
    args = {
        "success_us": ("Nike", "https://www.nike.com/", "US"),
        "success_gb": ("Nike", "https://www.nike.com/", "GB"),
        "no_input": ("", "", "US"),
        "no_browser": ("Nike", "nike.com", "US"),
        "google_raises": ("Nike", "nike.com", "US"),
    }[scenario]
    result = _run(*args)

    _assert_contract(result)
    if scenario == "no_input":
        assert result["meta"]["status"] == "not_checked"
        assert result["google"]["status"] == "not_checked"


def test_fetch_exception_on_one_platform_leaves_the_others_intact(monkeypatch):
    async def google_explodes(url: str):
        if "adstransparency" in url:
            raise RuntimeError("simulated crash")
        return _fixture_for(url), None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", google_explodes)
    result = _run("Nike", "https://www.nike.com/")
    assert result["google"]["status"] == "error" and result["google"]["checked"] is False
    assert "unexpected" in result["google"]["note"]
    assert result["google"]["search_url"].endswith("domain=nike.com")  # still a link to open by hand
    assert result["meta"]["status"] == "active"
    assert result["tiktok"]["status"] == "unsupported"
    assert "Google Ads: could not be checked" in result["summary"]

    async def meta_explodes(url: str):
        if "facebook.com" in url:
            raise TimeoutError("simulated hang")
        return _fixture_for(url), None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", meta_explodes)
    result = _run("Nike", "https://www.nike.com/")
    assert result["meta"]["status"] == "error"
    assert result["google"]["status"] == "active"


def test_platform_crash_outside_the_fetch_layer_is_contained(monkeypatch):
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch([]))

    def broken_parser(text):
        raise ValueError("layout changed")

    monkeypatch.setattr(ad_presence, "_parse_google_page", broken_parser)
    result = _run("Nike", "nike.com")
    assert result["google"]["status"] == "error"
    assert result["meta"]["status"] == "active"
    _assert_contract(result)


def test_page_load_budget_never_exceeds_seven(monkeypatch):
    calls = []
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch(calls))

    _run("Barringer Law Firm LLC", "https://www.barringerlawfirm.com/", "US")
    assert len(calls) == 4  # 3 Meta variants + 1 Google domain; TikTok: 0 for US

    calls.clear()
    _run("Nike", "https://www.nike.com/", "US")
    assert len(calls) == 2

    calls.clear()
    _run("Barringer Law Firm LLC", "https://www.barringerlawfirm.com/", "GB")
    assert len(calls) == 7  # worst case: 3 Meta + 1 Google + 3 TikTok


def test_summary_is_status_aware(monkeypatch):
    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", _recording_fetch([]))
    result = _run("Nike", "https://www.nike.com/")
    summary = result["summary"]

    assert result["google"]["note"].endswith("Nike, Inc.") and ".." not in result["google"]["note"]
    # Meta's count is a first-page floor, never presented as the client's total.
    assert "Meta (Facebook/Instagram): ACTIVE, at least 2 ads running (Page: Nike)" in summary
    assert "Google Ads: ACTIVE, ~9K ads running (verified advertisers: Nike Retail BV; Nike, Inc.)." in summary
    assert summary.endswith("TikTok: not verifiable for US campaigns (public library covers EU/UK only).")


def test_selector_event_loop_falls_back_to_a_private_loop(monkeypatch):
    # uvicorn --reload on Windows runs a SelectorEventLoop, which can't spawn
    # Chromium; the lookup then runs on its own loop in a worker thread.
    fetch_threads = set()

    async def fake_fetch(url: str):
        fetch_threads.add(threading.get_ident())
        return _fixture_for(url), None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch)
    monkeypatch.setattr(ad_presence, "_loop_can_spawn_subprocesses", lambda: False)
    result = _run("Nike", "nike.com")

    assert result["meta"]["status"] == "active" and result["google"]["status"] == "active"
    assert fetch_threads and threading.get_ident() not in fetch_threads


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only event-loop behavior")
def test_windows_selector_loop_is_detected():
    async def probe():
        return ad_presence._loop_can_spawn_subprocesses()

    for factory, expected in ((asyncio.SelectorEventLoop, False), (asyncio.ProactorEventLoop, True)):
        loop = factory()
        try:
            assert loop.run_until_complete(probe()) is expected
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# The real fetch layer, driven through a fake browser (no Chromium needed)
# ---------------------------------------------------------------------------

class _FakeRoute:
    def __init__(self, resource_type):
        self.request = type("Req", (), {"resource_type": resource_type})()
        self.outcome = None

    async def abort(self):
        self.outcome = "abort"

    async def continue_(self):
        self.outcome = "continue"


def test_heavy_resources_are_blocked():
    for resource_type, expected in (("image", "abort"), ("media", "abort"), ("font", "abort"),
                                    ("document", "continue"), ("script", "continue"),
                                    ("stylesheet", "continue"), ("xhr", "continue")):
        route = _FakeRoute(resource_type)
        asyncio.run(ad_presence._block_heavy_resources(route))
        assert route.outcome == expected, resource_type


def test_one_shared_browser_and_at_most_three_pages_at_once(monkeypatch):
    stats = {"launches": 0, "open": 0, "max_open": 0, "loads": [], "goto_waits": set(), "ready_args": []}

    class FakePage:
        def __init__(self):
            self.url = None

        def set_default_navigation_timeout(self, ms):
            pass

        def set_default_timeout(self, ms):
            pass

        async def goto(self, url, wait_until=None):
            self.url = url
            stats["goto_waits"].add(wait_until)
            stats["loads"].append(url)
            await asyncio.sleep(0.02)  # overlap the loads

        async def wait_for_function(self, expression, arg=None, polling=None, timeout=None):
            if isinstance(arg, list):
                stats["ready_args"].append((self.url, arg[0], timeout))
            return True

        async def evaluate(self, expression):
            return _fixture_for(self.url)

    class FakeContext:
        def __init__(self, options):
            self.options = options
            self.routes = []

        async def route(self, pattern, handler):
            self.routes.append((pattern, handler))

        async def new_page(self):
            assert self.routes == [("**/*", ad_presence._block_heavy_resources)]
            return FakePage()

        async def close(self):
            stats["open"] -= 1

    class FakeBrowser:
        async def new_context(self, **options):
            assert options.get("service_workers") == "block"
            stats["open"] += 1
            stats["max_open"] = max(stats["max_open"], stats["open"])
            return FakeContext(options)

        async def close(self):
            pass

    async def fake_get_browser(self):
        if getattr(self, "_fake_browser", None) is None:
            stats["launches"] += 1
            self._fake_browser = FakeBrowser()
        return self._fake_browser

    monkeypatch.setattr(ad_presence._BrowserSession, "_get_browser", fake_get_browser)
    result = _run("Barringer Law Firm LLC", "https://www.barringerlawfirm.com/", "GB")

    assert stats["launches"] == 1                  # ONE browser for the whole lookup
    assert len(stats["loads"]) == 7                # 3 Meta + 1 Google + 3 TikTok
    assert stats["max_open"] == 3                  # never more than 3 pages at once
    assert stats["open"] == 0                      # every context closed
    assert stats["goto_waits"] == {"domcontentloaded"}  # no networkidle wait
    assert all(pattern and timeout <= 10000 for _, pattern, timeout in stats["ready_args"])
    assert result["meta"]["status"] == "active" and result["google"]["status"] == "active"


def test_ready_markers_match_real_pages_but_not_the_empty_shell():
    cases = [
        (ad_presence._meta_search_url("Nike", "US"), META_NIKE_PAGE),
        (ad_presence._meta_search_url("x", "US"), META_EMPTY_PAGE),
        (ad_presence._google_search_url("nike.com", "US"), GOOGLE_NIKE_PAGE),
        (ad_presence._google_search_url("www.x.com", "US"), GOOGLE_WWW_EMPTY_PAGE),
        (ad_presence._tiktok_search_url("Nike", "GB", 90), TIKTOK_GB_NIKE_PAGE),
        (ad_presence._tiktok_search_url("Nike", "US", 90), TIKTOK_US_PAGE),
    ]
    for url, page in cases:
        pattern, timeout_ms = ad_presence._ready_marker_for(url)
        assert pattern and 0 < timeout_ms <= 10000, url
        assert re.search(pattern, ad_presence._clean_text(page), re.IGNORECASE), url
    # The page chrome Meta renders before its search XHR returns must not
    # count as "results are in".
    meta_pattern, _ = ad_presence._ready_marker_for(ad_presence._meta_search_url("Nike", "US"))
    assert not re.search(meta_pattern, META_HEADER, re.IGNORECASE)
