/* ============================================================================
 * ProposalHistory - the proposal list shared by the planner's "My Proposal History" dialog (index.html) and the Admin console's
 * Proposal History tab (admin.html), plus the dialog shell the "Duplicate for new client" quick form is built on.
 * Dependency-free (it needs no app.js / admin.js global: only window.fetch, AbortController and plain DOM), styled by
 * proposal-history.css (every selector there is .ph-*). Loaded before app.js / admin.js. Nothing here runs at load time that needs a
 * document, so a JavaScript engine without a DOM can load it (tests/test_proposal_history_frontend.py does).
 *
 *   // planner: a modal dialog
 *   const handle = ProposalHistory.openDialog({
 *     title: "My Proposal History", subtitle: "Open or continue a proposal, or copy one for a new client.",
 *     endpoint: "/api/my-proposals", pageSize: 25, returnFocus: document.getElementById("my-proposals-btn"),
 *     linkTarget: (p) => (state.parsed ? "_blank" : "_self"),
 *     canDelete: (p) => p.status === "draft",                    // default; a generated proposal is a permanent record and is never deletable
 *     canDuplicate: (p) => p.can_reopen !== false,               // default
 *     onDuplicate: ({ proposal, trigger, controller }) => { ... },      // the SLOT: the button calls this once and does nothing else
 *     emptyAction: { label: "Paste a Notion request", run: () => document.getElementById("notion-id-input").focus() },
 *   });                                                           // handle.close(); handle.controller
 *
 *   // admin: the same list inline, with a who cell, compact buttons and a My / All switch
 *   const ctl = ProposalHistory.mount(document.getElementById("proposals-host"), {
 *     surface: "admin", endpoint: "/api/admin/proposals", pageSize: 50,
 *     scopes: [{ key: "mine", label: "My proposals" }, { key: "all", label: "All proposals" }], scope: "mine",
 *     params: (scope) => ({ mine: scope === "mine" }), linkTarget: () => "_blank", onDuplicate, canDuplicate,
 *   });
 *
 *   ProposalHistory.close();  ProposalHistory.isOpen();           // the history dialog only
 *   const sh = ProposalHistory.shell({ title, subtitle, top, narrow, returnFocus, onClose, initialFocus, onEscape, pending });
 *   sh.dialog, sh.body (append your content), sh.close(result?), sh.setBusy(bool), sh.focusInitial()
 *
 * Controller (mount() and handle.controller): reload({ highlightId, keepRows }), setScope(key), focusSearch(), getState(), destroy().
 * reload({ highlightId }) is for "a row was just created": it puts search, status and sort back to their defaults (so the row is on page
 * one), reloads, flashes that row and moves the keyboard focus to its Open link.
 *
 * Options not shown above: now() (test seam for relative time, default Date.now), fetchJson(url, { signal }) (test seam, default
 * window.fetch + res.json(); a failure carries .status), searchDelay (250), copyLink (true: the menu's "Copy link"), me (the signed-in
 * email, for the admin's "delete my own drafts only"; without it and without canDelete the component asks /api/me once).
 *
 * The rows are the JSON of GET /api/my-proposals and /api/admin/proposals. Rows are never trusted: every field is coerced before it is
 * drawn, and everything is built with createElement and text nodes (no innerHTML anywhere), so a client called <img onerror=...> is text.
 *
 * Behaviour that is easy to get wrong:
 *  - Search: every typed word must match (the server does that); 250 ms debounce; Enter searches now; each change aborts the request in
 *    flight AND a counter drops any answer that is no longer the newest, so a slow "a" can never overwrite a quick "ab".
 *  - A refresh (filter, sort, search, retry) keeps the current rows on screen, dimmed, instead of blanking the list.
 *  - Show more asks for page floor(held / pageSize) + 1 and merges the rows BY ID, so a draft autosaved in another tab, or a row deleted
 *    here, never duplicates or skips a row.
 *  - Delete asks inline in the row (never confirm()); Keep draft is the focused default.
 *  - Escape closes one level per press: menu, delete confirmation, a non-empty search box (when it has the focus), then the dialog; it
 *    never reaches a listener behind the dialog (the Constanza dock closes on Escape).
 *  - shell(): a ref-counted page-scroll lock, a stack of open dialogs (only the topmost owns Escape, Tab and focus), a focus trap, focus
 *    back to the opener on close, and the second click of a double-click on the opener is swallowed.
 *
 * Hooks for tests (stable): .ph, .ph[data-state], .ph-dialog, .ph-search-input, .ph-search-clear, .ph-seg button[aria-pressed], .ph-seg-n,
 * .ph-select, .ph-sort[data-sort][data-dir][aria-pressed], .ph-count, .ph-live, .ph-row[data-id][data-status], .ph-client-name, .ph-subline,
 * .ph-lineage .ph-chip, .ph-open[data-action=open], .ph-dup[data-action=duplicate], .ph-kebab, .ph-menu, .ph-menu-item[data-action],
 * .ph-confirm, .ph-confirm-keep, .ph-confirm-delete, .ph-more-btn, .ph-more-left, .ph-state, .ph-retry, .ph-clear-search, .ph-show-all,
 * .ph-empty-action, .ph-skel, .ph-overlay--top, .ph-toast. ProposalHistory._pure holds the pure helpers.
 * ========================================================================== */
