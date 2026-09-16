import asyncio

from app.services import ad_presence


def test_ad_presence_uses_async_browser_path(monkeypatch):
    # _fetch_rendered_text returns (text, fail_reason) — fail_reason is
    # None on success (see its own docstring for the "no_browser" vs
    # "navigation" distinction this fake doesn't need to exercise).
    async def fake_fetch(url: str):
        if "facebook.com" in url:
            return "2 results\nLibrary ID: 123\nBarringer Law\nEnglish legal services", None
        if "adstransparency.google.com" in url:
            return "Barringer Law\nVerified\n~3 ads", None
        return "Total ads:\n0", None

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch)
    result = asyncio.run(ad_presence.check_ad_presence("Barringer Law", "barringerlawfirm.com"))

    assert result["meta"]["checked"] is True
    assert result["google"]["checked"] is True
    assert result["tiktok"]["checked"] is True


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
    assert "no chromium browser is installed" in result["meta"]["note"].lower()
    assert "playwright install chromium" in result["meta"]["note"]

    async def fake_fetch_navigation(url: str):
        return None, "navigation"

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch_navigation)
    result2 = asyncio.run(ad_presence.check_ad_presence("Barringer Law", "barringerlawfirm.com"))

    assert result2["meta"]["checked"] is False
    assert "chromium" not in result2["meta"]["note"].lower()
    assert "timeout or network error" in result2["meta"]["note"].lower()