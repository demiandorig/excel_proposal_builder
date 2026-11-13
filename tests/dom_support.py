"""Shared pieces for the tests that run a page in a REAL browser (headless Chromium through Playwright): a small static server over `app/` that can
also serve in-memory harness pages, a browser launcher that skips cleanly when Chromium is missing, a page factory that records page errors, and
the WCAG contrast helper (`window.contrastReport(selector)`) the segment-browser and proposal-history tests share.

It is a copy of the pattern in tests/test_segment_browser_dom.py (which is left as it is: it keeps its own server and its own helper), written
so a new browser test needs three lines of setup:

    from tests import dom_support as dom
    playwright_sync = pytest.importorskip("playwright.sync_api")

    @pytest.fixture(scope="module")
    def server():
        with dom.serve({"/harness.html": HARNESS}) as base:
            yield base

    @pytest.fixture(scope="module")
    def browser():
        with dom.launch(playwright_sync) as b:
            yield b

    def test_something(browser, server):
        page = dom.new_page(browser, server, "/harness.html", width=390)
        ...
        dom.close_page(page)           # asserts there were no page errors
"""
from __future__ import annotations

import contextlib
import http.server
import os
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"                           # served as the site root: /static/styles.css, /templates/admin.html, ...

# How long a page waits for an element or a condition. Everything is served from this machine, so the whole proposal-history file runs in under a minute;
# Playwright's own 30 s would turn a failure into a half-minute wait. Raise it (DOM_TEST_TIMEOUT_MS=30000) on a slow machine.
TIMEOUT_MS = int(os.environ.get("DOM_TEST_TIMEOUT_MS", "10000"))

# Text colours against their real backgrounds (WCAG AA: 4.5, 3 for large text) for everything under a selector. Elements that are dimmed
# (opacity < 1), disabled, aria-disabled or hidden are skipped, like the segment-browser helper (disabled controls are exempt from the rule).
# Include it in a harness page with <script>{CONTRAST_JS}</script>, then call page.evaluate("s => contrastReport(s)", ".ph").
CONTRAST_JS = r"""
window.contrastReport = function (root) {
  const parse = (c) => { const m = String(c).match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  const over = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1 });
  const backdrop = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a === 1) break; } }
    return layers.reduceRight((under, c) => over(c, under), { r: 255, g: 255, b: 255, a: 1 });
  };
  const out = [];
  document.querySelectorAll(root + " *").forEach((el) => {
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) || !el.getClientRects().length) return;
    if (el.closest(".ph-sr")) return;                                             // screen-reader-only text is clipped to 1px: nothing to read
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (parseFloat(cs.opacity) < 1 || e.disabled || e.getAttribute("aria-disabled") === "true" || cs.visibility === "hidden") return;
    }
    const cs = getComputedStyle(el), bg = backdrop(el), fg = over(parse(cs.color), bg);
    const hi = Math.max(lum(fg), lum(bg)), lo = Math.min(lum(fg), lum(bg)), ratio = (hi + 0.05) / (lo + 0.05);
    const size = parseFloat(cs.fontSize), large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
    if (ratio < (large ? 3 : 4.5)) out.push({ text: el.textContent.trim().slice(0, 40), cls: String(el.className), ratio: Math.round(ratio * 100) / 100 });
  });
  return out;
};
"""


def _handler(pages: dict[str, str]):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(APP), **k)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in pages:
                raw = pages[path].encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            else:
                super().do_GET()

        def log_message(self, *a):
            pass

    return Handler


class _Server(http.server.ThreadingHTTPServer):
    request_queue_size = 128                  # a page loads several files at once; the default backlog of 5 drops connections
    daemon_threads = True


@contextlib.contextmanager
def serve(pages: dict[str, str] | None = None):
    """Serves `app/` on a free local port; `pages` maps a URL path to an in-memory HTML document. Yields the base URL."""
    httpd = _Server(("127.0.0.1", 0), _handler(dict(pages or {})))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@contextlib.contextmanager
def launch(playwright_sync):
    """A headless Chromium, or a skip when there is none on this machine."""
    with playwright_sync.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:                                   # noqa: BLE001 - no browser installed here
            pytest.skip(f"Chromium is not available: {exc}")
        try:
            yield browser
        finally:
            browser.close()


def new_page(browser, base: str, path: str, *, width: int = 1280, height: int = 900, reduced_motion: str | None = None, color_scheme: str | None = None,
             forced_colors: str | None = None, timezone_id: str = "UTC", locale: str = "en-US", fonts: bool = False):
    """Opens `path` in a fresh context. The page records `page.errors` (uncaught exceptions and console errors, except the ones the component
    logs on purpose, which start with "ProposalHistory:" and go to `page.logged`). Web fonts are blocked unless `fonts` (they would be
    fetched from the network, and the layout must not depend on them)."""
    kwargs = {"viewport": {"width": width, "height": height}, "timezone_id": timezone_id, "locale": locale}
    if reduced_motion:
        kwargs["reduced_motion"] = reduced_motion
    if color_scheme:
        kwargs["color_scheme"] = color_scheme
    if forced_colors:
        kwargs["forced_colors"] = forced_colors
    context = browser.new_context(**kwargs)
    context.set_default_timeout(TIMEOUT_MS)
    page = context.new_page()
    if not fonts:
        page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
        page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    logged: list[str] = []

    def on_console(msg):
        if msg.type != "error":
            return
        (logged if msg.text.startswith("ProposalHistory:") else errors).append(msg.text)

    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", on_console)
    page.goto(base + path)
    page.errors, page.logged, page.context_ = errors, logged, context          # type: ignore[attr-defined]
    return page


def close_page(page, *, allow_errors: bool = False):
    """Closes the page's context and fails if the page threw."""
    errors = list(page.errors)
    page.context_.close()
    if not allow_errors:
        assert errors == [], errors
