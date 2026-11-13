/* ============================================================================
 * DuplicateProposal - the "Duplicate for a new client" quick form. A planner picks a proposal in the history list, types the basics of the
 * NEW opportunity (client, flight dates, Notion ID, agency, ...) and gets a fresh draft that keeps the products, prices and options (what carries
 * over and what resets is app/services/proposal_duplicate.py; docs/duplicate_proposal.md has the one-page version).
 *
 *   const handle = DuplicateProposal.open({
 *     proposal: { proposal_id, client_name?, notion_id? },     // a row of the history list: only proposal_id is needed
 *     trigger: buttonThatOpenedIt,                            // focus goes back here when the dialog closes
 *     onDone: (result) => { ... },                            // called ONCE, when the copy exists (result = the POST's answer)
 *     prepare: async () => "" | "why the copy cannot start yet",   // optional: runs before anything is fetched (the planner's page saves the open draft here)
 *   });                                                       // handle.close()
 *
 * The dialog fetches GET /api/proposal/{id}/duplicate-defaults first (it also says who may copy: a 403 / 404 / 410 is shown as a plain
 * message, with no form), then shows the form, then POSTs /api/proposal/{id}/duplicate. It builds on ProposalHistory.shell() (overlay, focus
 * trap, Escape, scroll lock, focus return) and the .ph-form primitives of proposal-history.css, loads on index.html and admin.html, and
 * needs no app.js / admin.js global: only window.fetch and plain DOM. Everything the person or the server wrote is put on the page as text
 * (createElement + textContent, no innerHTML), so a client called <img onerror=...> is only ever text.
 *
 * Retrying is safe: one request_id is minted when the dialog opens and kept for every retry of THAT dialog, and the server derives the
 * copy's id from it, so a double click or a retry after a lost answer finds the first copy instead of making a second one. A corrected
 * form is a new copy. A request in flight blocks Escape, the X and the backdrop; a form with typed text is not closed by a stray backdrop click.
 *
 * Nothing at load time needs a document, so a JavaScript engine without a DOM can load this file (tests/test_duplicate_frontend.py does).
 * DuplicateProposal._pure holds the helpers: validate, toRequestBody, mapServerErrors, normalizeNotionId, percentToFraction, flightHint, ...
 *
 * Hooks for tests (stable): #duplicate-dialog, #dup-client-name, #dup-start, #dup-end, #dup-notion, #dup-agency, #dup-fee, #dup-email, #dup-website,
 * #dup-requested-by, #dup-market, #dup-request-type, #dup-campaign, #dup-keep-avails, #dup-flight-hint, #dup-same-agency, #dup-alert, #dup-submit,
 * #dup-open-link, #dup-close, .dup-done.
 * ========================================================================== */
