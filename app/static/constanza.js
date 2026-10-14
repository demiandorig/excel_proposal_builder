/* ============================================================================
 * Constanza — the floating launcher and the avails-puller dock.
 *
 * Replaces the old masthead button + modal. A round character in the bottom-right corner opens a dock: pick a product,
 * type a geography (it is matched live, with suggestions when it is ambiguous or misspelled), set the flight and an
 * optional audience, and pull avails. The same engine and the same wording are used by the per-line "Auto-calculate"
 * button on Step 05 (renderCard / mountGeoHelp below), so both places read alike.
 *
 * Loaded after app.js: it relies on app.js's globals (state, escapeHtml, formatPlainDisplay, _replaceGeoItem,
 * applyConstanzaEstimate, _constanzaGeoInfo, _effectiveGeo, _effectiveStartDate/EndDate, _mbParseDate, _mbMonthsBetween)
 * only at call time. Server side: app/main.py (/api/constanza/*), app/services/constanza_engine.py.
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
  const CHANNEL_ORDER = ["ctv", "platform_model", "live_sports", "netflix", "pause_ads", "olv", "display", "_generic"];
  const CHANNEL_LABEL = {
    ctv: "Entravision+ Connect · Madhive", platform_model: "Streaming platforms", live_sports: "Live sports", netflix: "Netflix",
    pause_ads: "Pause ads", olv: "Online video", display: "Display", _generic: "Other products · population-share estimate",
  };
  const BASIS_LABEL = { platform_reported: "Reported avails", modeled: "Modeled", extrapolated: "Sample extended to all markets" };
  const UNIT_LABEL = { weekly: "weekly avails", monthly: "monthly avails" };

  function loadProducts() {
    const cat = (typeof state !== "undefined" && state.catalog) || null;
    if (!cat) return [];
    const out = [];
    for (const fam of cat.families || []) {
      for (const p of (cat.products_by_family || {})[fam] || []) {
        if (p.is_addon || !p.forecast || !p.forecast.enabled) continue;
        const series = p.forecast.series || null;
        out.push({ name: p.name, family: fam, method: p.forecast.method, series, channel: series && series.channel ? series.channel : "_generic" });
      }
    }
    return out;
  }

  function seriesMeta(series) {
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
    ageMin: "", ageMax: "", gender: "", interests: [],
    lineId: null, needTotal: false,
    resolve: null, result: null, busy: false, resSeq: 0, pullSeq: 0, taxonomy: null,
    resultCtx: null,                  // what the shown result was pulled for: the geography as typed and the areas it resolved to
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
  const looksLikeState = (piece) => /^[A-Z]{2}$/.test(String(piece).trim()) || US_STATES.indexOf(String(piece).trim().toLowerCase()) >= 0;
  const SEP = "[;|\\n,&+]";
  const CONJ = "\\s(?:and|or|plus)\\s";

  function itemRegex(item) {
    const body = item.trim().split(/\s*,\s*/).map((part) => part.split(/\s+/).map(escRe).join("\\s+")).join("\\s*,\\s*");
    return new RegExp("(^|" + SEP + "|" + CONJ + ")(\\s*)(" + body + ")(?=\\s*$|\\s*" + SEP + "|\\s+(?:and|or|plus)\\s)", "gi");
  }

  // `replacement` null removes the item. Returns the new text, or null when the item is not in the text as a whole item.
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
      return text.slice(0, m.index) + m[1] + m[2] + (replacement == null ? "" : replacement) + text.slice(end);
    }
    return null;
  }

  // Remove the separators a removal leaves behind: "; ; B", a list that now starts or ends with "and".
  function tidyList(text) {
    const sep = "(?:[;|,&+]|\\band\\b|\\bor\\b|\\bplus\\b)";
    return text
      // a separator followed by another (or by the end) goes; if that would glue two words together, one space stays
      .replace(new RegExp("\\s*" + sep + "\\s*(?=" + sep + "|$)", "gi"), (m, offset, whole) => {
        const before = whole[offset - 1], after = whole[offset + m.length];
        return before && after && /\w/.test(before) && /\w/.test(after) ? " " : "";
      })
      .replace(new RegExp("^\\s*" + sep + "\\s*", "i"), "")
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

  // Swap one item for the planner's pick. An item that cannot be found never wipes the list: a single typed item is replaced, and
  // a list gets the place added.
  function replaceItem(text, item, replacement) {
    const next = editGeoItem(text, item, replacement);
    if (next !== null) return next;
    const isList = /[;|\n&+]|\b(?:and|or|plus)\b/i.test(text) || (text.match(/,/g) || []).length > 1;
    if (!isList) return replacement;
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
    return {
      aud, months, weekly, weeks: weekly ? data.weeks : null,
      total: { low: src.imps_low, mid: src.imps_mid, high: src.imps_high },
      month: { low: per(src.imps_low, months), mid: per(src.imps_mid, months), high: per(src.imps_high, months) },
      week: weekly ? { low: per(src.imps_low, data.weeks), mid: per(src.imps_mid, data.weeks), high: per(src.imps_high, data.weeks) } : null,
      uniques: !aud && data.est_uniques != null ? data.est_uniques : null,
      baseMonthMid: aud ? per(data.imps_mid, months) : null,
    };
  }

  function badgesHtml(data) {
    const series = data.series || null;
    const b = [];
    if (series) {
      const basis = series.data_basis;
      const cls = basis === "platform_reported" ? "cz-reported" : (basis === "extrapolated" ? "cz-extended" : "cz-modeled");
      b.push(`<span class="cz-badge ${cls}">${esc(BASIS_LABEL[basis] || "Modeled")}</span>`);
      if (series.unit === "weekly") b.push(`<span class="cz-badge">Weekly avails${data.weeks ? ` · ${data.weeks.toFixed(1)} wk flight` : ""}</span>`);
    } else {
      b.push(`<span class="cz-badge cz-extended">Population-share estimate</span>`);
    }
    if (isLiveSports(series) && series.period_label) b.push(`<span class="cz-badge cz-period">📅 ${esc(series.period_label)}</span>`);
    const geo = { high: "High geo confidence", medium: "Medium geo confidence", low_confidence_international: "International · low confidence" }[data.geo_confidence];
    if (geo) b.push(`<span class="cz-badge">${esc(geo)}</span>`);
    return b.join("");
  }

  function notesOf(data) {
    return (data.notes || []).filter((n) => !String(n).startsWith("NOT INCLUDED in this estimate"));
  }

  function unmatchedHtml(data) {
    const tokens = data.unmatched_geo_tokens || [];
    if (!tokens.length) return "";
    const reasons = {};
    (data.unmatched_geo_details || []).forEach((d) => { reasons[d.input] = d.reason; });
    return `<div class="cz-error"><strong>Not included — couldn’t be matched:</strong><ul class="cz-list">${tokens.map((t) => `<li><strong>${esc(t)}</strong>${reasons[t] ? ` — ${esc(reasons[t])}` : ""}</li>`).join("")}</ul></div>`;
  }

  function audienceBoxHtml(aud) {
    if (!aud) return "";
    const tier = { audience_modeled_geo_only: "Geography only — no demographic narrowing", audience_modeled_single_cut: "1 targeting cut applied", audience_modeled_stacked: "Several cuts stacked — directional, not precise" }[aud.confidence_tier] || "";
    const lines = [];
    if (tier) lines.push(`<p><strong>${esc(tier)}</strong></p>`);
    lines.push(`<p>Underlying audience ≈ <strong>${num(aud.underlying_population)}</strong> people${aud.below_reliability_threshold ? ` — <span class="cz-warn-text">below the ~${num(aud.reliability_floor_population)} reliability floor for this cut; treat as order-of-magnitude only.</span>` : "."}</p>`);
    (aud.interests || []).forEach((i) => {
      const name = i.label || i.input_text;
      if (i.supported) {
        const link = i.source_url ? `<a href="${esc(i.source_url)}" target="_blank" rel="noopener noreferrer">${esc(i.source || "source")}</a>` : esc(i.source || "");
        const read = i.input_text && i.input_text !== i.label ? ` <small>(read “${esc(i.input_text)}” as this)</small>` : "";
        lines.push(`<p>✓ <strong>${esc(name)}</strong> — ${i.rate != null ? Math.round(i.rate * 1000) / 10 + "% of adults" : ""}${i.confidence ? ` · ${esc(i.confidence)} confidence` : ""}${read}<br><small>${link}${i.source_date ? ", " + esc(i.source_date) : ""}${i.status === "derived" ? " · derived" : ""}</small></p>`);
      } else {
        const alts = (i.suggestions || []).filter((x) => x.supported).slice(0, 3).map((x) => esc(x.label)).join(", ");
        lines.push(`<p class="cz-warn-text">⚠ <strong>${esc(name)}</strong> has no public rate — not applied to the estimate, not guessed.${alts ? ` <small>Closest audiences with a rate: ${alts}.</small>` : ""}</p>`);
      }
    });
    (aud.notes || []).forEach((n) => lines.push(`<p><small>${esc(n)}</small></p>`));
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
  // opts: { onApply(monthlyMid, uniques, geoInfo), onRefine(), onGeoText(next) }
  function renderCard(el, data, opts) {
    const m = summarize(data);
    const notes = notesOf(data).slice(0, 4).map((n) => `<li>${esc(n)}</li>`).join("");
    const monthRow = `<tr><td>${m.aud ? "Audience imps / month" : "Imps / month"}</td><td>${num(m.month.low)}</td><td>${num(m.month.mid)}</td><td>${num(m.month.high)}</td></tr>`;
    const weekRow = m.weekly ? `<tr><td>Per week</td><td>${num(m.week.low)}</td><td>${num(m.week.mid)}</td><td>${num(m.week.high)}</td></tr>` : "";
    const totalRow = m.months > 1 || m.weekly ? `<tr><td>Flight total${m.weekly ? "" : ` · ${m.months} mo`}</td><td>${num(m.total.low)}</td><td>${num(m.total.mid)}</td><td>${num(m.total.high)}</td></tr>` : "";
    el.style.display = "block";
    el.innerHTML = `
      <div class="cz-badges">${badgesHtml(data)}</div>
      <table class="constanza-range-table"><tr><th></th><th>Low</th><th>Mid</th><th>High</th></tr>${monthRow}${weekRow}${totalRow}</table>
      ${m.uniques != null ? `<p class="constanza-uniques">Est. monthly uniques: <strong>${num(m.uniques)}</strong></p>` : ""}
      ${audienceBoxHtml(m.aud)}
      ${unmatchedHtml(data)}
      <div class="cz-geohelp"></div>
      ${notes ? `<ul class="cz-list">${notes}</ul>` : ""}
      <div class="cz-card-actions">
        <button type="button" class="btn-secondary cz-apply">Apply monthly mid-case →</button>
        <button type="button" class="cz-link cz-refine">Refine in Constanza</button>
      </div>`;
    const help = el.querySelector(".cz-geohelp");
    mountGeoHelp(help, data, { getText: opts.getText, setText: (next) => opts.onGeoText(next) });
    el.querySelector(".cz-apply").addEventListener("click", () => opts.onApply(m.month.mid, m.uniques, typeof _constanzaGeoInfo === "function" ? _constanzaGeoInfo(data) : null));
    el.querySelector(".cz-refine").addEventListener("click", () => opts.onRefine());
  }

  function renderCardError(el, message, resolution, opts) {
    el.style.display = "block";
    el.innerHTML = `<p class="constanza-error">⚠️ ${esc(message)}</p><div class="cz-geohelp"></div>`;
    if (resolution) mountGeoHelp(el.querySelector(".cz-geohelp"), resolution, { getText: opts.getText, setText: (next) => opts.onGeoText(next) });
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
      <div class="cz-field">
        <label class="cz-label" for="cz-product-btn"><span>Product</span><span class="cz-hint" id="cz-product-count"></span></label>
        <div class="cz-picker" id="cz-picker">
          <button type="button" class="cz-picker-btn" id="cz-product-btn" aria-haspopup="listbox" aria-expanded="false" aria-controls="cz-picker-pop">
            <span class="cz-pname" id="cz-pname">Loading products…</span><span aria-hidden="true">▾</span>
          </button>
          <div class="cz-picker-pop hidden" id="cz-picker-pop">
            <input type="search" class="cz-input cz-picker-search" id="cz-picker-search" placeholder="Search products…" autocomplete="off" aria-label="Search products"
                   role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls="cz-picker-list" aria-haspopup="listbox" />
            <div class="cz-picker-list" id="cz-picker-list" role="listbox" aria-label="Products"></div>
          </div>
        </div>
        <div id="cz-product-note"></div>
      </div>

      <div class="cz-field">
        <label class="cz-label" for="cz-geo"><span>Geography</span><span class="cz-hint">market · county · city · ZIP · district</span></label>
        <input type="text" class="cz-input" id="cz-geo" placeholder="e.g. Adams County, WA" autocomplete="off" spellcheck="false" />
        <div class="cz-seg" id="cz-geotype" role="group" aria-label="Treat the text as">
          <button type="button" data-type="" aria-pressed="true" title="Work it out from what you typed">Auto</button>
          <button type="button" data-type="city" aria-pressed="false">City</button>
          <button type="button" data-type="county" aria-pressed="false">County</button>
          <button type="button" data-type="zip" aria-pressed="false">ZIP</button>
          <button type="button" data-type="congressional_district" aria-pressed="false">District</button>
          <button type="button" data-type="country" aria-pressed="false">Country</button>
        </div>
        <div id="cz-geo-chips"></div>
        <div id="cz-geo-help" aria-live="polite"></div>
      </div>

      <div class="cz-field">
        <label class="cz-label" for="cz-flight"><span>Flight length</span><span class="cz-hint" id="cz-flight-hint"></span></label>
        <div class="cz-row">
          <div class="cz-stepper">
            <button type="button" id="cz-flight-dec" aria-label="Decrease">−</button>
            <input type="number" id="cz-flight" min="1" step="1" value="1" inputmode="numeric" aria-label="Flight length" />
            <button type="button" id="cz-flight-inc" aria-label="Increase">+</button>
          </div>
          <div class="cz-seg" id="cz-unit" role="group" aria-label="Unit">
            <button type="button" data-unit="months" aria-pressed="true">Months</button>
            <button type="button" data-unit="days" aria-pressed="false">Days</button>
          </div>
        </div>
        <div class="cz-chips"><button type="button" class="cz-chip cz-sugg hidden" id="cz-use-dates"></button></div>
      </div>

      <div class="cz-field hidden" id="cz-total-field">
        <label class="cz-label" for="cz-total"><span>Impressions per month to spread</span><span class="cz-hint">by population share</span></label>
        <input type="number" class="cz-input" id="cz-total" min="0" step="1000" placeholder="e.g. 1,000,000" inputmode="numeric" />
        <p class="cz-note" id="cz-total-note"></p>
      </div>

      <details class="cz-details" id="cz-aud">
        <summary><span>Audience</span><span class="cz-count hidden" id="cz-aud-count">0</span><span class="cz-hint">age · gender · interests</span></summary>
        <div class="cz-row">
          <div class="cz-grow"><label class="cz-label" for="cz-age-min"><span>Age from</span></label><input type="number" class="cz-input" id="cz-age-min" min="0" max="120" placeholder="18" inputmode="numeric" /></div>
          <div class="cz-grow"><label class="cz-label" for="cz-age-max"><span>to</span></label><input type="number" class="cz-input" id="cz-age-max" min="0" max="120" placeholder="34" inputmode="numeric" /></div>
        </div>
        <div class="cz-field" style="margin-top:12px">
          <span class="cz-label"><span>Gender</span></span>
          <div class="cz-seg" id="cz-gender" role="group" aria-label="Gender">
            <button type="button" data-g="" aria-pressed="true">All</button>
            <button type="button" data-g="male" aria-pressed="false">Men</button>
            <button type="button" data-g="female" aria-pressed="false">Women</button>
          </div>
        </div>
        <div class="cz-field" style="margin-bottom:0">
          <label class="cz-label" for="cz-interest-q"><span>Interests</span><span class="cz-hint" id="cz-interest-hint"></span></label>
          <input type="search" class="cz-input" id="cz-interest-q" placeholder="Search — soccer, dentist, new parents…" autocomplete="off"
                 role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="cz-interest-results" aria-haspopup="listbox" />
          <div class="cz-interest-results hidden" id="cz-interest-results" role="listbox" aria-label="Matching audiences"></div>
          <div class="cz-chips" id="cz-interest-chips"></div>
          <p class="cz-note" id="cz-interest-note"></p>
          <details class="cz-browse" id="cz-browse"><summary>Browse every audience</summary><div id="cz-browse-body"><p class="cz-note">Loading…</p></div></details>
        </div>
      </details>
    </div>
    <div class="cz-result" id="cz-result"></div>
  </div>
  <div class="cz-sr" id="cz-status" role="status" aria-live="polite"></div>
  <footer class="cz-foot">
    <button type="button" class="cz-reset" id="cz-reset">Reset</button>
    <button type="button" class="btn-primary" id="cz-go">Pull avails →</button>
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
    applyWide();
    renderResultWelcome();
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
    const p = productByName(S.product);
    $("cz-pname").textContent = p ? p.name : (S.products.length ? "Choose a product…" : "Loading products…");
    $("cz-pname").title = p ? p.name : "";
    const note = $("cz-product-note");
    if (!p) { note.innerHTML = ""; return; }
    const bits = [`<span class="cz-hint">${esc(seriesMeta(p.series))}</span>`];
    let html = `<p class="cz-note">${bits.join("")}</p>`;
    if (isLiveSports(p.series) && p.series.period_label) {
      html = `<p class="cz-note"><span class="cz-badge cz-period">📅 ${esc(p.series.period_label)}</span></p>` + `<p class="cz-note">${esc(seriesMeta(p.series))} — the flight dates aren’t checked against this window.</p>`;
    } else if (p.series && p.series.relation === "baseline_proxy") {
      html += `<p class="cz-note">Uses <strong>${esc(p.series.title || p.series.key)}</strong> as its baseline.${p.series.note ? " " + esc(p.series.note) : ""}</p>`;
    } else if (p.series && p.series.data_basis === "extrapolated") {
      html += `<p class="cz-note">Markets outside the supplied sample are extended from the Connect avails, so their range is wider.</p>`;
    }
    note.innerHTML = html;
    updateTotalField();
  }

  // ---- picker
  function matchesQuery(p, words) {
    if (!words.length) return true;
    const hay = `${p.name} ${p.series ? p.series.title : ""} ${CHANNEL_LABEL[p.channel] || ""}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  }

  function renderPickerList() {
    const words = $("cz-picker-search").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const groups = {};
    S.products.filter((p) => matchesQuery(p, words)).forEach((p) => { (groups[p.channel] = groups[p.channel] || []).push(p); });
    pickerItems = [];
    const html = CHANNEL_ORDER.filter((c) => groups[c]).map((c) => {
      const opts = groups[c].map((p) => {
        const idx = pickerItems.push(p) - 1;
        const meta = [seriesMeta(p.series), isLiveSports(p.series) && p.series.period_label ? p.series.period_label : ""].filter(Boolean).join(" · ");
        return `<div class="cz-option" role="option" id="cz-opt-${idx}" data-idx="${idx}" aria-selected="${p.name === S.product}"><span class="cz-option-name">${esc(p.name)}</span><span class="cz-option-meta">${esc(meta)}</span></div>`;
      }).join("");
      return `<div class="cz-group-label" role="presentation">${esc(CHANNEL_LABEL[c])}</div>${opts}`;
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
    resolveTimer = setTimeout(resolveNow, ms == null ? 380 : ms);
  }

  async function resolveNow() {
    const text = S.geo.trim();
    const seq = ++S.resSeq;
    if (!text) { S.resolve = null; renderGeoStatus(); return; }
    let r;
    try { r = await api("/api/constanza/resolve", { geo_text: text, geo_type_hint: S.geoType || null }); } catch (e) { return; }
    if (seq !== S.resSeq) return;                 // a newer keystroke superseded this answer
    S.resolve = r.ok ? r.data : null;
    renderGeoStatus();
  }

  function areaChipLabel(a) {
    const name = a.label || a.dma || "";
    return `${esc(name)} <small>${compact(a.area_population)}</small>`;
  }

  function renderGeoStatus() {
    const chipsEl = $("cz-geo-chips"), helpEl = $("cz-geo-help");
    const r = S.resolve;
    if (!r) {
      chipsEl.innerHTML = S.geo.trim() ? "" : `<p class="cz-note">Try <button type="button" class="cz-link" data-example="National">National</button>, <button type="button" class="cz-link" data-example="Texas">Texas</button>, <button type="button" class="cz-link" data-example="Phoenix">Phoenix</button> or a ZIP like <button type="button" class="cz-link" data-example="90210">90210</button>.</p>`;
      helpEl.innerHTML = "";
      updateTotalField();
      return;
    }
    const areas = r.areas || [];
    const shown = areas.slice(0, 6);
    let chips = shown.map((a) => `<span class="cz-chip cz-ok" title="${esc(GEO_TYPE_LABEL[a.source_type] || a.source_type)}${a.dma && a.dma !== a.label ? " · " + esc(a.dma) : ""}">✓ ${areaChipLabel(a)}</span>`).join("");
    if (areas.length > shown.length) chips += `<span class="cz-chip">+${areas.length - shown.length} more</span>`;
    const totalPop = areas.reduce((s, a) => s + (a.area_population || 0), 0);
    const summary = areas.length ? `<p class="cz-note">${areas.length} area${areas.length === 1 ? "" : "s"} · about ${compact(totalPop)} people${r.geo_confidence === "medium" ? " · spans more than one county or market" : ""}${r.geo_confidence === "low_confidence_international" ? " · outside the U.S." : ""}</p>` : "";
    chipsEl.innerHTML = areas.length ? `<div class="cz-chips">${chips}</div>${summary}` : "";
    const notes = (r.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
    mountGeoHelp(helpEl, r, { getText: () => S.geo, setText: (next, meta) => setGeo(next, meta) });
    if (notes) helpEl.insertAdjacentHTML("beforeend", `<ul class="cz-list">${notes}</ul>`);
    updateTotalField();
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
  }

  // Commits a value: clamps it, writes it into the box and updates the hint. Used by the +/- buttons, the unit switch, "use the
  // proposal's dates" and when the box loses focus - NOT while the planner is typing (see the input handler).
  function setFlight(value, unit) {
    S.unit = unit || S.unit;
    S.flight = Math.max(1, Math.round(Number(value) || 1));
    $("cz-flight").value = S.flight;
    $("cz-unit").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.unit === S.unit)));
    updateFlightHint();
    markStale();
  }

  function refreshContextChip() {
    const chip = $("cz-use-dates"), ctx = contextFlight();
    if (!ctx) { chip.classList.add("hidden"); return; }
    chip.classList.remove("hidden");
    chip.textContent = `Use the proposal’s dates · ${ctx.label} (${ctx.days} days)`;
    chip.dataset.days = ctx.days;
  }

  // ---- population-share fallback field
  function updateTotalField() {
    const p = productByName(S.product);
    const intl = S.resolve && S.resolve.geo_confidence === "low_confidence_international";
    const need = !!((p && p.method === "generic") || intl || S.needTotal);
    $("cz-total-field").classList.toggle("hidden", !need);
    $("cz-total-note").textContent = intl
      ? "Platform data only exists for the U.S. Enter the impressions you want spread across the country by population (low confidence)."
      : (p && p.method === "generic" ? "This product has no platform avails data — enter the monthly impressions to spread across the geography by population." : "");
  }

  // ---- audience
  function audienceCount() {
    return (S.ageMin !== "" || S.ageMax !== "" ? 1 : 0) + (S.gender ? 1 : 0) + S.interests.length;
  }

  function refreshAudienceUI() {
    const n = audienceCount();
    const badge = $("cz-aud-count");
    badge.textContent = n;
    badge.classList.toggle("hidden", !n);
    $("cz-interest-chips").innerHTML = S.interests.map((i, idx) => {
      const rate = i.supported ? `${Math.round(i.rate_all * 100)}%` : "no public rate";
      return `<span class="cz-chip ${i.supported ? "cz-ok" : "cz-warn"}">${esc(i.label)} <small>${esc(rate)}</small><button type="button" class="cz-x" data-rm="${idx}" aria-label="Remove ${esc(i.label)}">×</button></span>`;
    }).join("");
    const notes = [];
    if (S.interests.some((i) => !i.supported)) notes.push("Audiences marked “no public rate” are listed for reference only — they won’t change the estimate.");
    if (S.interests.filter((i) => i.supported).length > 1) notes.push("Several interests are multiplied together as if they were independent, so the range widens with each one.");
    $("cz-interest-note").textContent = notes.join(" ");
    $("cz-interest-hint").textContent = S.interests.length ? `${S.interests.length} chosen` : "";
  }

  function addInterest(seg) {
    if (S.interests.some((i) => i.id === seg.id)) return;
    if (S.interests.length >= 10) { $("cz-interest-note").textContent = "At most 10 interests at a time."; return; }
    S.interests.push({ id: seg.id, label: seg.label, supported: !!seg.supported, rate_all: seg.rate_all });
    $("cz-interest-q").value = "";
    hideInterestResults();
    refreshAudienceUI();
    markStale();
  }

  function hideInterestResults() {
    $("cz-interest-results").classList.add("hidden");
    $("cz-interest-q").setAttribute("aria-expanded", "false");
    $("cz-interest-q").removeAttribute("aria-activedescendant");
    interestHits = []; interestActive = -1;
  }

  function markInterestActive() {
    $("cz-interest-results").querySelectorAll(".cz-interest-item").forEach((el, i) => el.classList.toggle("cz-active", i === interestActive));
    const input = $("cz-interest-q");
    if (interestActive >= 0 && interestHits[interestActive]) input.setAttribute("aria-activedescendant", `cz-int-${interestActive}`);
    else input.removeAttribute("aria-activedescendant");
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
    if (!r.ok) {
      interestHits = []; interestActive = -1;
      const failed = $("cz-interest-results");
      failed.classList.remove("hidden");
      failed.innerHTML = `<div class="cz-empty">The audience search isn’t available right now. Try again in a moment.</div>`;
      return;
    }
    interestHits = (r.data.results || []);
    interestActive = interestHits.length ? 0 : -1;
    const box = $("cz-interest-results");
    box.classList.remove("hidden");
    $("cz-interest-q").setAttribute("aria-expanded", "true");
    box.innerHTML = interestHits.length
      ? interestHits.map((h, i) => `<div class="cz-interest-item ${h.supported ? "" : "cz-norate"} ${i === interestActive ? "cz-active" : ""}" role="option" id="cz-int-${i}" data-i="${i}" title="${esc(h.supported ? `${h.source || ""}${h.source_date ? ", " + h.source_date : ""} — ${h.confidence} confidence` : "No defensible public rate was found for this audience")}"><span>${esc(h.label)} <span class="cz-grp">${esc(groupLabel(h.group))}</span></span><span class="cz-rate">${h.supported ? Math.round(h.rate_all * 100) + "% of adults" : "no public rate"}</span></div>`).join("")
      : `<div class="cz-empty">Nothing matches “${esc(q)}”. Try a simpler word, or browse the list below.</div>`;
    markInterestActive();
  }

  function groupLabel(key) {
    const g = S.taxonomy && S.taxonomy.groups.find((x) => x.key === key);
    return g ? g.label : "";
  }

  // A failed request is NOT remembered: the next open of "Browse every audience" tries again.
  async function loadTaxonomy() {
    if (S.taxonomy) return S.taxonomy;
    try {
      const r = await api("/api/constanza/interest-taxonomy");
      if (r.ok) { S.taxonomy = r.data; return S.taxonomy; }
    } catch (e) { /* offline: fall through to the unavailable message */ }
    return { groups: [] };
  }

  async function renderBrowse() {
    const t = await loadTaxonomy();
    $("cz-browse-body").innerHTML = (t.groups || []).filter((g) => g.segments.length).map((g, gi) =>
      `<div class="cz-browse-group"><h4>${esc(g.label)}</h4><div class="cz-chips">${g.segments.map((s, si) =>
        `<button type="button" class="cz-chip ${s.supported ? "" : "cz-norate"}" data-g="${gi}" data-s="${si}" title="${esc(s.supported ? Math.round(s.rate_all * 100) + "% of US adults — " + (s.source || "") : "No public rate yet")}">${esc(s.label)}</button>`).join("")}</div></div>`).join("")
      || `<p class="cz-note">The audience list isn’t available right now.</p>`;
  }

  // ---- results
  // The form changed after this result was pulled: say so on the card (the numbers are still the ones for what it names).
  function markStale() {
    if (!S.result || !$("cz-result")) return;
    const box = $("cz-result");
    if (box.querySelector(".cz-stale")) return;
    box.insertAdjacentHTML("afterbegin", `<p class="cz-stale">The form has changed since this was pulled — pull again to update.</p>`);
  }

  // A short status line for screen readers (the result pane itself is not a live region: it would be read out in full).
  function announce(text) {
    const el = $("cz-status");
    if (el) el.textContent = text;
  }

  function renderResultWelcome() {
    $("cz-result").innerHTML = `<p class="cz-welcome">Pick a product and a place, then <strong>Pull avails</strong>.<br>Numbers come with the data they were modeled from.</p>`;
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
      `Geography: ${asked.geo}${areas.length ? ` (${areas.slice(0, 6).join("; ")}${areas.length > 6 ? `; +${areas.length - 6} more` : ""})` : ""}`,
      `Flight: ${m.months} month${m.months === 1 ? "" : "s"}${m.weeks ? ` (~${m.weeks.toFixed(1)} weeks)` : ""}`,
      `${m.aud ? "Audience impressions" : "Impressions"} per month (low / mid / high): ${num(m.month.low)} / ${num(m.month.mid)} / ${num(m.month.high)}`,
      `Flight total (low / mid / high): ${num(m.total.low)} / ${num(m.total.mid)} / ${num(m.total.high)}`,
    ];
    if (data.series) lines.push(`Source: ${data.series.title || data.series.key}, version ${data.series.version}${data.series.period_label ? `; period: ${data.series.period_label}` : ""}`);
    notesOf(data).slice(0, 3).forEach((n) => lines.push(`Note: ${n}`));
    return lines.join("\n");
  }

  function renderResult(data) {
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
    if (m.uniques != null) side.push({ label: "Est. uniques / month", v: num(m.uniques), r: "" });
    if (m.aud) side.push({ label: "Before the audience filter", v: num(m.baseMonthMid) + " / mo", r: "" });
    const notes = notesOf(data).map((n) => `<li>${esc(n)}</li>`).join("");
    const inserts = insertButtons(data, m);
    $("cz-result").innerHTML = `
      <div class="cz-card-head">
        <div class="cz-card-title">${esc(data.product_name)}</div>
        ${S.resultCtx && S.resultCtx.geo ? `<div class="cz-card-geo">${esc(S.resultCtx.geo)}</div>` : ""}
        <div class="cz-badges">${badgesHtml(data)}</div>
      </div>
      <div class="cz-headline">
        <div class="cz-metric main"><span class="cz-mlabel">${esc(main.label)}</span><span class="cz-mvalue">${num(main.v.mid)}</span><span class="cz-mrange">${compact(main.v.low)} – ${compact(main.v.high)} range</span></div>
        ${side.map((s) => `<div class="cz-metric"><span class="cz-mlabel">${esc(s.label)}</span><span class="cz-mvalue">${esc(s.v)}</span>${s.r ? `<span class="cz-mrange">${esc(s.r)}</span>` : ""}</div>`).join("")}
      </div>
      ${audienceBoxHtml(m.aud)}
      ${unmatchedHtml(data)}
      <div class="cz-section-title">Where the impressions are</div>
      ${shareBarHtml(rows, rows.reduce((s, r) => s + r.imps_mid, 0))}
      ${rows.length ? `<table class="cz-table"><thead><tr><th>Area</th><th>Share</th><th>${m.weekly ? "Flight total" : "Per month"}</th></tr></thead><tbody>${tableRows}</tbody></table>` : ""}
      ${rows.length > 8 ? `<p class="cz-note"><button type="button" class="cz-link" id="cz-toggle-areas">${showAll ? "Show the top 8 only" : `Show all ${rows.length} areas`}</button></p>` : ""}
      ${notes ? `<div class="cz-section-title">Notes</div><ul class="cz-list">${notes}</ul>` : ""}
      ${provenanceHtml(data)}
      <div class="cz-actions">${inserts}<button type="button" class="btn-secondary" id="cz-copy">Copy summary</button></div>`;
    $("cz-result").querySelectorAll(".cz-insert").forEach((btn) => btn.addEventListener("click", () => {
      const stillThere = typeof state !== "undefined" && (state.lineItems || []).some((li) => li.id === btn.dataset.lid);
      const done = stillThere && applyConstanzaEstimate(btn.dataset.lid, m.month.mid, m.uniques, typeof _constanzaGeoInfo === "function" ? _constanzaGeoInfo(data) : null) !== false;
      btn.textContent = done ? "✓ Inserted into Avails" : "That line is no longer in the proposal";
      btn.disabled = true;
    }));
    const tog = $("cz-toggle-areas");
    if (tog) tog.addEventListener("click", () => { S.showAllAreas = !S.showAllAreas; renderResult(data); });
    $("cz-copy").addEventListener("click", async (ev) => {
      try { await navigator.clipboard.writeText(summaryText(data)); ev.target.textContent = "Copied ✓"; } catch (e) { ev.target.textContent = "Couldn’t copy"; }
      setTimeout(() => { ev.target.textContent = "Copy summary"; }, 1800);
    });
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
  function requestBody() {
    const days = S.unit === "days";
    const total = parseFloat(S.total);
    const audience = (S.ageMin !== "" || S.ageMax !== "" || S.gender || S.interests.length) ? {
      age_min: S.ageMin === "" ? null : parseInt(S.ageMin, 10),
      age_max: S.ageMax === "" ? null : parseInt(S.ageMax, 10),
      gender: S.gender || null,
      interest_terms: S.interests.map((i) => i.label),
    } : null;
    return {
      product_name: S.product, geo_text: S.geo.trim(), geo_type_hint: S.geoType || null,
      months: days ? Math.max(1, Math.round(S.flight / 30.4375)) : S.flight,
      flight_days: days ? S.flight : null,
      national_monthly_total: total > 0 ? total : null,
      audience,
    };
  }

  function setBusy(on) {
    S.busy = on;
    const go = $("cz-go");
    go.disabled = on;
    go.innerHTML = on ? `<span class="cz-spinner"></span>Pulling…` : "Pull avails →";
    $("constanza-dock").setAttribute("aria-busy", String(on));
  }

  async function pull() {
    if (S.busy) return;
    S.flight = Math.max(1, Math.round(Number($("cz-flight").value) || 1));
    S.total = $("cz-total").value;
    if (!S.product) { renderError("Choose a product first."); setMood("oops", 3000); return; }
    if (!S.geo.trim()) { renderError("Type a geography first — a TV market, county, city, ZIP or “National”."); setMood("oops", 3000); $("cz-geo").focus(); return; }
    setBusy(true); setMood("thinking");
    S.showAllAreas = false;
    const mine = ++S.pullSeq;                     // Reset (or a newer pull) makes this answer stale: it must not repaint the dock
    const asked = { geo: S.geo.trim(), areas: ((S.resolve && S.resolve.areas) || []).map((a) => a.label || a.dma).filter(Boolean) };
    let r;
    try {
      r = await api("/api/constanza/estimate", requestBody());
    } catch (e) {
      if (mine !== S.pullSeq) return;
      setBusy(false); renderError(String(e)); setMood("oops"); return;
    }
    if (mine !== S.pullSeq) return;
    setBusy(false);
    if (!r.ok) {
      const msg = errorText(r.data, `The request failed (${r.status}).`);
      renderError(msg);
      setMood("oops");
      if (/national_monthly_total|population share|no forecast avails/i.test(msg)) { S.needTotal = true; updateTotalField(); }
      resolveNow();                                              // refresh the suggestions next to the geography box
      scrollToResult();
      return;
    }
    S.result = r.data;
    S.resultCtx = asked;
    renderResult(r.data);
    announce(`Pulled ${r.data.product_name}: ${num(summarize(r.data).month.mid)} impressions per month.`);
    $("constanza-bubble").classList.add("cz-has-result");
    setMood("happy", 3200);
    scrollToResult();
  }

  // Forget the previous request's geography type, audience, total and result (a line's own request starts from nothing).
  function clearRequestState() {
    S.pullSeq++; S.resSeq++;
    clearTimeout(resolveTimer);
    setBusy(false);
    Object.assign(S, { geoType: "", total: "", ageMin: "", ageMax: "", gender: "", interests: [], needTotal: false, result: null, resultCtx: null });
    setGeoType("", true);
    $("cz-total").value = ""; $("cz-age-min").value = ""; $("cz-age-max").value = ""; $("cz-interest-q").value = "";
    $("cz-gender").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.g === "")));
    refreshAudienceUI(); hideInterestResults(); renderResultWelcome(); updateTotalField();
    $("constanza-bubble").classList.remove("cz-has-result");
  }

  // A new proposal begins: nothing of the old one (its geography, its lines, a result for them) may survive in the dock.
  function onProposalReset() {
    if (!$("constanza-bubble")) return;
    resetForm();
  }

  function resetForm() {
    S.pullSeq++; S.resSeq++;                      // discard any pull or resolve still in flight
    clearTimeout(resolveTimer);
    setBusy(false);
    Object.assign(S, { geo: "", geoType: "", flight: 1, unit: "months", total: "", ageMin: "", ageMax: "", gender: "", interests: [], needTotal: false, resolve: null, result: null, resultCtx: null, lineId: null });
    $("cz-geo").value = ""; $("cz-total").value = ""; $("cz-age-min").value = ""; $("cz-age-max").value = ""; $("cz-interest-q").value = "";
    setGeoType("", true); setFlight(1, "months");
    $("cz-gender").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.g === "")));
    $("cz-sub").textContent = "Avails for any market, from the platform data we’ve loaded.";
    refreshAudienceUI(); hideInterestResults(); renderGeoStatus(); renderResultWelcome(); updateTotalField();
    $("constanza-bubble").classList.remove("cz-has-result");
    setMood("idle");
  }

  // ---- open / close
  function open(opts) {
    opts = opts || {};
    mount();
    const wasOpen = S.open;
    S.open = true;
    $("constanza-dock").classList.remove("hidden");
    const bubble = $("constanza-bubble");
    bubble.setAttribute("aria-expanded", "true");
    bubble.classList.add("cz-open");
    bubble.classList.remove("cz-hinting");
    whenCatalogReady(() => {
      refreshProducts();
      if (opts.product && productByName(opts.product)) { S.product = opts.product; updateProductUI(); }
      if (opts.product && !productByName(opts.product)) {
        $("cz-product-note").innerHTML = `<p class="cz-note cz-warn">“${esc(opts.product)}” has no Constanza data enabled — pick another product, or turn it on in Admin → Forecast.</p>`;
      }
    });
    if (opts.lineId || opts.geo != null) clearRequestState();      // a line's own request, not whatever the dock held before
    S.lineId = opts.lineId || null;
    const typedAlready = S.geo.trim() !== "" && !opts.geo;
    if (opts.geo != null) { S.geo = opts.geo; $("cz-geo").value = opts.geo; }
    else if (!typedAlready && typeof _effectiveGeo === "function" && typeof state !== "undefined" && state.parsed) {
      const g = _effectiveGeo(state.activeTierLabel);
      if (g) { S.geo = g; $("cz-geo").value = g; }
    }
    if (opts.flightDays) setFlight(opts.flightDays, "days");
    else if (opts.months) setFlight(opts.months, "months");
    else if (!wasOpen) setFlight(S.flight, S.unit);
    if (opts.lineLabel) $("cz-sub").textContent = `For the Step 05 line: ${opts.lineLabel}`;
    else if (!wasOpen && !S.lineId) $("cz-sub").textContent = "Avails for any market, from the platform data we’ve loaded.";
    refreshContextChip();
    refreshAudienceUI();
    if (S.geo.trim()) resolveSoon(0); else renderGeoStatus();
    setTimeout(() => { (S.geo.trim() ? $("cz-go") : $("cz-geo")).focus({ preventScroll: true }); }, 60);
    if (opts.run) setTimeout(pull, 120);
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
    $("cz-reset").addEventListener("click", resetForm);

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
    $("cz-geo-chips").addEventListener("click", (e) => { const b = e.target.closest("[data-example]"); if (b) setGeo(b.dataset.example); });

    // flight
    // While typing, only the state follows: clearing the box and typing a new number must not be rewritten under the cursor.
    $("cz-flight").addEventListener("input", (e) => {
      const n = Math.round(Number(e.target.value));
      if (Number.isFinite(n) && n >= 1) { S.flight = n; updateFlightHint(); markStale(); }
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
    $("cz-use-dates").addEventListener("click", (e) => setFlight(Number(e.target.dataset.days), "days"));
    $("cz-total").addEventListener("input", (e) => { S.total = e.target.value; markStale(); });

    // audience
    $("cz-age-min").addEventListener("input", (e) => { S.ageMin = e.target.value; refreshAudienceUI(); markStale(); });
    $("cz-age-max").addEventListener("input", (e) => { S.ageMax = e.target.value; refreshAudienceUI(); markStale(); });
    $("cz-gender").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-g]");
      if (!b) return;
      S.gender = b.dataset.g;
      $("cz-gender").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      refreshAudienceUI();
      markStale();
    });
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
      } else if (e.key === "Escape" && interestHits.length) { e.preventDefault(); e.stopPropagation(); hideInterestResults(); }
    });
    $("cz-interest-results").addEventListener("click", (e) => {
      const el = e.target.closest(".cz-interest-item");
      if (el && interestHits[Number(el.dataset.i)]) addInterest(interestHits[Number(el.dataset.i)]);
    });
    $("cz-interest-chips").addEventListener("click", (e) => {
      const x = e.target.closest("[data-rm]");
      if (x) { S.interests.splice(Number(x.dataset.rm), 1); refreshAudienceUI(); markStale(); }
    });
    $("cz-browse").addEventListener("toggle", (e) => { if (e.target.open) renderBrowse(); });
    $("cz-browse-body").addEventListener("click", (e) => {
      const b = e.target.closest("[data-g]");
      if (!b || !S.taxonomy) return;
      addInterest(S.taxonomy.groups[Number(b.dataset.g)].segments[Number(b.dataset.s)]);
    });

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
    renderCard, renderCardError, mountGeoHelp, summarize, badgesHtml, iconHtml: () => MINI_ICON, compact, lineLabel,
    periodLineHtml: (series) => (isLiveSports(series) && series.period_label ? `<p class="cz-period-line">📅 Live sports period: ${esc(series.period_label)}</p>` : ""),
    _state: S,
    // Pure helpers, exposed so tests/test_constanza_frontend.py can run them without a browser.
    _pure: { compact, editGeoItem, tidyList, removeGeoItems, replaceItem, summarize, normGeo, geoHelpHtml, badgesHtml, lineLabel, seriesMeta, notesOf },
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();
})();
