/* ============================================================================
 * Constanza — the floating launcher and the avails-puller dock.
 *
 * Replaces the old masthead button + modal. A round character in the bottom-right corner opens a dock: pick a product,
 * type a geography (it is matched live, with suggestions when it is ambiguous or misspelled), set the flight and an
 * optional audience, and pull avails. The same engine and the same wording are used by the per-line "Auto-calculate"
 * button on Step 05 (renderCard / mountGeoHelp below), so both places read alike.
 *
 * Loaded after app.js: it relies on app.js's globals (state, escapeHtml, formatPlainDisplay, _replaceGeoItem,
 * applyConstanzaEstimate, _effectiveGeo, _effectiveStartDate/EndDate, _mbParseDate, _mbMonthsBetween) and on the page's host object
 * (window.constanzaHost, below) only at call time. Server side: app/main.py (/api/constanza/*), app/services/constanza_engine.py.
 *
 * Constanza is the avails hub: every source that has its own estimate and panel (TikTok, Meta, the next one) registers as a method
 * (registerMethod / CONTRACT below) and this file never names one. docs/constanza_methods.md has the contract and the checklist.
 * ========================================================================== */
(function () {
  "use strict";

  // ------------------------------------------------------------------ small helpers
  const $ = (id) => document.getElementById(id);
  const esc = (s) => {
    if (typeof escapeHtml === "function") return escapeHtml(s);
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
  };
  const num = (n) => (n == null || isNaN(n) ? "—" : Math.round(n).toLocaleString("en-US"));
  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  // JSON with every object's keys sorted. Two requests that ask for the same thing compare equal whatever order their keys come in: a saved
  // proposal comes back from the database (a JSONB column) with its keys re-sorted, and JSON.stringify of it would not equal the form's.
  function sortKeys(v) {
    if (Array.isArray(v)) return v.map(sortKeys);
    if (v && typeof v === "object") {
      if (typeof v.toJSON === "function") return sortKeys(v.toJSON());
      const out = {};
      Object.keys(v).sort().forEach((k) => { out[k] = sortKeys(v[k]); });
      return out;
    }
    return v;
  }
  const canonicalJson = (value) => JSON.stringify(sortKeys(value));

  function compact(n) {
    if (n == null || isNaN(n)) return "—";
    const a = Math.abs(n);
    const fmt = (v, unit) => v.toFixed(Math.abs(v) >= 10 ? 0 : 1).replace(/\.0$/, "") + unit;
    if (a >= 1e9) return fmt(n / 1e9, "B");
    if (a >= 1e6) return fmt(n / 1e6, "M");
    if (a >= 1e3) return fmt(n / 1e3, "K");
    return String(Math.round(n));
  }

  const store = {
    get() { try { return JSON.parse(localStorage.getItem("constanza.v2") || "{}") || {}; } catch (e) { return {}; } },
    set(patch) { try { localStorage.setItem("constanza.v2", JSON.stringify(Object.assign(store.get(), patch))); } catch (e) { /* private window */ } },
  };

  async function api(path, body) {
    const res = await fetch(path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    return { ok: res.ok, status: res.status, data: data || {} };
  }

  const errorText = (data, fallback) => (typeof _constanzaErrorText === "function" ? _constanzaErrorText(data, fallback) : (data && typeof data.detail === "string" && data.detail) || fallback || "Request failed.");

  // ------------------------------------------------------------------ the character
  const CHARACTER = `
<svg class="cz-char" viewBox="0 0 96 96" aria-hidden="true" focusable="false" data-mood="idle">
  <g class="cz-signal" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round">
    <path class="a1" d="M68 18c5 1.5 8.5 5 10 10"/>
    <path class="a2" d="M66 8c9 2.5 15 8.5 18 17"/>
  </g>
  <g class="cz-ant-l"><path d="M39 31 35 20" stroke="#fff" stroke-width="3.6" stroke-linecap="round"/><circle cx="34" cy="17.5" r="4.4" fill="#0098e2" stroke="#fff" stroke-width="1.6"/></g>
  <g class="cz-ant-r"><path d="M57 31 61 20" stroke="#fff" stroke-width="3.6" stroke-linecap="round"/><circle cx="62" cy="17.5" r="4.4" fill="#0098e2" stroke="#fff" stroke-width="1.6"/></g>
  <rect x="8" y="41" width="15" height="24" rx="3.5" fill="#0098e2"/>
  <path d="M8 53h15M15.5 41v24" stroke="#fff" stroke-opacity=".6" stroke-width="1.5"/>
  <rect x="73" y="41" width="15" height="24" rx="3.5" fill="#0098e2"/>
  <path d="M73 53h15M80.5 41v24" stroke="#fff" stroke-opacity=".6" stroke-width="1.5"/>
  <rect x="20" y="30" width="56" height="48" rx="19" fill="#fff"/>
  <rect x="26" y="37" width="44" height="28" rx="13" fill="#2b0f2e"/>
  <g class="cz-eyes">
    <ellipse class="cz-eye" cx="39" cy="50" rx="4.3" ry="5.2" fill="#8fe6ff"/>
    <ellipse class="cz-eye" cx="57" cy="50" rx="4.3" ry="5.2" fill="#8fe6ff"/>
    <path class="cz-eye-happy" d="M34.5 53c1-6.5 8-6.5 9 0" fill="none" stroke="#8fe6ff" stroke-width="3" stroke-linecap="round"/>
    <path class="cz-eye-happy" d="M52.5 53c1-6.5 8-6.5 9 0" fill="none" stroke="#8fe6ff" stroke-width="3" stroke-linecap="round"/>
  </g>
  <path class="cz-mouth" d="M43 57.5c2.8 3 7.2 3 10 0" fill="none" stroke="#8fe6ff" stroke-width="2.6" stroke-linecap="round"/>
  <ellipse class="cz-mouth-o" cx="48" cy="58" rx="2.7" ry="3.2" fill="#8fe6ff"/>
  <path class="cz-mouth-oops" d="M43 60.5c2.8-3 7.2-3 10 0" fill="none" stroke="#8fe6ff" stroke-width="2.6" stroke-linecap="round"/>
  <circle cx="28" cy="71" r="3" fill="#e6b8e9"/>
  <circle cx="68" cy="71" r="3" fill="#e6b8e9"/>
  <text class="cz-qmark" x="70" y="30" font-family="Poppins, sans-serif" font-weight="700" font-size="20" fill="#fff">?</text>
  <path class="cz-spark" d="M74 24l1.8 4.6 4.7 1.8-4.7 1.8L74 37l-1.8-4.8-4.7-1.8 4.7-1.8z" fill="#fff"/>
</svg>`;

  // A one-colour face for buttons (the per-line "Auto-calculate" button).
  const MINI_ICON = `<svg class="cz-btn-ico" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="4" y="7" width="16" height="13" rx="5" fill="currentColor"/><circle cx="9.3" cy="13.2" r="1.5" fill="#fff"/><circle cx="14.7" cy="13.2" r="1.5" fill="#fff"/><path d="M9 4.2 8 2.2M15 4.2l1-2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;

  let moodTimer = null;
  function setMood(mood, revertMs) {
    document.querySelectorAll(".cz-char").forEach((svg) => { svg.dataset.mood = mood; });
    clearTimeout(moodTimer);
    if (revertMs) moodTimer = setTimeout(() => setMood("idle"), revertMs);
  }

  // ------------------------------------------------------------------ product catalogue
  // The picker's groups: the data series' channels, then one group per registered method (key "m:<key>", in registration order: the prefix
  // keeps it from ever colliding with a data channel such as "display"), then the population-share products.
  const CHANNEL_ORDER = ["ctv", "platform_model", "live_sports", "netflix", "pause_ads", "olv", "display"];
  const CHANNEL_LABEL = {
    ctv: "Entravision+ Connect · Madhive", platform_model: "Streaming platforms", live_sports: "Live sports", netflix: "Netflix",
    pause_ads: "Pause ads", olv: "Online video", display: "Display",
    _generic: "Other products · population-share estimate",
  };
  const channelOrder = () => CHANNEL_ORDER.concat(groupKeys, ["_generic"]);
  const channelLabelOf = (channel) => (Object.prototype.hasOwnProperty.call(CHANNEL_LABEL, channel) ? CHANNEL_LABEL[channel] : groupLabels[channel]) || "";
  const BASIS_LABEL = { platform_reported: "Reported avails", modeled: "Modeled", extrapolated: "Sample extended to all markets" };
  const UNIT_LABEL = { weekly: "weekly avails", monthly: "monthly avails" };

  function loadProducts() {
    const cat = (typeof state !== "undefined" && state.catalog) || null;
    if (!cat) return [];
    const out = [];
    for (const fam of cat.families || []) {
      for (const p of (cat.products_by_family || {})[fam] || []) {
        if (p.is_addon || !p.forecast || !p.forecast.enabled) continue;
        if (!isDataMethod(p.forecast.method) && !plugins[p.forecast.method]) continue;      // a method whose script did not load is never offered (and never falls through to the data path)
        const series = p.forecast.series || null;
        const plug = plugins[p.forecast.method] || null;
        out.push({
          name: p.name, family: fam, method: p.forecast.method, series,
          channel: plug ? groupOf(plug) : (series && series.channel ? series.channel : "_generic"),
          // Hispanic targeting: on by default for this product, and whether its data is ALREADY built from Hispanic audiences (never cut twice)
          hispanicDefault: !!(p.forecast.hispanic_default != null ? p.forecast.hispanic_default : p.forecast.hispanic_weighted),
          hispanicAlready: /^(hispanic|spanish)/.test(String((series && series.targeting) || "")),
        });
      }
    }
    return out;
  }

  // The forecast method of a catalog product that Admin has enabled (whether or not its method's script loaded), else null.
  function methodOfCatalogProduct(name) {
    const cat = (typeof state !== "undefined" && state.catalog) || null;
    for (const fam of (cat && cat.families) || []) {
      const hit = ((cat.products_by_family || {})[fam] || []).find((p) => p.name === name && !p.is_addon && p.forecast && p.forecast.enabled);
      if (hit) return hit.forecast.method || null;
    }
    return null;
  }

  function seriesMeta(series, method) {
    if (method && plugins[method]) return plugins[method].sourceLabel || "Live estimate";
    if (!series) return "Population-share estimate";
    const bits = [BASIS_LABEL[series.data_basis] || "", UNIT_LABEL[series.unit] || ""].filter(Boolean);
    return bits.join(" · ");
  }

  const isLiveSports = (series) => !!(series && series.channel === "live_sports");

  // ------------------------------------------------------------------ state
  const S = {
    open: false, wide: !!store.get().wide,
    product: store.get().product || null, products: [],
    geo: "", geoType: "", flight: 1, unit: "months", total: "",
    ageMin: "", ageMax: "", gender: "", interests: [], hispanic: null,     // hispanic: null = the product's default, true / false = the planner's choice
    lineId: null, needTotal: false,
    resolve: null, resolving: false, resolveFailed: false, result: null, busy: false, resSeq: 0, pullSeq: 0, taxonomy: null,
    resultCtx: null,                  // what the shown result was pulled for: the geography as typed and the areas it resolved to
    resultSig: null,                  // the request that result was pulled with (canonicalJson), to tell whether the form still asks for it
    resultLid: null,                  // the Step 05 line that result was pulled for (or shown from): the one line Insert may file its snapshot under
    resultSnapshot: null,             // the snapshot kept for that line when the method saves its estimates (null for the rest)
    resultStale: null,                // why a RESTORED result no longer matches its line (budget, months ...), shown in the banner; null = nothing to say
    monthsOverride: null,             // the line's calendar months, sent instead of the flight box's figure while the box still shows what open() put there (caps.lineMonths)
  };
  let resolveTimer = null, interestTimer = null, pickerActive = -1, pickerItems = [], interestHits = [], interestActive = -1;

  // ------------------------------------------------------------------ geography help (shared with the Step 05 cards)
  const GEO_TYPE_LABEL = { dma: "TV market", state: "state", county: "county", city: "city", zip: "ZIP", congressional_district: "district", country: "country" };

  function normGeo(r) {
    r = r || {};
    return {
      ambiguous: r.ambiguous || [],
      suggestions: r.suggestions || r.geo_suggestions || [],
      interpretations: r.interpretations || r.geo_interpretations || [],
      unmatched: r.unmatched_details || r.unmatched_geo_details || [],
      unmatchedTokens: r.unmatched_tokens || r.unmatched_geo_tokens || [],
    };
  }

  function candidateLabel(c) {
    const pop = c.population && !/pop\./.test(c.label || "") ? ` <small>${compact(c.population)}</small>` : "";
    return `${esc(c.label)}${pop}`;
  }

  function geoHelpHtml(n) {
    const out = [];
    n.interpretations.forEach((i) => {
      out.push(`<p class="cz-note cz-read">Read “${esc(i.input)}” as <strong>${esc(i.became)}</strong>.<br><small>${esc(i.why)}</small></p>`);
    });
    n.ambiguous.forEach((entry, ei) => {
      const chips = (entry.candidates || []).map((c, ci) => `<button type="button" class="cz-chip cz-sugg" data-act="candidate" data-e="${ei}" data-c="${ci}">${candidateLabel(c)}</button>`).join("");
      out.push(`<div class="cz-help"><p class="cz-help-title">“${esc(entry.input)}” matches more than one place — pick yours:</p><div class="cz-chips">${chips}</div></div>`);
    });
    const dym = n.suggestions.map((s, i) => [s, i]).filter(([s]) => s.kind === "did_you_mean");
    if (dym.length) {
      out.push(`<div class="cz-help"><p class="cz-help-title">${n.unmatched.length || !n.interpretations.length ? "Did you mean" : "Other places it could be"}:</p><div class="cz-chips">${dym.map(([s, i]) =>
        `<button type="button" class="cz-chip cz-sugg" data-act="suggestion" data-i="${i}" title="${esc(s.why || "")}">${esc(s.label)} <small>${esc(GEO_TYPE_LABEL[s.geo_type] || "")}${s.population ? " · " + compact(s.population) : ""}</small></button>`).join("")}</div></div>`);
    }
    const overlap = n.suggestions.map((s, i) => [s, i]).filter(([s]) => s.kind === "overlap");
    overlap.forEach(([s, i]) => {
      out.push(`<div class="cz-help"><p class="cz-help-title">${esc(s.why)}</p><div class="cz-chips"><button type="button" class="cz-chip cz-sugg" data-act="suggestion" data-i="${i}">${esc(s.label)}</button></div></div>`);
    });
    const broaden = n.suggestions.map((s, i) => [s, i]).filter(([s]) => s.kind === "broaden");
    if (broaden.length) {
      out.push(`<div class="cz-help"><p class="cz-help-title">Or use a larger area:</p><div class="cz-chips">${broaden.map(([s, i]) =>
        `<button type="button" class="cz-chip cz-sugg" data-act="suggestion" data-i="${i}" title="${esc(s.why || "")}">${esc(s.label)}</button>`).join("")}</div></div>`);
    }
    const reasons = {};
    n.unmatched.forEach((d) => { reasons[d.input] = d.reason; });
    if (n.unmatched.length) {
      out.push(n.unmatched.map((d) => `<p class="cz-note cz-warn">${esc(d.reason)}</p>`).join(""));
    } else if (n.unmatchedTokens.length && !n.ambiguous.length) {
      out.push(`<p class="cz-note cz-warn">Couldn’t match: ${n.unmatchedTokens.map((t) => `<strong>${esc(t)}</strong>`).join(", ")}</p>`);
    }
    return out.join("");
  }

  // ---- editing ONE whole item of a typed geography list ------------------------------------------------------------------
  // Items are separated by ; | a new line, the words and / or / plus, & + or a comma. An item that is itself written with a
  // comma ("Othello, WA") is found as a whole. Matching is by WHOLE items, never by substring: "Orange" is not found inside
  // "Orange County", "90210" not inside "902101", and "Othello" is not taken out of "Othello, WA" (the state after it belongs
  // to it).
  const US_STATES = ["alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "district of columbia",
    "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee",
    "texas", "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming"];
  const US_ABBR = new Set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI".split(" "));
  const looksLikeState = (piece) => {
    const t = String(piece).trim();
    return US_ABBR.has(t.toUpperCase()) || US_STATES.indexOf(t.toLowerCase()) >= 0;
  };
  // Conjunction words. "or" only in lowercase: OR is Oregon (the server reads it the same way), and the matching below is
  // written out letter by letter instead of with the `i` flag so that a state code can never be taken for a word.
  const WORD = "(?:[Aa][Nn][Dd]|[Pp][Ll][Uu][Ss]|or)";
  const WORD_LC = "(?:and|plus|or)";                          // after a comma only lower case is a conjunction: "Dallas, Plus Ultra" is a name
  const SEP = "[;|\\n,&+]";
  const CONJ = "\\s" + WORD + "\\s";
  const ci = (s) => s.replace(/[A-Za-z]|[^A-Za-z]/g, (c) => (/[A-Za-z]/.test(c) ? "[" + c.toLowerCase() + c.toUpperCase() + "]" : escRe(c)));

  function itemRegex(item) {
    const body = item.trim().split(/\s*,\s*/).map((part) => part.split(/\s+/).map(ci).join("\\s+")).join("\\s*,\\s*");
    return new RegExp("(^|" + SEP + "|" + CONJ + ")(\\s*)(" + body + ")(?=\\s*$|\\s*" + SEP + "|\\s+" + WORD + "\\s)", "g");
  }

  // `replacement` null removes the item together with the separator that joined it to the list. Returns the new text, or null when
  // the item is not in the text as a whole item.
  function editGeoItem(text, item, replacement) {
    if (!item || !item.trim()) return null;
    const re = itemRegex(item);
    const lastPart = item.trim().split(/\s*,\s*/).pop();
    let m;
    while ((m = re.exec(text)) !== null) {
      const end = re.lastIndex;
      if (!looksLikeState(lastPart)) {
        const next = /^\s*,\s*([^,;|\n&+]+)/.exec(text.slice(end));
        if (next && looksLikeState(next[1])) continue;            // "Othello" in "Othello, WA" is not the whole item
      }
      if (replacement != null) return text.slice(0, m.index) + m[1] + m[2] + replacement + text.slice(end);
      if (m[1]) {                                                 // "A; X" -> "A"; "A and X" -> "A"; "A, B, or X" -> "A, B"
        const before = text.slice(0, m.index);
        // ... and an Oxford comma after the item goes too: "A, X, and B" -> "A and B"
        const after = text.slice(end).replace(new RegExp("^\\s*,(?=\\s+" + WORD_LC + "\\s)"), "");
        return (/^\s/.test(m[1]) ? before.replace(/\s*,\s*$/, "") : before) + after;
      }
      // the first item: its following separator goes with it ("X; A" -> "A", "X and A" -> "A", "X, and A" -> "A")
      return text.slice(0, m.index) + text.slice(end).replace(new RegExp("^(?:\\s*,\\s*" + WORD_LC + "(?=\\s)|\\s+" + WORD + "(?=\\s)|\\s*" + SEP + ")?\\s*"), "");
    }
    return null;
  }

  // Remove the separators that stand with nothing between them ("; ; B"), or at either end of the text. Words are never touched.
  function tidyList(text) {
    const sep = "[;|,&+]";
    return text
      .replace(new RegExp("\\s*" + sep + "\\s*(?=" + sep + "|$)", "g"), "")
      .replace(new RegExp("^\\s*" + sep + "\\s*"), "")
      .replace(/[ \t]{2,}/g, " ")
      .trim();
  }

  // Remove the typed items the planner chose to drop ("Othello, WA" when "Adams County, WA" is kept) - every copy of each (the same
  // place can be typed twice). Returns null when none of them is in the text, so the caller can fall back.
  function removeGeoItems(text, items) {
    let out = text, removed = 0;
    for (const item of items || []) {
      for (let guard = 0; guard < 20; guard++) {
        const next = editGeoItem(out, item, null);
        if (next === null) break;
        out = tidyList(next);
        removed++;
      }
    }
    return removed ? out : null;
  }

  // The tail of "City, <tail>" that qualifies the place instead of naming another one: a state, a state with a ZIP, a ZIP, "D.C." or
  // the country.
  const looksLikeQualifier = (piece) => {
    const t = String(piece).trim().replace(/\s*\b\d{5}(?:-\d{4})?$/, "");
    return t === "" || looksLikeState(t) || /^(?:d\.?\s?c\.?|u\.?s\.?a?\.?|united states)$/i.test(t);
  };

  // More than one place in the text: a separator, a conjunction word, or commas that are not just "name, state".
  function looksLikeList(text) {
    if (/[;|\n&+]/.test(text) || new RegExp(CONJ).test(text)) return true;
    const parts = text.split(",").map((p) => p.trim()).filter(Boolean);
    return parts.length > 2 || (parts.length === 2 && (/^\d{5}(?:-\d{4})?$/.test(parts[0]) || !looksLikeQualifier(parts[1])));
  }

  // Swap one item for the planner's pick. An item that cannot be found never wipes the list: a single typed item is replaced, and
  // a list gets the place added.
  function replaceItem(text, item, replacement) {
    const next = editGeoItem(text, item, replacement);
    if (next !== null) return next;
    if (!looksLikeList(text)) return replacement;
    return editGeoItem(text, replacement, replacement) !== null ? text : `${text.replace(/[;\s]+$/, "")}; ${replacement}`;
  }

  // ctx: { getText(), setText(next, {geoType}) }
  function mountGeoHelp(container, resolution, ctx) {
    const n = normGeo(resolution);
    container.innerHTML = geoHelpHtml(n);
    container.onclick = (ev) => {
      const btn = ev.target.closest("[data-act]");
      if (!btn || !container.contains(btn)) return;
      const text = ctx.getText();
      if (btn.dataset.act === "candidate") {
        const entry = n.ambiguous[Number(btn.dataset.e)];
        const cand = entry.candidates[Number(btn.dataset.c)];
        ctx.setText(replaceItem(text, entry.input, cand.resolve_hint), { geoType: "" });
        return;
      }
      const sg = n.suggestions[Number(btn.dataset.i)];
      if (!sg) return;
      if (sg.kind === "did_you_mean") ctx.setText(sg.replaces ? replaceItem(text, sg.replaces, sg.resolve_hint) : sg.resolve_hint, { geoType: "" });
      else if (sg.kind === "overlap") ctx.setText(removeGeoItems(text, sg.drop_inputs) || sg.resolve_hint, { geoType: "" });
      else ctx.setText(sg.resolve_hint, { geoType: "" });
    };
    return n;
  }

  // ------------------------------------------------------------------ numbers shared by the dock and the cards
  function summarize(data) {
    const aud = data.audience || null;
    const src = aud || data;
    const months = Math.max(1, data.months || 1);
    const weekly = !!(data.series && data.series.unit === "weekly" && data.weeks);
    const per = (v, d) => (v == null ? null : v / d);
    // The people the product reaches (the platform's reach universe) and its est. monthly uniques - both narrowed by the audience
    // cuts when there are any, exactly like the impressions, so impressions are normally larger than the audience (not always: a ZIP
    // the platform reports at its floor can hold more people than avails). null where the data has none.
    const people = aud ? aud.audience_people : data.reach_universe;
    const uniques = aud ? aud.est_uniques : data.est_uniques;
    const monthMid = per(src.imps_mid, months);
    // A win rate was applied (a MadHive / Trade Desk product, or Prime Video through the Amazon DSP): the impressions here are
    // the expected delivery, a share of the platform's avails. `raw` is what the platform reported, per the unit the card speaks in (week for live sports, else month).
    // With audience cuts the table is the AUDIENCE's, so the platform's figures are scaled by the same cut (the delivery already is):
    // the line then describes the very number in the headline instead of the whole geography's.
    const wr = data.win_rate || null;
    const wrPeriod = weekly ? data.weeks : months;
    const cut = aud && data.imps_mid ? aud.imps_mid / data.imps_mid : 1;
    return {
      aud, months, weekly, weeks: weekly ? data.weeks : null,
      winRate: wr ? {
        low: wr.low, mid: wr.mid, high: wr.high, unit: weekly ? "week" : "month", forAudience: !!aud,
        raw: per(wr.raw_imps_mid, wrPeriod) * cut, delivered: per(src.imps_mid, wrPeriod),
        atLow: wr.delivered_at_low == null ? null : per(wr.delivered_at_low, wrPeriod) * cut,
        atHigh: wr.delivered_at_high == null ? null : per(wr.delivered_at_high, wrPeriod) * cut,
      } : null,
      total: { low: src.imps_low, mid: src.imps_mid, high: src.imps_high },
      month: { low: per(src.imps_low, months), mid: monthMid, high: per(src.imps_high, months) },
      week: weekly ? { low: per(src.imps_low, data.weeks), mid: per(src.imps_mid, data.weeks), high: per(src.imps_high, data.weeks) } : null,
      uniques: uniques == null ? null : uniques,
      people: people == null ? null : people,
      peopleBase: data.reach_universe == null ? null : data.reach_universe,          // before the audience cuts
      perPerson: people ? monthMid / people : null,                                  // monthly impressions per person in the reach universe
      perUnique: uniques ? monthMid / uniques : null,                                // ... per est. unique: the frequency the Avails step shows
      proxy: !!(data.series && data.series.relation === "baseline_proxy"),         // another product's audience stands in for this one's
      baseMonthMid: aud ? per(data.imps_mid, months) : null,
    };
  }

  function badgesHtml(data) {
    const series = data.series || null;
    const plug = pluginFor(data);
    const b = [];
    if (plug) {                                                   // a live method says where its numbers came from itself (none = no badge)
      if (plug.badgeHtml) b.push(pluginHtml(plug, "badgeHtml", data));
    } else if (series) {
      const basis = series.data_basis;
      const cls = basis === "platform_reported" ? "cz-reported" : (basis === "extrapolated" ? "cz-extended" : "cz-modeled");
      b.push(`<span class="cz-badge ${cls}">${esc(BASIS_LABEL[basis] || "Modeled")}</span>`);
      if (series.unit === "weekly") b.push(`<span class="cz-badge">Weekly avails${data.weeks ? ` · ${data.weeks.toFixed(1)} wk flight` : ""}</span>`);
    } else {
      b.push(`<span class="cz-badge cz-extended">Population-share estimate</span>`);
    }
    if (isLiveSports(series) && series.period_label) b.push(`<span class="cz-badge cz-period">📅 ${esc(series.period_label)}</span>`);
    if (data.audience && data.audience.hispanic) {
      const h = data.audience.hispanic;
      if (h.mode === "not_applied") b.push(`<span class="cz-badge cz-extended" title="No Census data for this geography: the estimate is not narrowed">Hispanic targeting · not applied</span>`);
      else b.push(`<span class="cz-badge cz-reported" title="${h.mode === "already_targeted" ? "This product’s data is already built from Hispanic audiences" : "Avails narrowed to the Hispanic share of the population (Census ACS)"}">Hispanic targeting${h.mode === "cut" && h.share != null ? ` · ${pct(h.share)}` : ""}</span>`);
    }
    if (data.win_rate) b.push(`<span class="cz-badge" title="${data.win_rate.high > data.win_rate.low ? "Avails are bid opportunities; Entravision wins about this share of them" : "Entravision is expected to win about this share of the avails"}">Win rate ${pct(data.win_rate.mid)}</span>`);
    const geo = { high: "High geo confidence", medium: "Medium geo confidence", low_confidence_international: "International · low confidence" }[data.geo_confidence];
    if (geo) b.push(`<span class="cz-badge">${esc(geo)}</span>`);
    return b.join("");
  }

  const pct = (x) => `${Math.round(x * 1000) / 10}%`;                     // 0.15 -> "15%", 0.125 -> "12.5%"

  // The line that says the impressions are expected delivery, not the platform's raw avails (only when a win rate was applied), with
  // what the delivery would be at the low and high win rate — the range is its own figure, not folded into Low / High above.
  // "(bid opportunities) " only where there is a win-rate RANGE to explain — the programmatic (MadHive / Trade Desk) case; a single rate
  // (Prime Video through the Amazon DSP) applies to the platform's ad volume, which is not a pool of bids.
  const winRateWhat = (w) => (w.high > w.low ? "(bid opportunities) " : "");

  function winRateHtml(m) {
    const w = m && m.winRate;
    if (!w) return "";
    const sens = w.atLow != null && w.atHigh != null && w.high > w.low ? ` — ${num(w.atLow)} at ${pct(w.low)}, ${num(w.atHigh)} at ${pct(w.high)}` : "";
    // (not "the platform reports N avails": a modeled series is Entravision's own model, and with audience cuts N is that audience's slice)
    return `<p class="cz-winrate"><strong>Win rate ${pct(w.mid)}</strong> applied: ${num(w.raw)} avails a ${w.unit}${w.forAudience ? " for this audience" : ""} `
      + `${winRateWhat(w)}become an expected delivery of about <strong>${num(w.delivered)}</strong> a ${w.unit}${sens}.</p>`;
  }

  // The one people-count a planner reads beside the impressions: the est. UNIQUE IDs reached, always at or below them. The platform's
  // reach universe (the "addressable pool", a ceiling a flight only partly delivers to, which can be larger than the impressions) is
  // kept out of the headline — it was read as "the audience" and looked larger than the impressions — and is offered as a tooltip here
  // and in the dock's "About this data".
  function reachLinesHtml(m, plug) {
    if (m.uniques == null || plug) return "";
    const tip = m.people != null ? ` title="${esc(poolText(m.people, m.proxy))}"` : "";
    return `<p class="constanza-uniques"${tip}>Unique IDs reached / month${m.proxy ? " (baseline product)" : ""}: <strong>${num(m.uniques)}</strong>${m.perUnique ? ` · about ${m.perUnique.toFixed(1)} impressions each` : ""}</p>`;
  }

  const poolText = (people, proxy) => `Addressable pool${proxy ? " (baseline product)" : ""}: ${num(people)} IDs — the most the platform could reach here; a flight reaches part of it`;

  const isWinRateNote = (n) => String(n).startsWith("Win rate applied");

  function notesOf(data) {
    // (the win-rate note is for API readers: the card and the dock already show it as their own win-rate line)
    return (Array.isArray(data.notes) ? data.notes : []).filter((n) => !String(n).startsWith("NOT INCLUDED in this estimate") && !isWinRateNote(n));
  }

  function unmatchedHtml(data) {
    const tokens = data.unmatched_geo_tokens || [];
    if (!tokens.length) return "";
    const reasons = {};
    (data.unmatched_geo_details || []).forEach((d) => { reasons[d.input] = d.reason; });
    return `<div class="cz-error"><strong>Not included — couldn’t be matched:</strong><ul class="cz-list">${tokens.map((t) => `<li><strong>${esc(t)}</strong>${reasons[t] ? ` — ${esc(reasons[t])}` : ""}</li>`).join("")}</ul></div>`;
  }

  // `m` is summarize()'s result for the same data. What the product reaches is the est. UNIQUE IDs (narrowed by the cuts, never above
  // the impressions beside it); the platform's reach universe is the ADDRESSABLE POOL, a ceiling that is only partly delivered to; the
  // census population of the market that fits the cuts is context, said to be everyone in the market across all media — three
  // different counts, labelled apart so none is read as "the audience" of the impressions.
  // What the Hispanic targeting did, in one line: the real Hispanic share it cut to (Census ACS), that the product's data already is one, or
  // that it could not be applied. The server's own notes say the same things for API readers; the box shows this line instead (isHispanicNote).
  const HISPANIC_ASSUMPTION = "This assumes Hispanic households use the platform in proportion to their population; replace it with the platform’s own Hispanic segment size when you have one.";
  function hispanicLineHtml(h) {
    const source = h.vintage ? `Census ${esc(String(h.vintage))}` : "Census ACS";
    if (h.mode === "not_applied") return `<p class="cz-warn-text"><strong>Hispanic targeting not applied</strong> — there is no Census data for this geography, so the estimate is not narrowed.</p>`;
    const share = h.share != null ? `${pct(h.share)} of the people here are Hispanic, ${source}` : "no Census share was available for this geography";
    return h.mode === "already_targeted"
      ? `<p><strong>Hispanic targeting</strong> — this product’s data is already built from Hispanic audiences, so no further cut was applied. For context, ${share}.</p>`
      : `<p><strong>Hispanic targeting</strong> — avails narrowed to each area’s Hispanic share: ${share}. <small>${HISPANIC_ASSUMPTION}</small></p>`;
  }

  // The server's Hispanic notes that the line above already says (its warnings — small areas, the state proxy, areas not narrowed — stay).
  const isHispanicNote = (n) => /^(Hispanic targeting: the platform's avails|This series is already built from Hispanic audiences|Hispanic targeting was asked for but could not be applied)/.test(String(n));

  function audienceBoxHtml(aud, m) {
    if (!aud) return "";
    const tier = { audience_modeled_geo_only: "Geography only — no demographic narrowing", audience_modeled_single_cut: "1 targeting cut applied", audience_modeled_stacked: "Several cuts stacked — directional, not precise" }[aud.confidence_tier] || "";
    const lines = [];
    if (tier) lines.push(`<p><strong>${esc(tier)}</strong></p>`);
    if (aud.hispanic) lines.push(hispanicLineHtml(aud.hispanic));
    const people = m ? m.people : aud.audience_people;
    const uniques = m ? m.uniques : aud.est_uniques;
    const floor = aud.below_reliability_threshold ? ` <span class="cz-warn-text">That is below the ~${num(aud.reliability_floor_population)} reliability floor for this cut; treat the result as order-of-magnitude only.</span>` : "";
    const who = aud.market_population_adults ? "adults" : "people";                 // adults only when an interest rate (a share of adults) clipped it to 18+
    const proxy = m && m.proxy;
    if (uniques != null) {
      const poolTip = people != null ? ` title="${esc(poolText(people, proxy))}"` : "";
      lines.push(`<p${poolTip}>Unique IDs reached ${proxy ? "on the baseline product" : "on this product"} ≈ <strong>${num(uniques)}</strong> a month${m && m.perUnique ? ` — about ${m.perUnique.toFixed(1)} impressions each` : ""}.${proxy ? " <small>This product’s own targeting reaches fewer.</small>" : ""}</p>`);
    }
    if (people != null || uniques != null) {
      lines.push(`<p><small>Market population (census): ≈ ${num(aud.underlying_population)} ${who} in the market fit these cuts, across all media.${floor}</small></p>`);
    } else {
      lines.push(`<p>Market population (census): ≈ <strong>${num(aud.underlying_population)}</strong> ${who} in the market fit these cuts, across all media. This product’s own data has no audience count, so its reach is not shown.${floor}</p>`);
    }
    (aud.interests || []).forEach((i) => {
      const name = i.label || i.input_text;
      if (i.supported) {
        // (a saved result is data anybody can have edited: only a web address becomes a link, whatever else it says stays text)
        const link = /^https?:\/\//i.test(String(i.source_url || "")) ? `<a href="${esc(i.source_url)}" target="_blank" rel="noopener noreferrer">${esc(i.source || "source")}</a>` : esc(i.source || "");
        const read = i.input_text && i.input_text !== i.label ? ` <small>(read “${esc(i.input_text)}” as this)</small>` : "";
        lines.push(`<p>✓ <strong>${esc(name)}</strong> — ${i.rate != null ? Math.round(i.rate * 1000) / 10 + "% of adults" : ""}${i.confidence ? ` · ${esc(i.confidence)} confidence` : ""}${read}<br><small>${link}${i.source_date ? ", " + esc(i.source_date) : ""}${i.status === "derived" ? " · derived" : ""}</small></p>`);
      } else {
        const alts = (i.suggestions || []).filter((x) => x.supported).slice(0, 3).map((x) => esc(x.label)).join(", ");
        lines.push(`<p class="cz-warn-text">⚠ <strong>${esc(name)}</strong> has no public rate — not applied to the estimate, not guessed.${alts ? ` <small>Closest audiences with a rate: ${alts}.</small>` : ""}</p>`);
      }
    });
    (aud.notes || []).filter((n) => !isWinRateNote(n) && !isHispanicNote(n)).forEach((n) => lines.push(`<p><small>${esc(n)}</small></p>`));
    return `<div class="cz-audience-box">${lines.join("")}</div>`;
  }

  function areaRows(data) {
    return (data.audience ? data.audience.by_area : data.by_area) || [];
  }

  function shareBarHtml(rows, total) {
    if (!rows.length || !total) return "";
    const top = [...rows].sort((a, b) => b.imps_mid - a.imps_mid).slice(0, 3);
    const rest = total - top.reduce((s, r) => s + r.imps_mid, 0);
    const parts = top.map((r, i) => ({ label: r.area_label, v: r.imps_mid, cls: "b" + (i + 1) }));
    if (rest > total * 0.005 && rows.length > 3) parts.push({ label: `${rows.length - 3} other area${rows.length - 3 === 1 ? "" : "s"}`, v: rest, cls: "b4" });
    return `<div class="cz-bar" role="img" aria-label="Share of impressions by area">${parts.map((p) => `<span class="${p.cls}" style="width:${(100 * p.v / total).toFixed(2)}%"></span>`).join("")}</div>
      <div class="cz-legend">${parts.map((p) => `<span><i class="${p.cls}" style="background:var(--${p.cls === "b1" ? "accent" : p.cls === "b2" ? "brand-quaternary" : p.cls === "b3" ? "ink-faint" : "rule"})"></i>${esc(p.label)} ${(100 * p.v / total).toFixed(0)}%</span>`).join("")}</div>`;
  }

  function provenanceHtml(data) {
    const s = data.series;
    if (!s) return "";
    const items = [];
    items.push(`<li><strong>${esc(s.title || s.key)}</strong> · version ${esc(s.version)}${s.data_basis ? " · " + esc(BASIS_LABEL[s.data_basis] || s.data_basis) : ""}</li>`);
    const dates = [s.exported_on ? `exported ${esc(s.exported_on)}` : "", s.published_at ? `loaded ${esc(String(s.published_at).slice(0, 10))}` : ""].filter(Boolean);
    if (dates.length) items.push(`<li>${dates.join(" · ")}</li>`);
    if (s.levels && s.levels.length) items.push(`<li>Geographic detail in the file: ${esc(s.levels.join(", "))}</li>`);
    if (s.note) items.push(`<li>${esc(s.note)}</li>`);
    const pool = data.audience ? data.audience.audience_people : data.reach_universe;
    if (pool != null) items.push(`<li>${esc(poolText(pool, s.relation === "baseline_proxy"))}${data.audience ? " (with these audience cuts)" : ""}. It is a ceiling, not delivery, so it can be larger than the impressions.</li>`);
    if (s.top_publishers && s.top_publishers.length) {
      items.push(`<li>Top publishers: ${s.top_publishers.slice(0, 5).map((p) => `${esc(p[0])} ${(p[1] * 100).toFixed(0)}%`).join(", ")}</li>`);
    }
    if (s.device_mix) {
      const mix = Object.entries(s.device_mix).sort((a, b) => b[1] - a[1]).slice(0, 4);
      if (mix.length) items.push(`<li>Devices: ${mix.map(([k, v]) => `${esc(k)} ${(v * 100).toFixed(0)}%`).join(", ")}</li>`);
    }
    return `<details class="cz-details"><summary>About this data</summary><ul class="cz-list">${items.join("")}</ul></details>`;
  }

  // ------------------------------------------------------------------ the Step 05 per-line card
  // opts: { onApply(monthlyMid, uniques, geoInfo, snapshot?), onRefine(), onGeoText(next), getText(), onHispanic?(on), hispanicFromProposal?,
  //         line, stale, fromSnapshot, snapshot, onRecalc?(button) }
  //   line          the line's facts (LineFacts), handed to the method's cardHtml / canAutoPull
  //   stale         reasons the estimate no longer describes its line (snapshotStale): a banner, Insert withdrawn, Recalculate first
  //   fromSnapshot  the estimate is a saved one; `snapshot` is that snapshot, and onApply gets it back as its 4th argument so the line keeps it
  //   onRecalc      repeats the saved request with the line's current facts (a method with caps.autoPull "saved"); the button is passed to it
  function renderCard(el, data, opts) {
    opts = opts || {};
    const plug = pluginFor(data);
    try {
      drawCard(el, data, opts, plug);
    } catch (e) {
      // A method's answer can be a saved one that anybody edited: whatever in it breaks the drawing (the method's own code, or ours reading a field of
      // the wrong shape), the card says so in a sentence and offers the way forward instead of taking the page down. A data product's answer is never
      // saved, so a throw there is a real bug and stays one.
      if (!(e instanceof PluginFault) && !plug && !opts.fromSnapshot) throw e;
      if (!(e instanceof PluginFault)) logOnce("card", e, "a saved estimate could not be drawn on a card");
      drawUnreadable(el, data && data.method, opts);       // its empty state and a way to ask again
    }
  }

  const UNREADABLE = "The saved estimate couldn’t be read — estimate again.";

  // Is there a monthly figure an Insert could write into Avails? A saved estimate can be hand-edited into one with none (summarize then reads null / NaN), and
  // writing that would put 0 over what the line holds.
  const hasMonthlyMid = (m) => !!(m && m.month && Number.isFinite(m.month.mid) && m.month.mid > 0);

  function drawCard(el, data, opts, plug) {
    const m = summarize(data);
    const notes = notesOf(data).slice(0, 4).map((n) => `<li>${esc(n)}</li>`).join("");
    const stale = Array.isArray(opts.stale) ? opts.stale : [];
    const caps = plug ? capsOf(plug) : CONTRACT.caps;
    const cardOpts = { line: opts.line || null, stale, fromSnapshot: !!opts.fromSnapshot };
    // the saved estimate this card is drawn from: the page hands it over, else it is asked for (the same one: the page's own reading of the line)
    const snap = opts.snapshot || (opts.fromSnapshot && opts.line ? snapshotFor(opts.line.id) : null);
    const labels = plug && plug.rangeLabels ? plug.rangeLabels : ["Low", "Mid", "High"];
    const monthRow = `<tr><td>${m.aud ? "Audience imps / month" : "Imps / month"}</td><td>${num(m.month.low)}</td><td>${num(m.month.mid)}</td><td>${num(m.month.high)}</td></tr>`;
    const weekRow = m.weekly ? `<tr><td>Per week</td><td>${num(m.week.low)}</td><td>${num(m.week.mid)}</td><td>${num(m.week.high)}</td></tr>` : "";
    const totalRow = m.months > 1 || m.weekly ? `<tr><td>Flight total${m.weekly ? "" : ` · ${m.months} mo`}</td><td>${num(m.total.low)}</td><td>${num(m.total.mid)}</td><td>${num(m.total.high)}</td></tr>` : "";
    const staleHtml = stale.length ? `<p class="cz-stale"><strong>Inputs changed — recalculate:</strong> ${esc(stale.join(", "))}.</p>` : "";
    const badges = badgesHtml(data);
    const method = plug ? pluginHtml(plug, "cardHtml", data, m, cardOpts) : "";
    // The action row. Apply (Insert) is withdrawn while the estimate is stale. A method whose card repeats a saved request (autoPull "saved") also
    // offers Recalculate - only when it can (canAutoPull), and disabled with the reason while the method is not connected; where it cannot, the
    // button that opens the dock is the way forward.
    const saved = !!(plug && caps.autoPull === "saved");
    const canRecalc = saved && typeof opts.onRecalc === "function"
      && (!plug.canAutoPull || ((guard(plug, "canAutoPull", [opts.line || null, snap], { ok: false }) || {}).ok !== false));
    const offline = canRecalc && plug.connection && guard(plug, "connection", [], null) === false;
    const reason = offline ? (goStateOf(plug).reason || "Not connected yet: an admin needs to add its credentials.") : "";
    const applyLabel = plug && plug.applyLabel ? pluginHtml(plug, "applyLabel", m) : "";
    const apply = stale.length || !hasMonthlyMid(m) ? "" : `<button type="button" class="${saved ? "btn-primary" : "btn-secondary"} cz-apply">${applyLabel ? esc(applyLabel) : "Apply monthly mid-case →"}</button>`;
    const whyId = `cz-recalc-why-${attrId(opts.line && opts.line.id != null ? opts.line.id : "card")}`;           // (one card per line: the id must not repeat on the page)
    const recalc = canRecalc ? `<button type="button" class="${stale.length ? "btn-primary" : "btn-secondary"} cz-recalc"${offline ? ` aria-disabled="true" aria-describedby="${whyId}"` : ""}>Recalculate</button>` : "";
    const refine = saved && !canRecalc
      ? `<button type="button" class="btn-primary cz-refine">Estimate in Constanza →</button>`
      : `<button type="button" class="cz-link cz-refine">${saved ? "Change in Constanza" : "Refine in Constanza"}</button>`;
    el.style.display = "block";
    el.innerHTML = `
      ${staleHtml}<div class="cz-badges">${badges}</div>
      <table class="constanza-range-table"><tr><th></th><th>${esc(labels[0])}</th><th>${esc(labels[1])}</th><th>${esc(labels[2])}</th></tr>${monthRow}${weekRow}${totalRow}</table>
      ${winRateHtml(m)}
      ${method}
      ${m.aud ? "" : reachLinesHtml(m, plug)}
      ${audienceBoxHtml(m.aud, m)}
      ${unmatchedHtml(data)}
      ${caps.ownsGeography ? "" : '<div class="cz-geohelp"></div>'}
      ${notes ? `<ul class="cz-list">${notes}</ul>` : ""}
      ${hispanicToggleHtml(data, opts)}
      <div class="cz-card-actions">
        ${[apply, recalc, refine].filter(Boolean).join("\n        ")}
      </div>${offline ? `<p class="cz-note cz-warn cz-recalc-why" id="${whyId}">${esc(reason)}</p>` : ""}`;
    // (a method that picks its own places has no "did you mean" help: the proposal's geography, which that help would edit, was never what it asked about)
    const help = el.querySelector(".cz-geohelp");
    if (help) mountGeoHelp(help, data, { getText: opts.getText, setText: (next) => opts.onGeoText(next) });
    const applyBtn = el.querySelector(".cz-apply");
    if (applyBtn) applyBtn.addEventListener("click", () => {
      const info = geoInfo(data);
      if (opts.fromSnapshot && snap) opts.onApply(m.month.mid, m.uniques, info, snap);
      else opts.onApply(m.month.mid, m.uniques, info);
    });
    const recalcBtn = el.querySelector(".cz-recalc");
    if (recalcBtn) recalcBtn.addEventListener("click", () => { if (recalcBtn.getAttribute("aria-disabled") !== "true") opts.onRecalc(recalcBtn); });
    el.querySelector(".cz-refine").addEventListener("click", () => opts.onRefine());
    const toggle = el.querySelector(".cz-hispanic-toggle");
    if (toggle) toggle.addEventListener("change", () => opts.onHispanic(toggle.checked));
  }

  // A saved estimate the method cannot read (an old shape, a hand-edited proposal): the method's empty state, the plain sentence, and the way
  // to estimate again. Nothing here depends on the saved data.
  function drawUnreadable(el, method, opts) {
    const plug = plugins[method];
    const empty = plug && plug.cardEmptyHtml ? guard(plug, "cardEmptyHtml", [opts.line || null, null], "") : "";
    el.style.display = "block";
    el.innerHTML = `<p class="constanza-error">${esc(UNREADABLE)}</p>${typeof empty === "string" ? empty : ""}`
      + `<div class="cz-card-actions"><button type="button" class="btn-primary cz-refine">Estimate in Constanza →</button></div>`;
    el.querySelector(".cz-refine").addEventListener("click", () => { if (typeof opts.onRefine === "function") opts.onRefine(); });
  }

  // The Step 05 card's own switch for Hispanic targeting (the server says what it was and what the product's default is; TikTok has none).
  function hispanicToggleHtml(data, opts) {
    const t = data.hispanic_targeting;
    if (!t || t.applies === false || !opts || !opts.onHispanic) return "";
    if (data.audience && data.audience.hispanic && data.audience.hispanic.mode === "already_targeted") return "";      // the data IS that audience: nothing to switch
    const note = t.on && t.default ? " <small>(on by default for this product)</small>" : (t.on && opts.hispanicFromProposal ? " <small>(from the proposal’s targeting)</small>" : "");
    return `<label class="cz-check cz-card-hispanic"><input type="checkbox" class="cz-hispanic-toggle" ${t.on ? "checked" : ""} /> <span>Hispanic targeting${note}</span></label>`;
  }

  function renderCardError(el, message, resolution, opts) {
    el.style.display = "block";
    el.innerHTML = `<p class="constanza-error">⚠️ ${esc(message)}</p><div class="cz-geohelp"></div>`;
    if (resolution) mountGeoHelp(el.querySelector(".cz-geohelp"), resolution, { getText: opts.getText, setText: (next) => opts.onGeoText(next) });
  }

  // ------------------------------------------------------------------ forecast methods that bring their own panel
  // A product whose forecast method is not a loaded data series (TikTok, Meta: a live API) shows its own panel in the dock and its own result
  // card. A source registers with registerMethod(key, factory); the factory gets `ctx` (the dock's state and helpers) and returns the plugin
  // object that CONTRACT (below) describes: its panel, how its choices become a request, how its answer reads. This file never names a
  // method: what differs between them is a flag or a string the plugin gives (caps, channelLabel, sourceLabel, badgeHtml, geoInfo ...).
  //
  // ctx (what the dock hands a panel: use these rather than reaching for the dock's or the page's globals):
  //   S                          the dock's live state (S.lineId is the Step 05 line it is open for, or null)
  //   $(id), esc(text), num(n), compact(n)            document.getElementById, HTML escaping, number formats
  //   api(path, body?)           fetch: a GET, or a POST of `body` as JSON -> { ok, status, data };  errorText(data, fallback) the plain-language message of an error answer
  //   markStale()                the request changed: any result on the screen no longer matches it
  //   setMood(name, revertMs?), announce(text)        the dock's avatar mood, and a polite screen-reader announcement (#cz-status)
  //   summarize(data), productByName(name), requestBody()      the dock's own result reading, product lookup and the request it would send
  //   targetsHispanic()          -> boolean. Does the line (or product) the dock is open for target Hispanic audiences? Evaluated lazily, on
  //                              every call, for whatever the dock is open for at that moment: true when the dock was told so (S.hispanic: the
  //                              line's own choice or the proposal's targeting), else when the page reads the line's own targeting text that
  //                              way. A hint only: a panel may suggest something from it, never apply it by itself.
  //   line()                     -> LineFacts | null. The facts of the Step 05 line the dock is open for, read fresh from the page on every call
  //                              (window.constanzaHost.lineFacts): null for the bubble, for a line that is no longer in the proposal's active
  //                              option, and on a page with no proposal. Null means "behave as a standalone dock".
  //   proposalGeo()              -> string. That line's geography text, else the proposal's ("search for the places named in this proposal").
  //   snapshot(lid)              -> Snapshot | null. The line's saved estimate (a native one, or an older shape adopted in memory), read through the page.
  //   refreshGo()                the answer of goState() / connection() changed (a connection check came back): the dock reads it again, and so do
  //                              the page's Step 05 cards (window.constanzaHost.onDirty(null)), which switch Recalculate off while a method is not connected.
  const plugins = Object.create(null);                  // method key -> plugin (no prototype: a method the server calls "constructor" must not find Object's)
  const pluginInfo = new Map();                         // plugin -> { key, caps } as registerMethod checked it
  const pluginKeys = [];                                // registration order (the picker's groups follow it)
  const groupKeys = [];                                 // picker group keys of the registered methods, in that order
  const groupLabels = Object.create(null);              // picker group key -> its heading

  // What a plugin is. registerMethod() refuses one that does not match (a typo in a member name, a missing member, caps that need a member that
  // is not there, a legacy.key that is not a field of its own named like meta_forecast) and says why in the console; the dock carries on without it.
  // requestPart() is read for its own key only: whatever else it returns never reaches the request. `caps` are the behaviours the dock reads as flags:
  //   ownsGeography   the method picks its own places (Meta's Comscore markets): the shared Where card is hidden, whereHtml() draws its own in that slot,
  //                   the request sends no geography text, and context().geo says which place a result is for. Its autoPull is "saved" or "none"
  //                   ("geo" would offer Auto-calculate from the proposal's geography, which it does not ask about)
  //   persist         the estimate of a Step 05 line is saved with the proposal (a snapshot) and shown again on its card: needs restore + choicesChanged
  //   autoPull        what Step 05 does for it: "geo" = pull from the proposal's geography (data products, TikTok), "saved" = repeat the saved request
  //                   (Recalculate; needs cardRequest + canAutoPull), "none" = no pull button on the card, the dock is where it is asked
  //   lineMonths      a pull for a Step 05 line sends the line's calendar months, not the flight box's rounded days
  const CONTRACT = {
    version: 1,
    required: ["formHtml", "bind", "requestPart", "context", "reset", "headlineHtml", "bodyHtml", "aboutHtml", "cardHtml", "summaryLines"],
    optional: ["goLabel", "onSelect", "onLine", "canPull", "goState", "onError", "onResult", "onProposalReset", "errorText", "badgeHtml", "geoInfo",
      "whereHtml", "channel", "channelLabel", "sourceLabel", "rangeLabels", "caps", "cardRequest", "canAutoPull", "cardEmptyHtml", "applyLabel",
      "staleReasons", "choicesChanged", "restore", "connection", "legacy", "snapshotLine", "_pure"],
    caps: { ownsGeography: false, persist: false, autoPull: "geo", lineMonths: false },
    ctx: ["S", "$", "esc", "num", "compact", "api", "markStale", "errorText", "setMood", "announce", "summarize", "productByName", "requestBody", "targetsHispanic",
      "line", "proposalGeo", "snapshot", "refreshGo"],
  };
  const FUNCTION_MEMBERS = CONTRACT.required.concat(CONTRACT.optional).filter((k) => !["goLabel", "channel", "channelLabel", "sourceLabel", "rangeLabels", "caps", "legacy", "_pure"].includes(k));
  [CONTRACT.required, CONTRACT.optional, CONTRACT.ctx, CONTRACT.caps, CONTRACT].forEach(Object.freeze);
  const KEY_RE = /^[a-z][a-z0-9_]{1,31}$/;
  const LEGACY_KEY_RE = /^[a-z][a-z0-9_]*_forecast$/;      // an older shape's own field of an Avails entry (never forecast_snapshot, never a figure such as max_imps)
  const isDataMethod = (method) => !method || method === "platform" || method === "generic";      // the two built-ins: no plugin, and no plugin may take their keys

  const pluginOf = (p) => (p && plugins[p.method]) || null;
  const pluginFor = (data) => (data && typeof data.method === "string" && plugins[data.method]) || null;       // the plugin that produced this answer
  const activePlugin = () => pluginOf(productByName(S.product));
  const keyOf = (plug) => (pluginInfo.get(plug) || {}).key || "";
  const capsOf = (plug) => (pluginInfo.get(plug) || { caps: CONTRACT.caps }).caps;
  const groupOf = (plug) => "m:" + (plug.channel || keyOf(plug));
  const ownsGeography = () => { const plug = activePlugin(); return !!(plug && capsOf(plug).ownsGeography); };
  const goLabel = () => { const plug = activePlugin(); return plug && plug.goLabel ? plug.goLabel : "Pull avails →"; };

  // ---- the page's side of the contract: window.constanzaHost (app.js defines it; read lazily, every member optional, so a page with no proposal works)
  //   lineFacts(lid) -> LineFacts | null      the facts of one Step 05 line (null: not in the proposal's active option)
  //   snapshot(lid) -> Snapshot | null        its saved estimate
  //   saveSnapshot(lid, snapshot | null) -> boolean       stores (or deletes) it; false, having written nothing, when the line is gone
  //   onDirty(lid, dirty)                     the dock's form now differs from (dirty) / equals (not) the estimate saved for that line: redraw its card
  const host = () => (typeof window !== "undefined" && window.constanzaHost) || null;

  function lineFactsFor(lid) {
    const h = host();
    if (lid == null || lid === "" || !h || typeof h.lineFacts !== "function") return null;
    try { return h.lineFacts(lid) || null; } catch (e) { return null; }
  }

  const validSnapshot = (s) => !!(s && typeof s === "object" && s.v === 1 && typeof s.method === "string" && s.method);

  function snapshotFor(lid) {
    const h = host();
    if (lid == null || lid === "" || !h || typeof h.snapshot !== "function") return null;
    try { const s = h.snapshot(lid); return validSnapshot(s) ? s : null; } catch (e) { return null; }
  }

  // Does the line the dock is open for target Hispanic audiences? The dock's own switch says so when it was told (S.hispanic), else the page's own
  // reading of the line's targeting text (app.js _targetsHispanic) does. Read fresh on every call: the dock is opened for another line all the time.
  function targetsHispanic() {
    if (S.hispanic === true) return true;
    try {
      if (S.lineId && typeof state !== "undefined" && state && Array.isArray(state.lineItems) && typeof _targetsHispanic === "function") {
        const li = state.lineItems.find((x) => x.id === S.lineId);
        return !!(li && _targetsHispanic(li));
      }
    } catch (e) { /* the page has no such helper: not known to be Hispanic */ }
    return false;
  }

  // A line id as the page writes it into an attribute (app.js escapeAttr: letters, digits . _ - only, so the page can find the element again with a selector).
  const attrId = (v) => (typeof escapeAttr === "function" ? escapeAttr(v) : String(v == null ? "" : v).replace(/[^a-zA-Z0-9._-]/g, "_"));

  // The line's geography text, else the proposal's.
  function lineOrProposalGeo() {
    const l = lineFactsFor(S.lineId);
    return (l && typeof l.geo_text === "string" && l.geo_text.trim()) || proposalGeo();
  }

  // ---- a plugin never takes the dock down (what the host guards)
  // Every call the host makes into a plugin while it draws goes through here. guard() answers `fallback` when the plugin throws; strict() throws a
  // PluginFault instead, so the caller can stop drawing and show the method's empty state with "The saved estimate couldn't be read"
  // (a saved estimate is data that anybody can have edited: one broken card must never blank the page). Each failure is logged once.
  class PluginFault extends Error {}
  const faultSeen = Object.create(null);
  function logOnce(tag, err, what) {
    if (faultSeen[tag]) return;
    faultSeen[tag] = true;
    try { console.error(`[constanza] ${what || tag}: ${(err && err.message) || err}`); } catch (e) { /* no console */ }
  }
  const logFault = (plug, member, err) => logOnce(`${keyOf(plug)}.${member}`, err, `plugin "${keyOf(plug)}": ${member} threw`);
  function guard(plug, member, args, fallback) {
    try { return plug[member].apply(plug, args || []); } catch (e) { logFault(plug, member, e); return fallback; }
  }
  function strict(plug, member, args) {
    try { return plug[member].apply(plug, args || []); } catch (e) { logFault(plug, member, e); throw new PluginFault(member); }
  }
  const pluginHtml = (plug, member, ...args) => { const out = strict(plug, member, args); return typeof out === "string" ? out : ""; };

  // A method's connection answer came back (or changed): the pull button is judged again, and the Step 05 cards - which read the same answer to
  // switch Recalculate off - are drawn again through the page (onDirty with no line = "something every card reads changed"). A page with no host, or
  // a host that throws, leaves the dock's own update standing.
  function refreshGo() {
    applyGoState();
    const h = host();
    if (!h || typeof h.onDirty !== "function") return;
    try { h.onDirty(null); } catch (e) { logOnce("host.onDirty", e, "the page could not redraw its cards after a connection answer"); }
  }

  function pluginCtx() {
    return { S, $, esc, num, compact, api, markStale, errorText, setMood, announce, summarize, productByName, requestBody: () => requestBody(), targetsHispanic,
      line: () => lineFactsFor(S.lineId), proposalGeo: lineOrProposalGeo, snapshot: snapshotFor, refreshGo };
  }

  // ---- registering a method
  function checkPlugin(plug) {
    if (!plug || typeof plug !== "object" || Array.isArray(plug)) return ["the factory did not return an object"];
    const problems = [];
    const known = CONTRACT.required.concat(CONTRACT.optional);
    Object.keys(plug).forEach((k) => { if (known.indexOf(k) < 0) problems.push(`unknown member "${k}"`); });
    CONTRACT.required.forEach((k) => { if (!(k in plug)) problems.push(`missing ${k}`); });
    FUNCTION_MEMBERS.forEach((k) => { if (k in plug && typeof plug[k] !== "function") problems.push(`${k} must be a function`); });
    ["goLabel", "channel", "channelLabel", "sourceLabel"].forEach((k) => { if (k in plug && (typeof plug[k] !== "string" || !plug[k])) problems.push(`${k} must be a non-empty string`); });
    if ("channel" in plug && typeof plug.channel === "string" && !KEY_RE.test(plug.channel)) problems.push("channel must be lower-case letters, digits and _");
    if ("rangeLabels" in plug && !(Array.isArray(plug.rangeLabels) && plug.rangeLabels.length === 3 && plug.rangeLabels.every((s) => typeof s === "string" && s))) problems.push("rangeLabels must be three words");
    if ("legacy" in plug && !(plug.legacy && typeof plug.legacy.key === "string" && plug.legacy.key && typeof plug.legacy.adopt === "function")) problems.push("legacy must be { key, adopt() }");
    else if ("legacy" in plug && !LEGACY_KEY_RE.test(plug.legacy.key)) problems.push("legacy.key must be a field of its own, lower-case and ending in _forecast (like the older shapes of saved estimates): the page deletes it when a new estimate is filed");
    if ("_pure" in plug && !(plug._pure && typeof plug._pure === "object")) problems.push("_pure must be an object");
    let caps = CONTRACT.caps;
    if ("caps" in plug) {
      const c = plug.caps;
      if (!c || typeof c !== "object" || Array.isArray(c)) {
        problems.push("caps must be an object");
      } else {
        Object.keys(c).forEach((k) => { if (!Object.prototype.hasOwnProperty.call(CONTRACT.caps, k)) problems.push(`unknown cap "${k}"`); });
        ["ownsGeography", "persist", "lineMonths"].forEach((k) => { if (k in c && typeof c[k] !== "boolean") problems.push(`caps.${k} must be true or false`); });
        if ("autoPull" in c && ["geo", "saved", "none"].indexOf(c.autoPull) < 0) problems.push('caps.autoPull must be "geo", "saved" or "none"');
        caps = Object.assign({}, CONTRACT.caps, c);
      }
    }
    const need = (cap, ...members) => members.forEach((k) => { if (typeof plug[k] !== "function") problems.push(`${cap} needs ${k}`); });
    if (caps.ownsGeography) need("caps.ownsGeography", "whereHtml");
    if (caps.ownsGeography && caps.autoPull === "geo") problems.push('caps.ownsGeography needs caps.autoPull "saved" or "none": the proposal’s geography is not what it asks about, so Auto-calculate from it could never work');
    if (caps.persist) need("caps.persist", "restore", "choicesChanged");
    if (caps.autoPull === "saved") need('caps.autoPull "saved"', "cardRequest", "canAutoPull");
    return problems;
  }

  function rebuildGroups() {
    groupKeys.length = 0;
    Object.keys(groupLabels).forEach((k) => { delete groupLabels[k]; });
    pluginKeys.forEach((key) => {
      const plug = plugins[key];
      if (!plug) return;
      const group = groupOf(plug);
      if (groupKeys.indexOf(group) < 0) { groupKeys.push(group); groupLabels[group] = plug.channelLabel || key; }
    });
  }

  function dropPanels(key) {
    [$("cz-method-forms"), $("cz-method-where")].forEach((slot) => {
      const old = slot && slot.querySelector(`[data-method="${key}"]`);
      if (old) old.remove();                          // a panel still bound to the earlier registration would show state the request never sends
    });
  }

  function unregister(key) {
    const plug = plugins[key];
    if (plug) pluginInfo.delete(plug);
    delete plugins[key];
    const at = pluginKeys.indexOf(key);
    if (at >= 0) pluginKeys.splice(at, 1);
    rebuildGroups();
    dropPanels(key);
    if (S.products.length) refreshProducts();         // its products are no longer offered
  }

  // Draws the method's panel (and its Where card, when it owns the geography) and binds it. A panel that throws is taken out again: the
  // dock must open whatever a plugin does. Returns whether the method is still registered.
  function mountPlugin(name) {
    const host = $("cz-method-forms");
    const plug = plugins[name];
    if (!host || !plug || host.querySelector(`[data-method="${name}"]`)) return !!plug;
    try {
      const panel = document.createElement("div");
      panel.className = "cz-method-panel hidden";
      panel.dataset.method = name;
      panel.innerHTML = plug.formHtml();
      host.appendChild(panel);
      const slot = $("cz-method-where");
      if (capsOf(plug).ownsGeography && slot) {
        const where = document.createElement("div");
        where.className = "cz-method-where-panel hidden";
        where.dataset.method = name;
        where.innerHTML = plug.whereHtml();
        slot.appendChild(where);
      }
      plug.bind(pluginCtx());
    } catch (e) {
      try { console.error(`[constanza] plugin "${name}": its panel could not be drawn: ${(e && e.message) || e}`); } catch (e2) { /* no console */ }
      unregister(name);
      return false;
    }
    applyMethodUI();
    return true;
  }

  // registerMethod(key, factory) -> true once the plugin is in; false (and a console error saying why) when it was refused. Registering a key again
  // replaces the earlier plugin.
  function registerMethod(key, factory) {
    const fail = (why) => { try { console.error(`[constanza] plugin "${key}": ${why}`); } catch (e) { /* no console */ } return false; };
    if (typeof key !== "string" || !KEY_RE.test(key) || isDataMethod(key)) return fail("not a usable method key (lower-case letters, digits and _; platform and generic belong to the data methods)");
    if (typeof factory !== "function") return fail("the factory is not a function");
    let plug;
    try { plug = factory(pluginCtx()); } catch (e) { return fail(`the factory threw: ${(e && e.message) || e}`); }
    const problems = checkPlugin(plug);
    if (problems.length) return fail(problems.join("; "));
    if (plugins[key]) { pluginInfo.delete(plugins[key]); dropPanels(key); }
    plugins[key] = plug;
    pluginInfo.set(plug, { key, caps: Object.assign({}, CONTRACT.caps, plug.caps || {}) });
    if (pluginKeys.indexOf(key) < 0) pluginKeys.push(key);
    rebuildGroups();
    if (!mountPlugin(key)) return false;
    if (S.products.length) refreshProducts();         // the dock was opened before this method arrived: its products are listed now
    return true;
  }

  // Show the selected product's own panel (and hide the Entravision audience block it replaces), and name the button for it. A method that owns its
  // geography also swaps the shared Where card for its own.
  let ownedBefore = false;
  function applyMethodUI() {
    if (!$("cz-method-forms")) return;
    const p = productByName(S.product), plug = pluginOf(p);
    const owns = !!(plug && capsOf(plug).ownsGeography);
    document.querySelectorAll(".cz-method-panel").forEach((el) => el.classList.toggle("hidden", !(plug && el.dataset.method === p.method)));
    document.querySelectorAll(".cz-method-where-panel").forEach((el) => el.classList.toggle("hidden", !(owns && el.dataset.method === p.method)));
    if ($("cz-sec-where")) $("cz-sec-where").classList.toggle("hidden", owns);
    $("cz-aud").classList.toggle("hidden", !!plug);
    if (!S.busy) $("cz-go").innerHTML = goLabel();
    if (plug && plug.onSelect) guard(plug, "onSelect", [p]);
    applyGoState();
    if (ownedBefore && !owns && S.geo.trim() && !S.resolve) resolveSoon(0);      // the shared box is back: what was typed in it is matched again
    ownedBefore = owns;
  }

  // The pull button's state from the method's own goState() (Meta: not connected yet): aria-disabled, with the reason beside it.
  let goOff = false;
  function goStateOf(plug) {
    if (!plug || !plug.goState) return { enabled: true };
    const s = guard(plug, "goState", [], null);
    return s && typeof s === "object" ? s : { enabled: true };
  }
  function applyGoState() {
    const go = $("cz-go"), why = $("cz-go-reason");
    if (!go) return;
    const gs = goStateOf(activePlugin());
    goOff = gs.enabled === false;
    const reason = goOff && typeof gs.reason === "string" ? gs.reason : "";
    if (!S.busy) go.setAttribute("aria-disabled", String(goOff));
    if (why) { why.textContent = reason; why.hidden = !reason; }
  }

  // ---- what the page and the plugins share: saved estimates (snapshots), their staleness, the card's request
  // Snapshot (one shape for every method that saves its estimates, stored as availsData[lid].forecast_snapshot):
  //   { v: 1, method, saved_at: ISO, request: <the estimate body that was sent>, result: <the answer>,
  //     line: { product_name, method, months, geo_text, target_text, budget, cpm },    the line's facts WHEN it was pulled: what staleness compares with
  //     legacy?: true }                                                              only on one adopted in memory from an older shape (never stored)
  // LineFacts (built by the page, window.constanzaHost.lineFacts):
  //   { id, product_name, method, label, months, flight_days | null, has_dates, geo_text, target_text, budget, cpm | null, hispanic | null }

  // Why a saved estimate no longer describes its line: what changed since it was pulled, in words for the banner (empty = still current).
  // The generic facts first, then what the method adds (staleReasons) and whether the audience picked in the dock moved (choicesChanged).
  const sameText = (a, b) => String(a == null ? "" : a) === String(b == null ? "" : b);
  const finite = (v) => v != null && v !== "" && Number.isFinite(Number(v));
  function staleParts(snap, line, withChoices) {
    const reasons = [];
    if (!snap || typeof snap !== "object" || !line || typeof line !== "object") return reasons;
    const was = snap.line && typeof snap.line === "object" ? snap.line : {};
    if (!sameText(was.product_name, line.product_name)) reasons.push("the product changed");
    if (Number(was.months) !== Number(line.months)) reasons.push("the flight length changed");
    if (!sameText(was.geo_text, line.geo_text)) reasons.push("the proposal’s geography changed");
    if (!sameText(was.target_text, line.target_text)) reasons.push("the line’s target changed");
    if (Math.abs((Number(was.budget) || 0) - (Number(line.budget) || 0)) > 0.01) reasons.push("the budget changed");
    if (finite(was.cpm) && finite(line.cpm) && Math.abs(Number(was.cpm) - Number(line.cpm)) > 0.01) reasons.push("the CPM changed");
    const plug = typeof snap.method === "string" ? plugins[snap.method] : null;
    if (plug) {
      if (plug.staleReasons) {
        const extra = guard(plug, "staleReasons", [snap, line], []);
        (Array.isArray(extra) ? extra : []).forEach((r) => { if (typeof r === "string" && r) reasons.push(r); });
      }
      const block = snap.request && typeof snap.request === "object" ? snap.request[snap.method] : null;
      if (withChoices && plug.choicesChanged && block && typeof block === "object" && guard(plug, "choicesChanged", [line.id, block], false)) reasons.push("the audience in Constanza changed");
    }
    return reasons;
  }
  const snapshotStale = (snapshot, line) => staleParts(snapshot, line, true);

  // Builds the snapshot of a pull (null unless the method saves its estimates). `body` = the request that was sent, `data` = the answer,
  // `line` = the line's facts read BEFORE the request went out. line.months is the request's own: a snapshot never claims a length it was not computed for.
  function makeSnapshot(method, body, data, line) {
    const plug = plugins[method];
    if (!plug || !capsOf(plug).persist || !body || typeof body !== "object" || !data || typeof data !== "object") return null;
    const was = line && typeof line === "object" ? line : {};
    const facts = {
      product_name: String(was.product_name != null ? was.product_name : (body.product_name || "")), method, months: body.months,
      geo_text: String(was.geo_text || ""), target_text: String(was.target_text || ""),
      budget: Number(was.budget) > 0 ? Number(was.budget) : 0, cpm: finite(was.cpm) ? Number(was.cpm) : null,
    };
    if (plug.snapshotLine && (!(facts.budget > 0) || facts.cpm == null)) {            // what the answer says it REALLY used, when the line had no figure of its own
      const used = guard(plug, "snapshotLine", [data, line], null);
      if (used && typeof used === "object") {
        if (!(facts.budget > 0) && Number(used.budget) > 0) facts.budget = Number(used.budget);
        if (facts.cpm == null && finite(used.cpm)) facts.cpm = Number(used.cpm);
      }
    }
    return { v: 1, method, saved_at: new Date().toISOString(), request: body, result: data, line: facts };
  }

  // A line saved in an older shape (Meta's availsData[lid].meta_forecast), read as a snapshot in memory (never written back until the planner pulls again).
  function adoptLegacy(method, entry, line) {
    const plug = plugins[method];
    if (!plug || !plug.legacy || !entry || typeof entry !== "object" || entry[plug.legacy.key] == null) return null;
    let snap = null;
    try { snap = plug.legacy.adopt(entry[plug.legacy.key], line || null); } catch (e) { logFault(plug, "legacy.adopt", e); return null; }
    if (snap == null) return null;
    if (!validSnapshot(snap) || snap.method !== method) { logOnce(`${method}.legacy.shape`, new Error(`it returned something that is not a ${method} snapshot`), `plugin "${method}": legacy.adopt`); return null; }
    return Object.assign({}, snap, { legacy: true });
  }

  // What gets stored as entry.geo_resolution for an Insert: the plugin's own (its places are not by_area), else the data path's.
  function geoInfo(data) {
    data = data || {};
    const plug = pluginFor(data);
    if (plug && plug.geoInfo) {
      const own = guard(plug, "geoInfo", [data], null);
      if (own && typeof own === "object") return own;
    }
    const aud = data.audience;
    return {
      method: data.geo_method,
      dmas: [...new Set((data.by_area || []).map((d) => d.dma).filter(Boolean))],
      unmatched_tokens: data.unmatched_geo_tokens || [],
      // the programmatic win rate that was applied to the figures an Insert writes (null when none): the Avails step says so beside them
      win_rate: data.win_rate ? data.win_rate.mid : null,
      // the Hispanic share the avails were narrowed to (null when not narrowed), so the share-of-voice helper can say it
      hispanic_share: aud && aud.hispanic && aud.hispanic.mode === "cut" ? aud.hispanic.share : null,
    };
  }

  // What a Step 05 card adds to the estimate request it sends for its line, as fields to spread over the base body: the method's block with the
  // line's CURRENT facts (cardRequest), and no geography text when the method picks its own places. {} for a data product or a method that asks
  // for the geography alone.
  function cardRequest(method, lid, line, snapshot) {
    const plug = plugins[method];
    if (!plug) return {};
    const out = {};
    if (plug.cardRequest) {
      const block = guard(plug, "cardRequest", [lid, line, snapshot], null);
      if (block && typeof block === "object" && Object.keys(block).length) out[method] = block;
    }
    if (capsOf(plug).ownsGeography) { out.geo_text = ""; out.geo_type_hint = null; }
    return out;
  }

  // ---- the Step 05 block, generically (what app.js draws for any product whose forecast is enabled)
  // methodState(method) -> { kind: "data" | "plugin" | "missing", ok, key, label, caps, note, legacyKey }. "missing" = a product that names a method whose
  // script did not load: the page shows `note` and no buttons, instead of an estimate from the wrong source. legacyKey: the key an older shape of the
  // method's saved estimate was kept under (null when it has none).
  function methodState(method) {
    const key = String(method == null ? "" : method);
    if (isDataMethod(key)) return { kind: "data", ok: true, key, label: "", caps: Object.assign({}, CONTRACT.caps), note: "", legacyKey: null };
    const plug = plugins[key];
    if (!plug) {
      return { kind: "missing", ok: false, key, label: key, caps: Object.assign({}, CONTRACT.caps), legacyKey: null,
        note: "This product’s estimate source didn’t load. Reload the page; if it still doesn’t show, ask an admin." };
    }
    return { kind: "plugin", ok: true, key, label: plug.sourceLabel || plug.channelLabel || key, caps: Object.assign({}, capsOf(plug)), note: "",
      legacyKey: plug.legacy ? plug.legacy.key : null };
  }

  const hasEstimate = (snapshot) => !!(snapshot && typeof snapshot === "object" && snapshot.result && typeof snapshot.result === "object");

  // The buttons of a line's block, before any estimate is on its card. A product that pulls from the proposal's geography has the two it always had.
  // A method that is asked in the dock ("saved" / "none") has one: Estimate in Constanza; once its card shows an estimate, the card's own action row
  // (renderCard) carries Insert, Recalculate and Change in Constanza.
  function blockButtons(method, line, snapshot) {
    const st = methodState(method);
    if (!st.ok) return "";
    const lid = `data-lid="${attrId(line && line.id != null ? line.id : "")}"`;
    if (st.caps.autoPull === "geo") {
      return `<button type="button" class="btn-secondary constanza-autocalc-btn" ${lid}>${MINI_ICON}Auto-calculate avails</button>`
        + `<button type="button" class="cz-link constanza-line-dock-btn" ${lid}>Open in Constanza</button>`;
    }
    return hasEstimate(snapshot) ? "" : `<button type="button" class="btn-primary constanza-line-dock-btn" ${lid}>Estimate in Constanza →</button>`;
  }

  // What the card says before any estimate exists (or when a saved one has nothing to show but its budget): the method's cardEmptyHtml. Hides the
  // element when the method has nothing to say. -> whether anything was drawn.
  function renderEmpty(el, method, line, snapshot) {
    const plug = plugins[method];
    const text = plug && plug.cardEmptyHtml ? guard(plug, "cardEmptyHtml", [line || null, snapshot || null], "") : "";
    if (!el) return false;
    if (typeof text !== "string" || !text) { el.style.display = "none"; el.innerHTML = ""; return false; }
    el.style.display = "block";
    el.innerHTML = text;
    return true;
  }

  // ------------------------------------------------------------------ the dock: markup
  function bubbleHtml() {
    return `
<button type="button" class="constanza-bubble" id="constanza-bubble" aria-label="Open Constanza, the avails puller" aria-haspopup="dialog" aria-expanded="false" aria-controls="constanza-dock">
  <span class="cz-avatar">${CHARACTER}</span>
  <span class="cz-badge-dot" aria-hidden="true"></span>
  <span class="cz-bubble-tip" role="tooltip">Constanza<small>Pull avails for any market</small></span>
</button>`;
  }

  // The head every section card shares (the kit's .aud-sec-head): the numbered badge, the title, the live one-line summary, an optional
  // count pill and an optional Clear link. `stem` names the ids the script updates: #cz-<stem>-sum, -count, -clear.
  // The Clear link of each card that can be cleared: [what it says, what a screen reader is told it does].
  const CLEAR_WHO = ["Clear", "Clear age and gender"], CLEAR_LANG = ["Turn off", "Turn off Hispanic targeting"], CLEAR_AUD = ["Clear", "Clear all interests"];
  const clearLink = (stem, clear, extraClass) => `<button type="button" class="aud-clear${extraClass ? " " + extraClass : ""}" id="cz-${stem}-clear" aria-label="${esc(clear[1])}" hidden>${esc(clear[0])}</button>`;
  // A card that folds is a <details> with a <summary> head, and a <summary> may not hold a button (a control inside the toggle). So its link is made
  // by foldClear() and put BESIDE the <details>, in a .cz-fold wrapper (constanza.css), and the head (`opts.fold`) keeps room for it with an invisible
  // twin of the link.
  const foldClear = (stem, clear) => clearLink(stem, clear, "cz-fold-clear");
  function secHeadInner(stem, title, opts) {
    opts = opts || {};
    return `<span class="aud-step" aria-hidden="true"></span>
      <div class="aud-sec-text"><h3 class="aud-sec-title cz-sec-title" id="cz-${stem}-t">${title}</h3><p class="aud-sec-sum cz-sum" id="cz-${stem}-sum"></p></div>
      ${opts.count ? `<span class="aud-count" id="cz-${stem}-count" hidden>0</span>` : ""}
      ${opts.clear ? (opts.fold ? `<span class="aud-clear cz-fold-twin" aria-hidden="true">${esc(opts.clear[0])}</span>` : clearLink(stem, opts.clear)) : ""}`;
  }

  // The age presets: the usual media-buying brackets. A preset only fills the two boxes (which still take any age); null = open-ended.
  const AGE_PRESETS = [[18, null], [18, 34], [18, 49], [25, 54], [35, 54], [55, null]];
  const presetLabel = (p) => (p[1] == null ? `${p[0]}+` : `${p[0]}–${p[1]}`);

  function dockHtml() {
    return `
<section class="constanza-dock hidden" id="constanza-dock" role="dialog" aria-label="Constanza avails puller">
  <header class="cz-head">
    <span class="cz-avatar lg">${CHARACTER}</span>
    <div class="cz-head-text">
      <h2 class="cz-title">Constanza</h2>
      <p class="cz-sub" id="cz-sub">Avails for any market, from the platform data we’ve loaded.</p>
    </div>
    <div class="cz-head-actions">
      <button type="button" class="cz-icon-btn cz-expand" id="cz-expand" title="Widen the panel" aria-label="Widen the panel">⤢</button>
      <button type="button" class="cz-icon-btn" id="cz-close" title="Close" aria-label="Close">✕</button>
    </div>
  </header>
  <div class="cz-body" id="cz-body">
    <div class="cz-form" id="cz-form">
     <div class="aud-builder cz-builder" id="cz-builder">

      <section class="cz-prod" aria-labelledby="cz-prod-l">
        <label class="cz-prod-label" id="cz-prod-l" for="cz-product-btn"><span>Product</span><span class="cz-hint" id="cz-product-count"></span></label>
        <div class="cz-picker" id="cz-picker">
          <button type="button" class="cz-picker-btn" id="cz-product-btn" aria-haspopup="listbox" aria-expanded="false" aria-controls="cz-picker-pop">
            <span class="cz-ptext"><span class="cz-pname" id="cz-pname">Loading products…</span><span class="cz-pmeta" id="cz-pmeta"></span></span><span class="cz-pcaret" aria-hidden="true"></span>
          </button>
          <div class="cz-picker-pop hidden" id="cz-picker-pop">
            <input type="search" class="cz-input cz-picker-search" id="cz-picker-search" placeholder="Search products…" autocomplete="off" aria-label="Search products"
                   role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls="cz-picker-list" aria-haspopup="listbox" />
            <div class="cz-picker-list" id="cz-picker-list" role="listbox" aria-label="Products"></div>
          </div>
        </div>
        <div id="cz-product-note"></div>
      </section>

      <section class="aud-sec" id="cz-sec-where" aria-labelledby="cz-where-t">
        <div class="aud-sec-head">${secHeadInner("where", "Where", { clear: ["Clear", "Clear the geography"] })}</div>
        <div class="aud-sec-body">
          <div class="aud-field">
            <label class="aud-label" for="cz-geo"><span>Geography</span><span class="aud-label-aside">market · county · city · ZIP · district</span></label>
            <input type="text" class="aud-input" id="cz-geo" placeholder="e.g. Adams County, WA" autocomplete="off" spellcheck="false" aria-describedby="cz-geo-hint" />
            <p class="aud-hint" id="cz-geo-hint">One place or a list: separate several with “;” or “and”.</p>
          </div>
          <div class="aud-quick" id="cz-geo-quick" hidden></div>
          <div id="cz-geo-chips"></div>
          <div id="cz-geo-help" aria-live="polite"></div>
          <div class="aud-field">
            <span class="aud-label" id="cz-geotype-l">Treat what I typed as</span>
            <div class="aud-seg" id="cz-geotype" role="group" aria-labelledby="cz-geotype-l">
              <button type="button" data-type="" aria-pressed="true" title="Work it out from what you typed">Auto</button>
              <button type="button" data-type="city" aria-pressed="false">City</button>
              <button type="button" data-type="county" aria-pressed="false">County</button>
              <button type="button" data-type="zip" aria-pressed="false">ZIP</button>
              <button type="button" data-type="congressional_district" aria-pressed="false">District</button>
              <button type="button" data-type="country" aria-pressed="false">Country</button>
            </div>
          </div>
        </div>
      </section>

      <div id="cz-method-where"></div>

      <section class="aud-sec has-value" id="cz-sec-when" aria-labelledby="cz-when-t">
        <div class="aud-sec-head">${secHeadInner("when", "When")}</div>
        <div class="aud-sec-body">
          <div class="aud-field">
            <label class="aud-label" for="cz-flight"><span>Flight length</span><span class="aud-label-aside" id="cz-flight-hint"></span></label>
            <div class="cz-flight-row">
              <div class="cz-stepper">
                <button type="button" id="cz-flight-dec" aria-label="Decrease">−</button>
                <input type="number" id="cz-flight" min="1" step="1" value="1" inputmode="numeric" aria-label="Flight length" />
                <button type="button" id="cz-flight-inc" aria-label="Increase">+</button>
              </div>
              <div class="aud-seg" id="cz-unit" role="group" aria-label="Unit">
                <button type="button" data-unit="months" aria-pressed="true">Months</button>
                <button type="button" data-unit="days" aria-pressed="false">Days</button>
              </div>
            </div>
          </div>
          <div class="aud-quick" id="cz-dates-quick" hidden><button type="button" id="cz-use-dates"></button></div>
          <div class="aud-field hidden" id="cz-total-field">
            <label class="aud-label" for="cz-total"><span>Impressions per month to spread</span><span class="aud-label-aside">by population share</span></label>
            <input type="number" class="aud-input" id="cz-total" min="0" step="1000" placeholder="e.g. 1,000,000" inputmode="numeric" aria-describedby="cz-total-note" />
            <p class="aud-hint cz-hintline" id="cz-total-note"></p>
          </div>
        </div>
      </section>

      <div class="cz-aud" id="cz-aud">
        <div class="cz-fold">
          ${foldClear("who", CLEAR_WHO)}
          <details class="aud-sec" id="cz-sec-who">
            <summary class="aud-sec-head cz-sec-head">${secHeadInner("who", "Who", { count: true, clear: CLEAR_WHO, fold: true })}</summary>
            <div class="aud-sec-body">
              <div class="aud-field">
                <span class="aud-label" id="cz-age-l"><span>Age</span><span class="aud-label-aside">any age from 0 to 120</span></span>
                <div class="aud-range" role="group" aria-labelledby="cz-age-l">
                  <div class="aud-range-selects">
                    <input type="number" class="aud-input" id="cz-age-min" min="0" max="120" placeholder="From" inputmode="numeric" aria-label="Age from" />
                    <span class="aud-range-sep">to</span>
                    <input type="number" class="aud-input" id="cz-age-max" min="0" max="120" placeholder="To" inputmode="numeric" aria-label="Age to" />
                  </div>
                  <div class="aud-quick" id="cz-age-presets" role="group" aria-label="Common age ranges">${AGE_PRESETS.map((p) =>
                    `<button type="button" data-min="${p[0]}" data-max="${p[1] == null ? "" : p[1]}" aria-pressed="false">${presetLabel(p)}</button>`).join("")}</div>
                </div>
                <p class="aud-hint cz-hintline" id="cz-age-note" aria-live="polite">Leave both boxes empty for all ages.</p>
              </div>
              <div class="aud-field">
                <span class="aud-label" id="cz-gender-l">Gender</span>
                <div class="aud-seg" id="cz-gender" role="group" aria-labelledby="cz-gender-l">
                  <button type="button" data-g="" aria-pressed="true">All</button>
                  <button type="button" data-g="female" aria-pressed="false">Women</button>
                  <button type="button" data-g="male" aria-pressed="false">Men</button>
                </div>
              </div>
            </div>
          </details>
        </div>

        <div class="cz-fold">
          ${foldClear("lang", CLEAR_LANG)}
          <details class="aud-sec" id="cz-sec-lang" open>
            <summary class="aud-sec-head cz-sec-head">${secHeadInner("lang", "Language &amp; heritage", { clear: CLEAR_LANG, fold: true })}</summary>
            <div class="aud-sec-body">
              <label class="cz-switch" for="cz-hispanic">
                <input type="checkbox" id="cz-hispanic" role="switch" aria-labelledby="cz-hispanic-name" aria-describedby="cz-hispanic-desc cz-hispanic-note" />
                <span class="cz-switch-text"><span class="cz-switch-name" id="cz-hispanic-name">Hispanic targeting</span><span class="cz-switch-desc" id="cz-hispanic-desc">How Entravision’s own products reach Spanish-speaking audiences</span></span>
              </label>
              <p class="aud-hint cz-hintline" id="cz-hispanic-note"></p>
              <div class="aud-notice aud-notice--info" id="cz-lang-why"><div class="aud-notice-body">The avails data has no language field, so Constanza can’t filter by language directly. It weights each area’s avails by that area’s real Hispanic share (U.S. Census ACS), the closest stand-in for a Spanish-speaking audience.</div></div>
            </div>
          </details>
        </div>

        <div class="cz-fold">
          ${foldClear("aud", CLEAR_AUD)}
          <details class="aud-sec" id="cz-sec-aud">
            <summary class="aud-sec-head cz-sec-head">${secHeadInner("aud", "Audience", { count: true, clear: CLEAR_AUD, fold: true })}</summary>
            <div class="aud-sec-body">
              <div class="aud-field">
                <label class="aud-label" for="cz-interest-q"><span>Interests</span><span class="aud-label-aside" id="cz-interest-hint"></span></label>
                <div class="aud-ta">
                  <input type="search" class="aud-input" id="cz-interest-q" placeholder="Search — soccer, dentist, new parents…" autocomplete="off"
                         role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="cz-interest-results" aria-haspopup="listbox" />
                  <div class="aud-ta-list" id="cz-interest-results" role="listbox" aria-label="Matching audiences" hidden></div>
                </div>
                <div class="aud-chips" id="cz-interest-chips"></div>
                <p class="aud-hint cz-hintline" id="cz-interest-note"></p>
                <button type="button" class="aud-browse" id="cz-browse" aria-haspopup="dialog"><span class="aud-browse-label">Browse all audiences</span><span class="aud-browse-count" id="cz-browse-count" hidden></span></button>
              </div>
            </div>
          </details>
        </div>
      </div>

      <div id="cz-method-forms"></div>
     </div>
    </div>
    <div class="cz-result" id="cz-result"></div>
  </div>
  <div class="cz-sr" id="cz-status" role="status" aria-live="polite"></div>
  <footer class="cz-foot">
    <p class="cz-go-reason" id="cz-go-reason" hidden></p>
    <button type="button" class="cz-reset" id="cz-reset">Reset</button>
    <button type="button" class="btn-primary" id="cz-go" aria-describedby="cz-go-reason">Pull avails →</button>
  </footer>
</section>`;
  }

  // ------------------------------------------------------------------ the dock: behaviour
  function mount() {
    if ($("constanza-bubble")) return;
    const root = document.createElement("div");
    root.id = "constanza-root";
    root.innerHTML = bubbleHtml() + dockHtml();
    document.body.appendChild(root);
    bind();
    restoreSections();
    Object.keys(plugins).forEach(mountPlugin);
    applyWide();
    refreshAudienceUI(); refreshWhere(); refreshGeoQuick();
    renderResultWelcome();
  }

  // Which of the folding cards the planner left open last time (kept per browser). Language & heritage starts open: most campaigns need it.
  const SECTION_DEFAULTS = { "cz-sec-who": false, "cz-sec-lang": true, "cz-sec-aud": false };
  function restoreSections() {
    const saved = store.get().sections || {};
    document.querySelectorAll("#cz-aud details.aud-sec").forEach((d) => { d.open = d.id in saved ? !!saved[d.id] : !!SECTION_DEFAULTS[d.id]; });
  }

  function applyWide() {
    $("constanza-dock").classList.toggle("wide", S.wide);
    const b = $("cz-expand");
    b.textContent = S.wide ? "⤡" : "⤢";
    b.title = b.ariaLabel = S.wide ? "Narrow the panel" : "Widen the panel";
  }

  function whenCatalogReady(fn, tries) {
    tries = tries || 0;
    if (typeof state !== "undefined" && state.catalog) return fn();
    if (tries < 50) { setTimeout(() => whenCatalogReady(fn, tries + 1), 200); return; }
    $("cz-pname").textContent = "Products couldn’t be loaded";
    $("cz-product-note").innerHTML = `<p class="cz-note cz-warn">The product list isn’t available. Reload the page, or check your connection.</p>`;
  }

  function refreshProducts() {
    S.products = loadProducts();
    $("cz-product-count").textContent = S.products.length ? `${S.products.length} with avails data` : "";
    if (!S.product || !S.products.some((p) => p.name === S.product)) {
      const first = S.products.find((p) => p.series && p.series.key === "connect-default")
        || S.products.find((p) => p.channel === "ctv" && p.series && p.series.relation === "primary") || S.products[0];
      S.product = first ? first.name : null;
    }
    updateProductUI();
  }

  const productByName = (name) => S.products.find((p) => p.name === name) || null;

  function updateProductUI() {
    refreshAudienceUI();                                                        // Who, Language & heritage and Audience follow the product (its Hispanic default)
    const p = productByName(S.product);
    $("cz-pname").textContent = p ? p.name : (S.products.length ? "Choose a product…" : "Loading products…");
    $("cz-pname").title = p ? p.name : "";
    $("cz-pmeta").textContent = p ? channelLabelOf(p.channel) : "";
    const note = $("cz-product-note");
    if (!p) { note.innerHTML = ""; return; }
    const bits = [`<span class="cz-hint">${esc(seriesMeta(p.series, p.method))}</span>`];
    let html = `<p class="cz-note">${bits.join("")}</p>`;
    if (isLiveSports(p.series) && p.series.period_label) {
      html = `<p class="cz-note"><span class="cz-badge cz-period">📅 ${esc(p.series.period_label)}</span></p>` + `<p class="cz-note">${esc(seriesMeta(p.series, p.method))} — the flight dates aren’t checked against this window.</p>`;
    } else if (p.series && p.series.relation === "baseline_proxy") {
      html += `<p class="cz-note">Uses <strong>${esc(p.series.title || p.series.key)}</strong> as its baseline.${p.series.note ? " " + esc(p.series.note) : ""}</p>`;
    } else if (p.series && p.series.data_basis === "extrapolated") {
      html += `<p class="cz-note">Markets outside the supplied sample are extended from the Connect avails, so their range is wider.</p>`;
    }
    note.innerHTML = html;
    updateTotalField();
    applyMethodUI();
  }

  // ---- picker
  function matchesQuery(p, words) {
    if (!words.length) return true;
    const hay = `${p.name} ${p.series ? p.series.title : ""} ${channelLabelOf(p.channel)}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  }

  function renderPickerList() {
    const words = $("cz-picker-search").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const groups = {};
    S.products.filter((p) => matchesQuery(p, words)).forEach((p) => { (groups[p.channel] = groups[p.channel] || []).push(p); });
    pickerItems = [];
    const html = channelOrder().filter((c) => groups[c]).map((c) => {
      const opts = groups[c].map((p) => {
        const idx = pickerItems.push(p) - 1;
        const meta = [seriesMeta(p.series, p.method), isLiveSports(p.series) && p.series.period_label ? p.series.period_label : ""].filter(Boolean).join(" · ");
        return `<div class="cz-option" role="option" id="cz-opt-${idx}" data-idx="${idx}" aria-selected="${p.name === S.product}"><span class="cz-option-name">${esc(p.name)}</span><span class="cz-option-meta">${esc(meta)}</span></div>`;
      }).join("");
      return `<div class="cz-group-label" role="presentation">${esc(channelLabelOf(c))}</div>${opts}`;
    }).join("");
    $("cz-picker-list").innerHTML = html || `<div class="cz-empty">No product matches “${esc($("cz-picker-search").value)}”.</div>`;
    pickerActive = Math.max(0, pickerItems.findIndex((p) => p.name === S.product));
    markPickerActive(false);
  }

  function markPickerActive(scroll) {
    const opts = $("cz-picker-list").querySelectorAll(".cz-option");
    opts.forEach((o) => o.classList.toggle("cz-active", Number(o.dataset.idx) === pickerActive));
    const cur = $("cz-picker-list").querySelector(`.cz-option[data-idx="${pickerActive}"]`);
    if (cur && scroll) cur.scrollIntoView({ block: "nearest" });
    const search = $("cz-picker-search");
    if (cur) search.setAttribute("aria-activedescendant", cur.id); else search.removeAttribute("aria-activedescendant");
  }

  function openPicker() {
    $("cz-picker-pop").classList.remove("hidden");
    $("cz-product-btn").setAttribute("aria-expanded", "true");
    $("cz-picker-search").value = "";
    renderPickerList();
    $("cz-picker-search").focus();
    markPickerActive(true);
  }

  function closePicker(refocus) {
    if ($("cz-picker-pop").classList.contains("hidden")) return;
    $("cz-picker-pop").classList.add("hidden");
    $("cz-product-btn").setAttribute("aria-expanded", "false");
    $("cz-picker-search").removeAttribute("aria-activedescendant");
    if (refocus) $("cz-product-btn").focus();
  }

  function chooseProduct(name) {
    if (name !== S.product) S.hispanic = null;       // a Hispanic choice was about the product it was made for: the new one starts from ITS default
    S.product = name;
    store.set({ product: name });
    updateProductUI();
    updateFlightHint();
    closePicker(true);
    markStale();
  }

  // ---- geography
  function setGeo(text, meta) {
    S.geo = text;
    $("cz-geo").value = text;
    if (meta && meta.geoType !== undefined && meta.geoType !== S.geoType) setGeoType(meta.geoType, true);
    markStale();
    resolveSoon(0);
  }

  function setGeoType(type, silent) {
    S.geoType = type;
    $("cz-geotype").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.type === type)));
    if (!silent) { markStale(); resolveSoon(0); }
  }

  function resolveSoon(ms) {
    clearTimeout(resolveTimer);
    if (ownsGeography()) return;                  // the method picks its own places: the shared box is hidden and nothing in it is matched
    if (!S.geo.trim()) {                          // an emptied box: nothing to match, and what was shown for the old text goes at once
      S.resSeq++; S.resolve = null; S.resolving = false; S.resolveFailed = false;
      renderGeoStatus();
      return;
    }
    S.resolving = true;                           // the summary says "Matching…" from the first keystroke, not from when the request goes out
    refreshWhere();
    refreshGeoQuick();
    resolveTimer = setTimeout(resolveNow, ms == null ? 380 : ms);
  }

  async function resolveNow() {
    if (ownsGeography()) return;
    const text = S.geo.trim();
    const seq = ++S.resSeq;
    if (!text) { S.resolve = null; S.resolving = false; S.resolveFailed = false; renderGeoStatus(); return; }
    S.resolving = true;
    let r;
    try { r = await api("/api/constanza/resolve", { geo_text: text, geo_type_hint: S.geoType || null }); } catch (e) { r = { ok: false, data: {} }; }
    if (seq !== S.resSeq) return;                 // a newer keystroke superseded this answer
    S.resolving = false;
    S.resolveFailed = !r.ok;
    S.resolve = r.ok ? r.data : null;
    renderGeoStatus();
  }

  // The proposal's own geography (what the dock fills in when nothing is typed), for the "use it again" pill.
  function proposalGeo() {
    try {
      if (typeof _effectiveGeo !== "function" || typeof state === "undefined" || !state.parsed) return "";
      return String(_effectiveGeo(state.activeTierLabel) || "").trim();
    } catch (e) { return ""; }
  }

  const EXAMPLE_GEOS = [["National", "National"], ["Texas", "Texas"], ["Phoenix", "Phoenix"], ["90210", "ZIP 90210"]];

  // The one-click picks under the box: the proposal's geography (when the box says something else) and, while it is empty, a few examples.
  function refreshGeoQuick() {
    const box = $("cz-geo-quick");
    if (!box || ownsGeography()) return;
    const typed = S.geo.trim(), mine = proposalGeo(), btns = [];
    if (mine && mine !== typed) btns.push(`<button type="button" data-use-proposal="1" title="${esc(mine)}">Use the proposal’s geography${mine.length <= 28 ? ` · ${esc(mine)}` : ""}</button>`);
    if (!typed) EXAMPLE_GEOS.forEach(([text, label]) => btns.push(`<button type="button" data-example="${esc(text)}">${esc(label)}</button>`));
    const html = btns.length ? `<span class="aud-quick-label">${typed ? "Quick pick" : "Try"}</span>${btns.join("")}` : "";
    if (box.dataset.sig !== html) { box.dataset.sig = html; box.innerHTML = html; }
    box.hidden = !btns.length;
  }

  // The Where card's head: what the typed place came to (areas and people), the Clear link, the filled badge.
  function refreshWhere() {
    const phase = S.resolving ? "busy" : (S.resolveFailed ? "failed" : "");
    setSection("where", whereSummary(S.geo, S.resolve, phase), { clearable: !!S.geo.trim() });
  }

  function renderGeoStatus() {
    const chipsEl = $("cz-geo-chips"), helpEl = $("cz-geo-help");
    const r = S.resolve;
    refreshGeoQuick();
    refreshWhere();
    if (!r) {
      chipsEl.innerHTML = "";
      helpEl.innerHTML = "";
      updateTotalField();
      return;
    }
    const areas = r.areas || [];
    const shown = areas.slice(0, 6);
    let chips = shown.map((a) => areaChipHtml(a)).join("");
    if (areas.length > shown.length) chips += `<span class="aud-chip">+${areas.length - shown.length} more</span>`;
    // (how many areas and people is in the card's summary line; only what that line does not say goes here)
    const extra = [r.geo_confidence === "medium" ? "Spans more than one county or market." : "", r.geo_confidence === "low_confidence_international" ? "Outside the U.S." : ""].filter(Boolean).join(" ");
    chipsEl.innerHTML = areas.length ? `<div class="aud-chips">${chips}</div>${extra ? `<p class="aud-hint">${esc(extra)}</p>` : ""}` : "";
    const notes = (r.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
    mountGeoHelp(helpEl, r, { getText: () => S.geo, setText: (next, meta) => setGeo(next, meta) });
    if (notes) helpEl.insertAdjacentHTML("beforeend", `<ul class="cz-list">${notes}</ul>`);
    updateTotalField();
  }

  // A matched place: a green chip with what it is (TV market, county...) and how many people live there.
  function areaChipHtml(a) {
    const name = a.label || a.dma || "";
    const kind = GEO_TYPE_LABEL[a.source_type] || a.source_type || "";
    const meta = [kind, a.area_population != null ? compact(a.area_population) : ""].filter(Boolean).join(" · ");
    const title = `${kind}${a.dma && a.dma !== a.label ? " · " + a.dma : ""}`;
    return `<span class="aud-chip aud-chip--ok cz-areachip" title="${esc(title)}"><span class="cz-tick" aria-hidden="true">✓</span><span class="aud-chip-label">${esc(name)}</span>${meta ? `<span class="aud-chip-meta">${esc(meta)}</span>` : ""}</span>`;
  }

  // ---- flight
  function contextFlight() {
    try {
      if (typeof state === "undefined" || !state.parsed || typeof _effectiveStartDate !== "function" || typeof _mbParseDate !== "function") return null;
      const a = _mbParseDate(_effectiveStartDate(state.activeTierLabel));
      const b = _mbParseDate(_effectiveEndDate(state.activeTierLabel));
      if (!a || !b || b < a) return null;
      const days = Math.round((b - a) / 86400000) + 1;
      const months = typeof _mbMonthsBetween === "function" ? _mbMonthsBetween(a, b).length : Math.max(1, Math.round(days / 30.4));
      const f = (d) => d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
      return { days, months: Math.min(months, 120), label: `${f(a)} – ${f(b)}` };
    } catch (e) { return null; }
  }

  function updateFlightHint() {
    const weekly = (productByName(S.product) || {}).series;
    $("cz-flight-hint").textContent = S.unit === "days" ? `≈ ${(S.flight / 7).toFixed(1)} weeks` : `≈ ${(S.flight * 4.348).toFixed(1)} weeks`;
    if (!weekly || weekly.unit !== "weekly") $("cz-flight-hint").textContent = S.unit === "days" ? `≈ ${(S.flight / 30.4375).toFixed(1)} months` : "";
    setSection("when", { text: whenSummary(S.flight, S.unit), set: true });
  }

  // Commits a value: clamps it, writes it into the box and updates the hint. Used by the +/- buttons, the unit switch, "use the
  // proposal's dates" and when the box loses focus - NOT while the planner is typing (see the input handler). `months`: the line's calendar months to
  // keep sending (only for "use the proposal's dates", whose days ARE the line's flight); any other edit is the planner's own length.
  function setFlight(value, unit, months) {
    S.monthsOverride = months == null ? null : months;      // (open() sets the line's months again, after its own call)
    S.unit = unit || S.unit;
    S.flight = Math.max(1, Math.round(Number(value) || 1));
    $("cz-flight").value = S.flight;
    $("cz-unit").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.unit === S.unit)));
    updateFlightHint();
    refreshContextChip();
    markStale();
  }

  // The calendar months of the line the dock is open for, when the method it is open for asks for them (caps.lineMonths); else null.
  function lineMonthsToSend() {
    const plug = activePlugin(), line = lineFactsFor(S.lineId);
    return plug && capsOf(plug).lineMonths && line && Number.isInteger(line.months) && line.months >= 1 ? Math.min(line.months, 120) : null;
  }

  // "Use the proposal's dates": a one-click pick under the stepper while a proposal is loaded; it shows as chosen once the flight IS those days.
  function refreshContextChip() {
    const chip = $("cz-use-dates"), box = $("cz-dates-quick"), ctx = contextFlight();
    if (!ctx) { box.hidden = true; return; }
    box.hidden = false;
    chip.textContent = `Use the proposal’s dates · ${ctx.label} (${ctx.days} days)`;
    chip.dataset.days = ctx.days;
    chip.setAttribute("aria-pressed", String(S.unit === "days" && S.flight === ctx.days));
  }

  // ---- population-share fallback field
  function updateTotalField() {
    const p = productByName(S.product);
    const intl = S.resolve && S.resolve.geo_confidence === "low_confidence_international";
    const need = !pluginOf(p) && !!((p && p.method === "generic") || intl || S.needTotal);
    $("cz-total-field").classList.toggle("hidden", !need);
    $("cz-total-note").textContent = intl
      ? "Platform data only exists for the U.S. Enter the impressions you want spread across the country by population (low confidence)."
      : (p && p.method === "generic" ? "This product has no platform avails data — enter the monthly impressions to spread across the geography by population." : "");
  }

  // ---- the sections' live summaries
  // Each numbered card says in one line what it holds, so a collapsed card still tells the planner what the pull will use. These are pure
  // (they read only their arguments): tests/test_constanza_frontend.py runs them without a browser.
  const GENDER_LABEL = { male: "Men", female: "Women" };

  // 0.31 -> "31%", 0.079 -> "7.9%", 0.0053 -> "0.5%": one decimal under 10%, where a whole number would hide most of the rate
  const rateText = (r) => (r == null || isNaN(r) ? "" : r >= 0.1 ? `${Math.round(r * 100)}%` : `${Math.round(r * 1000) / 10}%`);

  function ageText(min, max) {
    const a = min === "" || min == null ? null : Number(min), b = max === "" || max == null ? null : Number(max);
    if (a != null && b != null) return `${a}–${b}`;
    if (a != null) return `${a}+`;
    if (b != null) return `Up to ${b}`;
    return "";
  }

  // resolve: the /resolve answer for what is typed (or null); phase: "busy" (a request is out), "failed" (it did not come back) or "".
  function whereSummary(geo, resolve, phase) {
    const text = String(geo || "").trim();
    if (!text) return { text: "Type a place to start", set: false };
    if (phase === "busy") return { text: "Matching what you typed…", set: false };
    if (!resolve) return { text: phase === "failed" ? "Couldn’t check this place just now" : "Matching what you typed…", set: false };
    const areas = resolve.areas || [];
    if (!areas.length) return { text: (resolve.ambiguous || []).length ? "Pick the place you mean" : "No match yet — see the suggestions", set: false };
    const pop = areas.reduce((s, a) => s + (a.area_population || 0), 0);
    const more = (resolve.ambiguous || []).length ? " · one more needs your pick" : "";
    return { text: `${areas.length} area${areas.length === 1 ? "" : "s"} · about ${compact(pop)} people${more}`, set: true };
  }

  const whenSummary = (flight, unit) => `${flight} ${unit === "days" ? (flight === 1 ? "day" : "days") : (flight === 1 ? "month" : "months")}`;

  function whoSummary(ageMin, ageMax, gender) {
    const parts = [GENDER_LABEL[gender] || "", ageText(ageMin, ageMax)].filter(Boolean);
    return parts.length ? { text: parts.join(" · "), set: true } : { text: "Everyone, all ages", set: false };
  }

  // p: the product (or null); on: whether the cut will run; chosen: the planner made the choice (false = the product's default)
  function languageSummary(p, on, chosen) {
    if (p && p.hispanicAlready) return { text: "Built from Hispanic audiences", set: true };
    if (!on) return { text: "Not applied", set: false };
    return { text: chosen ? "Hispanic targeting on" : "Hispanic targeting on · default", set: true };
  }

  function interestsSummary(list) {
    if (!list.length) return { text: "No interests chosen", set: false };
    const names = list.map((i) => i.label);
    return { text: names.length > 2 ? `${names.slice(0, 2).join(", ")} +${names.length - 2}` : names.join(", "), set: true };
  }

  // Draws one section's head: the summary line, the filled badge, the count pill and the Clear link.
  function setSection(stem, sum, opts) {
    const sec = $(`cz-sec-${stem}`), line = $(`cz-${stem}-sum`);
    if (!sec || !line) return;
    opts = opts || {};
    line.textContent = sum.text;
    line.classList.toggle("is-set", !!sum.set);
    sec.classList.toggle("has-value", !!sum.set);
    const count = $(`cz-${stem}-count`), clear = $(`cz-${stem}-clear`);
    if (count) { count.textContent = opts.count || 0; count.hidden = !opts.count; }
    if (clear) clear.hidden = !opts.clearable;
  }

  // ---- audience
  const MAX_INTERESTS = 10;                                      // the server's limit (_CONSTANZA_MAX_INTERESTS)

  // Hispanic targeting for the product in the dock: the planner's choice when they made one, else the product's own default.
  function hispanicOn() {
    const p = productByName(S.product);
    if (p && p.hispanicAlready) return true;          // its data IS a Hispanic audience: it cannot be switched off
    return S.hispanic === null ? !!(p && p.hispanicDefault) : S.hispanic;
  }

  const whoCount = () => (S.ageMin !== "" || S.ageMax !== "" ? 1 : 0) + (S.gender ? 1 : 0);

  function refreshHispanicUI() {
    const box = $("cz-hispanic");
    if (!box) return;
    const p = productByName(S.product);
    const on = hispanicOn();
    box.checked = on;
    box.disabled = !!(p && p.hispanicAlready);
    let note;
    if (p && p.hispanicAlready) note = "This product’s data is already built from Hispanic audiences, so no further cut is applied; an age or gender cut is read against the Hispanic population.";
    else if (on) note = `Narrows the avails to each area’s real Hispanic share (Census ACS)${S.hispanic === null && p && p.hispanicDefault ? " — on by default for this product" : ""}. Assumes Hispanic households use the platform in proportion to their population.`;
    else note = "Narrow the avails to the Hispanic audience, using each area’s Census Hispanic %.";
    $("cz-hispanic-note").textContent = note;
    setSection("lang", languageSummary(p, on, S.hispanic !== null), { clearable: on && !(p && p.hispanicAlready) });
  }

  // The age presets and the gender buttons show what the state holds (the two boxes are the planner's own typing: never rewritten here).
  function refreshWho() {
    const norm = (v) => (v === "" ? "" : String(Number(v)));
    const min = norm(S.ageMin), max = norm(S.ageMax);
    $("cz-age-presets").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.min === min && b.dataset.max === max)));
    $("cz-gender").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.g === S.gender)));
    const backwards = S.ageMin !== "" && S.ageMax !== "" && Number(S.ageMin) > Number(S.ageMax);
    const note = $("cz-age-note");
    note.textContent = backwards ? "The “from” age is above the “to” age. Swap them before you pull avails." : "Leave both boxes empty for all ages.";
    note.classList.toggle("cz-warn-text", backwards);
    setSection("who", whoSummary(S.ageMin, S.ageMax, S.gender), { count: whoCount(), clearable: whoCount() > 0 });
  }

  // Puts an age range in the two boxes (they still take any age 0-120); "" leaves that end open.
  function setAge(min, max) {
    S.ageMin = min == null ? "" : String(min);
    S.ageMax = max == null ? "" : String(max);
    $("cz-age-min").value = S.ageMin;
    $("cz-age-max").value = S.ageMax;
    refreshAudienceUI();
    markStale();
  }

  const interestFrom = (seg) => ({ id: seg.id, label: seg.label, supported: !!seg.supported, rate_all: seg.rate_all });

  function refreshAudienceUI() {
    refreshHispanicUI();
    refreshWho();
    const n = S.interests.length;
    $("cz-interest-chips").innerHTML = S.interests.map((i, idx) => {
      const rate = i.supported ? rateText(i.rate_all) : "no public rate";
      return `<span class="aud-chip ${i.supported ? "aud-chip--interest" : "aud-chip--warn"} cz-ichip"><span class="aud-chip-label">${esc(i.label)}</span><span class="aud-chip-meta cz-chipmeta">${esc(rate)}</span><button type="button" class="aud-chip-x" data-rm="${idx}" aria-label="Remove ${esc(i.label)}">×</button></span>`;
    }).join("");
    const notes = [];
    if (S.interests.some((i) => !i.supported)) notes.push("Audiences marked “no public rate” are listed for reference only — they won’t change the estimate.");
    if (S.interests.filter((i) => i.supported).length > 1) notes.push("Several interests are multiplied together as if they were independent, so the range widens with each one.");
    $("cz-interest-note").textContent = notes.join(" ");
    $("cz-interest-hint").textContent = n ? `${n} of ${MAX_INTERESTS} chosen` : `up to ${MAX_INTERESTS}`;
    setSection("aud", interestsSummary(S.interests), { count: n, clearable: n > 0 });
  }

  function addInterest(seg) {
    if (S.interests.some((i) => i.id === seg.id)) return;
    if (S.interests.length >= MAX_INTERESTS) { $("cz-interest-note").textContent = `At most ${MAX_INTERESTS} interests at a time.`; return; }
    S.interests.push(interestFrom(seg));
    $("cz-interest-q").value = "";
    hideInterestResults();
    refreshAudienceUI();
    markStale();
  }

  // The typeahead is open (a list of options, or a line saying there are none): Escape closes it and the dock stays.
  const interestPopupOpen = () => !$("cz-interest-results").hidden;

  function hideInterestResults() {
    $("cz-interest-results").hidden = true;
    $("cz-interest-q").setAttribute("aria-expanded", "false");
    $("cz-interest-q").removeAttribute("aria-activedescendant");
    interestHits = []; interestActive = -1;
  }

  function markInterestActive() {
    $("cz-interest-results").querySelectorAll(".aud-ta-opt").forEach((el, i) => {
      const on = i === interestActive;
      el.classList.toggle("is-active", on);
      el.setAttribute("aria-selected", String(on));
      if (on && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
    });
    const input = $("cz-interest-q");
    if (interestActive >= 0 && interestHits[interestActive]) input.setAttribute("aria-activedescendant", `cz-int-${interestActive}`);
    else input.removeAttribute("aria-activedescendant");
  }

  // `text` with the typed words marked, escaped piece by piece (the kit styles <mark> inside the list).
  function markHtml(text, q) {
    const words = String(q || "").trim().split(/\s+/).filter(Boolean).map(escRe);
    if (!words.length) return esc(text);
    return String(text).split(new RegExp(`(${words.join("|")})`, "gi")).map((part, i) => (i % 2 ? `<mark>${esc(part)}</mark>` : esc(part))).join("");
  }

  function interestOptionHtml(h, i, q) {
    const tip = h.supported ? `${h.source || ""}${h.source_date ? ", " + h.source_date : ""} — ${h.confidence} confidence` : "No defensible public rate was found for this audience";
    const grp = groupLabel(h.group);
    return `<div class="aud-ta-opt${h.supported ? "" : " cz-norate"}${i === interestActive ? " is-active" : ""}" role="option" id="cz-int-${i}" data-i="${i}" aria-selected="${i === interestActive}" title="${esc(tip)}">`
      + `<span class="aud-ta-text"><span class="aud-ta-name cz-ta-name">${markHtml(h.label, q)}</span>${grp ? `<span class="aud-ta-path">${esc(grp)}</span>` : ""}</span>`
      + `<span class="aud-ta-size cz-ta-rate">${h.supported ? `${esc(rateText(h.rate_all))} of adults` : "no public rate"}</span></div>`;
  }

  async function searchInterests() {
    const q = $("cz-interest-q").value.trim();
    if (!q) { hideInterestResults(); return; }
    let r;
    try {
      r = await api(`/api/constanza/interests?q=${encodeURIComponent(q)}&limit=8`);
    } catch (e) {
      r = { ok: false, data: {} };
    }
    if ($("cz-interest-q").value.trim() !== q) return;
    const box = $("cz-interest-results");
    $("cz-interest-q").setAttribute("aria-expanded", "true");
    if (!r.ok) {
      interestHits = []; interestActive = -1;
      box.hidden = false;
      box.innerHTML = `<div class="aud-ta-note">The audience search isn’t available right now. Try again in a moment.</div>`;
      markInterestActive();
      if (box.scrollIntoView) box.scrollIntoView({ block: "nearest" });
      return;
    }
    interestHits = (r.data.results || []);
    interestActive = interestHits.length ? 0 : -1;
    box.hidden = false;
    box.innerHTML = interestHits.length
      ? interestHits.map((h, i) => interestOptionHtml(h, i, q)).join("")
      : `<div class="aud-ta-note">Nothing matches “${esc(q)}”. Try a simpler word, or browse all audiences below.</div>`;
    markInterestActive();
    if (box.scrollIntoView) box.scrollIntoView({ block: "nearest" });                 // the list opens downwards: bring all of it into the dock's view
  }

  function groupLabel(key) {
    const g = S.taxonomy && S.taxonomy.groups.find((x) => x.key === key);
    return g ? g.label : "";
  }

  // A failed request is NOT remembered: the next open of "Browse all audiences" tries again.
  async function loadTaxonomy() {
    if (S.taxonomy) return S.taxonomy;
    try {
      const r = await api("/api/constanza/interest-taxonomy");
      if (r.ok) { S.taxonomy = r.data; return S.taxonomy; }
    } catch (e) { /* offline: fall through to the unavailable message */ }
    return { groups: [] };
  }

  // "Browse all audiences  90" - the count appears once the list has come (the dock asks for it quietly when it opens).
  function showBrowseCount() {
    const el = $("cz-browse-count"), n = S.taxonomy ? (S.taxonomy.groups || []).reduce((s, g) => s + g.segments.length, 0) : 0;
    if (!el) return;
    el.hidden = !n;
    el.textContent = n ? num(n) : "";
  }

  // The taxonomy as the window's rows. The keywords and company names go into `path`, which the window searches but does not draw
  // (the typeahead finds "Toyota" under Auto Intenders the same way).
  function interestItems(taxonomy) {
    const out = [];
    ((taxonomy && taxonomy.groups) || []).forEach((g) => (g.segments || []).forEach((s) => {
      out.push({ id: s.id, name: s.label, group: g.label, path: [g.label].concat(s.keywords || [], s.company_names || []),
        supported: !!s.supported, rate_all: s.supported ? s.rate_all : null, seg: s });
    }));
    return out;
  }

  const BROWSE_COLUMNS = [
    { key: "name", label: "Name" },
    { key: "group", label: "Group" },
    // an audience with no sourced rate stays in the list (the planner may pick it for reference) but says so, and sorts last either way
    { key: "rate", label: "Incidence rate", numeric: true, value: (it) => (it.supported ? it.rate_all : null), format: (v) => (v == null ? "No public rate" : rateText(v)) },
  ];

  // The window. What it hands back is only the CHANGES, so the picks made with the typeahead stay, and the same cap (10) applies.
  function openBrowse() {
    const SB = window.SegmentBrowser;
    if (!SB) return;
    SB.open({
      title: "Browse all audiences",
      subtitle: "Tick the audiences to narrow the avails to. The incidence rate is the share of U.S. adults who fit it; “no public rate” ones are kept for reference and never change the estimate.",
      noun: "audiences", nounSingular: "audience", searchPlaceholder: "Search audiences: soccer, dentist, new parents…",
      columns: BROWSE_COLUMNS,
      load: () => loadTaxonomy().then((t) => {
        showBrowseCount();
        const items = interestItems(t);
        return items.length ? items : { error: "The audience list isn’t available right now. Try again in a moment." };
      }),
      isChosen: (tab, item) => S.interests.some((i) => i.id === item.id),
      max: MAX_INTERESTS, chosenCount: () => S.interests.length,
      returnFocus: $("cz-browse"),
      onApply: ({ added, removed }) => {
        removed.forEach(({ item }) => { const at = S.interests.findIndex((i) => i.id === item.id); if (at >= 0) S.interests.splice(at, 1); });
        added.forEach(({ item }) => { if (S.interests.length < MAX_INTERESTS && !S.interests.some((i) => i.id === item.id)) S.interests.push(interestFrom(item.seg)); });
        refreshAudienceUI();
        markStale();
        announce(`${S.interests.length} of ${MAX_INTERESTS} interests chosen.`);
      },
    });
  }

  // ---- results
  // The form no longer asks for what this result was pulled with: say so on the card (the numbers are still the ones for what it
  // names). Judged by comparing the two requests (canonicalJson: the order of their keys never matters), so opening the dock again, or
  // changing a field and putting it back, shows nothing.
  // One banner (.cz-stale, first in the result) with two possible reasons: the line moved on since a RESTORED result was pulled (S.resultStale: budget,
  // months ... said in words, and only a new pull clears it), or the form no longer asks for what the result was pulled with.
  let lastDirty = null;
  let staleSaid = false;                            // the banner is up and a screen reader was told: a repaint, or one more change while it is stale, says nothing
  function staleBannerText(changed) {
    const line = Array.isArray(S.resultStale) && S.resultStale.length ? S.resultStale : null;
    if (line) return `The line changed since this was pulled (${line.join(", ")}) — pull again to update.`;
    return changed ? "The form has changed since this was pulled — pull again to update." : "";
  }

  function markStale() {
    const box = $("cz-result");
    if (!S.result || S.resultSig == null || !box) return;
    const changed = canonicalJson(requestBody()) !== S.resultSig;
    const text = staleBannerText(changed);
    const banner = box.querySelector(".cz-stale");
    if (text && !banner) box.insertAdjacentHTML("afterbegin", `<p class="cz-stale">${esc(text)}</p>`);
    else if (text && banner.textContent !== text) banner.textContent = text;
    else if (!text && banner) banner.remove();
    // The banner is a plain paragraph, not a live region (the result is not read out in full): the change of state is said once, in the status line - that the
    // result is out of date, and that it matches the choices again - never on every choice made while it is already one or the other.
    if (!!text !== staleSaid) { staleSaid = !!text; announce(text || "The result matches your choices again."); }
    tellCard(S.lineId, changed);
  }

  // The card behind the dock lives on the page: when the dock is open for a line that has a saved estimate, it is told whether the form differs from it
  // (only when that changes) so it can say "the audience in Constanza changed" without waiting for something else to redraw it. "Changes" includes the
  // estimate itself: the card's own Recalculate saves a new one, which the card then compares with (and reads clean), so the next edit is news again.
  function tellCard(lid, dirty) {
    const h = host();
    if (!lid || !h || typeof h.onDirty !== "function") return;
    const snap = snapshotFor(lid);
    if (!snap) return;
    const key = `${lid}|${dirty}|${snap.saved_at || ""}`;
    if (key === lastDirty) return;
    lastDirty = key;
    try { h.onDirty(lid, dirty); } catch (e) { /* the page's card is its own business */ }
  }

  // A short status line for screen readers (the result pane itself is not a live region: it would be read out in full).
  function announce(text) {
    const el = $("cz-status");
    if (el) el.textContent = text;
  }

  function renderResultWelcome() {
    $("cz-result").innerHTML = `<div class="cz-welcome"><span class="cz-welcome-ico" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>`
      + `<p class="cz-welcome-lead">Pick a product and a place, then <strong>Pull avails</strong>.</p><p class="cz-welcome-sub">Numbers come with the data they were modeled from.</p></div>`;
  }

  // "Live Sports - MLB · All 18+ | Registered to vote…" — enough to tell two lines of one product apart, short enough for a button.
  function lineLabel(li) {
    const product = String(li.product_name || "").replace(/^Entravision(?:\+| Plus)\s*(?:-\s*)?/i, "");
    const target = String(li.target_override || "").trim();
    return target ? `${product} · ${target.length > 34 ? target.slice(0, 33) + "…" : target}` : product;
  }

  function insertButtons(data, m) {
    if (typeof state === "undefined" || !state.lineItems || typeof applyConstanzaEstimate !== "function") return "";
    const lines = state.lineItems.filter((li) => li.product_name === data.product_name);
    if (!lines.length) return "";
    lines.sort((a, b) => (b.id === S.lineId) - (a.id === S.lineId));
    return lines.map((li) => `<button type="button" class="${li.id === S.lineId ? "btn-primary" : "btn-secondary"} cz-insert" data-lid="${esc(li.id)}">Insert into Avails — ${esc(lineLabel(li))}</button>`).join("");
  }

  function summaryText(data) {
    const m = summarize(data);
    const asked = S.resultCtx || { geo: S.geo.trim(), areas: [] };        // what THIS result was pulled for, not what the form says now
    const areas = asked.areas;
    const lines = [
      `Constanza estimate — ${data.product_name}`,
      // (a restored result whose form was edited since has no place of its own to name here: the method's lines below say it from the answer)
      ...(asked.geo ? [`Geography: ${asked.geo}${areas.length ? ` (${areas.slice(0, 6).join("; ")}${areas.length > 6 ? `; +${areas.length - 6} more` : ""})` : ""}`] : []),
      `Flight: ${m.months} month${m.months === 1 ? "" : "s"}${m.weeks ? ` (~${m.weeks.toFixed(1)} weeks)` : ""}`,
      `${m.aud ? "Audience impressions" : "Impressions"} per month (low / mid / high): ${num(m.month.low)} / ${num(m.month.mid)} / ${num(m.month.high)}`,
      `Flight total (low / mid / high): ${num(m.total.low)} / ${num(m.total.mid)} / ${num(m.total.high)}`,
    ];
    if (m.winRate) {
      const w = m.winRate;
      lines.push(`Win rate ${pct(w.mid)} applied: ${num(w.raw)} avails a ${w.unit}${w.forAudience ? " for this audience" : ""}${w.high > w.low ? " (bid opportunities)" : ""}; expected delivery ${num(w.delivered)} a ${w.unit}`
        + (w.high > w.low ? ` (${num(w.atLow)} at ${pct(w.low)}, ${num(w.atHigh)} at ${pct(w.high)})` : ""));
    }
    if (data.audience && data.audience.hispanic) {
      const h = data.audience.hispanic;
      lines.push(h.mode === "not_applied"
        ? "Hispanic targeting: not applied (no Census data for this geography)"
        : (h.mode === "already_targeted"
          ? "Hispanic targeting: this product’s data is already built from Hispanic audiences (no further cut applied)"
          : `Hispanic targeting: avails narrowed to each area’s Hispanic share${h.share != null ? ` (${pct(h.share)} of the people here are Hispanic, Census ${String(h.vintage || "ACS")})` : ""}`));
    }
    const plug = pluginFor(data);
    if (m.uniques != null && !plug) lines.push(`Unique IDs reached per month${m.proxy ? " (baseline product)" : ""}: ${num(m.uniques)}${m.perUnique ? ` (about ${m.perUnique.toFixed(1)} impressions each)` : ""}`);
    if (plug) {
      const own = guard(plug, "summaryLines", [data, asked], []);
      (Array.isArray(own) ? own : []).forEach((l) => lines.push(l));
    }
    if (data.series) lines.push(`Source: ${data.series.title || data.series.key}, version ${data.series.version}${data.series.period_label ? `; period: ${data.series.period_label}` : ""}`);
    notesOf(data).slice(0, 3).forEach((n) => lines.push(`Note: ${n}`));
    return lines.join("\n");
  }

  // Draws a result; a method that cannot read its own answer (a saved estimate that was edited, an old shape) leaves a plain sentence instead.
  function renderResult(data) {
    try {
      drawResult(data);
    } catch (e) {
      // (as renderCard: a method's answer may be a saved one of any shape, so anything that breaks drawing it is "couldn't be read"; a data answer's is a bug)
      if (!(e instanceof PluginFault) && !pluginFor(data)) throw e;
      if (!(e instanceof PluginFault)) logOnce("result", e, "a saved estimate could not be drawn in the dock");
      drawResultUnreadable();
    }
  }

  function drawResultUnreadable() {
    forgetResult();
    $("cz-result").innerHTML = `<div class="cz-error"><strong>The saved estimate couldn’t be read</strong>Estimate again to get a fresh one.</div>`;
    announce(UNREADABLE);
  }

  function drawResult(data) {
    const m = summarize(data);
    const rows = areaRows(data);
    const sorted = [...rows].sort((a, b) => b.imps_mid - a.imps_mid);
    const showAll = !!S.showAllAreas;
    const list = showAll ? sorted : sorted.slice(0, 8);
    const areaDivisor = m.weekly ? 1 : m.months;
    const tableRows = list.map((a) => `<tr><td>${esc(a.area_label)}${a.dma && a.dma !== a.area_label ? ` <span class="cz-dma">(${esc(a.dma)})</span>` : ""}</td><td>${(a.weight * 100).toFixed(1)}%</td><td>${num(a.imps_mid / areaDivisor)}</td></tr>`).join("");
    const main = m.weekly
      ? { label: `Flight total · ${m.weeks.toFixed(1)} weeks`, v: m.total }
      : { label: `${m.aud ? "Audience impressions" : "Impressions"} · per month`, v: m.month };
    const side = [];
    if (m.weekly) {
      side.push({ label: "Per week", v: num(m.week.mid), r: `${compact(m.week.low)} – ${compact(m.week.high)}` });
      if (m.months > 1) side.push({ label: "Per month", v: num(m.month.mid), r: `${compact(m.month.low)} – ${compact(m.month.high)}` });
    } else if (m.months > 1) {
      side.push({ label: `Flight total · ${m.months} mo`, v: num(m.total.mid), r: `${compact(m.total.low)} – ${compact(m.total.high)}` });
    }
    if (m.uniques != null) side.push({ label: m.proxy ? "Unique IDs / month (baseline product)" : "Unique IDs reached / month", v: num(m.uniques), r: m.perUnique ? `${m.perUnique.toFixed(1)} imps each` : "" });
    if (m.aud) side.push({ label: "Before the audience filter", v: num(m.baseMonthMid) + " imps / mo", r: "" });
    const notes = notesOf(data).map((n) => `<li>${esc(n)}</li>`).join("");
    const lineStale = Array.isArray(S.resultStale) && S.resultStale.length ? S.resultStale : null;      // a restored result whose line has moved on: no Insert for it
    const inserts = lineStale || !hasMonthlyMid(m) ? "" : insertButtons(data, m);
    const plug = pluginFor(data);                  // a result a forecast method brought (TikTok, Meta) draws its own headline and middle
    const headline = plug ? pluginHtml(plug, "headlineHtml", data, m) : `<div class="cz-headline">
        <div class="cz-metric main"><span class="cz-mlabel">${esc(main.label)}</span><span class="cz-mvalue">${num(main.v.mid)}</span><span class="cz-mrange">${compact(main.v.low)} – ${compact(main.v.high)} range</span></div>
        ${side.map((s) => `<div class="cz-metric"><span class="cz-mlabel">${esc(s.label)}</span><span class="cz-mvalue">${esc(s.v)}</span>${s.r ? `<span class="cz-mrange">${esc(s.r)}</span>` : ""}</div>`).join("")}
      </div>`;
    const before = plug ? "" : audienceBoxHtml(m.aud, m);
    const middle = plug ? pluginHtml(plug, "bodyHtml", data, m, S.resultCtx) : `<div class="cz-section-title">Where the impressions are</div>
      ${shareBarHtml(rows, rows.reduce((s, r) => s + r.imps_mid, 0))}
      ${rows.length ? `<table class="cz-table"><thead><tr><th>Area</th><th>Share</th><th>${m.weekly ? "Flight total" : "Per month"}</th></tr></thead><tbody>${tableRows}</tbody></table>` : ""}
      ${rows.length > 8 ? `<p class="cz-note"><button type="button" class="cz-link" id="cz-toggle-areas">${showAll ? "Show the top 8 only" : `Show all ${rows.length} areas`}</button></p>` : ""}`;
    const about = plug && plug.aboutHtml ? pluginHtml(plug, "aboutHtml", data) : provenanceHtml(data);
    const badges = badgesHtml(data);
    $("cz-result").innerHTML = `<div class="cz-card-head">
        <div class="cz-card-title">${esc(data.product_name)}</div>
        ${S.resultCtx && S.resultCtx.geo ? `<div class="cz-card-geo">${esc(S.resultCtx.geo)}</div>` : ""}
        <div class="cz-badges">${badges}</div>
      </div>
      ${headline}
      ${plug ? "" : winRateHtml(m)}
      ${before}
      ${unmatchedHtml(data)}
      ${middle}
      ${notes ? `<div class="cz-section-title">Notes</div><ul class="cz-list">${notes}</ul>` : ""}
      ${about}
      <div class="cz-actions">${inserts}<button type="button" class="btn-secondary" id="cz-copy">Copy summary</button></div>`;
    $("cz-result").querySelectorAll(".cz-insert").forEach((btn) => btn.addEventListener("click", () => {
      const stillThere = typeof state !== "undefined" && (state.lineItems || []).some((li) => li.id === btn.dataset.lid);
      // The snapshot goes to the one line this result was pulled for (it describes that line's numbers); another line gets the figures without it, and
      // the page then drops that line's own snapshot, which no longer describes what is in its Avails fields.
      const mine = btn.dataset.lid === S.resultLid && S.resultSnapshot ? S.resultSnapshot : undefined;
      const done = stillThere && applyConstanzaEstimate(btn.dataset.lid, m.month.mid, m.uniques, geoInfo(data), mine) !== false;
      btn.textContent = done ? "✓ Inserted into Avails" : "That line is no longer in the proposal";
      btn.disabled = true;
    }));
    const tog = $("cz-toggle-areas");
    if (tog) tog.addEventListener("click", () => { S.showAllAreas = !S.showAllAreas; renderResult(data); });
    $("cz-copy").addEventListener("click", async (ev) => {
      try { await navigator.clipboard.writeText(summaryText(data)); ev.target.textContent = "Copied ✓"; } catch (e) { ev.target.textContent = "Couldn’t copy"; }
      setTimeout(() => { ev.target.textContent = "Copy summary"; }, 1800);
    });
    markStale();                                  // a repaint (or a form that changed while the request was in flight) keeps the banner
  }

  function renderError(message) {
    $("cz-result").innerHTML = `<div class="cz-error"><strong>Couldn’t pull avails</strong>${esc(message)}</div>`;
    announce(`Couldn’t pull avails: ${message}`);
  }

  function scrollToResult() {
    const body = $("cz-body"), res = $("cz-result");
    if (!body || S.wide) return;
    body.scrollTo({ top: res.offsetTop - 8, behavior: "smooth" });
  }

  // ---- the pull
  // What the plugin adds to the request: its own block, `{ [key]: ... }` and nothing else (a product_name or months of its own never overrides the
  // dock's). A plugin that throws or returns something else adds nothing (the dock asks for this on every change of the form, so it must not be able to
  // break typing).
  function pluginPart(plug) {
    const part = guard(plug, "requestPart", [], null);
    const key = keyOf(plug);
    return part && typeof part === "object" && !Array.isArray(part) && Object.prototype.hasOwnProperty.call(part, key) ? { [key]: part[key] } : {};
  }

  // The request the form describes. A method that owns its geography sends none (the shared box is hidden and keeps what was typed in it); a method
  // that asks for the line's calendar months (caps.lineMonths) sends them, not the flight box's rounded days, while the box still shows what open()
  // put there (S.monthsOverride; any edit of the box clears it).
  function requestBody() {
    const days = S.unit === "days";
    const total = parseFloat(S.total);
    const plug = activePlugin();
    const caps = plug ? capsOf(plug) : CONTRACT.caps;
    const audience = !plug && (S.ageMin !== "" || S.ageMax !== "" || S.gender || S.interests.length) ? {
      age_min: S.ageMin === "" ? null : parseInt(S.ageMin, 10),
      age_max: S.ageMax === "" ? null : parseInt(S.ageMax, 10),
      gender: S.gender || null,
      interest_terms: S.interests.map((i) => i.label),
    } : null;
    return {
      product_name: S.product, geo_text: caps.ownsGeography ? "" : S.geo.trim(), geo_type_hint: caps.ownsGeography ? null : (S.geoType || null),
      months: caps.lineMonths && S.monthsOverride != null ? S.monthsOverride : (days ? Math.max(1, Math.round(S.flight / 30.4375)) : S.flight),
      flight_days: days ? S.flight : null,
      national_monthly_total: plug ? null : (total > 0 ? total : null),
      audience,
      ...(!plug && S.hispanic !== null && !(productByName(S.product) || {}).hispanicAlready ? { hispanic: S.hispanic } : {}),
      ...(plug ? pluginPart(plug) : {}),
    };
  }

  function setBusy(on) {
    S.busy = on;
    const go = $("cz-go");
    go.setAttribute("aria-disabled", String(on || goOff));    // not `disabled`: that would drop the keyboard focus of the person who just pressed it (pull() ignores a press while busy)
    go.innerHTML = on ? `<span class="cz-spinner"></span>Pulling…` : goLabel();
    $("constanza-dock").setAttribute("aria-busy", String(on));
  }

  // The planner's words for a refused request: the method's own (Meta names the pick a 422 was about), else the shared reading of the answer.
  function refusalText(plug, data, fallback, body) {
    if (plug && plug.errorText) {
      const own = guard(plug, "errorText", [data, fallback, body], "");
      if (typeof own === "string" && own) return own;
    }
    return errorText(data, fallback);
  }

  // Files the answer under the line it was pulled for (a method with caps.persist): only when that line is still in the proposal's active option and still
  // holds the product that was asked about - a dock whose product was switched to another SKU while S.lineId still names the old line must not put its
  // answer under it. -> the snapshot that was kept, or null.
  function persistResult(plug, lid, lineAtPull, body, data) {
    if (!plug || !capsOf(plug).persist || !lid || !lineAtPull || lineAtPull.product_name !== body.product_name) return null;
    const h = host();
    if (!h || typeof h.saveSnapshot !== "function") return null;
    const snap = makeSnapshot(keyOf(plug), body, data, lineAtPull);
    if (!snap) return null;
    try { return h.saveSnapshot(lid, snap) === false ? null : snap; } catch (e) { return null; }
  }

  async function pull() {
    if (S.busy) return;
    S.flight = Math.max(1, Math.round(Number($("cz-flight").value) || 1));
    S.total = $("cz-total").value;
    if (!S.product) { renderError("Choose a product first."); setMood("oops", 3000); return; }
    const plug = activePlugin();
    const caps = plug ? capsOf(plug) : CONTRACT.caps;
    if (!caps.ownsGeography && !S.geo.trim()) { renderError("Type a geography first — a TV market, county, city, ZIP or “National”."); setMood("oops", 3000); $("cz-geo").focus(); return; }
    const gs = goStateOf(plug);
    if (gs.enabled === false) {                                  // the button is aria-disabled and says why beside itself; a press only says it again
      announce(typeof gs.reason === "string" && gs.reason ? gs.reason : "This can’t be pulled right now.");
      setMood("oops", 3000);
      return;
    }
    if (plug && plug.canPull) {
      const ready = guard(plug, "canPull", [], { ok: true });
      if (!(ready && ready.ok)) { renderError((ready && ready.reason) || "Check the form and try again."); setMood("oops", 3000); return; }
    }
    // What this pull is for, read BEFORE the request goes out (the form and the page can change while it is in flight): the line, that line's facts
    // (what a saved estimate is later compared with) and what the method says it was asked.
    const lid = S.lineId;
    const lineAtPull = lineFactsFor(lid);
    setBusy(true); setMood("thinking");
    S.showAllAreas = false;
    const mine = ++S.pullSeq;                     // Reset (or a newer pull) makes this answer stale: it must not repaint the dock
    const asked = { geo: S.geo.trim(), areas: ((S.resolve && S.resolve.areas) || []).map((a) => a.label || a.dma).filter(Boolean) };
    if (plug && plug.context) asked.method = guard(plug, "context", [], null);         // what THIS result was asked with (the form may change afterwards)
    if (caps.ownsGeography) {                     // its place is the method's own, not the shared box's
      const place = asked.method && asked.method.geo;
      if (typeof place !== "string" || !place) logOnce(`${keyOf(plug)}.context.geo`, new Error("context() must say where (geo) when the method owns its geography"), `plugin "${keyOf(plug)}"`);
      asked.geo = typeof place === "string" ? place : "";
      asked.areas = [];
    }
    const body = requestBody();
    let r;
    try {
      r = await api("/api/constanza/estimate", body);
    } catch (e) {
      if (mine !== S.pullSeq) return;
      setBusy(false); renderError(String(e)); setMood("oops"); return;
    }
    if (mine !== S.pullSeq) return;
    setBusy(false);
    if (!r.ok) {
      const msg = refusalText(plug, r.data, `The request failed (${r.status}).`, body);
      renderError(msg);
      setMood("oops");
      if (plug && plug.onError) guard(plug, "onError", [r.status]);
      if (/national_monthly_total|population share|no forecast avails/i.test(msg)) { S.needTotal = true; updateTotalField(); }
      if (!caps.ownsGeography) resolveNow();                    // refresh the suggestions next to the geography box
      scrollToResult();
      return;
    }
    S.result = r.data;
    S.resultCtx = asked;
    S.resultSig = canonicalJson(body);            // (not JSON.stringify: a saved request comes back with its keys in another order)
    S.resultLid = lid;
    S.resultStale = null;
    S.resultSnapshot = persistResult(plug, lid, lineAtPull, body, r.data);
    renderResult(r.data);
    if (plug && plug.onResult) guard(plug, "onResult", [r.data]);
    announce(`Pulled ${r.data.product_name}: ${num(summarize(r.data).month.mid)} impressions per month.`);
    $("constanza-bubble").classList.add("cz-has-result");
    setMood("happy", 3200);
    scrollToResult();
  }

  // Forget the previous request's geography type, audience, total and result (a line's own request starts from nothing). `prevLid` is the Step 05 line
  // the dock WAS open for, `nextLid` the one it is open for now (or none). S.lineId is set FIRST, so a method that reads ctx.S.lineId / ctx.line() while
  // it hears about the change sees the new line; then every method is told: onLine(facts | null) where it has one (a per-line method switches to that
  // line's workspace, or keeps it when the SAME line is opened again), else reset(prevLid) - what every method did before onLine existed.
  function clearRequestState(prevLid, nextLid) {
    S.pullSeq++; S.resSeq++;
    clearTimeout(resolveTimer);
    setBusy(false);
    Object.assign(S, { geoType: "", total: "", ageMin: "", ageMax: "", gender: "", interests: [], hispanic: null, needTotal: false, resolving: false, resolveFailed: false,
      lineId: nextLid || null, monthsOverride: null });
    forgetResult();
    setGeoType("", true);
    $("cz-total").value = ""; $("cz-age-min").value = ""; $("cz-age-max").value = ""; $("cz-interest-q").value = "";
    const line = lineFactsFor(S.lineId);
    Object.values(plugins).forEach((plug) => { if (plug.onLine) guard(plug, "onLine", [line]); else guard(plug, "reset", [prevLid == null ? null : prevLid]); });
    refreshAudienceUI(); hideInterestResults(); renderResultWelcome(); updateTotalField(); applyGoState();
    $("constanza-bubble").classList.remove("cz-has-result");
  }

  // The result on the screen, and everything kept about it, goes.
  function forgetResult() {
    Object.assign(S, { result: null, resultCtx: null, resultSig: null, resultLid: null, resultSnapshot: null, resultStale: null });
    lastDirty = null; staleSaid = false;
  }

  // The dock is opened with no line (the bubble) while it was last open for one: the dock is on its own again. A method that keeps one workspace per
  // line hears onLine(null); a result a saving method brought for that line belonged to the line, and goes too (what stays on screen for the other
  // methods is as it always was).
  function leaveLine(prevLid) {
    S.lineId = null;
    if (prevLid == null) return;
    S.monthsOverride = null;
    const shown = S.result && S.resultLid != null ? pluginFor(S.result) : null;
    if (shown && capsOf(shown).persist) {
      forgetResult();
      renderResultWelcome();
      $("constanza-bubble").classList.remove("cz-has-result");
    }
    Object.values(plugins).forEach((plug) => { if (plug.onLine) guard(plug, "onLine", [null]); });
    applyGoState();
  }

  // A new proposal begins: nothing of the old one (its geography, its lines, a result for them) may survive in the dock.
  function onProposalReset() {
    Object.values(plugins).forEach((plug) => { if (plug.onProposalReset) guard(plug, "onProposalReset", []); });        // every workspace goes
    if (!$("constanza-bubble")) return;
    resetForm({ proposal: true });
  }

  function resetForm(opts) {
    const prevLid = S.lineId;
    S.pullSeq++; S.resSeq++;                      // discard any pull or resolve still in flight
    clearTimeout(resolveTimer);
    setBusy(false);
    Object.assign(S, { geo: "", geoType: "", flight: 1, unit: "months", total: "", ageMin: "", ageMax: "", gender: "", interests: [], hispanic: null, needTotal: false, resolve: null, resolving: false, resolveFailed: false, lineId: null, monthsOverride: null });
    forgetResult();
    $("cz-geo").value = ""; $("cz-total").value = ""; $("cz-age-min").value = ""; $("cz-age-max").value = ""; $("cz-interest-q").value = "";
    setGeoType("", true); setFlight(1, "months");
    $("cz-sub").textContent = "Avails for any market, from the platform data we’ve loaded.";
    // (S.lineId is already null: a method that keeps a workspace per line deletes the one of the line the dock WAS open for, `prevLid`; a new proposal has none)
    const was = opts && opts.proposal ? null : prevLid;
    Object.values(plugins).forEach((plug) => guard(plug, "reset", [was == null ? null : was]));
    refreshAudienceUI(); hideInterestResults(); renderGeoStatus(); renderResultWelcome(); updateTotalField();
    $("constanza-bubble").classList.remove("cz-has-result");
    setMood("idle");
    if (prevLid != null) tellCard(prevLid, false);      // that line's card no longer has an unpulled audience to report
  }

  // The saved estimate of the line the dock was just opened for, shown again with the choices that made it. The method puts the choices back into the
  // line's workspace (restore; it keeps edits the planner already made there), then the dock shows the result and says what no longer matches:
  // a changed line (budget, months ...: S.resultStale, and Insert is withdrawn) or a form that differs (the usual banner). The "form" signature is read
  // AFTER the restore, so an estimate whose choices are all back reads as current.
  function restoreLine() {
    const plug = activePlugin(), lid = S.lineId;
    if (!plug || !lid || !capsOf(plug).persist) return;
    const line = lineFactsFor(lid), snap = snapshotFor(lid);
    if (!line || !snap || line.product_name !== S.product || snap.method !== keyOf(plug)) return;
    if (!snap.result || typeof snap.result !== "object") return;                  // nothing to show (an estimate with only a budget): the form starts as the method has it
    const block = snap.request && typeof snap.request === "object" ? snap.request[snap.method] : null;
    if (block && typeof block === "object") guard(plug, "restore", [block]);
    // What this result was asked with is known from context() only while the form holds the saved choices again. A method may leave a workspace the planner
    // already edited alone (restore above), and context() then describes THEIR unpulled choices: the saved numbers must not be labelled with those. With no
    // context the method describes the result from the answer's own echo, and the banner below says the form differs.
    const back = !!(block && typeof block === "object") && !guard(plug, "choicesChanged", [lid, block], true);
    const was = back ? guard(plug, "context", [], null) : null;
    const kept = Object.assign({}, snap);
    delete kept.legacy;                                                           // (an older shape read in memory: what an Insert files is the current one)
    Object.assign(S, { result: snap.result, resultCtx: { geo: was && typeof was.geo === "string" ? was.geo : "", areas: [], method: was }, resultLid: lid,
      resultSnapshot: kept, resultStale: snapshotStale(snap, line) });
    S.resultSig = canonicalJson(requestBody());
    lastDirty = null;
    renderResult(snap.result);
    if (S.result) $("constanza-bubble").classList.add("cz-has-result");
  }

  // ---- open / close
  // Order (docs/constanza_methods.md): mount; which line the dock is open for (methods hear it); the form's own seeds; then, once the catalog is there, the
  // product - which tells its method it was chosen, so ctx.line() is already the new line inside onSelect - and the saved estimate of the line.
  function open(opts) {
    opts = opts || {};
    mount();
    const wasOpen = S.open;
    const prevLid = S.lineId;
    S.open = true;
    $("constanza-dock").classList.remove("hidden");
    const bubble = $("constanza-bubble");
    bubble.setAttribute("aria-expanded", "true");
    bubble.classList.add("cz-open");
    bubble.classList.remove("cz-hinting");
    if (opts.lineId || opts.geo != null) clearRequestState(prevLid, opts.lineId || null);      // a line's own request, not whatever the dock held before
    else leaveLine(prevLid);
    if (opts.hispanic != null) S.hispanic = !!opts.hispanic;       // the line's own choice (or the proposal's targeting), else the product's default
    const typedAlready = S.geo.trim() !== "" && !opts.geo;
    if (opts.geo != null) { S.geo = opts.geo; $("cz-geo").value = opts.geo; }
    else if (!typedAlready && typeof _effectiveGeo === "function" && typeof state !== "undefined" && state.parsed) {
      const g = _effectiveGeo(state.activeTierLabel);
      if (g) { S.geo = g; $("cz-geo").value = g; }
    }
    if (opts.flightDays) setFlight(opts.flightDays, "days");
    else if (opts.months) setFlight(opts.months, "months");
    else if (!wasOpen) setFlight(S.flight, S.unit);
    // The line's calendar months travel instead of the flight box's rounded days, for the methods that ask for them, until the planner edits the box.
    if (S.lineId && Number.isInteger(opts.months) && opts.months >= 1) S.monthsOverride = Math.min(opts.months, 120);
    whenCatalogReady(() => {
      refreshProducts();
      if (opts.product && productByName(opts.product)) { S.product = opts.product; updateProductUI(); }
      if (opts.product && !productByName(opts.product)) {
        // enabled in Admin but its method's script did not load: say that, not "turn it on" (it is on)
        const down = methodOfCatalogProduct(opts.product);
        $("cz-product-note").innerHTML = down && !methodState(down).ok
          ? `<p class="cz-note cz-warn">“${esc(opts.product)}” can’t be estimated right now: ${esc(methodState(down).note)}</p>`
          : `<p class="cz-note cz-warn">“${esc(opts.product)}” has no Constanza data enabled — pick another product, or turn it on in Admin → Forecast.</p>`;
      }
      updateFlightHint();
      if (S.lineId) restoreLine();
    });
    if (opts.lineLabel) $("cz-sub").textContent = `For the Step 05 line: ${opts.lineLabel}`;
    else if (!wasOpen && !S.lineId) $("cz-sub").textContent = "Avails for any market, from the platform data we’ve loaded.";
    refreshContextChip();
    refreshAudienceUI();
    if (!S.taxonomy) loadTaxonomy().then(showBrowseCount);           // quietly: the "Browse all audiences" count and the typeahead's group names
    if (S.geo.trim()) resolveSoon(0); else renderGeoStatus();
    setTimeout(focusStart, 60);
    if (opts.run) setTimeout(pull, 120);
  }

  // The controls the keyboard can reach: enabled, and drawn (a card hidden with display: none has no boxes, and nothing in it can take focus).
  const FOCUSABLE = 'input:not([type="hidden"]):not([disabled]), button:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, a[href], [tabindex]:not([tabindex="-1"])';
  const firstFocusable = (root) => (root ? Array.from(root.querySelectorAll(FOCUSABLE)).find((el) => el.getClientRects().length > 0) || null : null);

  // Where the keyboard starts when the dock opens: the pull button when there is a place to pull for, else the box that asks for one. A method that
  // owns its geography hides that box (a hidden control cannot take focus, and the keyboard would be left on the page behind the dock): its place is its
  // own Where card's, so the focus goes to the first control there, and to the pull button when the line's saved estimate was just shown (it was pulled
  // for a place already). Failing both, the first control the form offers. (Read from the dock's own state: this runs a moment after open(), and a method's
  // context() is for the moment of a pull.)
  function focusStart() {
    // The keyboard is only taken when it is not already at work in the dock: a planner who clicked into a box and began typing in the moment since the
    // dock opened keeps it. Pulling it away would move what they typed to a box that has lost focus, and a method's typeahead closes its list - and drops
    // the search that was about to fill it - when the keyboard leaves its box (the search they asked for would never be answered).
    const held = document.activeElement, dockEl = $("constanza-dock");
    if (held && dockEl && held !== dockEl && dockEl.contains(held)) return;
    const plug = activePlugin();
    let target;
    if (plug && capsOf(plug).ownsGeography) {
      const placed = !!(S.result && S.lineId != null && S.resultLid === S.lineId);
      target = placed ? $("cz-go") : (firstFocusable($("cz-method-where")) || firstFocusable($("cz-form")) || $("cz-go"));
    } else {
      target = S.geo.trim() ? $("cz-go") : $("cz-geo");
    }
    if (target) target.focus({ preventScroll: true });
  }

  function close() {
    if (!S.open) return;
    S.open = false;
    $("constanza-dock").classList.add("hidden");
    const bubble = $("constanza-bubble");
    bubble.setAttribute("aria-expanded", "false");
    bubble.classList.remove("cz-open");
    closePicker(false);
    setMood("idle");
    bubble.focus({ preventScroll: true });
  }

  function toggle() { S.open ? close() : open(); }

  // ---- wiring
  function bind() {
    $("constanza-bubble").addEventListener("click", toggle);
    $("cz-close").addEventListener("click", close);
    $("cz-expand").addEventListener("click", () => { S.wide = !S.wide; store.set({ wide: S.wide }); applyWide(); });
    $("cz-go").addEventListener("click", pull);
    $("cz-reset").addEventListener("click", () => resetForm());

    // product picker
    $("cz-product-btn").addEventListener("click", () => ($("cz-picker-pop").classList.contains("hidden") ? openPicker() : closePicker(true)));
    $("cz-picker-search").addEventListener("input", renderPickerList);
    $("cz-picker-search").addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); pickerActive = Math.min(pickerItems.length - 1, pickerActive + 1); markPickerActive(true); }
      else if (e.key === "ArrowUp") { e.preventDefault(); pickerActive = Math.max(0, pickerActive - 1); markPickerActive(true); }
      else if (e.key === "Enter") { e.preventDefault(); if (pickerItems[pickerActive]) chooseProduct(pickerItems[pickerActive].name); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePicker(true); }
    });
    $("cz-picker-list").addEventListener("click", (e) => {
      const o = e.target.closest(".cz-option");
      if (o && pickerItems[Number(o.dataset.idx)]) chooseProduct(pickerItems[Number(o.dataset.idx)].name);
    });

    // geography
    $("cz-geo").addEventListener("input", (e) => { S.geo = e.target.value; markStale(); resolveSoon(); });
    $("cz-geo").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); clearTimeout(resolveTimer); pull(); } });
    $("cz-geotype").addEventListener("click", (e) => { const b = e.target.closest("button[data-type]"); if (b) setGeoType(b.dataset.type); });
    $("cz-geo-quick").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.example != null) setGeo(b.dataset.example);
      else if (b.dataset.useProposal) setGeo(proposalGeo(), { geoType: "" });
      $("cz-geo").focus();                                   // the pick is spent (its button goes): the planner carries on in the box
    });
    $("cz-where-clear").addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      setGeo("", { geoType: "" });
      $("cz-geo").focus();
    });

    // flight
    // While typing, only the state follows: clearing the box and typing a new number must not be rewritten under the cursor.
    $("cz-flight").addEventListener("input", (e) => {
      S.monthsOverride = null;
      const n = Math.round(Number(e.target.value));
      if (Number.isFinite(n) && n >= 1) { S.flight = n; updateFlightHint(); refreshContextChip(); markStale(); }
    });
    $("cz-flight").addEventListener("change", (e) => setFlight(e.target.value));
    $("cz-flight").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); pull(); } });
    $("cz-flight-dec").addEventListener("click", () => setFlight(S.flight - 1));
    $("cz-flight-inc").addEventListener("click", () => setFlight(S.flight + 1));
    $("cz-unit").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-unit]");
      if (!b || b.dataset.unit === S.unit) return;
      const conv = b.dataset.unit === "days" ? S.flight * 30 : Math.max(1, Math.round(S.flight / 30.4375));   // keep roughly the same length
      setFlight(conv, b.dataset.unit);
    });
    $("cz-use-dates").addEventListener("click", (e) => setFlight(Number(e.target.dataset.days), "days", lineMonthsToSend()));
    $("cz-total").addEventListener("input", (e) => { S.total = e.target.value; markStale(); });

    // who: age and gender
    $("cz-age-min").addEventListener("input", (e) => { S.ageMin = e.target.value; refreshAudienceUI(); markStale(); });
    $("cz-age-max").addEventListener("input", (e) => { S.ageMax = e.target.value; refreshAudienceUI(); markStale(); });
    $("cz-age-presets").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-min]");
      if (!b) return;
      if (b.getAttribute("aria-pressed") === "true") setAge("", "");       // pressing the chosen range again lets go of it
      else setAge(b.dataset.min, b.dataset.max);
    });
    $("cz-gender").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-g]");
      if (!b) return;
      S.gender = b.dataset.g;
      refreshAudienceUI();
      markStale();
    });
    $("cz-who-clear").addEventListener("click", () => {             // (beside the <details>, not in its summary: pressing it never folds the card)
      S.gender = "";
      setAge("", "");
      $("cz-sec-who").querySelector("summary").focus();                   // the Clear link goes with what it cleared: the card's head keeps the focus
    });

    // language and heritage
    $("cz-hispanic").addEventListener("change", (e) => {
      const p = productByName(S.product);
      S.hispanic = (p && e.target.checked === !!p.hispanicDefault) ? null : e.target.checked;       // back to the default = no preference, so nothing looks changed
      refreshAudienceUI();
      markStale();
    });
    $("cz-lang-clear").addEventListener("click", () => {                 // "Turn off" is the switch, flipped: the same handler decides what that means
      const box = $("cz-hispanic");
      if (box.checked && !box.disabled) { box.checked = false; box.dispatchEvent(new Event("change", { bubbles: true })); }
      $("cz-sec-lang").querySelector("summary").focus();
    });

    // audience: the typeahead, the chips and the browse window
    $("cz-interest-q").addEventListener("input", () => { clearTimeout(interestTimer); interestTimer = setTimeout(searchInterests, 180); });
    $("cz-interest-q").addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (!interestHits.length) return;
        e.preventDefault();
        interestActive = (interestActive + (e.key === "ArrowDown" ? 1 : -1) + interestHits.length) % interestHits.length;
        markInterestActive();
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (interestHits[interestActive]) addInterest(interestHits[interestActive]);
      } else if (e.key === "Escape" && interestPopupOpen()) { e.preventDefault(); e.stopPropagation(); hideInterestResults(); }       // the popup closes; the dock stays
    });
    $("cz-interest-results").addEventListener("click", (e) => {
      const el = e.target.closest(".aud-ta-opt");
      if (el && interestHits[Number(el.dataset.i)]) addInterest(interestHits[Number(el.dataset.i)]);
    });
    $("cz-interest-chips").addEventListener("click", (e) => {
      const x = e.target.closest("[data-rm]");
      if (!x) return;
      const at = Number(x.dataset.rm);
      S.interests.splice(at, 1);
      refreshAudienceUI();
      markStale();
      const next = $("cz-interest-chips").querySelectorAll(".aud-chip-x")[Math.min(at, S.interests.length - 1)];
      (next || $("cz-interest-q")).focus();                               // the chip that was pressed is gone: the focus moves to its neighbour
    });
    $("cz-aud-clear").addEventListener("click", () => {
      S.interests = [];
      refreshAudienceUI();
      markStale();
      $("cz-sec-aud").querySelector("summary").focus();
    });
    $("cz-browse").addEventListener("click", openBrowse);
    if (!window.SegmentBrowser) $("cz-browse").hidden = true;             // the window's script did not load: the typeahead is still there

    // the cards that fold: remember which ones the planner left open
    document.querySelectorAll("#cz-aud details.aud-sec").forEach((d) => d.addEventListener("toggle", () => store.set({ sections: Object.assign({}, store.get().sections, { [d.id]: d.open }) })));

    // outside clicks close the dropdowns; Escape closes the dock
    document.addEventListener("mousedown", (e) => {
      if (!e.target.closest("#cz-picker")) closePicker(false);
      if (!e.target.closest("#cz-interest-q, #cz-interest-results")) hideInterestResults();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || !S.open) return;
      const dock = $("constanza-dock");
      if (dock.contains(document.activeElement) || document.activeElement === document.body) close();
    });
  }

  // ------------------------------------------------------------------ the launcher hint
  function hintOnce() {
    try { if (sessionStorage.getItem("constanza.hint")) return; sessionStorage.setItem("constanza.hint", "1"); } catch (e) { /* show it anyway */ }
    const b = $("constanza-bubble");
    if (!b || S.open) return;
    b.classList.add("cz-hinting");
    setTimeout(() => b.classList.remove("cz-hinting"), 6500);
  }

  // ------------------------------------------------------------------ public surface
  window.Constanza = {
    open, close, toggle, isOpen: () => S.open, hintOnce, setMood, onProposalReset, replaceGeoItem: replaceItem,
    registerMethod, renderCard, renderCardError, mountGeoHelp, summarize, badgesHtml, iconHtml: () => MINI_ICON, compact, lineLabel,
    periodLineHtml: (series) => (isLiveSports(series) && series.period_label ? `<p class="cz-period-line">📅 Live sports period: ${esc(series.period_label)}</p>` : ""),
    // The plugin contract and what the page needs to draw any method's Step 05 block without naming one (docs/constanza_methods.md).
    CONTRACT, methodState, blockButtons, renderEmpty, snapshotStale, makeSnapshot, adoptLegacy, geoInfo, cardRequest, canonicalJson,
    _state: S, _plugin: (name) => plugins[name] || null,
    // Pure helpers, exposed so tests/test_constanza_frontend.py can run them without a browser.
    _pure: { compact, editGeoItem, tidyList, removeGeoItems, replaceItem, looksLikeList, summarize, normGeo, geoHelpHtml, badgesHtml, lineLabel, seriesMeta, notesOf,
      markStale, requestBody, audienceBoxHtml, winRateHtml, reachLinesHtml, summaryText, hispanicToggleHtml, loadProducts,
      whereSummary, whenSummary, whoSummary, languageSummary, interestsSummary, ageText, rateText, markHtml, interestItems, interestFrom, BROWSE_COLUMNS, AGE_PRESETS,
      pluginFor, canonicalJson, snapshotStale, makeSnapshot, adoptLegacy, geoInfo, cardRequest, methodState, blockButtons, renderEmpty },
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