(function () {
  "use strict";

  const MAX_FLIGHT_DAYS = 3660;            // ten years: the same ceiling as the server (and Constanza's flight limit)
  const YEAR_RANGE = [2000, 2100];         // a typo like 2207 for 2027 is refused instead of making a flight nobody meant
  const DAY_MS = 86400000;
  const MAX_LENGTHS = {                    // [characters, how the server's message names the field]
    client_name: [200, "The client name"], agency_name: [200, "The agency name"], client_website: [300, "The website"], requested_by: [200, "Requested by"],
    salesperson_market: [200, "The market"], request_type: [200, "The request type"], campaign_name: [200, "The campaign name"], salesperson_email: [200, "The email"],
    notion_id: [200, "The Notion ID"],
  };
  // The inputs the person sees, in the order they appear: validation errors name the first one in this order, and a 422's loc[-1] must be one of them.
  const VISIBLE = ["client_name", "start_date", "end_date", "notion_id", "salesperson_email", "agency_name", "agency_fee"];
  const MORE = ["client_website", "requested_by", "salesperson_market", "request_type", "campaign_name"];
  const FORM_FIELDS = VISIBLE.concat(MORE);
  const IDS = {
    client_name: "dup-client-name", start_date: "dup-start", end_date: "dup-end", notion_id: "dup-notion", salesperson_email: "dup-email", agency_name: "dup-agency",
    agency_fee: "dup-fee", client_website: "dup-website", requested_by: "dup-requested-by", salesperson_market: "dup-market", request_type: "dup-request-type",
    campaign_name: "dup-campaign",
  };
  const UNWANTED = /[\p{Cc}\p{Cf}\p{Cs}]/gu;                      // controls, zero-width / bidi marks, lone surrogates: the server turns them into a space too
  const GENERIC_FAIL = "We couldn't create the copy. Please try again.";

  // ---------------------------------------------------------------- pure helpers (no DOM; tests/test_duplicate_frontend.py runs them)

  // User-typed text the way the server stores it: unwanted characters become a space, runs of space collapse, the ends are trimmed.
  function plainText(value) {
    if (value == null) return "";
    return String(value).replace(UNWANTED, " ").split(/\s+/).filter(Boolean).join(" ");
  }

  function textLength(text) { return Array.from(text).length; }

  // "EVC-48213", "evc 48213" or "48213" -> "EVC-48213"; blank -> ""; anything else -> null (not an ID). The server's rule, and Step 02's field.
  function normalizeNotionId(raw) {
    const text = plainText(raw);
    if (!text) return "";
    const found = /^(?:evc[-\s]?)?(\d{4,5})$/i.exec(text);
    return found ? "EVC-" + found[1] : null;
  }

  // What the planner typed in the "Agency fee (%)" box -> { fraction } (null when blank) or { error }. The server wants a FRACTION (0 <= f < 1) and does
  // not guess whether 15 means 15%, so the conversion happens here: 15 -> 0.15, "12.5%" -> 0.125.
  function percentToFraction(raw) {
    const text = String(raw == null ? "" : raw).replace(/%/g, "").trim();
    if (!text) return { fraction: null };
    if (!/^\d+(?:[.,]\d*)?$/.test(text)) return { error: "The agency fee must be a number between 0% and 99%." };
    const percent = Number(text.replace(",", "."));
    if (!(percent >= 0 && percent < 100)) return { error: "The agency fee must be between 0% and 99%." };
    return { fraction: Math.round(percent * 10000) / 1000000 };       // four decimals of a percent, so 15 is exactly 0.15 and not 0.15000000000000002
  }

  // The fraction the server sent back as the text a person types: 0.15 -> "15", 0.125 -> "12.5".
  function fractionToPercent(fraction) {
    if (typeof fraction !== "number" || !isFinite(fraction) || fraction <= 0 || fraction >= 1) return "";
    return String(Math.round(fraction * 1000000) / 10000);
  }

  // A YYYY-MM-DD string -> { time (UTC ms), y, m, d } or { error } in the server's words. Done by hand (never `new Date(text)`): no time zone can shift a date.
  function checkDate(raw, what) {
    const text = typeof raw === "string" ? raw.trim() : "";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return { error: "Enter the " + what + " as YYYY-MM-DD." };
    const y = +text.slice(0, 4), m = +text.slice(5, 7), d = +text.slice(8, 10);
    const time = Date.UTC(y, m - 1, d), back = new Date(time);
    if (back.getUTCFullYear() !== y || back.getUTCMonth() !== m - 1 || back.getUTCDate() !== d) return { error: "The " + what + " isn't a real calendar date." };
    if (y < YEAR_RANGE[0] || y > YEAR_RANGE[1]) return { error: "The " + what + " must be in a year from " + YEAR_RANGE[0] + " to " + YEAR_RANGE[1] + "." };
    return { time, y, m, d };
  }

  // The form's raw values -> { field: message } for everything that would make the server answer 422, with the server's own messages.
  // Empty object = valid. `form` holds the typed strings (agency_fee as a percent, notion_id as typed).
  function validate(form) {
    form = form || {};
    const out = {};
    const tooLong = (name) => {
      const spec = MAX_LENGTHS[name];
      return textLength(plainText(form[name])) > spec[0] ? spec[1] + " can be at most " + spec[0] + " characters." : "";
    };
    if (!plainText(form.client_name)) out.client_name = "Enter the new client's name.";
    else if (tooLong("client_name")) out.client_name = tooLong("client_name");

    const start = checkDate(form.start_date, "start date"), end = checkDate(form.end_date, "end date");
    if (start.error) out.start_date = start.error;
    if (end.error) out.end_date = end.error;
    else if (!start.error) {
      if (end.time < start.time) out.end_date = "The end date can't be before the start date.";
      else if ((end.time - start.time) / DAY_MS + 1 > MAX_FLIGHT_DAYS) out.end_date = "The flight can't be longer than 10 years.";
    }

    if (tooLong("notion_id")) out.notion_id = tooLong("notion_id");
    else if (normalizeNotionId(form.notion_id) === null) out.notion_id = "The Notion ID is a 4 or 5 digit number, for example EVC-48213.";

    const email = plainText(form.salesperson_email);
    if (tooLong("salesperson_email")) out.salesperson_email = tooLong("salesperson_email");
    else if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) out.salesperson_email = "That doesn't look like an email address.";

    const fee = percentToFraction(form.agency_fee);
    if (fee.error) out.agency_fee = fee.error;

    ["agency_name", "client_website", "requested_by", "salesperson_market", "request_type", "campaign_name"].forEach((name) => {
      if (tooLong(name)) out[name] = tooLong(name);
    });
    return out;
  }

  // The form's raw values (plus request_id and keep_avails) -> the body of POST .../duplicate. Every field is sent as the form shows it, so a
  // blank "requested by" means blank (an OMITTED field would mean "keep the original's"). Call it after validate() found nothing wrong.
  function toRequestBody(form) {
    form = form || {};
    const fee = percentToFraction(form.agency_fee);
    return {
      request_id: String(form.request_id || ""),
      client_name: plainText(form.client_name),
      start_date: String(form.start_date || "").trim(),
      end_date: String(form.end_date || "").trim(),
      notion_id: normalizeNotionId(form.notion_id) || "",
      agency_name: plainText(form.agency_name),
      agency_fee: fee.error ? null : fee.fraction,
      client_website: plainText(form.client_website),
      requested_by: plainText(form.requested_by),
      salesperson_email: plainText(form.salesperson_email),
      salesperson_market: plainText(form.salesperson_market),
      request_type: plainText(form.request_type),
      campaign_name: plainText(form.campaign_name),
      keep_avails: !!form.keep_avails,
    };
  }

  // A 422's pydantic text -> the sentence for the planner. Only OUR messages ("Value error, <sentence>") are shown; pydantic's own
  // ("Field required", "Input should be ...") are not words for a planner.
  function serverSentence(msg) {
    const text = typeof msg === "string" ? msg : "";
    return /^Value error, /.test(text) ? text.replace(/^Value error, /, "") : "Check this and try again.";
  }

  // The body of a 422 -> { fields: { name: sentence }, general: sentence }. `loc[-1]` names the field; one that is not an input of the form
  // (request_id, a malformed body) goes to `general`, the banner. The first message for a field wins.
  function mapServerErrors(payload) {
    const out = { fields: {}, general: "" };
    const detail = payload && typeof payload === "object" ? payload.detail : null;
    if (!Array.isArray(detail)) return out;
    detail.forEach((item) => {
      if (!item || typeof item !== "object") return;
      const loc = Array.isArray(item.loc) ? item.loc : [];
      const field = loc.length ? String(loc[loc.length - 1]) : "";
      const sentence = serverSentence(item.msg);
      if (FORM_FIELDS.indexOf(field) >= 0) { if (!out.fields[field]) out.fields[field] = sentence; }
      else if (!out.general) out.general = sentence === "Check this and try again." ? GENERIC_FAIL : sentence;
    });
    return out;
  }

  // What to say for a failed request, by status (the table of docs/duplicate_proposal.md); the server's own text is never printed raw.
  // `phase` is "defaults" (loading the form) or "submit". `offline`: the request never reached the server.
  function failureText(status, phase, offline) {
    if (offline) {
      return phase === "submit"
        ? "We couldn't reach the server. Check your connection and try again. Pressing Create copy again with the same details won't make a second copy."
        : "We couldn't reach the server. Check your connection and try again.";
    }
    switch (status) {
      case 401: return "Your session ended. Signing you in again…";
      case 403: return "You can only duplicate your own proposals.";
      case 404: return "We couldn't find that proposal. It may have been deleted.";
      case 410: return "This proposal was saved before copying was supported, so it can't be duplicated.";
      case 409: return phase === "defaults" ? "There's nothing to copy yet: this draft has no request details." : GENERIC_FAIL;
      case 429: return "Too many requests. Wait a moment, then try again.";
      case 500: return phase === "submit" ? "We couldn't create the copy. Nothing was saved." : "We couldn't load this proposal's details. Try again in a moment.";
      case 503: return phase === "submit"
        ? "The database isn't reachable right now. Nothing was created. Try again in a moment."
        : "The database isn't reachable right now. Try again in a moment.";
      default:
        if (status >= 500) return "The server had a problem. " + (phase === "submit" ? "Nothing was created. " : "") + "Try again in a moment.";
        return phase === "submit" ? GENERIC_FAIL : "We couldn't load this proposal's details. Please try again.";
    }
  }

  // A failure that no retry can fix (not the owner, gone, nothing saved): the dialog offers Close only.
  function isFinalFailure(status) { return status === 403 || status === 404 || status === 410 || status === 409; }

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const UNIT_WORDS = { month: ["month", "months"], week: ["week", "weeks"], quarter: ["quarter", "quarters"] };
  const knownUnit = (unit) => unit === "full_flight" || Object.prototype.hasOwnProperty.call(UNIT_WORDS, unit);

  // "Feb 1 to Apr 30, 2027" (the year once when both dates share it), "Nov 15, 2026 to Jun 20, 2027" across years.
  function formatRange(s, e) {
    const a = MONTHS[s.m - 1] + " " + s.d, b = MONTHS[e.m - 1] + " " + e.d;
    return s.y === e.y ? a + " to " + b + ", " + e.y : a + ", " + s.y + " to " + b + ", " + e.y;
  }

  // How many billing periods a flight has: calendar months touched, 7-day weeks from the start (the last may be short), groups of three
  // months from the start month, or one for the whole flight. The same counting as the page's own period builders (monthly_allocation.py
  // and app.js _mbPeriodsBetween), kept here so the dialog needs no app.js global.
  function countPeriods(unit, s, e) {
    if (unit === "full_flight") return 1;
    if (unit === "week") return Math.ceil(((e.time - s.time) / DAY_MS + 1) / 7);
    const months = (e.y * 12 + e.m) - (s.y * 12 + s.m) + 1;
    return unit === "quarter" ? Math.ceil(months / 3) : months;
  }

  function periodsText(unit, n) {
    const words = UNIT_WORDS[unit];
    return n + " " + words[n === 1 ? 0 : 1];
  }

  // The one live line under the flight dates. `source` is duplicate-defaults' source block ({ start_date, end_date, time_unit, periods }).
  // With both new dates valid: "3 months (Feb 1 to Apr 30, 2027). The original was 3 months." and, when the count differs,
  // "Budgets stay per month, so the totals will change." Before that: what the original ran, so the planner has something to go by. "" when unknown.
  function flightHint(source, startIso, endIso) {
    source = source && typeof source === "object" ? source : {};
    const unit = knownUnit(source.time_unit) ? source.time_unit : "month";
    const s = checkDate(startIso, "start date"), e = checkDate(endIso, "end date");
    if (!s.error && !e.error && e.time >= s.time) {
      const n = countPeriods(unit, s, e);
      const range = formatRange(s, e);
      const had = typeof source.periods === "number" && source.periods > 0 ? source.periods : null;
      if (unit === "full_flight") return "Billed as one period for the whole flight (" + range + "). The budgets are flight totals, so they stay the same.";
      let text = periodsText(unit, n) + " (" + range + ").";
      if (had !== null) {
        text += " The original was " + periodsText(unit, had) + ".";
        if (had !== n) text += " Budgets stay per " + UNIT_WORDS[unit][0] + ", so the totals will change.";
      }
      return text;
    }
    const os = checkDate(source.start_date, "start date"), oe = checkDate(source.end_date, "end date");
    if (os.error || oe.error) return "";
    const was = typeof source.periods === "number" && source.periods > 0 && unit !== "full_flight" ? " (" + periodsText(unit, source.periods) + ")" : "";
    return "The original ran " + formatRange(os, oe) + was + ".";
  }

  // How many notes of the answer say "check": the "N things to check in Step 02" of the success message.
  function checkCount(notes) {
    return Array.isArray(notes) ? notes.filter((n) => n && typeof n === "object" && n.level === "check").length : 0;
  }

  // Where "Open the copy" goes. The server sends /?reopen=<id>; anything else (a stale or hostile answer) falls back to building it from the id.
  function openUrlFor(result) {
    const url = result && typeof result.open_url === "string" ? result.open_url : "";
    if (/^\/(?!\/)/.test(url) && url.indexOf("\\") < 0) return url;
    return "/?reopen=" + encodeURIComponent(String(result && result.proposal_id || ""));
  }

  // One id per dialog, 16 to 64 characters of [A-Za-z0-9_-] (what the server accepts): a UUID where the browser has one.
  function newRequestId() {
    const c = typeof crypto !== "undefined" ? crypto : null;
    if (c && typeof c.randomUUID === "function") return c.randomUUID();
    let hex = "";
    if (c && typeof c.getRandomValues === "function") {
      const bytes = c.getRandomValues(new Uint8Array(16));
      for (let i = 0; i < bytes.length; i++) hex += (bytes[i] + 256).toString(16).slice(1);
      return hex;
    }
    for (let i = 0; i < 32; i++) hex += Math.floor(Math.random() * 16).toString(16);
    return hex;
  }

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  // ---------------------------------------------------------------- DOM helpers

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

  function safeFocus(el, opts) {
    if (!el || typeof el.focus !== "function") return false;
    try { el.focus(opts); return true; } catch (e) { return false; }
  }

  const logFailure = (what) => { if (typeof console !== "undefined") console.error("DuplicateProposal: " + what); };

  // ---------------------------------------------------------------- the dialog

  let current = null;                        // the one open dialog: { close }
  const inertHandle = () => ({ close() {} });

  function open(opts) {
    if (typeof document === "undefined") return inertHandle();
    opts = opts || {};
    const PH = typeof window !== "undefined" ? window.ProposalHistory : null;
    const proposal = opts.proposal && typeof opts.proposal === "object" ? opts.proposal : {};
    const sourceId = typeof proposal.proposal_id === "string" ? proposal.proposal_id : "";
    if (!PH || typeof PH.shell !== "function" || !sourceId) { logFailure("cannot open (no proposal id, or proposal-history.js is not loaded)"); return inertHandle(); }
    if (current) return current;                                              // one dialog at a time: a second call is the first one again

    const S = {
      phase: "loading",                      // loading | blocked | form | done
      inFlight: false, dirty: false, closed: false, doneCalled: false,
      requestId: newRequestId(),             // kept for every retry of THIS dialog
      source: null, defaults: null, result: null, aborter: null, firstField: null,
    };
    const fields = {};                       // name -> { input, error, hint }
    let formEl = null, alertEl = null, liveEl = null, submitBtn = null, hintEl = null, detailsEl = null, keepInput = null, locker = null;

    const initialName = typeof proposal.client_name === "string" && proposal.client_name.trim() ? proposal.client_name.trim() : "this proposal";
    const notionBit = typeof proposal.notion_id === "string" && proposal.notion_id.trim() ? " (" + proposal.notion_id.trim() + ")" : "";
    const subtitle = (name, bit) => "Starts from " + name + bit + ". You can change everything in the steps that follow.";

    const sh = PH.shell({
      title: "Duplicate for a new client",
      subtitle: subtitle(initialName, notionBit),
      top: true, narrow: true,
      returnFocus: opts.trigger || undefined,
      pending: () => S.phase === "form" && S.dirty,                           // typed text is not thrown away by a stray click on the backdrop
      initialFocus: () => S.firstField,
      onClose: () => {
        S.closed = true;
        if (S.aborter) { try { S.aborter.abort(); } catch (e) { /* nothing to cancel */ } }
        if (locker) document.removeEventListener("focusin", locker, true);
        if (current === handle) current = null;
        if (S.phase === "done" && S.result) {                                  // the list behind was redrawn: the keyboard goes to the new row, else stays where it is
          const trigger = opts.trigger;
          if (!trigger || !trigger.isConnected) {
            const row = Array.from(document.querySelectorAll(".ph-row")).find((li) => li.getAttribute("data-id") === S.result.proposal_id);
            safeFocus(row && row.querySelector("[data-action='open']"), { preventScroll: true });
          }
        }
      },
    });
    sh.dialog.id = "duplicate-dialog";
    const handle = { close() { sh.close(); } };
    current = handle;
    const root = h("div", { class: "dup" });
    sh.body.append(root);

    const say = (text) => { if (!liveEl) return; liveEl.textContent = ""; setTimeout(() => { if (!S.closed && liveEl) liveEl.textContent = text; }, 40); };
    const setSub = (name, bit) => { const el = sh.dialog.querySelector(".ph-sub"); if (el) el.textContent = subtitle(name, bit); };

    // ------------------------------------------------ requests
    function request(url, init) {
      const aborter = new AbortController();
      S.aborter = aborter;
      return window.fetch(url, Object.assign({ signal: aborter.signal, headers: { Accept: "application/json" } }, init)).then(
        (res) => res.json().then((data) => ({ status: res.status, ok: res.ok, data }), () => ({ status: res.status, ok: res.ok, data: null })),
        (err) => { if (err && err.name === "AbortError") throw err; return { status: 0, ok: false, data: null, offline: true }; });
    }

    // ------------------------------------------------ states
    function renderLoading() {
      S.phase = "loading";
      root.textContent = "";
      root.append(h("div", { class: "dup-state", role: "status" }, h("span", { class: "ph-btn-wait", "aria-hidden": "true" }), h("span", { text: "Loading the proposal's details…" })));
    }

    // The form cannot open: the reason in words, a Close button, and Try again where trying again can help.
    function renderBlocked(message, retryable) {
      S.phase = "blocked";
      root.textContent = "";
      const retry = retryable ? h("button", { type: "button", class: "ph-btn", id: "dup-retry", text: "Try again" }) : null;
      const close = h("button", { type: "button", class: "ph-btn ph-btn--primary", id: "dup-close", text: "Close" });
      if (retry) retry.addEventListener("click", start);
      close.addEventListener("click", () => sh.close());
      root.append(h("div", { class: "dup-state dup-state--blocked" }, h("p", { class: "ph-form-alert", role: "alert", text: message })),
        h("div", { class: "ph-form-foot" }, retry, close));
      safeFocus(retry || close);
    }

    function start() {
      renderLoading();
      Promise.resolve().then(() => (typeof opts.prepare === "function" ? opts.prepare() : "")).then((blockedBy) => {
        if (S.closed) return null;
        if (blockedBy) { renderBlocked(String(blockedBy), true); return null; }
        return request("/api/proposal/" + encodeURIComponent(sourceId) + "/duplicate-defaults").then((res) => {
          if (S.closed) return;
          const ok = res.ok && res.data && typeof res.data === "object" && res.data.source && typeof res.data.source === "object" && res.data.defaults && typeof res.data.defaults === "object";
          if (!ok) { renderBlocked(failureText(res.ok ? 0 : res.status, "defaults", !!res.offline), !isFinalFailure(res.status)); return; }
          S.source = res.data.source;
          S.defaults = res.data.defaults;
          const name = plainText(S.source.client_name) || initialName;
          setSub(name, notionBit);
          renderForm();
        });
      }).catch((err) => {
        if (S.closed || (err && err.name === "AbortError")) return;
        logFailure("loading the form failed");
        renderBlocked(failureText(0, "defaults", false), true);
      });
    }

    // ------------------------------------------------ the form
    function field(name, label, o) {
      o = o || {};
      const id = IDS[name], errId = id + "-err", hintId = id + "-hint";
      const input = h("input", Object.assign({ class: "ph-input", id, name, type: o.type || "text", autocomplete: "off", placeholder: o.placeholder, inputmode: o.inputmode,
        "aria-required": o.req ? "true" : null }));
      if (o.value) input.value = o.value;
      const error = h("p", { class: "ph-ferror", id: errId, hidden: true });
      const hint = o.hint ? h("p", { class: "ph-hint", id: hintId, text: o.hint }) : null;
      const describedBy = (o.describedBy || []).concat(hint ? [hintId] : []);
      fields[name] = { input, error, hint, id, describedBy };
      if (describedBy.length) input.setAttribute("aria-describedby", describedBy.join(" "));
      return h("div", { class: "ph-field" + (o.wide ? " ph-field--wide" : "") },
        h("label", { class: "ph-flabel", for: id }, label, o.req ? [h("span", { class: "ph-req", "aria-hidden": "true", text: " *" }), h("span", { class: "ph-sr", text: " (required)" })] : null),
        input, error, hint);
    }

    function readForm() {
      const form = { request_id: S.requestId, keep_avails: !!(keepInput && keepInput.checked) };
      FORM_FIELDS.forEach((name) => { form[name] = fields[name] ? fields[name].input.value : ""; });
      return form;
    }

    function setError(name, message) {
      const f = fields[name];
      if (!f) return;
      const ids = f.describedBy.concat(message ? [f.id + "-err"] : []).join(" ");
      f.error.textContent = message || "";
      f.error.hidden = !message;
      if (message) f.input.setAttribute("aria-invalid", "true"); else f.input.removeAttribute("aria-invalid");
      if (ids) f.input.setAttribute("aria-describedby", ids); else f.input.removeAttribute("aria-describedby");
    }

    function showAlert(message) {
      if (!alertEl) return;
      alertEl.textContent = message || "";
      alertEl.hidden = !message;
    }

    function refreshHint() {
      if (hintEl) hintEl.textContent = flightHint(S.source, fields.start_date.input.value, fields.end_date.input.value);
    }

    // The first field with a problem, in the order the form shows them; "More details" opens when any problem sits in there.
    function focusFirstProblem(names) {
      const first = FORM_FIELDS.find((n) => names.indexOf(n) >= 0);
      if (!first) return;
      if (detailsEl && names.some((n) => MORE.indexOf(n) >= 0)) detailsEl.open = true;     // a message in there must be visible, whichever field gets the keyboard
      safeFocus(fields[first].input);
    }

    function renderForm() {
      S.phase = "form";
      root.textContent = "";
      const d = S.defaults || {};
      const text = (v) => (typeof v === "string" ? v : "");

      hintEl = h("p", { class: "ph-hint dup-flight-hint", id: "dup-flight-hint", "aria-live": "polite", text: "" });
      alertEl = h("div", { class: "ph-form-alert", role: "alert", id: "dup-alert", hidden: true });
      liveEl = h("p", { class: "ph-sr", role: "status", "aria-live": "polite", id: "dup-live" });

      const grid = h("div", { class: "ph-form-grid" },
        field("client_name", "New client name", { req: true, wide: true, placeholder: "New client" }),
        field("start_date", "Flight start", { req: true, type: "date" }),
        field("end_date", "Flight end", { req: true, type: "date", describedBy: ["dup-flight-hint"] }),
        h("div", { class: "ph-field--wide" }, hintEl),
        field("notion_id", "Notion request ID", { placeholder: "EVC-48213", hint: "Leave blank if there's no request yet. You can add it in Step 02." }),
        field("salesperson_email", "Salesperson email", { type: "email", value: text(d.salesperson_email), placeholder: "name@entravision.com" }),
        field("agency_name", "Agency"),
        field("agency_fee", "Agency fee (%)", { inputmode: "decimal", placeholder: "15" }));

      // "Same as the original: Initiative Media, 15%": the agency is never pre-filled (a direct client must not inherit it), but it is one click away.
      const feeText = fractionToPercent(d.agency_fee);
      const sameLabel = [plainText(d.agency_name), feeText ? feeText + "%" : ""].filter(Boolean).join(", ");
      if (sameLabel) {
        const sameBtn = h("button", { type: "button", class: "ph-linkbtn", id: "dup-same-agency", text: "Same as the original: " + sameLabel });
        sameBtn.addEventListener("click", () => {
          fields.agency_name.input.value = plainText(d.agency_name);
          fields.agency_fee.input.value = feeText;
          setError("agency_name", ""); setError("agency_fee", "");
          S.dirty = true;
          say("Agency filled in from the original.");
          safeFocus(fields.agency_name.input);
        });
        grid.append(h("div", { class: "ph-field--wide" }, sameBtn));
      }

      keepInput = h("input", { type: "checkbox", id: "dup-keep-avails", name: "keep_avails" });
      detailsEl = h("details", { class: "ph-details dup-more" }, h("summary", { text: "More details" }),
        h("div", { class: "ph-form-grid" },
          field("client_website", "Client website", { value: "", placeholder: "https://" }),
          field("requested_by", "Requested by", { value: text(d.requested_by) }),
          field("salesperson_market", "Market", { value: text(d.salesperson_market) }),
          field("request_type", "Request type", { value: text(d.request_type), wide: true }),
          field("campaign_name", "Campaign name", { hint: "Leave blank to name it after the new client.", wide: true }),
          h("div", { class: "ph-field--wide" },
            h("label", { class: "ph-check", for: "dup-keep-avails" }, keepInput, h("span", { text: "Keep the avails estimates" })),
            h("p", { class: "ph-hint", text: "Off by default: the estimates from Constanza were calculated for the original client, so they are cleared. Numbers a planner typed by hand are kept either way." }))));

      formEl = h("form", { class: "ph-form dup-form", id: "dup-form", novalidate: true, "aria-label": "Duplicate for a new client" }, alertEl,
        h("fieldset", { class: "dup-fields" }, h("legend", { class: "ph-sr", text: "The new opportunity" }), grid, detailsEl), liveEl);
      const cancel = h("button", { type: "button", class: "ph-btn", id: "dup-cancel", text: "Cancel" });
      submitBtn = h("button", { type: "submit", class: "ph-btn ph-btn--primary", id: "dup-submit", form: "dup-form" }, h("span", { text: "Create copy" }));
      cancel.addEventListener("click", () => { if (!S.inFlight) sh.close(); });
      root.append(formEl, h("div", { class: "ph-form-foot" }, cancel, submitBtn));

      S.firstField = fields.client_name.input;
      refreshHint();

      formEl.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
      formEl.addEventListener("input", (e) => {
        S.dirty = true;
        const name = e.target && e.target.name;
        if (fields[name]) setError(name, "");                                  // typing clears that field's message; the next blur or submit checks again
        if (name === "start_date" || name === "end_date") refreshHint();
      });
      formEl.addEventListener("change", (e) => {
        const name = e.target && e.target.name;
        if (name === "start_date" || name === "end_date") refreshHint();
      });
      // On blur a field that has something in it is checked (an empty required one is not nagged at while tabbing through).
      formEl.addEventListener("focusout", (e) => {
        const name = e.target && e.target.name;
        if (!fields[name] || S.inFlight) return;
        const input = fields[name].input;
        if (name === "notion_id") { const id = normalizeNotionId(input.value); if (id) input.value = id; }
        if (!input.value.trim()) return;
        const problems = validate(readForm());
        setError(name, problems[name] || "");
        if (name === "start_date" && fields.end_date.input.value.trim()) setError("end_date", problems.end_date || "");
      });
      sh.focusInitial();
    }

    function lockForm(on) {
      const set = formEl && formEl.querySelector("fieldset");
      if (set) set.disabled = on;
      if (submitBtn) {
        submitBtn.setAttribute("aria-disabled", on ? "true" : "false");
        submitBtn.textContent = "";
        if (on) submitBtn.append(h("span", { class: "ph-btn-wait", "aria-hidden": "true" }), h("span", { text: "Creating…" }));
        else submitBtn.append(h("span", { text: "Create copy" }));
      }
      sh.setBusy(on);
    }

    async function submit() {
      if (S.inFlight || S.phase !== "form" || S.closed) return;                // a double click is ONE request
      showAlert("");
      const form = readForm();
      const problems = validate(form);
      const names = Object.keys(problems);
      FORM_FIELDS.forEach((name) => setError(name, problems[name] || ""));
      if (names.length) {
        focusFirstProblem(names);
        say(plural(names.length, "thing needs", "things need") + " fixing.");
        return;
      }
      S.inFlight = true;
      lockForm(true);
      let res = null;
      try {
        res = await request("/api/proposal/" + encodeURIComponent(sourceId) + "/duplicate", {
          method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(toRequestBody(form)),
        });
      } catch (err) {
        if (!(err && err.name === "AbortError")) logFailure("the request failed");
      } finally {
        S.inFlight = false;
        if (!S.closed) lockForm(false);
      }
      if (S.closed) return;
      const answer = res && res.ok && res.data && typeof res.data === "object" && typeof res.data.proposal_id === "string" ? res.data : null;
      if (answer) { renderDone(answer); return; }
      if (res && res.status === 422) {
        const mapped = mapServerErrors(res.data);
        Object.keys(mapped.fields).forEach((name) => setError(name, mapped.fields[name]));
        const bad = Object.keys(mapped.fields);
        showAlert(mapped.general || (bad.length ? "Some details need fixing. They are marked below." : GENERIC_FAIL));
        if (bad.length) focusFirstProblem(bad); else safeFocus(submitBtn);
        return;
      }
      // Failed: the form stays as it was, the request id stays too, and pressing Create copy again is safe.
      showAlert(res && res.ok ? GENERIC_FAIL : failureText(res ? res.status : 0, "submit", !!(res && res.offline)));
      logFailure("the copy was not created" + (res && res.status ? " (HTTP " + res.status + ")" : ""));
      safeFocus(submitBtn);
    }

    // ------------------------------------------------ success
    function renderDone(result) {
      S.phase = "done";
      S.result = result;
      const checks = checkCount(result.notes);
      const created = result.created !== false;
      const title = typeof result.proposal_title === "string" ? result.proposal_title : "";
      const countText = checks ? plural(checks, "thing", "things") + " to check in Step 02." : "Nothing was flagged, but give Step 02 a quick look before you go on.";
      const close = h("button", { type: "button", class: "ph-btn", id: "dup-close", text: "Close" });
      const link = h("a", { class: "ph-btn ph-btn--primary", id: "dup-open-link", href: openUrlFor(result), target: "_blank", rel: "noopener" },
        h("span", { text: "Open the copy" }), h("span", { class: "ph-sr", text: " (opens in a new tab)" }));
      close.addEventListener("click", () => sh.close());
      root.textContent = "";
      root.append(
        h("div", { class: "dup-done", role: "status" },
          h("span", { class: "dup-done-mark", "aria-hidden": "true", text: "✓" }),
          h("h3", { class: "dup-done-title", text: created ? "Copy created" : "This copy already exists" }),
          title ? h("p", { class: "dup-done-name", text: title }) : null,
          h("p", { class: "dup-done-text", text: (created ? "" : "The same request already went through, so no second copy was made. ") + countText })),
        h("div", { class: "ph-form-foot" }, close, link));
      // A list that reloads behind this dialog tries to move the keyboard to its new row: the focus trap pulls it back to the top of THIS dialog,
      // which is not where the person is. Focus that lands outside goes to the primary link again.
      locker = (e) => { if (!S.closed && !sh.dialog.contains(e.target)) setTimeout(() => { if (!S.closed) safeFocus(link); }, 0); };
      document.addEventListener("focusin", locker, true);
      safeFocus(link);
      setTimeout(() => safeFocus(link), 0);
      if (!S.doneCalled) {
        S.doneCalled = true;
        if (typeof opts.onDone === "function") { try { opts.onDone(result); } catch (e) { logFailure("onDone failed"); } }
      }
    }

    start();
    return handle;
  }

  const api = {
    open,
    _pure: {
      plainText, normalizeNotionId, percentToFraction, fractionToPercent, checkDate, validate, toRequestBody, serverSentence, mapServerErrors, failureText,
      isFinalFailure, formatRange, countPeriods, flightHint, checkCount, openUrlFor, newRequestId,
    },
  };
  if (typeof window !== "undefined") window.DuplicateProposal = api;
})();
