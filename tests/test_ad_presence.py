import asyncio

from app.services import ad_presence


def test_ad_presence_uses_async_browser_path(monkeypatch):
    async def fake_fetch(url: str):
        if "facebook.com" in url:
            return "2 results\nLibrary ID: 123\nBarringer Law\nEnglish legal services"
        if "adstransparency.google.com" in url:
            return "Barringer Law\nVerified\n~3 ads"
        return "Total ads:\n0"

    monkeypatch.setattr(ad_presence, "_fetch_rendered_text", fake_fetch)
    result = asyncio.run(ad_presence.check_ad_presence("Barringer Law", "barringerlawfirm.com"))

    assert result["meta"]["checked"] is True
    assert result["google"]["checked"] is True
    assert result["tiktok"]["checked"] is True