/* ========================================================================
 * Session guard — shared by index.html AND admin.html (loaded before
 * app.js/admin.js). Two jobs:
 *
 * 1. Wrap window.fetch so EVERY existing fetch() call in app.js/admin.js
 *    (25+ call sites, none of which know anything about auth) gets 401
 *    handling for free — a session that's missing, expired, or just got
 *    revoked (an admin disabled the account, or changed its password)
 *    sends the browser to /login instead of silently failing every
 *    request. No changes needed at any individual call site.
 * 2. Populate the masthead's "signed in as ... / Log out" widget, present
 *    on every page, from GET /api/me.
 * ========================================================================= */

(function () {
  const nextParam = () => "?next=" + encodeURIComponent(window.location.pathname + window.location.search);

  const realFetch = window.fetch.bind(window);
  window.fetch = async function guardedFetch(...args) {
    const res = await realFetch(...args);
    if (res.status === 401) {
      // Never bounce /api/login itself — a wrong-password attempt is
      // ALSO a 401, and that must stay on the login page as a normal
      // "incorrect email or password" error, not a redirect loop.
      const url = typeof args[0] === "string" ? args[0] : (args[0] && args[0].url) || "";
      if (!url.includes("/api/login") && !window.location.pathname.startsWith("/login")) {
        window.location.href = "/login" + nextParam();
      }
    }
    return res;
  };

  async function renderAccountWidget() {
    const el = document.getElementById("account-widget");
    if (!el) return;
    try {
      const res = await realFetch("/api/me");
      if (!res.ok) return;  // guardedFetch above would have already redirected on a real 401
      const me = await res.json();
      el.innerHTML =
        `<span class="account-email">${escapeHtmlLocal(me.email)}</span>` +
        (me.is_admin ? ' <a href="/admin" class="account-link">Admin</a>' : "") +
        ' <button type="button" class="account-logout" id="account-logout-btn">Log out</button>';
      document.getElementById("account-logout-btn").addEventListener("click", async () => {
        await realFetch("/api/logout", { method: "POST" });
        window.location.href = "/login";
      });
    } catch (e) {
      // Non-fatal — the widget just stays empty if /api/me is unreachable.
    }
  }

  function escapeHtmlLocal(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderAccountWidget);
  } else {
    renderAccountWidget();
  }
})();