(function () {
  "use strict";

  const DEFAULT_PAGE_SIZE = 25;
  const MAX_PAGE_SIZE = 100;               // the server's cap
  const SEARCH_DELAY_MS = 250;
  const SEARCH_MAX_LEN = 200;              // the server caps the typed search at 200 characters,
  const SEARCH_MAX_WORDS = 6;              // uses at most 6 words
  const SEARCH_MAX_WORD_LEN = 80;          // of at most 80 characters each
  const TICK_MS = 60000;                   // relative times ("7 min ago") are refreshed this often
  const OPEN_GUARD_MS = 700;               // a repeat click this soon after opening, whose first click was not in the dialog, is the opener's
  const SAY_DELAY_MS = 40;                 // the live region is emptied, then filled this much later, so a repeated message is read out again
  const ECHO_MAX = 40;                     // characters of the typed search echoed back in "Nothing matches ..."
  const FLASH_MS = 2400;                   // how long a just-created row is highlighted
  const TOAST_MS = 3200;
  const WIZARD_STEPS = 8;
  const FLIGHT_TEXT_MAX = 60;
  const SKELETON_ROWS = 6;
  const PHONE_QUERY = "(max-width: 640px)";
  const STATUSES = ["all", "draft", "generated"];
  const SORTS = ["updated", "client", "value"];
  const NATURAL_DIR = { updated: "desc", client: "asc", value: "desc" };    // what the first click on a sort does (and what the server assumes)
  const NO_ACCESS_REOPEN = "Saved before reopening was supported";
  const NO_ACCESS_COPY = "Saved before copying was supported";

  // ---------------------------------------------------------------- pure helpers (no DOM; tests/test_proposal_history_frontend.py runs them)

  const HAS_NORMALIZE = typeof "".normalize === "function";

  // Lower-case with the accents taken off, so "Peña" and "pena" are the same word to the highlight.
  function fold(value) {
    let s = value == null ? "" : String(value);
    if (HAS_NORMALIZE) s = s.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    return s.toLowerCase();
  }

  // The typed words the way the server will use them (at most 6, each at most 80 characters), folded, for the highlight.
  function searchWords(query) {
    return fold(query).split(/\s+/).filter(Boolean).slice(0, SEARCH_MAX_WORDS).map((w) => w.slice(0, SEARCH_MAX_WORD_LEN));
  }

  // [start, end) ranges of `text` (original characters) covered by the typed words, merged. Same algorithm as SegmentBrowser's.
  function highlightRanges(text, words) {
    text = text == null ? "" : String(text);
    if (!words || !words.length || !text) return [];
    let folded = "";
    const map = [];                                              // folded index -> index in `text`
    for (let i = 0; i < text.length;) {
      const ch = String.fromCodePoint(text.codePointAt(i));
      const f = fold(ch);
      for (let k = 0; k < f.length; k++) map.push(i);
      folded += f;
      i += ch.length;
    }
    const spans = [];
    words.forEach((w) => {
      if (!w) return;
      for (let from = 0, at; (at = folded.indexOf(w, from)) >= 0; from = at + w.length) spans.push([at, at + w.length]);
    });
    spans.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const merged = [];
    spans.forEach(([s, e]) => {
      const last = merged[merged.length - 1];
      if (last && s <= last[1]) last[1] = Math.max(last[1], e);
      else merged.push([s, e]);
    });
    return merged.map(([s, e]) => [map[s], e < map.length ? map[e] : text.length]).filter(([s, e]) => e > s);
  }

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const WEEKDAYS_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function toInt(v) {
    if (typeof v === "number") return isFinite(v) ? Math.trunc(v) : null;
    if (typeof v === "string" && /^\s*\d+\s*$/.test(v)) return Number(v);
    return null;
  }

  function str(v, max) {
    if (typeof v !== "string") return "";
    const s = v.trim();
    return max && s.length > max ? s.slice(0, max) : s;
  }

  function daysIn(y, m) {                                        // m is 1..12
    if (m === 2) return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 29 : 28;
    return m === 4 || m === 6 || m === 9 || m === 11 ? 30 : 31;
  }

  // "2027-01-05" -> { y, m, d } (m 1..12), null when it is not a real calendar date. Done by hand, never through Date: a calendar date
  // must not shift with the viewer's time zone.
  function parseISODate(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(typeof value === "string" ? value.trim() : "");
    if (!m) return null;
    const y = +m[1], mo = +m[2], d = +m[3];
    if (mo < 1 || mo > 12 || d < 1 || d > daysIn(y, mo)) return null;
    return { y, m: mo, d };
  }

  function dateText(p, withYear) { return MONTHS[p.m - 1] + " " + p.d + (withYear ? ", " + p.y : ""); }

  // The flight cell's main line: "Jan 5 – Mar 31, 2027" (the year once when both dates share it), "Dec 15, 2026 – Jan 4, 2027" across years,
  // "Starts Jan 5, 2027" / "Ends Mar 31, 2027" with only one date, else the raw text the server kept (at most 60 characters), else "".
  function formatFlight(p) {
    p = p || {};
    const a = parseISODate(p.flight_start), b = parseISODate(p.flight_end);
    if (a && b) {
      if (a.y === b.y && a.m === b.m && a.d === b.d) return dateText(a, true);
      return a.y === b.y ? dateText(a, false) + " – " + dateText(b, true) : dateText(a, true) + " – " + dateText(b, true);
    }
    if (a) return "Starts " + dateText(a, true);
    if (b) return "Ends " + dateText(b, true);
    return str(p.flight_text).replace(/\s+/g, " ").slice(0, FLIGHT_TEXT_MAX);
  }

  const BILLING = { week: "Billed weekly", month: "Billed monthly", quarter: "Billed quarterly", full_flight: "One full-flight bill" };
  function billingText(unit) { return typeof unit === "string" && Object.prototype.hasOwnProperty.call(BILLING, unit) ? BILLING[unit] : ""; }

  // A timestamp from the server ("2026-11-11T15:23:00.123456+00:00", also "... 15:23:00Z" or with no zone, which is the server's UTC) to
  // epoch milliseconds, NaN when it is not one. Parsed by hand: Date.parse differs between engines on 6-digit fractions.
  const TS_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$/i;
  function parseTime(value) {
    if (typeof value !== "string") return NaN;
    const m = TS_RE.exec(value.trim());
    if (!m) return NaN;
    const y = +m[1], mo = +m[2], d = +m[3], hh = +m[4], mi = +m[5], ss = m[6] ? +m[6] : 0;
    if (mo < 1 || mo > 12 || d < 1 || d > daysIn(y, mo) || hh > 23 || mi > 59 || ss > 59) return NaN;
    const ms = m[7] ? Number((m[7] + "00").slice(0, 3)) : 0;
    let offset = 0;
    if (m[8] && m[8].toUpperCase() !== "Z") {
      const digits = m[8].replace(":", "");
      offset = (m[8][0] === "-" ? -1 : 1) * (Number(digits.slice(1, 3)) * 60 + Number(digits.slice(3, 5) || 0));
    }
    return Date.UTC(y, mo - 1, d, hh, mi, ss, ms) - offset * 60000;
  }

  function dateParts(t, utc) {
    const dt = new Date(t);
    return utc
      ? { y: dt.getUTCFullYear(), m: dt.getUTCMonth(), d: dt.getUTCDate(), wd: dt.getUTCDay(), h: dt.getUTCHours(), mi: dt.getUTCMinutes() }
      : { y: dt.getFullYear(), m: dt.getMonth(), d: dt.getDate(), wd: dt.getDay(), h: dt.getHours(), mi: dt.getMinutes() };
  }
  function clockText(pt) { return (pt.h % 12 || 12) + ":" + (pt.mi < 10 ? "0" : "") + pt.mi + " " + (pt.h < 12 ? "AM" : "PM"); }

  // The last-edited cell. `now` is epoch ms; opts.timeZone "UTC" formats in UTC (tests), anything else in the viewer's own zone.
  //   main   "Just now" | "7 min ago" | "3 hr ago" | "Yesterday" | "3 days ago", and from 7 days on the date with its year ("Oct 28, 2026")
  //   minor  the exact time ("Nov 11, 3:23 PM"), empty from 7 days on
  //   title  "Wednesday, November 11, 2026 at 3:23 PM"
  // A timestamp a little in the future (clock skew) reads "Just now"; one more than a day ahead is shown as a date, not as "just now".
  function formatWhen(value, now, opts) {
    const t = parseTime(value);
    if (isNaN(t)) return { valid: false, main: "—", minor: "", title: "", iso: "" };
    const utc = !!opts && opts.timeZone === "UTC";
    const pt = dateParts(t, utc);
    const title = WEEKDAYS_LONG[pt.wd] + ", " + MONTHS_LONG[pt.m] + " " + pt.d + ", " + pt.y + " at " + clockText(pt);
    const dateOnly = MONTHS[pt.m] + " " + pt.d + ", " + pt.y;
    const minor = MONTHS[pt.m] + " " + pt.d + ", " + clockText(pt);
    const secs = ((typeof now === "number" && isFinite(now) ? now : Date.now()) - t) / 1000;
    let main;
    if (secs < -86400) return { valid: true, main: dateOnly, minor: "", title, iso: value };
    if (secs < 60) main = "Just now";
    else if (secs < 3600) main = Math.floor(secs / 60) + " min ago";
    else if (secs < 86400) main = Math.floor(secs / 3600) + " hr ago";
    else if (secs < 172800) main = "Yesterday";
    else if (secs < 7 * 86400) main = Math.floor(secs / 86400) + " days ago";
    else return { valid: true, main: dateOnly, minor: "", title, iso: value };
    return { valid: true, main, minor, title, iso: value };
  }

  function fmtInt(n) {
    n = Math.round(Number(n));
    return isFinite(n) ? String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",") : "0";
  }

  // "$412,500" (no cents: a proposal total is a planning figure); null when it is not a believable number, so the cell shows a dash.
  function formatMoney(n) {
    if (typeof n !== "number" || !isFinite(n) || Math.abs(n) >= 1e15) return null;
    const r = Math.round(Math.abs(n));
    return (n < 0 && r > 0 ? "-" : "") + "$" + fmtInt(r);
  }

  function plural(n, one, many) { return n === 1 ? one : (many || one + "s"); }

  // The typed search, shortened for the "Nothing matches ..." lines (a pasted list must not fill the screen or the screen reader).
  function echoQuery(query) {
    const chars = Array.from(String(query == null ? "" : query).replace(/\s+/g, " ").trim());
    return chars.length > ECHO_MAX ? chars.slice(0, ECHO_MAX).join("") + "…" : chars.join("");
  }

  // "Showing 25 of 140 proposals", "14 proposals", "5 drafts", "3 proposals match “acme”", "No drafts".
  function countText(o) {
    o = o || {};
    const shown = Math.max(0, toInt(o.shown) || 0), total = Math.max(0, toInt(o.total) || 0), q = echoQuery(o.query);
    const nouns = o.status === "draft" ? ["draft", "drafts"] : o.status === "generated" ? ["generated proposal", "generated proposals"] : ["proposal", "proposals"];
    const noun = (n) => (n === 1 ? nouns[0] : nouns[1]);
    if (q) {
      if (!total) return "No " + nouns[1] + " match “" + q + "”";
      if (shown < total) return "Showing " + fmtInt(shown) + " of " + fmtInt(total) + " " + nouns[1] + " that match “" + q + "”";
      return fmtInt(total) + " " + noun(total) + (total === 1 ? " matches" : " match") + " “" + q + "”";
    }
    if (!total) return "No " + nouns[1];
    if (shown < total) return "Showing " + fmtInt(shown) + " of " + fmtInt(total) + " " + nouns[1];
    return fmtInt(total) + " " + noun(total);
  }

  // What the list says when it could not load. Never the server's own text: that goes to the console (the status code only).
  function describeError(status, offline) {
    if (offline) return "Check your connection and try again.";
    if (status === 401) return "Your session ended. Signing you in again…";
    if (status === 403) return "You don’t have access to this list.";
    if (status === 429) return "Too many requests. Wait a moment, then try again.";
    if (typeof status === "number" && status >= 500) return "The server had a problem. Try again in a moment. Nothing has been lost.";
    return "Something went wrong. Try again in a moment.";
  }

  // The typed search as it is sent: whitespace collapsed, at most 200 characters.
  function normQuery(query) {
    return Array.from(String(query == null ? "" : query).replace(/\s+/g, " ").trim()).slice(0, SEARCH_MAX_LEN).join("");
  }

  // encodeURIComponent throws on a lone surrogate (a half of an emoji, pasted): replace it rather than lose the whole search.
  function enc(s) {
    s = String(s);
    try { return encodeURIComponent(s); } catch (e) { return encodeURIComponent(s.replace(/[\uD800-\uDFFF]/g, "\uFFFD")); }
  }

  // The list request's query string. Defaults are left out (page 1, status all, sort updated, the natural direction of the sort) so
  // the common request is short; page_size is always sent (the server's own default is 10). `extra` is {key: value} from opts.params(scope).
  function buildQuery(o) {
    o = o || {};
    const size = Math.min(MAX_PAGE_SIZE, Math.max(1, toInt(o.pageSize) || DEFAULT_PAGE_SIZE));
    const page = Math.max(1, toInt(o.page) || 1);
    const sort = SORTS.indexOf(o.sort) >= 0 ? o.sort : "updated";
    const dir = o.dir === "asc" || o.dir === "desc" ? o.dir : NATURAL_DIR[sort];
    const status = STATUSES.indexOf(o.status) >= 0 ? o.status : "all";
    const parts = [];
    const add = (k, v) => parts.push(enc(k) + "=" + enc(v));
    if (page > 1) add("page", page);
    add("page_size", size);
    const q = normQuery(o.query);
    if (q) add("search", q);
    if (status !== "all") add("status", status);
    if (sort !== "updated") add("sort", sort);
    if (dir !== NATURAL_DIR[sort]) add("dir", dir);
    const extra = o.extra && typeof o.extra === "object" ? o.extra : {};
    Object.keys(extra).forEach((k) => { if (extra[k] != null) add(k, extra[k]); });
    return parts.join("&");
  }

  // Show more asks for the page after the rows already held.
  function nextPage(held, pageSize) {
    const size = Math.max(1, toInt(pageSize) || DEFAULT_PAGE_SIZE);
    return Math.floor(Math.max(0, toInt(held) || 0) / size) + 1;
  }

  function rowId(r) { return r && typeof r === "object" && typeof r.proposal_id === "string" && r.proposal_id !== "" ? r.proposal_id : ""; }

  // The held rows, then the incoming ones that are not held yet, by proposal_id. A row that is held keeps its place (its node keeps its
  // focus); a repeated id inside `incoming` counts once; a row without an id is dropped.
  function mergeRows(held, incoming) {
    const out = [], seen = new Set();
    (Array.isArray(held) ? held : []).forEach((r) => { const id = rowId(r); if (id && !seen.has(id)) { seen.add(id); out.push(r); } });
    (Array.isArray(incoming) ? incoming : []).forEach((r) => { const id = rowId(r); if (id && !seen.has(id)) { seen.add(id); out.push(r); } });
    return out;
  }

  const SORT_WORDS = {
    updated: { label: "Last edited", asc: "oldest first", desc: "newest first" },
    client: { label: "Client", asc: "A to Z", desc: "Z to A" },
    value: { label: "Net value", asc: "low to high", desc: "high to low" },
  };
  // The six choices of the narrow sort <select>.
  function sortChoices() {
    const out = [];
    SORTS.forEach((sort) => [NATURAL_DIR[sort], NATURAL_DIR[sort] === "asc" ? "desc" : "asc"].forEach((dir) => {
      out.push({ value: sort + ":" + dir, sort, dir, label: SORT_WORDS[sort].label + " · " + SORT_WORDS[sort][dir] });
    }));
    return out;
  }
  // "client, A to Z": for "Sorted by client, A to Z." and the header button's screen-reader text.
  function sortPhrase(sort, dir) {
    const w = SORT_WORDS[sort] || SORT_WORDS.updated;
    return w.label.toLowerCase() + ", " + w[dir === "asc" ? "asc" : "desc"];
  }

  // duplicated_from as the server stores it: an object {proposal_id, client_name, notion_id, title}, a bare id string, or nothing -> a
  // clean object (each value at most 200 characters) or null.
  function lineageOf(raw) {
    let o;
    if (typeof raw === "string") o = { proposal_id: raw };
    else if (raw && typeof raw === "object") o = raw;                          // (an array has none of the keys: it ends as null below)
    else return null;
    const out = { proposal_id: str(o.proposal_id, 200), client_name: str(o.client_name, 200), notion_id: str(o.notion_id, 200), title: str(o.title, 200) };
    return out.proposal_id || out.client_name || out.notion_id || out.title ? out : null;
  }

  // The "Copied from ..." chip: the client's name, else the Notion ID, else "another proposal"; the tooltip adds the ID.
  function lineageLabel(dup) {
    const d = lineageOf(dup);
    if (!d) return null;
    const name = d.client_name || d.notion_id || "another proposal";
    return { text: "Copied from " + name, title: "Copied from " + (d.client_name || "another proposal") + (d.notion_id ? " (" + d.notion_id + ")" : "") };
  }

  // "Mac · Chrome 141 · 10.0.0.3" from a user-agent string and an address (admin's who cell). The full user agent is the tooltip.
  function deviceLabel(ua, ip) {
    ua = typeof ua === "string" ? ua : "";
    let os = "", browser = "";
    if (/Windows NT/.test(ua)) os = "Windows";
    else if (/iPhone/.test(ua)) os = "iPhone";
    else if (/iPad/.test(ua)) os = "iPad";
    else if (/Android/.test(ua)) os = "Android";
    else if (/CrOS/.test(ua)) os = "ChromeOS";
    else if (/Mac OS X/.test(ua)) os = "Mac";
    else if (/Linux/.test(ua)) os = "Linux";
    const ver = (re) => { const m = re.exec(ua); return m ? " " + m[1].split(".")[0] : ""; };
    if (/Edg(e|A|iOS)?\//.test(ua)) browser = "Edge" + ver(/Edg(?:e|A|iOS)?\/([\d.]+)/);
    else if (/OPR\//.test(ua)) browser = "Opera" + ver(/OPR\/([\d.]+)/);
    else if (/Firefox\/|FxiOS\//.test(ua)) browser = "Firefox" + ver(/(?:Firefox|FxiOS)\/([\d.]+)/);
    else if (/Chrome\/|CriOS\//.test(ua)) browser = "Chrome" + ver(/(?:Chrome|CriOS)\/([\d.]+)/);
    else if (/Safari\//.test(ua)) browser = "Safari" + ver(/Version\/([\d.]+)/);
    const address = typeof ip === "string" ? ip.trim().slice(0, 64) : "";
    const parts = [os, browser].filter(Boolean);
    if (!parts.length && ua) parts.push(ua.slice(0, 40));
    if (address) parts.push(address);
    return parts.join(" · ");
  }

  // The muted mono line under the client: the proposal title. Its Notion ID is added in front only when the title does not already
  // contain it (titles normally start with it). `title` is the full text for the tooltip.
  function subline(p) {
    p = p || {};
    const title = str(p.proposal_title) || str(p.filename), id = str(p.notion_id);
    const text = id && !fold(title).includes(fold(id)) ? [id, title].filter(Boolean).join(" ") : title;
    return { text, title: text };
  }

  // One row of the server's JSON, coerced: every field has the type the drawing code expects, whatever came over the wire.
  function rowFacts(p) {
    p = p && typeof p === "object" ? p : {};
    const step = toInt(p.wizard_step), lines = toInt(p.line_count);
    const tabs = Array.isArray(p.tabs_built) ? p.tabs_built.filter((t) => typeof t === "string" && t.trim()).map((t) => t.trim()) : [];
    return {
      id: str(p.proposal_id),
      name: str(p.client_name).replace(/\s+/g, " "),
      draft: p.status === "draft",
      title: str(p.proposal_title) || str(p.filename),
      notionId: str(p.notion_id),
      net: typeof p.total_net === "number" && isFinite(p.total_net) ? p.total_net : null,
      lines: lines !== null && lines >= 0 ? lines : null,
      step: step !== null && step >= 1 && step <= WIZARD_STEPS ? step : null,
      unit: typeof p.time_unit === "string" ? p.time_unit : "",
      tabs,
      lineage: lineageOf(p.duplicated_from),
      canReopen: p.can_reopen !== false,
      updatedAt: typeof p.updated_at === "string" ? p.updated_at : "",
      createdBy: str(p.created_by_email),
      requestedBy: str(p.requested_by) || str(p.seller_email),
      userAgent: typeof p.requester_user_agent === "string" ? p.requester_user_agent : "",
      ip: typeof p.requester_ip === "string" ? p.requester_ip : "",
    };
  }

  // ---------------------------------------------------------------- DOM helpers

  let uid = 0;

  function h(tag, props) {
    const node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach((k) => {
        const v = props[k];
        if (v == null || v === false) return;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = v;
        else node.setAttribute(k, v === true ? "" : String(v));
      });
    }
    for (let i = 2; i < arguments.length; i++) addKids(node, arguments[i]);
    return node;
  }
  function addKids(node, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) kid.forEach((k) => addKids(node, k));
    else node.append(typeof kid === "object" ? kid : String(kid));           // a string becomes a text node: never parsed as HTML
  }

  // `text` into `el` with the searched words wrapped in <mark>. Built from text nodes, so a client name can never inject markup.
  function appendHighlighted(el, text, words) {
    text = text == null ? "" : String(text);
    const ranges = highlightRanges(text, words);
    if (!ranges.length) { el.append(text); return; }
    let at = 0;
    ranges.forEach(([s, e]) => {
      if (s > at) el.append(text.slice(at, s));
      el.append(h("mark", { class: "ph-mark", text: text.slice(s, e) }));
      at = e;
    });
    if (at < text.length) el.append(text.slice(at));
  }

  function visible(el) { return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length); }
  function phone() { return !!(typeof window !== "undefined" && window.matchMedia && window.matchMedia(PHONE_QUERY).matches); }
  function safeFocus(el, opts) {
    if (!el || typeof el.focus !== "function") return false;
    try { el.focus(opts); return true; } catch (e) { return false; }
  }

  // ---------------------------------------------------------------- the shell: overlay, dialog, focus, scroll lock, stack of dialogs

  const stack = [];                        // open shells, bottom to top: only the last one owns Escape, Tab and the focus
  let scrollLockCount = 0;
  let scrollLock = null;
  let docListening = false;

  // The page behind must not scroll while a dialog is open, and must come back EXACTLY as it was: the whole inline style attribute is put
  // back when nothing else touched it meanwhile; if something did, only the properties we changed are restored. Counted: two dialogs
  // stacked (the history and the copy form) share one lock, which is released when the last one closes.
  function lockPageScroll() {
    if (scrollLockCount++ > 0) return;
    const body = document.body, root = document.documentElement;
    const bar = Math.max(0, window.innerWidth - root.clientWidth);          // keep the page from jumping sideways when its scrollbar goes
    const pad = (parseFloat(window.getComputedStyle(body).paddingRight) || 0) + bar;
    const was = {};
    ["overflow-x", "overflow-y", "padding-right"].forEach((p) => { was[p] = [body.style.getPropertyValue(p), body.style.getPropertyPriority(p)]; });
    const before = body.getAttribute("style");
    body.style.setProperty("overflow", "hidden");
    if (bar) body.style.setProperty("padding-right", pad + "px");
    scrollLock = { before, after: body.getAttribute("style"), was };
  }

  function unlockPageScroll() {
    if (scrollLockCount === 0) return;
    if (--scrollLockCount > 0) return;
    const body = document.body, s = scrollLock;
    scrollLock = null;
    if (!s) return;
    if (body.getAttribute("style") === s.after) {
      if (s.before === null) body.removeAttribute("style"); else body.setAttribute("style", s.before);
      return;
    }
    Object.keys(s.was).forEach((p) => { if (s.was[p][0]) body.style.setProperty(p, s.was[p][0], s.was[p][1]); else body.style.removeProperty(p); });
  }

  function focusablesIn(root) {
    return Array.from(root.querySelectorAll("button, input, select, textarea, a[href], [tabindex]"))
      .filter((el) => !el.disabled && el.tabIndex >= 0 && visible(el));
  }

  // A top-level form dialog that someone else opened (not through shell) still has the keyboard: the history yields to it.
  function foreignTop(top) {
    const el = document.querySelector(".ph-overlay--top");
    return !!el && el !== top.overlay;
  }

  function onDocKey(e) {
    const top = stack[stack.length - 1];
    if (!top || foreignTop(top)) return;
    if (e.key === "Escape") {
      if (e.isComposing || e.keyCode === 229) return;                      // Esc that cancels an IME composition is not "close"
      e.preventDefault(); e.stopPropagation();                             // stopped here so the dock behind does not close too
      if (typeof top.opts.onEscape === "function" && top.opts.onEscape(e)) return;   // the host used it up (closed a menu, cleared a box)
      if (!top.busy) top.close();
      return;
    }
    if (e.key !== "Tab") return;
    const active = document.activeElement;
    if (top.owns(active) && !top.dialog.contains(active)) return;           // in the list's popup menu: it handles its own Tab
    const list = focusablesIn(top.dialog);
    if (!list.length) { e.preventDefault(); top.dialog.focus(); return; }
    const first = list[0], last = list[list.length - 1];
    if (!top.dialog.contains(active) || active === top.dialog) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
    else if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
  }

  function onFocusIn(e) {
    const top = stack[stack.length - 1];
    if (!top || foreignTop(top) || top.dialog.contains(e.target) || top.owns(e.target)) return;
    const list = focusablesIn(top.dialog);
    (list[0] || top.dialog).focus();                                        // focus that escapes (a script, a click-through) comes straight back
  }

  function listenToDocument(on) {
    if (on === docListening) return;
    docListening = on;
    const f = on ? "addEventListener" : "removeEventListener";
    document[f]("keydown", onDocKey, true);
    document[f]("focusin", onFocusIn, true);
  }

  const inertShell = () => ({ dialog: null, body: null, close() {}, setBusy() {}, focusInitial() {} });

  // A modal dialog: heading, an X, and an empty body for the host to fill. See the header comment for the options.
  function shell(opts) {
    if (typeof document === "undefined") return inertShell();
    opts = opts || {};
    const opener = opts.returnFocus || document.activeElement;
    const pre = "ph" + (++uid);
    const titleId = pre + "-t", subId = pre + "-s";
    const sub = opts.subtitle == null || opts.subtitle === "" ? "" : String(opts.subtitle);
    const dialog = h("div", { class: "ph-dialog" + (opts.narrow ? " ph-dialog--form" : ""), role: "dialog", "aria-modal": "true", "aria-labelledby": titleId, "aria-describedby": sub ? subId : null, tabindex: "-1" });
    const overlay = h("div", { class: "ph-overlay" + (opts.top ? " ph-overlay--top" : "") }, dialog);
    const closeBtn = h("button", { type: "button", class: "ph-x", "aria-label": "Close" }, "×");
    const body = h("div", { class: "ph-shell-body" });
    dialog.append(h("header", { class: "ph-head" },
      h("div", { class: "ph-head-text" },
        h("h2", { class: "ph-title", id: titleId, text: opts.title == null ? "" : String(opts.title) }),
        sub ? h("p", { class: "ph-sub", id: subId, text: sub }) : null),
      closeBtn), body);

    const S = {
      opts, overlay, dialog, body, closed: false, busy: false, focused: false,
      owns: (el) => !!el && typeof opts.owns === "function" && !!opts.owns(el),
      close,
    };

    function close(result) {
      if (S.closed) return;
      S.closed = true;
      const at = stack.indexOf(S);
      if (at >= 0) stack.splice(at, 1);
      if (!stack.length) listenToDocument(false);
      overlay.remove();
      unlockPageScroll();
      if (opener && opener.isConnected) safeFocus(opener, { preventScroll: true });
      if (typeof opts.onClose === "function") {
        try { opts.onClose(result); } catch (e) { if (typeof console !== "undefined") console.error("ProposalHistory: onClose failed"); }
      }
    }

    // Where the keyboard starts: initialFocus (an element, or a function that returns one, asked only now so the host can have filled the
    // body first), else the first control in the body. On a phone the dialog itself, so the on-screen keyboard stays down.
    function focusInitial() {
      if (S.closed) return;
      S.focused = true;
      let el = null;
      if (!phone()) {
        try { el = typeof opts.initialFocus === "function" ? opts.initialFocus() : opts.initialFocus; } catch (e) { el = null; }
        if (!el || !dialog.contains(el) || !visible(el)) el = focusablesIn(body)[0] || null;
      }
      safeFocus(el || dialog);
    }

    closeBtn.addEventListener("click", () => { if (!S.busy) close(); });

    // The second click of a double-click on the opener lands on the dialog that the first one just opened (on the backdrop: it would close
    // it). A press that is the 2nd or 3rd of a multi-click, soon after opening, whose FIRST press was not in the dialog (it was the opener),
    // belongs to the opener: it, and the mouseup and click that follow, are dropped. A double-click that starts inside is the person's own.
    const openedAt = Date.now();
    let startedHere = false, dropping = false;
    const dropOpenersRepeat = (e) => {
      if (e.type === "mousedown") {
        if (e.detail <= 1) { startedHere = true; dropping = false; }
        else dropping = !startedHere && Date.now() - openedAt < OPEN_GUARD_MS;
      }
      if (!dropping) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (e.type === "click") dropping = false;
    };
    ["mousedown", "mouseup", "click"].forEach((type) => overlay.addEventListener(type, dropOpenersRepeat, true));

    // A click on the backdrop closes only when nothing is pending: not while busy, and not when the host says it has work to lose.
    let downOnBackdrop = false;
    overlay.addEventListener("mousedown", (e) => { downOnBackdrop = e.target === overlay; });
    overlay.addEventListener("click", (e) => {
      if (e.target !== overlay || !downOnBackdrop || S.busy) return;
      const pending = typeof opts.pending === "function" ? !!opts.pending() : !!opts.pending;
      if (!pending) close();
    });

    lockPageScroll();
    document.body.append(overlay);
    stack.push(S);
    listenToDocument(true);
    // The host usually fills the body in the same turn: focus is settled after it, unless the host asks for it sooner (focusInitial()).
    Promise.resolve().then(() => { if (!S.closed && !S.focused) focusInitial(); });

    return {
      dialog, body, overlay, close,
      setBusy(b) {
        S.busy = !!b;
        dialog.classList.toggle("is-busy", S.busy);
        if (S.busy) dialog.setAttribute("aria-busy", "true"); else dialog.removeAttribute("aria-busy");
      },
      focusInitial,
    };
  }

  // ---------------------------------------------------------------- the list

  function defaultFetchJson(url, o) {
    return window.fetch(url, { signal: o && o.signal, headers: { Accept: "application/json" } }).then((res) => {
      if (!res.ok) { const err = new Error("HTTP " + res.status); err.status = res.status; throw err; }
      return res.json().catch(() => { const err = new Error("Not JSON"); err.status = 0; throw err; });
    });
  }

  function isAbort(err) { return !!err && err.name === "AbortError"; }
  function isOffline(err) {
    if (err && typeof err.status === "number") return false;
    if (typeof navigator !== "undefined" && navigator.onLine === false) return true;
    return !!err && err.name === "TypeError";                                  // fetch() rejects with a TypeError when it cannot reach the server
  }

  function normalizeScopes(scopes) {
    if (!Array.isArray(scopes)) return [];
    return scopes.filter((s) => s && typeof s === "object" && s.key != null).map((s) => ({ key: String(s.key), label: s.label == null ? String(s.key) : String(s.label) }));
  }

  function normalizeCfg(opts, dialog) {
    opts = opts || {};
    const admin = opts.surface === "admin";
    const scopes = admin ? normalizeScopes(opts.scopes) : [];
    const size = toInt(opts.pageSize);
    const cfg = {
      admin, dialog: !!dialog,
      endpoint: typeof opts.endpoint === "string" && opts.endpoint ? opts.endpoint : admin ? "/api/admin/proposals" : "/api/my-proposals",
      pageSize: size && size > 0 ? Math.min(size, MAX_PAGE_SIZE) : admin ? 50 : DEFAULT_PAGE_SIZE,
      scopes,
      scope: scopes.length ? (scopes.find((s) => s.key === String(opts.scope)) || scopes[0]).key : "",
      params: typeof opts.params === "function" ? opts.params : () => ({}),
      linkTarget: typeof opts.linkTarget === "function" ? opts.linkTarget : () => "_blank",
      canDelete: typeof opts.canDelete === "function" ? opts.canDelete : null,
      canDuplicate: typeof opts.canDuplicate === "function" ? opts.canDuplicate : (p) => !!p && p.can_reopen !== false,
      onDuplicate: typeof opts.onDuplicate === "function" ? opts.onDuplicate : null,
      emptyAction: opts.emptyAction && typeof opts.emptyAction === "object" && opts.emptyAction.label ? opts.emptyAction : null,
      now: typeof opts.now === "function" ? opts.now : () => Date.now(),
      fetchJson: typeof opts.fetchJson === "function" ? opts.fetchJson : defaultFetchJson,
      searchDelay: typeof opts.searchDelay === "number" && opts.searchDelay >= 0 ? opts.searchDelay : SEARCH_DELAY_MS,
      copyLink: opts.copyLink !== false,
      me: typeof opts.me === "string" ? opts.me.trim().toLowerCase() : "",
    };
    return cfg;
  }

  // Builds the list into `host` and starts loading. Returns { controller, root, handleEscape, owns, teardown }.
  function createList(host, cfg, closeDialog) {
    const S = {
      phase: "loading", refreshing: false, query: "", words: [], status: "all", sort: "updated", dir: "desc", scope: cfg.scope,
      rows: [], total: 0, facets: null, moreError: false, loadingMore: false, exhausted: false,
      token: 0, abort: null, destroyed: false, highlight: "", focusFirst: false, me: cfg.me,
    };
    const admin = cfg.admin;
    const pre = "ph" + (++uid);

    // ------------------------------------------------ DOM: tools, count, body, states
    const root = h("div", { class: "ph" + (admin ? " ph--admin ph--compact" : ""), "data-state": "loading", tabindex: "-1" });

    const scopeButtons = cfg.scopes.map((s) => h("button", { type: "button", "data-scope": s.key, "aria-pressed": s.key === S.scope ? "true" : "false", text: s.label }));
    const scopeSeg = scopeButtons.length > 1 ? h("div", { class: "ph-seg ph-seg--scope", role: "group", "aria-label": "Whose proposals" }, scopeButtons) : null;

    const searchInput = h("input", { type: "search", class: "ph-search-input", autocomplete: "off", spellcheck: "false", "aria-label": "Search proposals", placeholder: "Search by client, Notion ID or title" });
    const searchClear = h("button", { type: "button", class: "ph-search-clear", "aria-label": "Clear search", hidden: true }, "×");

    const select = h("select", { class: "ph-select", "aria-label": "Sort by" });
    sortChoices().forEach((c) => select.append(h("option", { value: c.value, text: c.label })));

    const statusButtons = [["all", "All"], ["draft", "Drafts"], ["generated", "Generated"]].map(([key, label]) => {
      const n = h("span", { class: "ph-seg-n", hidden: true });
      // The space between the word and its count is for the accessible name ("All 14", not "All14"): in a flex button it takes no room.
      return { key, n, btn: h("button", { type: "button", "data-status": key, "aria-pressed": key === "all" ? "true" : "false" }, label, " ", n) };
    });
    const statusSeg = h("div", { class: "ph-seg ph-seg--status", role: "group", "aria-label": "Show" }, statusButtons.map((b) => b.btn));
    const countEl = h("p", { class: "ph-count", role: "status", "aria-live": "polite", "aria-atomic": "true" });

    const sortDefs = [["client", "Client"], ["value", "Net value"], ["updated", "Last edited"]];
    const sortButtons = sortDefs.map(([key, label]) => {
      const sr = h("span", { class: "ph-sr" });
      return { key, sr, btn: h("button", { type: "button", class: "ph-sort", "data-sort": key, "aria-pressed": "false" }, label, sr, h("span", { class: "ph-sort-ico", "aria-hidden": "true" })) };
    });
    const sortBtn = (key) => sortButtons.find((b) => b.key === key).btn;
    const col = (kids, cls) => h("span", { class: "ph-col" + (cls ? " " + cls : "") }, kids);
    const cols = h("div", { class: "ph-cols" },
      col(sortBtn("client")),
      admin ? col("Planner / AE", "ph-col--who") : null,
      col("Flight"),
      col(sortBtn("value"), "ph-col--right"),
      col("Status"),
      col(sortBtn("updated")),
      col(h("span", { class: "ph-sr", text: "Actions" })));

    const listEl = h("ul", { class: "ph-list", role: "list", "aria-label": "Proposals" });
    const moreBtn = h("button", { type: "button", class: "ph-btn ph-more-btn" });
    const moreLeft = h("span", { class: "ph-more-left" });
    const moreError = h("p", { class: "ph-more-error", role: "status", hidden: true }, "Couldn’t load more. Try again.");
    const moreEl = h("div", { class: "ph-more", hidden: true }, moreError, moreBtn, moreLeft);
    const stateEl = h("div", { class: "ph-state", hidden: true });
    const skelEl = h("div", { class: "ph-skel", "aria-hidden": "true", hidden: true });
    for (let i = 0; i < SKELETON_ROWS; i++) skelEl.append(h("div", { class: "ph-skel-row" }, h("span"), h("span"), h("span")));
    const bodyEl = h("div", { class: "ph-body", "aria-busy": "true" }, cols, listEl, moreEl, stateEl, skelEl);
    const liveEl = h("p", { class: "ph-sr ph-live", role: "status", "aria-live": "polite", "aria-atomic": "true" });
    const toastEl = h("div", { class: "ph-toast", "aria-hidden": "true", hidden: true });

    root.append(
      h("div", { class: "ph-tools" }, scopeSeg, h("div", { class: "ph-search" }, searchInput, searchClear), h("label", { class: "ph-select-wrap" }, h("span", { class: "ph-select-label", text: "Sort" }), select)),
      h("div", { class: "ph-status-row" }, statusSeg, countEl),
      h("div", { class: "ph-progress", "aria-hidden": "true" }),
      bodyEl, liveEl, toastEl);
    host.append(root);

    // ------------------------------------------------ small helpers
    let sayTimer = null, toastTimer = null;
    // What a screen reader hears. The region is emptied first and filled a moment later: the same sentence twice in a row is still a change.
    function say(text) {
      clearTimeout(sayTimer);
      liveEl.textContent = "";
      sayTimer = setTimeout(() => { sayTimer = null; if (!S.destroyed) liveEl.textContent = text; }, SAY_DELAY_MS);
    }
    // The same words, shown for a moment for people who are not using a screen reader ("Link copied.").
    function toast(text, withText) {
      clearTimeout(toastTimer);
      toastEl.textContent = "";
      toastEl.append(text);
      if (withText) {
        const box = h("input", { type: "text", class: "ph-toast-input", readonly: "", value: withText, "aria-label": "Text to copy" });
        box.addEventListener("focus", () => box.select());
        toastEl.append(box);
      }
      toastEl.hidden = false;
      toastTimer = setTimeout(() => { toastTimer = null; toastEl.hidden = true; }, withText ? TOAST_MS * 4 : TOAST_MS);
    }
    function announce(text) { say(text); toast(text); }

    const canDelete = (p) => {
      if (!p || p.status !== "draft") return false;                        // a generated proposal is a permanent record
      if (cfg.canDelete) { try { return !!cfg.canDelete(p); } catch (e) { return false; } }
      if (admin) return !!S.me && String(p.created_by_email || "").toLowerCase() === S.me;   // the server answers 403 for anyone else's draft
      return true;
    };
    const canDuplicate = (p) => {
      if (!cfg.onDuplicate) return false;
      try { return !!cfg.canDuplicate(p); } catch (e) { return false; }
    };
    const targetFor = (p) => {
      try { return cfg.linkTarget(p) === "_blank" ? "_blank" : "_self"; } catch (e) { return "_blank"; }
    };
    const rowOf = (id) => S.rows.find((r) => rowId(r) === id) || null;
    const liOf = (id) => Array.from(listEl.children).find((li) => li.dataset.id === id) || null;
    const primaryOf = (li) => (li ? li.querySelector(".ph-open, .ph-open-off") : null);
    // An element that is always there to hand the keyboard to: the search box (hidden in the first-use state), else the dialog / the list itself.
    const stableFocus = () => (visible(searchInput) && !(phone() && cfg.dialog) ? searchInput : root.closest(".ph-dialog") || root);

    // ------------------------------------------------ the rows
    function applyTarget(a, p) {
      if (targetFor(p) === "_blank") { a.setAttribute("target", "_blank"); a.setAttribute("rel", "noopener"); }
      else { a.removeAttribute("target"); a.removeAttribute("rel"); }
    }

    function cell(cls, label, ...kids) { return h("div", { class: "ph-cell " + cls }, h("span", { class: "ph-sr", text: label + ": " }), ...kids); }

    function buildRow(p) {
      const f = rowFacts(p);
      const label = f.name || "this proposal";
      const li = h("li", { class: "ph-row" + (f.draft ? " is-draft" : ""), "data-id": f.id, "data-status": f.draft ? "draft" : "generated" });

      const nameEl = h("h3", { class: "ph-client-name" + (f.name ? "" : " is-blank"), title: f.name || null });
      if (f.name) appendHighlighted(nameEl, f.name, S.words); else nameEl.append("No client name yet");
      const sub = subline(p);
      const subEl = sub.text ? h("p", { class: "ph-subline", title: sub.title }) : null;
      if (subEl) appendHighlighted(subEl, sub.text, S.words);
      const lin = lineageLabel(f.lineage);
      li.append(h("div", { class: "ph-cell ph-client" }, nameEl, subEl,
        lin ? h("p", { class: "ph-lineage" }, h("span", { class: "ph-chip ph-chip--copy", title: lin.title }, h("span", { "aria-hidden": "true", text: "⧉ " }), h("span", { class: "ph-chip-text", text: lin.text }))) : null));

      if (admin) {
        const ae = f.requestedBy ? h("span", { class: "ph-minor" }, "AE: ") : null;
        if (ae) appendHighlighted(ae, f.requestedBy, S.words);
        const device = deviceLabel(f.userAgent, f.ip);
        li.append(h("div", { class: "ph-cell ph-who" }, h("span", { class: "ph-sr", text: "Planner: " }),
          h("span", { class: "ph-main", title: f.createdBy || null, text: f.createdBy || "—" }), ae,
          device ? h("span", { class: "ph-device", title: f.userAgent || null, text: device }) : null));
      }

      const flight = formatFlight(p), billing = billingText(f.unit);
      li.append(cell("ph-flight", "Flight",
        flight ? h("span", { class: "ph-main", text: flight }) : h("span", { class: "ph-main ph-none", text: "No dates yet" }),
        billing ? h("span", { class: "ph-minor", text: billing }) : null));

      const money = f.draft ? null : formatMoney(f.net);
      li.append(cell("ph-value", "Net value",
        money ? h("span", { class: "ph-main ph-num", text: money })
          : h("span", { class: "ph-main ph-num ph-none", title: f.draft ? "A draft has no total until the proposal is generated" : null }, "—", h("span", { class: "ph-sr", text: " not available" })),
        f.lines !== null ? h("span", { class: "ph-minor", text: fmtInt(f.lines) + " " + plural(f.lines, "product") }) : null));

      let statusMinor = null;
      if (f.draft && f.step !== null) statusMinor = h("span", { class: "ph-minor", text: "Step " + f.step + " of " + WIZARD_STEPS });
      else if (!f.draft && admin && f.tabs.length) statusMinor = h("span", { class: "ph-minor", title: f.tabs.join(", "), text: f.tabs.join(" · ") });
      li.append(cell("ph-status", "Status", h("span", { class: "ph-badge " + (f.draft ? "ph-badge--draft" : "ph-badge--generated"), text: f.draft ? "Draft" : "Generated" }), statusMinor));

      const when = formatWhen(f.updatedAt, cfg.now());
      li.append(cell("ph-edited", "Last edited",
        when.valid ? h("time", { class: "ph-main", datetime: when.iso, title: when.title, "data-ts": f.updatedAt, text: when.main }) : h("span", { class: "ph-main ph-none", text: "—" }),
        when.minor ? h("span", { class: "ph-minor", text: when.minor }) : null));

      const acts = h("div", { class: "ph-cell ph-actions" });
      if (f.canReopen) {
        const a = h("a", { class: "ph-btn ph-btn--primary ph-open", "data-action": "open", href: "/?reopen=" + enc(f.id) }, f.draft ? "Continue" : "Open", h("span", { class: "ph-sr", text: " " + label }));
        applyTarget(a, p);
        acts.append(a);
      } else {
        acts.append(h("button", { type: "button", class: "ph-btn ph-btn--primary ph-open-off", "data-action": "open-off", "aria-disabled": "true", title: NO_ACCESS_REOPEN }, "Can’t reopen", h("span", { class: "ph-sr", text: ". " + NO_ACCESS_REOPEN })));
      }
      if (cfg.onDuplicate) {
        const on = canDuplicate(p);
        acts.append(h("button", { type: "button", class: "ph-btn ph-dup", "data-action": "duplicate", "aria-label": "Duplicate for new client: " + label, "aria-disabled": on ? null : "true", title: on ? null : NO_ACCESS_COPY },
          h("span", { "aria-hidden": "true", text: "⧉ " }), h("span", { class: "ph-dup-long", text: "Duplicate for new client" }), h("span", { class: "ph-dup-short", text: "Duplicate" }),
          on ? null : h("span", { class: "ph-sr", text: ". " + NO_ACCESS_COPY })));
      }
      if (menuItemsFor(p).length) {
        acts.append(h("button", { type: "button", class: "ph-btn ph-kebab", "data-action": "menu", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": "More actions for " + label }, "⋯"));
      }
      li.append(acts);
      return li;
    }

    function renderRows() {
      const hadFocus = listEl.contains(document.activeElement);
      listEl.textContent = "";
      const frag = document.createDocumentFragment();
      S.rows.forEach((p) => frag.append(buildRow(p)));
      listEl.append(frag);
      if (hadFocus && !listEl.contains(document.activeElement)) safeFocus(stableFocus());   // the focused row was rebuilt: do not drop the focus to <body>
    }

    // ------------------------------------------------ controls and states
    function hasDrafts() { return S.facets ? S.facets.draft > 0 : S.rows.some((r) => r.status === "draft"); }

    function updateCount() {
      let text = "";
      if (S.phase !== "loading" && S.phase !== "error") {
        const total = S.exhausted ? S.rows.length : S.total;
        text = countText({ shown: S.rows.length, total, status: S.status, query: S.query });
        if (S.sort === "value" && S.status === "all" && S.phase === "ready" && hasDrafts()) text += ". Drafts have no total yet, so they come last.";
      }
      if (countEl.textContent !== text) countEl.textContent = text;
    }

    function syncControls() {
      scopeButtons.forEach((b) => b.setAttribute("aria-pressed", b.dataset.scope === S.scope ? "true" : "false"));
      statusButtons.forEach(({ key, n, btn }) => {
        btn.setAttribute("aria-pressed", key === S.status ? "true" : "false");
        const v = S.facets ? S.facets[key] : null;
        n.hidden = v == null;
        n.textContent = v == null ? "" : fmtInt(v);
      });
      select.value = S.sort + ":" + S.dir;
      sortButtons.forEach(({ key, btn, sr }) => {
        const on = key === S.sort;
        btn.setAttribute("aria-pressed", on ? "true" : "false");
        if (on) btn.setAttribute("data-dir", S.dir); else btn.removeAttribute("data-dir");
        sr.textContent = on ? ", sorted " + SORT_WORDS[key][S.dir] : "";
      });
      searchClear.hidden = searchInput.value === "";
    }

    function updateMore() {
      const left = S.total - S.rows.length;
      const show = S.phase === "ready" && !S.exhausted && (left > 0 || S.moreError);
      moreEl.hidden = !show;
      if (!show) return;
      const busy = S.loadingMore || S.refreshing;
      moreBtn.textContent = "";
      if (S.loadingMore) moreBtn.append(h("span", { class: "ph-btn-wait", "aria-hidden": "true" }), "Loading…");
      else moreBtn.append("Show " + fmtInt(Math.max(1, Math.min(cfg.pageSize, left))) + " more");
      if (busy) moreBtn.setAttribute("aria-disabled", "true"); else moreBtn.removeAttribute("aria-disabled");   // not "disabled": a focused button that is disabled loses the focus
      moreBtn.setAttribute("aria-busy", S.loadingMore ? "true" : "false");
      moreLeft.textContent = "Showing " + fmtInt(S.rows.length) + " of " + fmtInt(Math.max(S.total, S.rows.length));
      moreError.hidden = !S.moreError;
    }

    function showState(kind, o) {
      const hadFocus = stateEl.contains(document.activeElement);
      stateEl.hidden = false;
      stateEl.textContent = "";
      stateEl.className = "ph-state ph-state--" + kind + (o.quiet ? " ph-state--quiet" : "");
      stateEl.removeAttribute("role");
      if (o.role) stateEl.setAttribute("role", o.role);
      const actions = h("div", { class: "ph-state-actions" }, o.actions);
      stateEl.append(h("span", { class: "ph-state-title", text: o.title }), h("p", { class: "ph-state-text", text: o.text }), actions);
      if (hadFocus) safeFocus(stateEl.querySelector("button") || stableFocus());   // a focused button that is rebuilt would drop the focus to <body>
    }

    function statusNoun() { return S.status === "draft" ? "drafts" : S.status === "generated" ? "generated proposals" : "proposals"; }

    function renderState() {
      const btn = (cls, text, primary) => h("button", { type: "button", class: "ph-btn " + cls + (primary ? " ph-btn--primary" : ""), text });
      if (S.phase === "error") {
        const o = S.error || {};
        showState("error", {
          title: admin ? "Couldn’t load proposals" : "Couldn’t load your proposals", text: o.text || describeError(0, false),
          role: o.status === 401 ? "status" : "alert", quiet: o.status === 401, actions: btn("ph-retry", "Try again", true),
        });
      } else if (S.phase === "empty") {
        const scoped = cfg.scopes.length > 1 && S.scope === cfg.scopes[0].key;
        showState("empty", {
          title: "No proposals yet",
          text: admin ? (scoped ? "You haven’t saved a proposal yet. Choose All proposals to see everyone’s." : "No proposal has been saved on this server yet.")
            : "Proposals you save or generate show up here, newest first. Drafts save by themselves while you work.",
          actions: cfg.emptyAction ? btn("ph-empty-action", String(cfg.emptyAction.label), true) : null,
        });
      } else if (S.phase === "no-results") {
        const q = echoQuery(S.query), noun = statusNoun();
        if (q) {
          showState("empty", {
            title: S.status === "all" ? "Nothing matches “" + q + "”" : "No " + noun + " match “" + q + "”",
            text: S.status === "all" ? "Check the spelling, or try a client name or a Notion ID like EVC-48213." : "Try other words, or look at all proposals.",
            actions: [btn("ph-clear-search", "Clear search"), S.status === "all" ? null : btn("ph-show-all", "Show all")],
          });
        } else {
          showState("empty", {
            title: "No " + noun + " match",
            text: S.status === "draft" ? "Nothing is saved as a draft right now." : "No proposal has been generated yet.",
            actions: btn("ph-show-all", "Show all"),
          });
        }
      } else stateEl.hidden = true;
    }

    function render() {
      const loading = S.phase === "loading", ready = S.phase === "ready";
      const hadFocus = root.contains(document.activeElement);
      root.setAttribute("data-state", S.phase);
      root.classList.toggle("is-loading", S.refreshing);
      bodyEl.setAttribute("aria-busy", loading || S.refreshing ? "true" : "false");
      skelEl.hidden = !loading;
      cols.hidden = !ready;
      listEl.hidden = !ready;
      renderState();
      syncControls();
      updateCount();
      updateMore();
      // What had the keyboard may have just been hidden ("Try again" when the list is back, the search box in the first-use state): hand it to
      // a place that is always there, so it never falls to <body> behind the dialog.
      if (hadFocus) {
        const a = document.activeElement;
        if (!a || a === document.body || !root.contains(a) || !visible(a)) safeFocus(stableFocus());
      }
    }

    // The phase a set of rows means: rows, nothing at all yet (first use), or nothing that matches the search / status.
    function settlePhase() {
      if (S.rows.length) S.phase = "ready";
      else if (!S.query && S.status === "all") S.phase = "empty";
      else S.phase = "no-results";
    }

    // ------------------------------------------------ loading
    function parseResponse(data) {
      const d = data && typeof data === "object" ? data : {};
      const rows = Array.isArray(d.proposals) ? d.proposals : [];                // (a row without an id is dropped when the rows are merged)
      let facets = null;
      if (d.facets && typeof d.facets === "object") {
        const all = toInt(d.facets.all), draft = toInt(d.facets.draft), generated = toInt(d.facets.generated);
        if (all !== null && draft !== null && generated !== null && all >= 0 && draft >= 0 && generated >= 0) facets = { all, draft, generated };
      }
      return { rows, total: toInt(d.total_count), facets, applied: d.applied && typeof d.applied === "object" ? d.applied : null };
    }

    function extraParams() {
      try { return cfg.params(S.scope) || {}; } catch (e) { return {}; }
    }

    function load(o) {
      o = o || {};
      const append = !!o.append;
      if (S.destroyed) return;
      if (S.abort) { try { S.abort.abort(); } catch (e) { /* nothing to cancel */ } }
      const token = ++S.token;
      S.abort = typeof AbortController === "function" ? new AbortController() : null;
      if (append) {
        S.loadingMore = true;
        S.moreError = false;
      } else {
        S.moreError = false; S.loadingMore = false; S.exhausted = false;
        cancelConfirm(false);
        closeMenu(false);
        if (S.rows.length && o.keepRows !== false) S.refreshing = true;
        else { S.rows = []; S.refreshing = false; S.phase = "loading"; listEl.textContent = ""; }
      }
      render();
      const query = buildQuery({
        page: append ? nextPage(S.rows.length, cfg.pageSize) : 1, pageSize: cfg.pageSize, query: S.query, status: S.status, sort: S.sort, dir: S.dir,
        extra: extraParams(),
      });
      const url = cfg.endpoint + (cfg.endpoint.indexOf("?") >= 0 ? "&" : "?") + query;
      let p;
      try { p = Promise.resolve(cfg.fetchJson(url, { signal: S.abort ? S.abort.signal : undefined })); } catch (err) { p = Promise.reject(err); }
      p.then((data) => { if (!S.destroyed && token === S.token) accept(parseResponse(data), append); },
        (err) => { if (!S.destroyed && token === S.token && !isAbort(err)) fail(err, append); });
    }

    function accept(res, append) {
      S.abort = null;
      S.refreshing = false;
      S.loadingMore = false;
      if (res.facets) S.facets = res.facets; else if (!append) S.facets = null;
      if (res.applied) {                                                          // the server's own reading of status / sort / dir wins
        if (STATUSES.indexOf(res.applied.status) >= 0) S.status = res.applied.status;
        if (SORTS.indexOf(res.applied.sort) >= 0) S.sort = res.applied.sort;
        if (res.applied.dir === "asc" || res.applied.dir === "desc") S.dir = res.applied.dir;
      }
      if (append) {
        const before = S.rows.length;
        S.rows = mergeRows(S.rows, res.rows);
        const added = S.rows.length - before;
        // An empty page (the last rows were deleted elsewhere, so total_count reads 0) or one with nothing new: that is the end of the list.
        // The previous total stands; it must never turn the list into the "empty" state.
        if (!added) S.exhausted = true;
        else if (res.total !== null) S.total = res.total;
        S.phase = "ready";
        const hadFocus = moreEl.contains(document.activeElement);
        render();
        if (added) {                                                              // only the new rows are built: the ones on screen keep their nodes and their focus
          const frag = document.createDocumentFragment();
          S.rows.slice(before).forEach((r) => frag.append(buildRow(r)));
          const firstNew = frag.firstElementChild;
          listEl.append(frag);
          safeFocus(primaryOf(firstNew));
          say(fmtInt(added) + " more " + plural(added, "proposal") + " loaded. Showing " + fmtInt(S.rows.length) + " of " + fmtInt(Math.max(S.total, S.rows.length)) + ".");
        } else if (hadFocus) safeFocus(primaryOf(listEl.lastElementChild) || stableFocus());     // the button that had the focus is gone
        return;
      }
      S.rows = mergeRows([], res.rows);
      S.total = Math.max(res.total === null ? 0 : res.total, S.rows.length);
      settlePhase();
      render();
      if (S.phase === "ready") renderRows(); else listEl.textContent = "";
      bodyEl.scrollTop = 0;
      const wanted = S.highlight;
      S.highlight = "";
      if (wanted) flashRow(wanted);
      else if (S.focusFirst && S.phase === "ready") safeFocus(primaryOf(listEl.firstElementChild));   // the delete used up the page: the keyboard goes to the first row now shown
      S.focusFirst = false;
    }

    function fail(err, append) {
      S.abort = null;
      S.refreshing = false;
      S.loadingMore = false;
      S.highlight = "";
      S.focusFirst = false;
      if (typeof console !== "undefined") console.error("ProposalHistory: the list request failed" + (err && typeof err.status === "number" ? " (HTTP " + err.status + ")" : ""));
      if (append) {                                                               // the rows stay; only the button says so
        S.moreError = true;
        render();
        return;
      }
      const status = err && typeof err.status === "number" ? err.status : 0;
      S.error = { status, text: describeError(status, isOffline(err)) };
      S.rows = [];
      S.phase = "error";
      listEl.textContent = "";
      render();
    }

    function flashRow(id) {
      const li = liOf(id);
      if (!li) return;
      li.classList.add("is-new");
      setTimeout(() => li.classList.remove("is-new"), FLASH_MS);
      safeFocus(primaryOf(li));
    }

    // ------------------------------------------------ search, status, sort, scope
    // The typed words become the list's search (S.query / S.words) without loading; true when that changed it.
    function commitQuery() {
      const q = normQuery(searchInput.value);
      if (q === S.query) return false;
      S.query = q;
      S.words = searchWords(q);
      return true;
    }
    function applyQuery() {
      if (commitQuery()) load(); else syncControls();
    }
    let searchTimer = null;
    // Enter: search now, not after the pause.
    function searchNow() {
      clearTimeout(searchTimer);
      searchTimer = null;
      applyQuery();
    }
    // A filter, a sort or a scope is chosen while a search is still waiting out its pause: the words join THAT request (one request, not two).
    function flushSearch() {
      if (searchTimer === null) return;
      clearTimeout(searchTimer);
      searchTimer = null;
      commitQuery();
    }
    function clearSearch() {
      clearTimeout(searchTimer);
      searchTimer = null;
      searchInput.value = "";
      applyQuery();
      syncControls();
      safeFocus(searchInput);
    }
    function setStatus(key) {
      if (STATUSES.indexOf(key) < 0 || key === S.status) return;
      flushSearch();
      S.status = key;
      load();
    }
    function setSort(sort, dir) {
      if (SORTS.indexOf(sort) < 0 || (sort === S.sort && dir === S.dir)) return;
      flushSearch();
      S.sort = sort; S.dir = dir;
      load();
      say("Sorted by " + sortPhrase(sort, dir) + ".");
    }
    function setScope(key) {
      const s = cfg.scopes.find((x) => x.key === String(key));
      if (!s || s.key === S.scope) return;
      flushSearch();
      S.scope = s.key;
      load();
    }
    function resetFilters() {
      clearTimeout(searchTimer);
      searchTimer = null;
      searchInput.value = "";
      S.query = ""; S.words = []; S.status = "all"; S.sort = "updated"; S.dir = "desc";
    }

    // ------------------------------------------------ the kebab menu (one element for the whole list, in <body>)
    let menuEl = null;
    let menuFor = null;                                                          // { kebab, id, size: the window's size when it opened }

    function menuItemsFor(p) {
      const items = [];
      if (cfg.copyLink) items.push({ action: "copy-link", label: "Copy link" });
      if (rowFacts(p).title) items.push({ action: "copy-name", label: "Copy proposal name" });
      if (canDelete(p)) { if (items.length) items.push({ sep: true }); items.push({ action: "delete", label: "Delete draft…", danger: true }); }
      return items;
    }

    function menuItemEls() { return menuEl ? Array.from(menuEl.querySelectorAll(".ph-menu-item")) : []; }

    function onMenuKey(e) {
      const items = menuItemEls();
      const at = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); safeFocus(items[(at + 1) % items.length]); }
      else if (e.key === "ArrowUp") { e.preventDefault(); safeFocus(items[(at - 1 + items.length) % items.length]); }
      else if (e.key === "Home") { e.preventDefault(); safeFocus(items[0]); }
      else if (e.key === "End") { e.preventDefault(); safeFocus(items[items.length - 1]); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(true); }          // inside a dialog the shell gets it first; this is for the mounted list
      else if (e.key === "Tab") closeMenu(true);                                // focus is back on the kebab, so the Tab goes on from there
    }
    const onOutsidePress = (e) => {
      if (!menuEl || menuEl.hidden) return;
      if (menuEl.contains(e.target) || (menuFor && menuFor.kebab.contains(e.target))) return;
      closeMenu(false);
    };
    // A scroll INSIDE the menu (it is not scrollable, but be safe) keeps it open; any other scroll, and a resize that changed the window's size,
    // closes it (it is placed from the kebab's position, which has just moved) and puts the keyboard back on the kebab. A resize event with the
    // size it had when the menu opened moved nothing and is ignored. The target of a resize is the window, which is not a Node: contains() would
    // throw on it.
    const onScrollOrResize = (e) => {
      if (!menuEl || menuEl.hidden) return;
      const t = e && e.target;
      if (t && t.nodeType && menuEl.contains(t)) return;
      if (e && e.type === "resize" && menuFor && window.innerWidth === menuFor.size[0] && window.innerHeight === menuFor.size[1]) return;
      closeMenu(true);
    };

    function closeMenu(returnFocus) {
      if (!menuEl || menuEl.hidden) return false;
      const kebab = menuFor && menuFor.kebab;
      menuEl.hidden = true;
      menuFor = null;
      document.removeEventListener("mousedown", onOutsidePress, true);
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
      if (kebab) {
        kebab.setAttribute("aria-expanded", "false");
        if (returnFocus && kebab.isConnected) safeFocus(kebab);
      }
      return true;
    }

    function openMenu(kebab, li, p) {
      closeMenu(false);
      const items = menuItemsFor(p);
      if (!items.length) return;
      if (!menuEl) {
        menuEl = h("div", { class: "ph-menu", role: "menu", id: pre + "-menu", hidden: true });
        menuEl.addEventListener("keydown", onMenuKey);
        menuEl.addEventListener("click", (e) => {
          const item = e.target.closest(".ph-menu-item");
          if (item && menuFor) runMenuAction(item.dataset.action, menuFor.kebab);
        });
        document.body.append(menuEl);
      }
      menuEl.textContent = "";
      items.forEach((it) => menuEl.append(it.sep ? h("hr", { class: "ph-menu-sep" }) : h("button", { type: "button", role: "menuitem", class: "ph-menu-item" + (it.danger ? " ph-menu-item--danger" : ""), "data-action": it.action, text: it.label })));
      menuFor = { kebab, id: li.dataset.id, size: [window.innerWidth, window.innerHeight] };
      kebab.setAttribute("aria-expanded", "true");
      kebab.setAttribute("aria-controls", menuEl.id);
      menuEl.style.visibility = "hidden";
      menuEl.hidden = false;
      // Placed from the kebab's own rectangle, right edges aligned, flipped above it near the bottom of the window, and always inside it.
      const k = kebab.getBoundingClientRect(), m = menuEl.getBoundingClientRect(), vw = window.innerWidth, vh = window.innerHeight, gap = 4, edge = 8;
      let left = Math.min(Math.max(edge, k.right - m.width), Math.max(edge, vw - m.width - edge));
      let top = k.bottom + gap;
      if (top + m.height > vh - edge) top = Math.max(edge, k.top - gap - m.height);
      menuEl.style.left = Math.round(left) + "px";
      menuEl.style.top = Math.round(top) + "px";
      menuEl.style.visibility = "";
      document.addEventListener("mousedown", onOutsidePress, true);
      window.addEventListener("scroll", onScrollOrResize, true);
      window.addEventListener("resize", onScrollOrResize);
      safeFocus(menuEl.querySelector(".ph-menu-item"));
    }

    function runMenuAction(action, kebab) {
      const id = menuFor ? menuFor.id : "";
      const p = rowOf(id);
      closeMenu(action !== "delete");
      if (!p) return;
      if (action === "copy-link") copy(location.origin + "/?reopen=" + enc(id), "Link copied.");
      else if (action === "copy-name") copy(rowFacts(p).title, "Name copied.");
      else if (action === "delete") askDelete(liOf(id), p, kebab);
    }

    function fallbackCopy(text) {
      const area = h("textarea", { readonly: "", "aria-hidden": "true", tabindex: "-1", style: "position:fixed;left:-9999px;top:0;opacity:0" });
      area.value = text;
      document.body.append(area);
      area.select();
      let ok = false;
      try { ok = !!document.execCommand("copy"); } catch (e) { ok = false; }
      area.remove();
      return ok;
    }
    function copy(text, done) {
      const finish = (ok) => {
        if (S.destroyed) return;
        if (ok) announce(done);
        else { say("Couldn’t copy. Select the text instead. " + text); toast("Couldn’t copy. Select the text instead:", text); }
      };
      if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        let p;
        try { p = Promise.resolve(navigator.clipboard.writeText(text)); } catch (e) { p = Promise.reject(e); }
        p.then(() => finish(true), () => finish(fallbackCopy(text)));
      } else finish(fallbackCopy(text));
    }

    // ------------------------------------------------ delete, confirmed inline
    let confirming = null;                                                      // { id, li, kebab }

    function askDelete(li, p, kebab) {
      if (!li) return;
      cancelConfirm(false);
      const f = rowFacts(p);
      const keep = h("button", { type: "button", class: "ph-btn ph-confirm-keep", text: "Keep draft" });
      const del = h("button", { type: "button", class: "ph-btn ph-btn--danger ph-confirm-delete", text: "Delete draft" });
      const text = h("p", { class: "ph-confirm-text", id: pre + "-ct" });
      if (f.name) text.append("Delete the draft for ", h("strong", { text: f.name }), "? This can’t be undone.");
      else text.append("Delete this draft? This can’t be undone.");
      const strip = h("div", { class: "ph-confirm", role: "group", "aria-labelledby": pre + "-ct" }, text, h("div", { class: "ph-confirm-actions" }, keep, del));
      li.classList.add("is-confirming");
      li.append(strip);
      confirming = { id: f.id, li, kebab: kebab || li.querySelector(".ph-kebab"), strip, keep, del, text, failed: false };
      safeFocus(keep);                                                           // the safe choice is the one the keyboard starts on
    }

    function cancelConfirm(returnFocus) {
      if (!confirming) return false;
      const c = confirming;
      confirming = null;
      c.strip.remove();
      c.li.classList.remove("is-confirming", "is-busy");
      if (returnFocus && c.kebab && c.kebab.isConnected) safeFocus(c.kebab);
      return true;
    }

    function deleteFailed(c, status) {
      c.li.classList.remove("is-busy");
      c.keep.removeAttribute("aria-disabled");
      c.del.removeAttribute("aria-disabled");
      c.del.textContent = "Try again";
      c.failed = true;
      c.text.textContent = status === 403 ? "You can only delete your own drafts." : status === 400 ? "Only drafts can be deleted." : "Couldn’t delete this draft. Try again.";
      c.text.setAttribute("role", "alert");
      safeFocus(c.del);
    }

    function doDelete() {
      const c = confirming;
      if (!c || c.li.classList.contains("is-busy")) return;
      c.li.classList.add("is-busy");
      c.keep.setAttribute("aria-disabled", "true");                               // not "disabled": the focused button would lose the focus
      c.del.setAttribute("aria-disabled", "true");
      let p;
      try { p = Promise.resolve(window.fetch("/api/proposal/" + enc(c.id), { method: "DELETE", headers: { Accept: "application/json" } })); } catch (e) { p = Promise.reject(e); }
      p.then((res) => {
        if (S.destroyed || confirming !== c) return;
        if (res && (res.ok || res.status === 404)) removeRow(c, res.status === 404 ? "That draft was already deleted." : "Draft deleted.");
        else deleteFailed(c, res ? res.status : 0);
      }, () => { if (!S.destroyed && confirming === c) deleteFailed(c, 0); });
    }

    // The row is gone on the server: take it out here too, keep the counts honest, and put the keyboard somewhere sensible.
    function removeRow(c, message) {
      confirming = null;
      const li = c.li, next = li.nextElementSibling || li.previousElementSibling;
      const p = rowOf(c.id);
      li.remove();
      S.rows = S.rows.filter((r) => rowId(r) !== c.id);
      S.total = Math.max(0, S.total - 1);
      if (S.facets) {
        S.facets.all = Math.max(0, S.facets.all - 1);
        if (p && p.status === "draft") S.facets.draft = Math.max(0, S.facets.draft - 1); else S.facets.generated = Math.max(0, S.facets.generated - 1);
      }
      announce(message);
      if (!S.rows.length && S.total > 0) { S.focusFirst = true; load({ keepRows: false }); return; }   // this page is used up but the list is not: fetch the first page again
      settlePhase();
      render();
      if (S.phase === "ready") safeFocus(primaryOf(next) || stableFocus());
      else safeFocus(stateEl.querySelector("button") || stableFocus());
    }

    // ------------------------------------------------ events
    listEl.addEventListener("click", (e) => {
      const t = e.target.closest("[data-action]");
      const li = e.target.closest(".ph-row");
      if (!t || !li) return;
      const p = rowOf(li.dataset.id);
      if (!p) return;
      const action = t.getAttribute("data-action");
      if (action === "open") applyTarget(t, p);                                  // decided again at the click: the wizard may have changed since the row was drawn
      else if (action === "duplicate") {
        if (t.getAttribute("aria-disabled") === "true" || !cfg.onDuplicate) return;
        try { cfg.onDuplicate({ proposal: p, trigger: t, controller }); } catch (err) { if (typeof console !== "undefined") console.error("ProposalHistory: onDuplicate failed"); }
      } else if (action === "menu") {
        if (menuFor && menuFor.kebab === t) closeMenu(true); else openMenu(t, li, p);
      }
    });

    listEl.addEventListener("keydown", (e) => {
      const t = e.target;
      if (t.classList && t.classList.contains("ph-kebab") && e.key === "ArrowDown") {
        e.preventDefault();
        const li = t.closest(".ph-row"), p = li && rowOf(li.dataset.id);
        if (p) openMenu(t, li, p);
        return;
      }
      if (!t.closest || !t.closest(".ph-open, .ph-open-off")) return;
      const rows = Array.from(listEl.children);
      const at = rows.indexOf(t.closest(".ph-row"));
      let to = -1;
      if (e.key === "ArrowDown") to = at + 1;
      else if (e.key === "ArrowUp") to = at - 1;
      else if (e.key === "Home") to = 0;
      else if (e.key === "End") to = rows.length - 1;
      if (to < 0 && e.key === "ArrowUp" && at === 0) { e.preventDefault(); safeFocus(searchInput); return; }
      if (to < 0 || to >= rows.length) return;
      e.preventDefault();
      safeFocus(primaryOf(rows[to]));
    });

    // The confirm strip lives in the row but is its own little form: its two buttons are handled here, not by [data-action].
    listEl.addEventListener("click", (e) => {
      if (!confirming) return;
      if (e.target.closest(".ph-confirm-keep")) { if (!confirming.li.classList.contains("is-busy")) cancelConfirm(true); }
      else if (e.target.closest(".ph-confirm-delete")) doDelete();
    });

    searchInput.addEventListener("input", () => {
      searchClear.hidden = searchInput.value === "";
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { searchTimer = null; applyQuery(); }, cfg.searchDelay);
    });
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); searchNow(); }
      else if (e.key === "ArrowDown") {
        const first = primaryOf(listEl.firstElementChild);
        if (first && !listEl.hidden) { e.preventDefault(); safeFocus(first); }
      }
    });
    searchClear.addEventListener("click", () => { clearSearch(); });
    statusButtons.forEach(({ key, btn }) => btn.addEventListener("click", () => setStatus(key)));
    scopeButtons.forEach((btn) => btn.addEventListener("click", () => setScope(btn.dataset.scope)));
    sortButtons.forEach(({ key, btn }) => btn.addEventListener("click", () => {
      if (S.sort === key) setSort(key, S.dir === "asc" ? "desc" : "asc");
      else setSort(key, NATURAL_DIR[key]);
    }));
    select.addEventListener("change", () => {
      const c = sortChoices().find((x) => x.value === select.value);
      if (c) setSort(c.sort, c.dir);
    });
    moreBtn.addEventListener("click", () => { if (moreBtn.getAttribute("aria-disabled") !== "true") load({ append: true }); });
    stateEl.addEventListener("click", (e) => {
      if (e.target.closest(".ph-retry")) load({ keepRows: false });
      else if (e.target.closest(".ph-clear-search")) clearSearch();
      else if (e.target.closest(".ph-show-all")) { setStatus("all"); safeFocus(stableFocus()); } else if (e.target.closest(".ph-empty-action")) {
        const run = cfg.emptyAction && cfg.emptyAction.run;
        if (closeDialog) closeDialog();                                          // the dialog first, so the action's own focus is the one that stays
        if (typeof run === "function") { try { run(); } catch (err) { if (typeof console !== "undefined") console.error("ProposalHistory: emptyAction failed"); } }
      }
    });

    // Escape, one level per press, for the dialog's shell (it asks first) and for a mounted list (this listener).
    function handleEscape(e) {
      if (closeMenu(true)) return true;
      if (confirming && confirming.li.classList.contains("is-busy")) return true;       // the request is running: Escape does not pretend to cancel it
      if (cancelConfirm(true)) return true;
      if (e && e.target === searchInput && searchInput.value !== "") { clearSearch(); say("Search cleared."); return true; }
      return false;
    }
    root.addEventListener("keydown", (e) => { if (e.key === "Escape" && !cfg.dialog && handleEscape(e)) { e.preventDefault(); e.stopPropagation(); } });

    // ------------------------------------------------ relative times stay fresh while the list is open
    const tickTimer = setInterval(() => {
      const now = cfg.now();
      Array.from(listEl.querySelectorAll("time[data-ts]")).forEach((el) => {
        const w = formatWhen(el.getAttribute("data-ts"), now);
        if (w.valid && el.textContent !== w.main) el.textContent = w.main;
      });
    }, TICK_MS);

    // ------------------------------------------------ the controller
    function teardown() {
      if (S.destroyed) return;
      S.destroyed = true;
      clearInterval(tickTimer);
      clearTimeout(searchTimer); clearTimeout(sayTimer); clearTimeout(toastTimer);
      if (S.abort) { try { S.abort.abort(); } catch (e) { /* nothing to cancel */ } }
      closeMenu(false);
      if (menuEl) { menuEl.remove(); menuEl = null; }
      if (!cfg.dialog) root.remove();
    }

    const controller = {
      reload(o) {
        o = o || {};
        if (S.destroyed) return;
        if (o.highlightId) { resetFilters(); S.highlight = String(o.highlightId); }
        load({ keepRows: o.keepRows !== false });
      },
      setScope,
      focusSearch() { safeFocus(visible(searchInput) ? searchInput : stableFocus()); },
      getState() {
        return {
          phase: S.phase, query: S.query, status: S.status, sort: S.sort, dir: S.dir, scope: S.scope,
          rows: S.rows.map((r) => Object.assign({}, r)), total: S.total, facets: S.facets ? Object.assign({}, S.facets) : null,
        };
      },
      destroy() { if (cfg.dialog && closeDialog) closeDialog(); else teardown(); },
    };

    // The admin list asks who is signed in, once, to know whose drafts it may offer to delete. No answer means no Delete item.
    if (admin && !cfg.canDelete && !S.me) {
      let p;
      try { p = Promise.resolve(cfg.fetchJson("/api/me", {})); } catch (e) { p = Promise.reject(e); }
      p.then((me) => { if (!S.destroyed && me && typeof me.email === "string") S.me = me.email.trim().toLowerCase(); }, () => { /* no Delete items, nothing else lost */ });
    }

    load();
    return { controller, root, handleEscape, owns: (el) => !!menuEl && menuEl.contains(el), teardown, searchInput };
  }

  // ---------------------------------------------------------------- the public API

  let currentDialog = null;                 // the one open history dialog: { close, controller }
  const mounts = [];                        // { host, list } of the mounted lists

  function openDialog(opts) {
    if (typeof document === "undefined") return { close() {}, controller: null };
    opts = opts || {};
    if (currentDialog) currentDialog.close();                                 // one history dialog at a time
    const cfg = normalizeCfg(opts, true);
    let list = null, sh = null;
    sh = shell({
      title: opts.title == null ? "My Proposal History" : opts.title,
      subtitle: opts.subtitle === undefined ? "Open or continue a proposal" + (cfg.onDuplicate ? ", or copy one for a new client." : ".") : opts.subtitle,
      returnFocus: opts.returnFocus,
      initialFocus: () => (list ? list.searchInput : null),
      onEscape: (e) => (list ? list.handleEscape(e) : false),
      owns: (el) => (list ? list.owns(el) : false),
      onClose: () => {
        if (list) list.teardown();
        if (currentDialog === handle) currentDialog = null;
        if (typeof opts.onClose === "function") { try { opts.onClose(); } catch (e) { /* the host's own callback failed: nothing to undo here */ } }
      },
    });
    list = createList(sh.body, cfg, () => sh.close());
    const handle = { close() { sh.close(); }, controller: list.controller };
    currentDialog = handle;
    sh.focusInitial();                                                        // the search box (the dialog itself on a phone)
    return handle;
  }

  const inertController = () => ({ reload() {}, setScope() {}, focusSearch() {}, getState() { return { phase: "loading", query: "", status: "all", sort: "updated", dir: "desc", scope: "", rows: [], total: 0, facets: null }; }, destroy() {} });

  function mount(host, opts) {
    if (typeof document === "undefined" || !host) return inertController();
    for (let i = mounts.length - 1; i >= 0; i--) {                            // one list per host: mounting again replaces the first
      if (mounts[i].host === host || !mounts[i].host.isConnected) { mounts[i].list.teardown(); mounts.splice(i, 1); }
    }
    const list = createList(host, normalizeCfg(opts, false), null);
    const entry = { host, list };
    mounts.push(entry);
    const ctl = list.controller, destroy = ctl.destroy;
    ctl.destroy = () => { destroy(); const at = mounts.indexOf(entry); if (at >= 0) mounts.splice(at, 1); };
    return ctl;
  }

  const api = {
    openDialog,
    mount,
    shell,
    close() { if (currentDialog) currentDialog.close(); },
    isOpen() { return !!currentDialog; },
    // Pure helpers, exposed so tests/test_proposal_history_frontend.py can run them without a browser.
    _pure: {
      fold, searchWords, highlightRanges, parseISODate, formatFlight, billingText, parseTime, formatWhen, formatMoney, fmtInt, plural, echoQuery, countText,
      describeError, normQuery, buildQuery, nextPage, mergeRows, sortChoices, sortPhrase, lineageOf, lineageLabel, deviceLabel, subline, rowFacts,
    },
  };
  if (typeof window !== "undefined") window.ProposalHistory = api;
})();
