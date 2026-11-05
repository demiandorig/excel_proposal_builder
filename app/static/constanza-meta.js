/* ============================================================================
 * Constanza x Meta - the Meta panel of the avails dock.
 *
 * Choosing one of the three paid Facebook & Instagram Ads products makes the dock ask Meta's own reach estimate how many accounts match a
 * targeting, then turns that head count into impressions with Entravision's planning rule: the LOWER end of Meta's range x 4.3 / 5.5 / 8.6 a
 * month (conservative / mid / upper; mid is the default and the figure the Avails step takes; the multipliers come from the server). Est.
 * uniques is the lower-end audience, so the Avails frequency reads the mid multiplier. The server (app/services/constanza_meta.py) owns every
 * number, the budget cross-check included; this file only gathers the planner's choices and words the answer.
 *
 * Meta owns its geography: its TV markets are Comscore markets, so nothing is guessed from Nielsen DMAs. The dock therefore hides its own
 * Where card for this product and shows the one here (whereHtml), where the planner searches Meta's places; the proposal's own places are only
 * one-click search suggestions. The cards, numbered by the audience kit (audience-kit.css) in the dock's own sequence:
 *   1 Where       kind of place + Meta's place search (whereHtml, in the slot the dock keeps for it)
 *   2 When        the dock's own card (the flight; months come from it, or from the Step 05 line)
 *   3 Who         age (with presets) and gender
 *   4 Language    quick picks (Spanish / English / both, looked up LIVE in Meta's list), a search over the whole list, chips, and - when the
 *                 line targets Hispanic audiences - a suggestion to add Spanish (never silent), with Meta's caveat on what a language is
 *   5 Audience    interests, behaviors and demographics: search + chips, and "Browse all" = the SegmentBrowser pop-up (segment-browser.js)
 *   6 Budget cross-check (folded, optional)   a line's budget and CPM, or one optional amount when the dock is used on its own
 *
 * What the planner has chosen is kept PER LINE for the page's life (a Step 05 line, or "dock" for the dock on its own): two lines of one
 * product keep separate audiences, Reset forgets the line it was open for, a new proposal forgets all. A line's last estimate is saved with the
 * proposal by the dock (availsData[lid].forecast_snapshot); this plugin restores its choices from it and can read the older meta_forecast
 * shape (legacy.adopt).
 *
 * Loaded after constanza.js, which owns the dock: this file only registers a forecast method with it (registerMethod) and reaches the dock
 * only through the `ctx` it is given (Constanza.CONTRACT in constanza.js says what that is), never through fetch or a page global. The server
 * side is /api/constanza/meta/* and /api/constanza/meta-* (the lists) and /api/constanza/estimate (the numbers).
 * ========================================================================== */
(function () {
  "use strict";
  if (!window.Constanza || typeof window.Constanza.registerMethod !== "function") return;

  window.Constanza.registerMethod("meta", function (ctx) {
    const { $, esc, api, markStale, errorText } = ctx;
    const announce = (text) => { if (typeof ctx.announce === "function") ctx.announce(text); };
    // What the dock knows about the Step 05 line it is open for (null for the dock on its own) and about the proposal. Read fresh on every call.
    const opt = (name, ...args) => { try { return typeof ctx[name] === "function" ? ctx[name](...args) : undefined; } catch (e) { return undefined; } };
    const lineFacts = () => opt("line") || null;
    const proposalGeo = () => String(opt("proposalGeo") || "");

    // ---------------------------------------------------------------- what Meta can target (the lists that come from Meta are fetched on demand)
    const GEO_TYPES = [["geo_market", "TV market"], ["city", "City"], ["region", "State / region"], ["zip", "ZIP code"], ["country", "Country"]];
    const GEO_KIND_LOWER = { geo_market: "TV market", city: "city", region: "state / region", zip: "ZIP", country: "country" };
    const GEO_PLACEHOLDER = { geo_market: "TV market, e.g. Los Angeles", city: "City, e.g. Phoenix", region: "State or region, e.g. Texas", zip: "ZIP code, e.g. 90210", country: "Country, e.g. United States" };
    const COMSCORE_HINT = "Meta’s TV markets are Comscore markets, so they can differ a little from the Nielsen markets used elsewhere in this app.";
    const AGE_MIN = 18;
    const AGE_MAX = 65;
    const AGE_PRESETS = [["All ages", 18, 65], ["18–24", 18, 24], ["25–34", 25, 34], ["25–54", 25, 54], ["35–54", 35, 54], ["55+", 55, 65]];
    // A quick pick names the language(s) to look for: the locale ids always come from Meta's own list (GET /meta-languages?q=...), never from here.
    const LANG_QUICK = [{ key: "spanish", label: "Spanish speakers", words: ["Spanish"] }, { key: "english", label: "English", words: ["English"] }, { key: "both", label: "Spanish + English", words: ["Spanish", "English"] }];
    const KIND_LABEL = { interests: "Interest", behaviors: "Behavior", life_events: "Life event", industries: "Industry", income: "Income", family_statuses: "Family status", politics: "Politics", generation: "Generation", home_ownership: "Home ownership", home_type: "Home type", home_value: "Home value", household_composition: "Household", net_worth: "Net worth", office_type: "Office type", moms: "Moms" };
    const MAX_LANGUAGES = 25;                  // constanza_meta.MAX_LOCALES
    const MAX_DETAILED = 50;                   // constanza_meta.MAX_TARGETING_ITEMS
    const STATUS_TTL_MS = 30000;
    // The longest search text each box can hold: what the server takes (search_geography 2-100, search_targeting 2-80, search_locales 2-60), so a long
    // paste is cut in the box instead of coming back as "Search text must be 2-80 characters".
    const SEARCH_MAX = { geo: 100, aud: 80, lang: 60 };
    const LABEL_MAX = 160;                     // MetaGeoModel.label
    // "Browse all ..." opens the shared pop-up (segment-browser.js). If that script did not load, the controls say so instead of doing nothing.
    const BROWSE_MISSING = "The browse window didn’t load, so “Browse all” is off. Reload the page to get it back.";
    // What a request field is called when the server refuses it by name (FastAPI's 422 lists the field as `loc`, here under body.meta).
    const FIELD_WORDS = { product_name: "the product", months: "the flight length", "geo.type": "the kind of place", "geo.key": "the place", "geo.label": "the place’s name", geo: "the place",
      targeting: "the interests and behaviors", languages: "the languages",
      age_min: "the youngest age", age_max: "the oldest age", gender: "gender", retail_budget: "the retail budget", retail_cpm: "the retail CPM" };
    // What an empty list is called in "Meta sent back an empty list of ...".
    const CATALOG_WORDS = { interests: "interests", behaviors: "behaviors", demographics: "demographic options" };
    // Shown only until the server's own wording arrives (status.language_note / the languages answer); keep it the same as constanza_meta.LANGUAGE_NOTE.
    const LANGUAGE_NOTE = "Meta's languages are the language people have set Facebook or Instagram to, not a count of who speaks it. A Spanish speaker whose app is set to English is not included, so a Spanish audience can come out smaller than the Spanish-speaking population. Leave languages empty to count everyone in the geography, whatever language they use.";
    const CLEAR_LABEL = { where: "Clear the place", who: "Clear age and gender", lang: "Clear languages", aud: "Clear interests and behaviors" };
    const SCENARIOS = [["conservative", "Conservative"], ["mid", "Mid · default"], ["upper", "Upper"]];

    // ---------------------------------------------------------------- small pure helpers
    const isObj = (v) => !!v && typeof v === "object" && !Array.isArray(v);
    const list = (v) => (Array.isArray(v) ? v : []);
    const text = (v) => (v == null ? "" : String(v));

    // 33862.5 -> "$33,862.50", 2000 -> "$2,000": whole dollars when there are no cents, two decimals otherwise, never "$33,862.5". One rule for every amount.
    function money(value) {
      const n = Number(value);
      if (value == null || value === "" || !isFinite(n)) return "—";
      const cents = Math.round(Math.abs(n) * 100);
      const digits = cents % 100 ? 2 : 0;
      return `${n < 0 ? "-" : ""}$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
    }

    function count(value) {
      if (value == null || value === "" || !isFinite(Number(value))) return "—";
      return Math.round(Number(value)).toLocaleString("en-US");
    }

    // Lower case without accents: "Español" is found by "espanol".
    function fold(value) {
      let s = text(value);
      if (typeof s.normalize === "function") s = s.normalize("NFD").replace(/[̀-ͯ]/g, "");
      return s.toLowerCase();
    }

    const typeLabel = (type) => { const hit = GEO_TYPES.find(([key]) => key === type); return hit ? hit[1] : text(type); };
    const kindLabel = (type) => KIND_LABEL[type] || text(type).replace(/_/g, " ");

    // 25–44, 55+ (65 is "65 and over" to Meta), or "" for all ages.
    function ageText(min, max) {
      if (min <= AGE_MIN && max >= AGE_MAX) return "";
      return max >= AGE_MAX ? `${min}+` : `${min}–${max}`;
    }

    // "Spanish (Mexico)" -> "Spanish": the language without its regional variant.
    const langFamily = (name) => text(name).split(/\s*[(,]/)[0].trim() || text(name);
    const langStarts = (name, word) => fold(name).startsWith(fold(word));

    // The chosen languages grouped by family, in the order chosen. A family with several variants shows as one ("Spanish", 3 versions): the quick pick
    // adds every Spanish locale Meta lists, and a row of near-identical chips would bury the one that matters.
    function langFamilies(languages) {
      const out = [];
      list(languages).forEach((l) => {
        const name = langFamily(l.name);
        let group = out.find((x) => x.key === fold(name));
        if (!group) { group = { key: fold(name), name, items: [] }; out.push(group); }
        group.items.push(l);
      });
      out.forEach((g) => { g.label = g.items.length > 1 ? g.name : g.items[0].name; });
      return out;
    }

    // 1,234,567 -> "1.2M": an audience size is an estimate, so the short form reads better than seven digits.
    function compactSize(value) {
      const n = Number(value);
      if (!isFinite(n)) return "";
      const a = Math.abs(n);
      if (a < 1000) return String(Math.round(a));
      const round = (x) => (x >= 10 ? Math.round(x) : Math.round(x * 10) / 10);
      if (round(a / 1e3) < 1000) return round(a / 1e3) + "K";
      if (round(a / 1e6) < 1000) return round(a / 1e6) + "M";
      return round(a / 1e9) + "B";
    }

    function sizeText(lower, upper) {
      if (lower == null || !isFinite(Number(lower))) return "";
      return upper != null && Number(upper) > Number(lower) ? `${compactSize(lower)} – ${compactSize(upper)}` : compactSize(lower);
    }

    // The text with the typed words in <mark>, everything escaped.
    function highlight(value, query) {
      const t = text(value);
      const words = text(query).toLowerCase().split(/\s+/).filter(Boolean);
      const low = t.toLowerCase();
      if (!words.length || low.length !== t.length) return esc(t);
      const spans = [];
      words.forEach((w) => { for (let at = low.indexOf(w); at >= 0; at = low.indexOf(w, at + w.length)) spans.push([at, at + w.length]); });
      spans.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
      const merged = [];
      spans.forEach(([start, end]) => {
        const last = merged[merged.length - 1];
        if (last && start <= last[1]) last[1] = Math.max(last[1], end); else merged.push([start, end]);
      });
      let out = "", pos = 0;
      merged.forEach(([start, end]) => {
        out += esc(t.slice(pos, start)) + "<mark>" + esc(t.slice(start, end)) + "</mark>";
        pos = end;
      });
      return out + esc(t.slice(pos));
    }

    // The places named in the proposal's geography, as search words: "Phoenix, AZ and Los Angeles DMA" -> ["Phoenix", "Los Angeles"]. At most three;
    // a state code, "TBD" or "national" is no place to search for.
    function geoSuggestions(value) {
      const seen = new Set();
      const out = [];
      text(value).split(/[,;\n|]+|\s+(?:and|&)\s+/i).forEach((raw) => {
        const q = raw.replace(/\([^)]*\)/g, " ").replace(/\b(?:dmas?|markets?|metro|area)\s*$/i, "").replace(/\s+/g, " ").trim();
        if (q.length < 3 || q.length > 60 || seen.has(q.toLowerCase()) || /^(?:tbd|n\/?a|none|national|nationwide|usa)$/i.test(q)) return;
        seen.add(q.toLowerCase());
        out.push(q);
      });
      return out.slice(0, 3);
    }

    // The language caveat: a short first sentence, the rest on demand.
    function shortNote(note) {
      const first = text(note).match(/^[\s\S]*?[.!?](?=\s|$)/);
      return first ? first[0] : text(note);
    }

    // ---------------------------------------------------------------- state: what the planner has chosen, per line
    const emptySel = () => ({ geoType: "geo_market", geo: null, ageMin: AGE_MIN, ageMax: AGE_MAX, gender: "all", languages: [], detailed: [] });
    // `touched`: the planner has changed something in this workspace, so a saved estimate must not overwrite it (see restore).
    const newWorkspace = () => ({ sel: emptySel(), budget: "", touched: false });
    const T = {
      byKey: {}, active: "dock", product: null, status: null, languageNote: "", languages: null, langQuick: {}, catalog: {}, wired: false, quickBusy: {},
      ta: { geo: { opts: [], rows: [], wait: false, active: -1, seq: 0, timer: null }, lang: { opts: [], rows: [], wait: false, active: -1, seq: 0, timer: null }, aud: { opts: [], rows: [], wait: false, active: -1, seq: 0, timer: null } },
    };
    const peek = (key) => T.byKey[String(key)] || null;                       // never creates a workspace
    const ws = () => T.byKey[T.active] || (T.byKey[T.active] = newWorkspace());
    const sel = () => ws().sel;

    // The choices a saved request block holds (what a snapshot's request.meta says), clamped to what Meta takes. Tolerates any shape: a saved proposal
    // is data anyone could have edited, and a block with nothing usable in it is simply "no choices yet".
    function selFromBlock(block) {
      const s = emptySel();
      const b = isObj(block) ? block : {};
      const g = isObj(b.geo) ? b.geo : null;
      if (g && g.key != null && g.key !== "") {
        if (GEO_TYPES.some(([key]) => key === g.type)) s.geoType = g.type;
        const label = text(g.label);
        const [name, region, country] = label ? label.split(" · ") : [];
        s.geo = { type: s.geoType, key: text(g.key), name: name || `Meta geography ${g.key}`, region: region || "", country: country || "", label };
      }
      const age = (value) => Math.min(Math.max(Math.round(Number(value)) || AGE_MIN, AGE_MIN), AGE_MAX);
      if (b.age_min != null) s.ageMin = age(b.age_min);
      if (b.age_max != null) s.ageMax = Math.max(age(b.age_max), s.ageMin);
      if (b.gender === "male" || b.gender === "female") s.gender = b.gender;
      const usable = (x) => isObj(x) && x.id != null && x.name;
      s.languages = list(b.languages).filter(usable).map((x) => ({ id: text(x.id), name: text(x.name) }));
      s.detailed = list(b.targeting || b.detailed).filter(usable).map((x) => ({ id: text(x.id), name: text(x.name), type: text(x.type || "interests") }));
      return s;
    }

    // What an estimate depends on, as text: two choices with the same signature ask Meta the same question (the order they were clicked in does not matter).
    function signature(s) {
      return JSON.stringify([s.geo ? `${s.geo.type}:${s.geo.key}` : "", s.ageMin, s.ageMax, s.gender,
        s.languages.map((x) => text(x.id)).sort(), s.detailed.map((x) => `${x.type}:${x.id}`).sort()]);
    }

    // ---------------------------------------------------------------- the request
    // Budget and CPM are sent only when known: the line's (Step 04), else - on its own - the one optional amount the planner typed. Without a CPM the
    // server uses the product's own planning CPM.
    function parseMoney(value) {
      const n = Number(text(value).replace(/[$,\s]/g, ""));
      return isFinite(n) && n > 0 ? n : 0;
    }

    function factsNow() {
      const line = lineFacts();
      if (line) return { budget: Number(line.budget) > 0 ? Number(line.budget) : 0, cpm: Number(line.cpm) > 0 ? Number(line.cpm) : null };
      return { budget: parseMoney(ws().budget), cpm: null };
    }

    // The meta block of POST /api/constanza/estimate for some choices: age only when it narrows (18 and 65+ are Meta's own limits), ids from Meta's lists
    // only, sorted so the request does not depend on the order things were clicked in, budget fields only when known.
    function metaBlock(s, facts) {
      const g = s.geo;
      const block = {
        geo: g ? { type: g.type, key: g.key, label: (g.label || g.name || "").slice(0, LABEL_MAX) || null } : null,
        age_min: s.ageMin > AGE_MIN ? s.ageMin : null,
        age_max: s.ageMax < AGE_MAX ? s.ageMax : null,
        gender: s.gender,
        languages: s.languages.map((x) => ({ id: x.id, name: x.name })).sort((a, b) => (a.id < b.id ? -1 : (a.id > b.id ? 1 : 0))),
        targeting: s.detailed.map((x) => ({ id: x.id, name: x.name, type: x.type })).sort((a, b) => { const ka = `${a.type}:${a.id}`, kb = `${b.type}:${b.id}`; return ka < kb ? -1 : (ka > kb ? 1 : 0); }),
      };
      if (facts && facts.budget > 0) block.retail_budget = facts.budget;
      if (facts && facts.cpm > 0) block.retail_cpm = facts.cpm;
      return block;
    }

    // ---------------------------------------------------------------- words for what is chosen
    // The live one-line summary and the count pill of each section.
    function summaries(s) {
      const age = ageText(s.ageMin, s.ageMax);
      const gender = s.gender === "female" ? "Women" : (s.gender === "male" ? "Men" : "");
      const families = langFamilies(s.languages);
      const names = s.detailed.map((x) => x.name);
      const picks = !names.length ? "No interests or behaviors yet" : (names.length <= 2 ? names.join(", ") : `${names.slice(0, 2).join(", ")} +${names.length - 2} more`);
      return {
        where: { text: s.geo ? `${s.geo.name} · ${typeLabel(s.geo.type)}` : "Choose a place", set: !!s.geo, count: 0 },
        who: { text: `${gender || "All genders"} · ${age || "all ages"}`, set: !!(gender || age), count: (gender ? 1 : 0) + (age ? 1 : 0) },
        lang: { text: families.length ? families.map((f) => f.label).join(" · ") : "Any language", set: families.length > 0, count: families.length },
        aud: { text: picks, set: names.length > 0, count: names.length },
      };
    }

    // What an estimate was asked, in pieces of words, from the echo the server returns (meta.targeting) or from a request block: a language is what people
    // have SET their Facebook / Instagram to, so it is never said to be what they speak.
    function fragments(t) {
      const x = isObj(t) ? t : {};
      const gender = x.gender === "male" ? "men" : (x.gender === "female" ? "women" : "");
      const age = x.age_min != null || x.age_max != null ? `ages ${x.age_min != null ? x.age_min : AGE_MIN}–${x.age_max != null ? x.age_max : AGE_MAX + "+"}` : "";
      const languages = langFamilies(list(x.languages).filter(isObj)).map((f) => f.label);
      const picks = list(x.detailed || x.targeting).filter(isObj).map((p) => text(p.name));
      return { gender, age, language: languages.length ? `language set to ${languages.join(" or ")}` : "", detailed: picks.length ? `any of: ${picks.join(", ")}` : "", hasLanguage: languages.length > 0 };
    }

    function placeOf(t) {
      const g = isObj(t) && isObj(t.geo) ? t.geo : null;
      if (!g) return { name: "", kind: "" };
      return { name: text(g.label) || `Meta geography ${text(g.key)}`, kind: GEO_KIND_LOWER[g.type] || text(g.type) };
    }

    // "Los Angeles · California · US (TV market), women ages 25–44, language set to Spanish, any of: Soccer"
    function pulledFor(t) {
      if (!isObj(t) || !isObj(t.geo)) return "";
      const place = placeOf(t), f = fragments(t);
      return [`${place.name} (${place.kind})`, [f.gender, f.age].filter(Boolean).join(" "), f.language, f.detailed].filter(Boolean).join(", ");
    }

    const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

    // The plain-language lines of what is chosen now (what the result and the copied summary say about the request).
    function summaryParts() {
      const s = sel();
      const block = metaBlock(s, null);
      const f = fragments({ age_min: block.age_min, age_max: block.age_max, gender: block.gender, languages: block.languages, detailed: block.targeting });
      const parts = [];
      if (s.geo) parts.push(`${s.geo.name} (${GEO_KIND_LOWER[s.geo.type] || s.geo.type})`);
      [f.gender, f.age, f.language, f.detailed].filter(Boolean).forEach((x) => parts.push(cap(x)));
      return parts;
    }

    // ---------------------------------------------------------------- the panel
    // The head every section shares (the kit's .aud-sec-head). The words stay "Clear"; the accessible name says what it clears, since a screen reader lists
    // the buttons on their own.
    const secHead = (key, title) => `
      <span class="aud-step" aria-hidden="true"></span>
      <div class="aud-sec-text"><h3 class="aud-sec-title" id="cz-mt-${key}-title">${title}</h3><p class="aud-sec-sum" id="cz-mt-${key}-sum" data-mt-sum></p></div>
      <span class="aud-count" id="cz-mt-count-${key}" data-mt-count hidden></span>
      <button type="button" class="aud-clear" id="cz-mt-clear-${key}" data-mt-act="clear" data-sec="${key}" aria-label="${CLEAR_LABEL[key]}" hidden>Clear</button>`;

    function typeahead(which, label, placeholder, aside, after) {
      return `<div class="aud-field"><label class="aud-label" for="cz-mt-${which}-q">${label}${aside ? `<span class="aud-label-aside">${aside}</span>` : ""}</label>
        <div class="aud-ta" data-mt-ta="${which}"><input class="aud-input" id="cz-mt-${which}-q" data-mt-ta-input="${which}" data-mt-needs-api type="text" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="cz-mt-${which}-l" aria-haspopup="listbox" autocomplete="off" maxlength="${SEARCH_MAX[which]}" placeholder="${esc(placeholder)}" />
          <div class="aud-ta-list" id="cz-mt-${which}-l" role="listbox" aria-label="${esc(label)}" data-mt-ta-list="${which}" hidden></div></div>${after || ""}</div>`;
    }

    // 1 Where: a bare section (the dock puts it in the slot between its own Where and When; a nested .aud-builder would restart the numbers).
    function whereHtml() {
      const types = GEO_TYPES.map(([key, label]) => `<button type="button" data-mt-act="geotype" data-type="${key}" aria-pressed="false">${label}</button>`).join("");
      return `
<section class="aud-sec mt-where" id="cz-mt-sec-where" aria-labelledby="cz-mt-where-title">
  <div class="aud-sec-head">${secHead("where", "Where")}</div>
  <div class="aud-sec-body">
    <div class="aud-field"><span class="aud-label" id="cz-mt-gt-l">Kind of place</span>
      <div class="aud-seg aud-seg--wrap" id="cz-mt-geotype" role="group" aria-labelledby="cz-mt-gt-l">${types}</div>
      <p class="aud-hint" id="cz-mt-geohint" data-mt-geohint></p></div>
    ${typeahead("geo", "Find the place", GEO_PLACEHOLDER.geo_market, "one place at a time", `<p class="aud-hint" data-mt-off hidden></p>`)}
    <div class="aud-quick mt-quick--plain" id="cz-mt-geo-pills" role="group" aria-label="Places named in this proposal" hidden></div>
    <ul class="aud-chips" id="cz-mt-geo-chips" data-mt-chips="geo" aria-label="Chosen place"></ul>
  </div>
</section>`;
    }

    function whoBody() {
      const options = (from, to) => { let out = ""; for (let a = from; a <= to; a++) out += `<option value="${a}">${a === AGE_MAX ? "65+" : a}</option>`; return out; };
      const presets = AGE_PRESETS.map(([label, min, max]) => `<button type="button" data-mt-act="age-preset" data-min="${min}" data-max="${max}" aria-pressed="false">${label}</button>`).join("");
      const genders = [["all", "All"], ["female", "Women"], ["male", "Men"]].map(([key, label]) => `<button type="button" data-mt-act="gender" data-gender="${key}" aria-pressed="false">${label}</button>`).join("");
      return `<div class="aud-field"><span class="aud-label" id="cz-mt-age-l">Age</span>
          <div class="aud-range" role="group" aria-labelledby="cz-mt-age-l">
            <div class="aud-range-selects"><select class="aud-select" id="cz-mt-age-min" data-mt-age="min" aria-label="Youngest age">${options(AGE_MIN, AGE_MAX)}</select><span class="aud-range-sep">to</span><select class="aud-select" id="cz-mt-age-max" data-mt-age="max" aria-label="Oldest age">${options(AGE_MIN, AGE_MAX)}</select></div>
            <div class="aud-quick" role="group" aria-label="Age ranges">${presets}</div></div></div>
        <div class="aud-field"><span class="aud-label" id="cz-mt-gender-l">Gender</span>
          <div class="aud-seg" id="cz-mt-gender" role="group" aria-labelledby="cz-mt-gender-l">${genders}</div></div>`;
    }

    function languageBody() {
      const quick = LANG_QUICK.map((q) => `<button type="button" data-mt-act="lang-quick" data-mt-needs-api data-key="${q.key}" aria-pressed="false">${esc(q.label)}</button>`).join("");
      return `<div class="aud-quick mt-suggest" id="cz-mt-suggest" data-mt-suggest hidden><button type="button" data-mt-act="lang-quick" data-mt-needs-api data-key="spanish">This line targets Hispanic audiences — add Spanish?</button></div>
        <div class="aud-quick" id="cz-mt-lang-quick" role="group" aria-label="Quick language picks"><span class="aud-quick-label">Quick picks</span>${quick}</div>
        <ul class="aud-chips" id="cz-mt-lang-chips" data-mt-chips="lang" aria-label="Chosen languages"></ul>
        ${typeahead("lang", "Another language", "Language, e.g. Portuguese", "", `<p class="aud-hint"><button type="button" class="aud-link" data-mt-act="browse-lang" data-mt-needs-api>Browse all languages</button></p><p class="aud-hint" data-mt-browse-reason hidden></p>`)}
        <div class="aud-notice aud-notice--error" data-mt-msg="lang" role="alert" hidden><div class="aud-notice-body"></div></div>
        <div class="mt-langnote"><p class="aud-hint" data-mt-langshort></p>
          <details class="mt-more"><summary>Why this can be fewer than all Spanish speakers</summary><p class="aud-hint" data-mt-langfull></p></details></div>`;
    }

    function audienceBody() {
      return `${typeahead("aud", "Interests, behaviors and more", "e.g. soccer, new parents, travel", "optional", "")}
        <ul class="aud-chips" id="cz-mt-aud-chips" data-mt-chips="aud" aria-label="Chosen interests, behaviors and more"></ul>
        <p class="aud-hint" data-mt-audhint>Meta’s own lists. Choose several and the audience gets wider: Meta counts people who match <strong>any</strong> of them. <span data-mt-none>None chosen yet: everyone in the place, age, gender and language above is counted.</span></p>
        <button type="button" class="aud-browse" id="cz-mt-browse" data-mt-act="browse" data-mt-needs-api aria-haspopup="dialog"><span class="aud-browse-label">Browse all interests, behaviors &amp; demographics</span><span class="aud-browse-count" data-mt-browsecount hidden></span></button>
        <p class="aud-hint" data-mt-browse-reason hidden></p>`;
    }

    const section = (key, title, body) => `
    <section class="aud-sec" id="cz-mt-sec-${key}" aria-labelledby="cz-mt-${key}-title">
      <div class="aud-sec-head">${secHead(key, title)}</div>
      <div class="aud-sec-body">${body}</div>
    </section>`;

    // 6 Budget cross-check (optional, folded). With a Step 05 line open the budget and CPM are the line's and only shown; on its own the planner may type
    // one amount for the whole flight. Either way it only adds the cross-check to the answer: the audience and the scenarios never depend on it.
    function budgetHtml() {
      return `
    <details class="aud-sec" id="cz-mt-sec-budget">
      <summary class="aud-sec-head cz-sec-head">
        <span class="aud-step" aria-hidden="true"></span>
        <div class="aud-sec-text"><span class="aud-sec-title" id="cz-mt-budget-title">Budget cross-check</span><p class="aud-sec-sum" id="cz-mt-budget-sum"></p></div>
      </summary>
      <div class="aud-sec-body">
        <div class="aud-field" id="cz-mt-budget-line" hidden><p class="aud-hint" id="cz-mt-budget-fromline"></p></div>
        <div class="aud-field" id="cz-mt-budget-own">
          <label class="aud-label" for="cz-mt-budget"><span>Retail budget for the whole flight</span><span class="aud-label-aside">optional</span></label>
          <input class="aud-input" id="cz-mt-budget" data-mt-budget type="text" inputmode="decimal" autocomplete="off" placeholder="e.g. 2,000" aria-describedby="cz-mt-budget-note" />
          <p class="aud-hint" id="cz-mt-budget-note"></p>
        </div>
        <p class="aud-hint">The budget never changes the audience or the scenarios. It only adds one line: how many impressions that budget buys at the planning CPM, 2x retail over media. That is a cross-check, not avails.</p>
      </div>
    </details>`;
    }

    function formHtml() {
      return `
<div class="cz-mt mt-panel" id="cz-mt">
  <div id="cz-mt-status" class="aud-notice aud-notice--info" data-state="checking" role="status" tabindex="-1"></div>
  <div id="cz-mt-dates" class="aud-notice aud-notice--info" hidden><div class="aud-notice-body">No flight dates on this option: the flight length is not known, so 1 month is assumed.</div></div>
  <div class="aud-builder">
    ${section("who", "Who", whoBody())}
    ${section("lang", "Language", languageBody())}
    ${section("aud", "Audience", audienceBody())}
    ${budgetHtml()}
  </div>
</div>`;
    }

    // ---------------------------------------------------------------- the connection to Meta
    // null while unknown (not asked yet, or the check failed: the estimate then tells its own error), else whether the admin has set Meta up.
    const connected = () => { const st = T.status; return st && st.data && typeof st.data.configured === "boolean" ? st.data.configured : null; };

    function offReason() {
      const st = T.status;
      const names = ((st && st.data && st.data.missing) || []).map((x) => text(x).split(/\s/)[0]).filter(Boolean);
      return `Meta isn't connected yet: an admin needs to add ${names.length ? names.join(" and ") : "the Meta settings"} in the app's secrets.`;
    }

    // The planning rule the server reports, in words: "lower-end audience × 5.5 (4.3 – 8.6) a month".
    function ruleShort(scenarios) {
      const m = isObj(scenarios) && isObj(scenarios.multipliers) ? scenarios.multipliers : null;
      return m && m.mid != null ? `lower-end audience × ${text(m.mid)} (${text(m.conservative)} – ${text(m.upper)}) a month` : "";
    }

    // The banner above the cards: asking, connected (with the ad account's last four digits), not set up (naming what is missing), or unreachable. The
    // banner itself is a status region that stays put (only its contents are replaced), so a change of answer is announced; when the keyboard was in it
    // ("Check again" is replaced by the new answer) it stays there: on the new "Check again", else on the banner.
    function renderStatus() {
      const el = $("cz-mt-status");
      if (!el) return;
      const st = T.status;
      const held = !!document.activeElement && el.contains(document.activeElement);
      const say = (kind, state, html) => {
        el.className = `aud-notice aud-notice--${kind}`;
        el.dataset.state = state;
        el.innerHTML = `<div class="aud-notice-body">${html}</div>`;
        if (held) (el.querySelector('[data-mt-act="recheck"]') || el).focus();
      };
      if (!st || (!st.data && !st.error)) { say("info", "checking", `<span class="aud-notice-title">Checking the connection to Meta…</span>`); return; }
      if (!st.data) {
        say("warn", "error", `<span class="aud-notice-title">Couldn’t check the Meta connection</span>${esc(st.error)} <button type="button" class="aud-notice-action" data-mt-act="recheck">Check again</button>`);
        return;
      }
      const d = st.data;
      if (d.configured) {
        const rule = ruleShort(d.scenarios);
        say("connected", "on", `<span class="aud-notice-title">Connected to Meta</span>${d.account ? `Ad account <code>${esc(d.account)}</code>` : "Ad account connected"}${d.version ? ` · API ${esc(d.version)}` : ""}${rule ? ` · impressions = ${esc(rule)}` : ""}`);
        return;
      }
      const missing = list(d.missing).map((item) => {
        const m = text(item).match(/^(\S+)([\s\S]*)$/);
        return `<li><code>${esc(m ? m[1] : item)}</code>${m && m[2] ? esc(m[2]) : ""}</li>`;
      }).join("");
      say("warn", "off", `<span class="aud-notice-title">Meta isn’t connected yet</span>An admin needs to add ${missing ? "these settings" : "the Meta settings"} in the app’s secrets before audiences can be pulled.${missing ? `<ul class="mt-missing">${missing}</ul>` : ""}`
        + `${d.setup_hint ? `<details class="mt-more"><summary>How an admin sets this up</summary><p>${esc(d.setup_hint)}</p></details>` : ""}`);
    }

    // Asks the server whether Meta is set up, at most once per STATUS_TTL_MS and never twice at once. Called when the product is chosen and by the first
    // connection() of a Step 05 line - never when the page loads. The answer is shown by the banner, the pull button (goState) and the controls that ask Meta.
    function ensureStatus(force) {
      const cur = T.status;
      if (cur && cur.pending) return cur.pending;
      if (!force && cur && cur.at && Date.now() - cur.at < STATUS_TTL_MS) return Promise.resolve(cur);
      const pending = (async () => {
        let next;
        try {
          const r = await api("/api/constanza/meta/status");
          next = r.ok ? { at: Date.now(), data: r.data, error: null } : { at: Date.now(), data: null, error: errorText(r.data, "The connection check didn’t work.") };
        } catch (e) {
          next = { at: Date.now(), data: null, error: text((e && e.message) || e) };
        }
        T.status = Object.assign(next, { pending: null });
        renderStatus();
        applyStatus();
        opt("refreshGo");
        return T.status;
      })();
      T.status = Object.assign({}, cur || {}, { pending });
      return pending;
    }

    // The language caveat in the server's own words (status.language_note, or the languages answer's note).
    const languageNote = () => (T.status && T.status.data && T.status.data.language_note) || T.languageNote || LANGUAGE_NOTE;
    const browseOff = () => (window.SegmentBrowser ? "" : BROWSE_MISSING);

    // ---------------------------------------------------------------- finding things in the two roots (the Where card is mounted apart from the panel)
    const roots = () => [$("cz-mt"), $("cz-mt-sec-where")].filter(Boolean);
    const all = (selector) => roots().reduce((acc, root) => acc.concat(Array.from(root.querySelectorAll(selector))), []);
    const one = (selector) => all(selector)[0] || null;
    const taInput = (which) => $(`cz-mt-${which}-q`);
    const taList = (which) => $(`cz-mt-${which}-l`);
    const mounted = () => !!$("cz-mt");

    // What the connection state means for the controls: the controls that ask Meta are off when Meta is known not to be connected (each would only answer
    // with the same error), the browse controls are off when the pop-up's script is missing, and the caveat shows the server's wording.
    function applyStatus() {
      if (!mounted()) return;
      const off = connected() === false, noBrowse = browseOff();
      all("[data-mt-needs-api]").forEach((el) => {
        el.disabled = off || (!!noBrowse && /^browse/.test(el.dataset.mtAct || ""));
        if (el.dataset.mtAct !== "lang-quick") return;
        // A quick pick that is waiting for Meta is aria-disabled and aria-busy, never `disabled`: that would take the keyboard off the button just pressed.
        // (quickLanguage ignores a press while it is set.)
        const busy = !!T.quickBusy[el.dataset.key];
        if (busy) { el.setAttribute("aria-disabled", "true"); el.setAttribute("aria-busy", "true"); } else { el.removeAttribute("aria-disabled"); el.removeAttribute("aria-busy"); }
      });
      all("[data-mt-off]").forEach((el) => { el.hidden = !off; el.textContent = off ? offReason() : ""; });
      all("[data-mt-browse-reason]").forEach((el) => { el.hidden = !noBrowse; el.textContent = noBrowse; });
      const note = languageNote();
      const short = one("[data-mt-langshort]"), full = one("[data-mt-langfull]");
      if (short) short.textContent = shortNote(note);
      if (full) full.textContent = note;
    }

    // ---------------------------------------------------------------- keeping the screen in step with the choices
    const chipHtml = (cls, label, meta, act, attrs, title) => `<li class="aud-chip ${cls}"${title ? ` title="${esc(title)}"` : ""}><span class="aud-chip-label">${esc(label)}</span>`
      + `${meta ? `<span class="aud-chip-meta">${esc(meta)}</span>` : ""}`
      + `<button type="button" class="aud-chip-x" data-mt-act="${act}" ${attrs} aria-label="Remove ${esc(label)}">×</button></li>`;

    function geoChips(s) {
      const g = s.geo;
      return g ? chipHtml("aud-chip--geo", g.name, [typeLabel(g.type), g.region].filter(Boolean).join(" · "), "remove-geo", "", g.label || "") : "";
    }
    function langChips(s) {
      return langFamilies(s.languages).map((f) => chipHtml("aud-chip--lang", f.label, f.items.length > 1 ? `${f.items.length} versions` : "", "remove-lang",
        `data-fam="${esc(f.key)}"`, f.items.length > 1 ? f.items.map((x) => x.name).join(", ") : "")).join("");
    }
    function detailedChips(s) {
      return s.detailed.map((x, i) => chipHtml(x.type === "interests" || x.type === "behaviors" ? "aud-chip--interest" : "aud-chip--demo", x.name, kindLabel(x.type), "remove-detailed", `data-i="${i}"`, "")).join("");
    }

    const quickOn = (s, q) => q.words.every((w) => s.languages.some((l) => langStarts(l.name, w)));

    // The proposal's own places, as one-click searches (never run by themselves): redrawn only when the places change, so a button holding the keyboard
    // is not replaced under it.
    function syncPills(s) {
      const box = $("cz-mt-geo-pills");
      if (!box) return;
      const places = geoSuggestions(proposalGeo());
      const sig = JSON.stringify(places);
      if (box.dataset.sig !== sig) {
        box.dataset.sig = sig;
        box.innerHTML = places.length
          ? `<span class="aud-quick-label">In this proposal</span>` + places.map((q) => `<button type="button" data-mt-act="geo-suggest" data-mt-needs-api data-q="${esc(q)}">Search “${esc(q)}”</button>`).join("")
          : "";
        applyStatus();
      }
      box.hidden = !places.length || !!s.geo;
    }

    // The budget card: what it says is the line's (read-only) or the planner's own optional amount.
    function syncBudget() {
      const sum = $("cz-mt-budget-sum");
      if (!sum) return;
      const line = lineFacts();
      const fromLine = $("cz-mt-budget-line"), own = $("cz-mt-budget-own");
      fromLine.hidden = !line;
      own.hidden = !!line;
      if (line) {
        const budget = Number(line.budget) > 0 ? Number(line.budget) : 0, months = Number(line.months) || 1, cpm = Number(line.cpm) > 0 ? Number(line.cpm) : 0;
        const said = budget
          ? `${money(budget)} over ${months} month${months === 1 ? "" : "s"}${cpm ? ` at a ${money(cpm)} retail CPM` : ""}`
          : "No budget on this line";
        sum.textContent = budget ? said : "None · optional";
        $("cz-mt-budget-fromline").textContent = budget
          ? `From this line (Step 04): ${said}. Change it there.`
          : "This line has no budget yet, so there is no cross-check. Add a budget in Step 04 to see one.";
        sum.classList.toggle("is-set", !!budget);
        $("cz-mt-sec-budget").classList.toggle("has-value", !!budget);
        return;
      }
      const input = $("cz-mt-budget");
      const typed = ws().budget;
      if (input && input.value !== typed && document.activeElement !== input) input.value = typed;
      const amount = parseMoney(typed);
      sum.textContent = amount ? `${money(amount)} for the flight` : "None · optional";
      sum.classList.toggle("is-set", !!amount);
      $("cz-mt-sec-budget").classList.toggle("has-value", !!amount);
      // The CPM itself is named by the result's cross-check (the dock's product object does not carry it).
      $("cz-mt-budget-note").textContent = text(typed).trim() && !amount ? "Type a dollar amount, like 2,000." : "Checked at the product’s planning CPM.";
    }

    // Brings everything that shows a choice into line with the active workspace: each section's summary, count and Clear, the chips, the pressed buttons,
    // the age menus (a "to" age below the "from" age cannot be picked). It never replaces an input, so typing and focus are not disturbed.
    function sync() {
      if (!mounted()) return;
      const s = sel(), sums = summaries(s);
      ["where", "who", "lang", "aud"].forEach((key) => {
        const sec = $(`cz-mt-sec-${key}`);
        if (!sec) return;
        const x = sums[key];
        sec.classList.toggle("has-value", x.set);
        const sum = $(`cz-mt-${key}-sum`);
        sum.textContent = x.text;
        sum.classList.toggle("is-set", x.set);
        const n = $(`cz-mt-count-${key}`);
        n.hidden = !x.count;
        n.textContent = String(x.count);
        $(`cz-mt-clear-${key}`).hidden = !x.set;
      });
      const chips = { geo: geoChips(s), lang: langChips(s), aud: detailedChips(s) };
      Object.keys(chips).forEach((key) => { const el = $(`cz-mt-${key}-chips`); if (el && el.innerHTML !== chips[key]) el.innerHTML = chips[key]; });
      const pressed = (selector, test) => all(selector).forEach((b) => b.setAttribute("aria-pressed", String(test(b))));
      pressed('[data-mt-act="geotype"]', (b) => b.dataset.type === s.geoType);
      pressed('[data-mt-act="gender"]', (b) => b.dataset.gender === s.gender);
      pressed('[data-mt-act="age-preset"]', (b) => Number(b.dataset.min) === s.ageMin && Number(b.dataset.max) === s.ageMax);
      pressed('#cz-mt-lang-quick [data-mt-act="lang-quick"]', (b) => { const q = LANG_QUICK.find((x) => x.key === b.dataset.key); return !!q && quickOn(s, q); });
      const input = taInput("geo");
      if (input) input.placeholder = GEO_PLACEHOLDER[s.geoType] || GEO_PLACEHOLDER.geo_market;
      const hint = $("cz-mt-geohint");
      if (hint) hint.textContent = s.geoType === "geo_market" ? COMSCORE_HINT : "";
      syncPills(s);
      const min = $("cz-mt-age-min"), max = $("cz-mt-age-max");
      if (min && max) {
        min.value = String(s.ageMin);
        max.value = String(s.ageMax);
        Array.from(min.options).forEach((o) => { o.disabled = Number(o.value) > s.ageMax; });
        Array.from(max.options).forEach((o) => { o.disabled = Number(o.value) < s.ageMin; });
      }
      const suggest = $("cz-mt-suggest");
      if (suggest) suggest.hidden = !(!!opt("targetsHispanic") && !s.languages.some((l) => langStarts(l.name, "Spanish")));
      const none = one("[data-mt-none]");
      if (none) none.hidden = s.detailed.length > 0;
      const browse = one("[data-mt-browsecount]");
      if (browse) { browse.hidden = !s.detailed.length; browse.textContent = s.detailed.length ? `${s.detailed.length} chosen` : ""; }
      const dates = $("cz-mt-dates");
      if (dates) { const line = lineFacts(); dates.hidden = !(line && line.has_dates === false); }       // the line's months are a stand-in when it has no readable dates
      applyStatus();
      syncBudget();
    }

    function renderAll() {
      if (!mounted()) return;
      renderStatus();
      sync();
    }

    // A choice that changes the estimate was changed: show it, remember the planner has taken over this workspace, and tell the dock the request changed.
    function changed() {
      ws().touched = true;
      sync();
      markStale();
    }

    function localMessage(sec, message) {
      const el = one(`[data-mt-msg="${sec}"]`);
      if (!el) return;
      el.hidden = !message;
      el.querySelector(".aud-notice-body").textContent = message || "";
    }

    // After a chip's remove button is gone, the keyboard goes to the next chip's remove button, else the one before it, else the section's own control.
    function focusChips(which, index, fallbackId) {
      const buttons = $(`cz-mt-${which}-chips`).querySelectorAll(".aud-chip-x");
      const target = buttons[Math.min(index, buttons.length - 1)] || $(fallbackId);
      if (target) target.focus();
    }

    // ---------------------------------------------------------------- typeahead lists (Where, Language, Audience)
    // rows: { group } a label | { note } a line of text | { foot: { act, label } } the "Browse all" row | an option { nameHtml, path, size, disabled, pick }.
    // `wait`: the rows are only a note that an answer is on its way ("Searching Meta…"): nothing real to show again if the search is dropped (see taClose).
    function taShow(which, rows, wait) {
      const input = taInput(which), box = taList(which);
      if (!input || !box) return;
      const t = T.ta[which];
      t.rows = rows;
      t.wait = !!wait;
      t.opts = [];
      t.active = -1;
      let html = "";
      rows.forEach((row) => {
        if (row.group) html += `<div class="aud-ta-group" role="presentation">${esc(row.group)}</div>`;
        else if (row.note != null) html += `<div class="aud-ta-note" role="presentation">${esc(row.note)}</div>`;
        else if (row.foot) {
          const off = /^browse/.test(row.foot.act) ? browseOff() : "";
          html += `<div class="aud-ta-foot" role="presentation"><button type="button" class="aud-link" data-mt-act="${row.foot.act}"${off ? ` disabled title="${esc(off)}"` : ""}>${esc(row.foot.label)}</button></div>`;
        } else {
          const k = t.opts.length;
          t.opts.push(row);
          html += `<div class="aud-ta-opt${row.disabled ? " is-disabled" : ""}" role="option" id="${box.id}-o${k}" aria-selected="false"${row.disabled ? ' aria-disabled="true"' : ""} data-k="${k}">`
            + `<span class="aud-ta-text"><span class="aud-ta-name">${row.nameHtml}</span>${row.path ? `<span class="aud-ta-path">${esc(row.path)}</span>` : ""}</span>`
            + `${row.size ? `<span class="aud-ta-size">${esc(row.size)}</span>` : ""}</div>`;
        }
      });
      box.innerHTML = html;
      box.hidden = false;
      input.setAttribute("aria-expanded", "true");
      input.removeAttribute("aria-activedescendant");
    }

    const taWait = (which, note) => taShow(which, [{ note }], true);

    // Closes a list AND drops the search that was about to fill it (a debounce that has not fired, or an answer still on its way): otherwise it would
    // open the list again after Escape, or after the keyboard has left the box. A wait note is forgotten with the search it stood for: the key that opens
    // the list again (ArrowDown) would otherwise show "Searching Meta…" with nothing left to answer it, for good; it asks again instead (askAgain).
    function taClose(which) {
      const t = T.ta[which];
      clearTimeout(t.timer);
      t.seq++;
      if (t.wait) { t.rows = []; t.opts = []; t.wait = false; }
      const input = taInput(which), box = taList(which);
      if (box) box.hidden = true;
      if (input) { input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); }
    }

    function taMove(which, delta) {
      const t = T.ta[which], box = taList(which), input = taInput(which);
      const enabled = t.opts.map((o, k) => (o.disabled ? -1 : k)).filter((k) => k >= 0);
      if (!enabled.length || !box) return;
      const at = enabled.indexOf(t.active);
      const next = at < 0 ? (delta > 0 ? enabled[0] : enabled[enabled.length - 1]) : enabled[(at + delta + enabled.length) % enabled.length];
      box.querySelectorAll(".aud-ta-opt").forEach((el) => {
        const on = Number(el.dataset.k) === next;
        el.classList.toggle("is-active", on);
        el.setAttribute("aria-selected", String(on));
        if (on) { input.setAttribute("aria-activedescendant", el.id); if (el.scrollIntoView) el.scrollIntoView({ block: "nearest" }); }
      });
      t.active = next;
    }

    function taPick(which, k) {
      const o = T.ta[which].opts[k];
      if (o && !o.disabled && typeof o.pick === "function") o.pick();
    }

    // The list of a typeahead is done with: close it, empty the box, put the keyboard back in the box for the next pick.
    function taDone(which) {
      const input = taInput(which);
      taClose(which);
      if (input) { input.value = ""; input.focus(); }
    }

    const closeLists = () => ["geo", "lang", "aud"].forEach((which) => { if (taList(which)) taClose(which); });

    // 1 Where: Meta's own geography search for the chosen kind of place.
    async function geoSearch(query) {
      const t = T.ta.geo;
      const seq = ++t.seq;
      taWait("geo", "Searching Meta…");
      let r;
      try { r = await api("/api/constanza/meta-geos", { query, location_type: sel().geoType }); } catch (e) { r = { ok: false, data: { detail: text((e && e.message) || e) } }; }
      if (seq !== t.seq || !taInput("geo")) return;
      if (!r.ok) { taShow("geo", [{ note: errorText(r.data, "Meta’s place search isn’t available right now.") }]); return; }
      const rows = list(r.data.locations).map((loc) => ({
        nameHtml: highlight(loc.name, query), path: [loc.region, loc.country_code].filter(Boolean).join(" · "),
        pick: () => {
          const label = [loc.name, loc.region, loc.country_code].filter(Boolean).join(" · ");
          const s = sel();
          s.geo = { type: loc.type || s.geoType, key: text(loc.key), name: text(loc.name), region: text(loc.region), country: text(loc.country_code), label };
          taDone("geo");
          changed();
          announce(`Place chosen: ${loc.name}`);
        },
      }));
      taShow("geo", rows.length ? rows : [{ note: "Meta found no matching place. Try a city, ZIP, state, or the market name as Meta lists it (Meta’s markets are Comscore markets, which can differ from Nielsen DMAs)." }]);
      announce(rows.length ? `${rows.length} places found` : "No places found");
    }

    // 5 Audience: what Meta's keyword search finds, grouped by kind, with the audience size beside each.
    async function audSearch(query) {
      const t = T.ta.aud;
      const seq = ++t.seq;
      taWait("aud", "Searching Meta…");
      let r;
      try { r = await api(`/api/constanza/meta-targeting?q=${encodeURIComponent(query)}`); } catch (e) { r = { ok: false, data: { detail: text((e && e.message) || e) } }; }
      if (seq !== t.seq || !taInput("aud")) return;
      if (!r.ok) { taShow("aud", [{ note: errorText(r.data, "Meta’s interest search isn’t available right now.") }]); return; }
      const s = sel();
      const have = new Set(s.detailed.map((x) => `${x.type}:${x.id}`));
      const full = s.detailed.length >= MAX_DETAILED;
      const found = isObj(r.data.results) ? r.data.results : {};
      let rows = [];
      [["Interests", found.interests], ["Behaviors", found.behaviors], ["Demographics", found.demographics]].forEach(([label, items]) => {
        if (!items || !items.length) return;
        rows.push({ group: label });
        items.slice(0, 40).forEach((it) => {
          const chosen = have.has(`${it.type}:${it.id}`);
          rows.push({
            nameHtml: highlight(it.name, query), path: list(it.path).join(" › "), size: chosen ? "Added ✓" : sizeText(it.audience_lower, it.audience_upper),
            disabled: chosen || full, pick: () => addDetailed(it),
          });
        });
      });
      if (!rows.length) rows = [{ note: `Nothing found for “${query}”. Try other words, or browse the full lists.` }];
      if (full) rows.unshift({ note: `You have reached the limit of ${MAX_DETAILED} picks. Remove one to add another.` });
      rows.push({ foot: { act: "browse", label: "Browse all interests, behaviors & demographics" } });
      taShow("aud", rows);
    }

    function addDetailed(it) {
      const s = sel();
      if (s.detailed.length >= MAX_DETAILED || s.detailed.some((x) => x.id === text(it.id) && x.type === it.type)) return;
      s.detailed.push({ id: text(it.id), name: text(it.name), type: text(it.type) });
      taDone("aud");
      changed();
      announce(`Added ${it.name}`);
    }

    // 4 Language: Meta's whole list of languages, asked for once and then searched here. Only a list with something in it is kept: an empty answer (like an
    // error) is asked again the next time, as the server does, so a hiccup at Meta does not leave the picker empty until the page is reloaded.
    function languageList() {
      if (!T.languages) {
        const asked = (async () => {
          let r;
          try { r = await api("/api/constanza/meta-languages"); } catch (e) { throw new Error(text((e && e.message) || e)); }
          if (!r.ok) throw new Error(errorText(r.data, "Meta’s list of languages isn’t available right now."));
          if (r.data.note) T.languageNote = r.data.note;
          return list(r.data.items).map((x) => ({ id: text(x.id), name: text(x.name) }));
        })().then((items) => { if (!items.length && T.languages === asked) T.languages = null; return items; },
          (error) => { if (T.languages === asked) T.languages = null; throw error; });
        T.languages = asked;
      }
      return T.languages;
    }

    async function langSearch(query) {
      const t = T.ta.lang;
      const seq = ++t.seq;
      if (!query) { taClose("lang"); return; }
      if (!T.languages) taWait("lang", "Loading Meta’s languages…");
      try {
        const everything = await languageList();
        if (seq !== t.seq || !taInput("lang")) return;
        if (!everything.length) {
          taShow("lang", [{ note: "Meta’s list of languages came back empty just now." }, { foot: { act: "lang-retry", label: "Try again" } }]);
          return;
        }
        const s = sel();
        const words = fold(query).split(/\s+/).filter(Boolean);
        const have = new Set(s.languages.map((x) => x.id));
        const hits = everything.filter((it) => words.every((w) => fold(it.name).includes(w)));
        hits.sort((a, b) => Number(fold(b.name).startsWith(words[0])) - Number(fold(a.name).startsWith(words[0])));
        const rows = hits.slice(0, 40).map((it) => ({
          nameHtml: highlight(it.name, query), path: "", size: have.has(it.id) ? "Added ✓" : "", disabled: have.has(it.id),
          pick: () => {
            const capped = addLanguages(sel(), [it]);
            taDone("lang");
            localMessage("lang", capped ? `You can choose at most ${MAX_LANGUAGES} languages.` : "");
            changed();
            announce(`Added ${it.name}`);
          },
        }));
        if (hits.length > 40) rows.push({ note: `${hits.length - 40} more: type more letters to narrow.` });
        taShow("lang", rows.length ? rows : [{ note: `Meta lists no language matching “${query}”.` }]);
      } catch (error) {
        if (seq === t.seq && taInput("lang")) taShow("lang", [{ note: text((error && error.message) || error) }, { foot: { act: "lang-retry", label: "Try again" } }]);
      }
    }

    // The locales Meta lists whose name starts with one word ("Spanish" -> Spanish, Spanish (Spain), ...). Remembered for the session once found.
    async function resolveLanguages(word) {
      const key = fold(word);
      if (T.langQuick[key]) return T.langQuick[key];
      const r = await api(`/api/constanza/meta-languages?q=${encodeURIComponent(word)}`);
      if (!r.ok) throw new Error(errorText(r.data, "Meta’s list of languages isn’t available right now."));
      if (r.data.note) T.languageNote = r.data.note;
      const found = list(r.data.items).filter((it) => langStarts(it.name, word)).map((it) => ({ id: text(it.id), name: text(it.name) }));
      if (found.length) T.langQuick[key] = found;
      return found;
    }

    // Adds the languages not yet chosen, up to Meta's limit here; true when some were left out for that.
    function addLanguages(s, items) {
      let capped = false;
      items.forEach((it) => {
        if (s.languages.some((x) => x.id === it.id)) return;
        if (s.languages.length >= MAX_LANGUAGES) { capped = true; return; }
        s.languages.push({ id: it.id, name: it.name });
      });
      return capped;
    }

    // A language quick pick: pressed = every locale of those languages is in, so a press takes them out; otherwise they are looked up LIVE and added.
    async function quickLanguage(key) {
      const quick = LANG_QUICK.find((q) => q.key === key);
      if (!quick || T.quickBusy[key]) return;                      // a press while Meta is still being asked does nothing
      const w = ws();
      localMessage("lang", "");
      if (quickOn(w.sel, quick)) {
        w.sel.languages = w.sel.languages.filter((l) => !quick.words.some((word) => langStarts(l.name, word)));
        changed();
        announce(`${quick.label} removed`);
        return;
      }
      T.quickBusy[key] = true;
      applyStatus();
      let message = "", found = [];
      try {
        for (const word of quick.words) {
          const hits = await resolveLanguages(word);
          if (!hits.length) { message = `Meta lists no language called ${word} for this ad account.`; found = []; break; }
          found = found.concat(hits);
        }
      } catch (error) {
        message = text((error && error.message) || error);
        found = [];
      }
      delete T.quickBusy[key];
      if (!mounted()) return;
      // The planner moved on while Meta was asked (the dock opened for another line, or Reset): the answer belongs to a workspace that is not on screen, and
      // putting it in the one that is would change another line's request. It is dropped (the lookup is remembered, so a second press is instant).
      if (T.byKey[T.active] !== w) { applyStatus(); return; }
      if (message) { localMessage("lang", message); applyStatus(); return; }
      const capped = addLanguages(w.sel, found);
      localMessage("lang", capped ? `You can choose at most ${MAX_LANGUAGES} languages: some were left out.` : "");
      changed();                                                    // also hands the buttons back to the connection state (applyStatus)
      announce(`${quick.label} added`);
    }

    // ---------------------------------------------------------------- the whole lists in the pop-up
    // One list from Meta (interests, behaviors or demographics) as SegmentBrowser items; kept for the session once it has loaded with something in it. An
    // empty answer is not kept and is handed to the window as a message, which gives it a Retry that asks Meta again.
    function loadCatalog(tab) {
      if (!T.catalog[tab]) {
        const asked = (async () => {
          let r;
          try { r = await api(`/api/constanza/meta-targeting?kind=${encodeURIComponent(tab)}`); } catch (e) { return { error: text((e && e.message) || e) }; }
          if (!r.ok) return { error: errorText(r.data, "Meta’s list isn’t available right now.") };
          const items = list(r.data.items).map((it) => ({
            id: `${it.type}:${it.id}`, metaId: text(it.id), name: it.name, type: it.type, path: list(it.path),
            size: it.audience_lower, sizeUpper: it.audience_upper, description: it.description || "",
          }));
          return items.length ? items : { error: `Meta sent back an empty list of ${CATALOG_WORDS[tab] || "options"} just now. Press Retry to ask again.` };
        })().then((result) => { if (result.error && T.catalog[tab] === asked) T.catalog[tab] = null; return result; });
        T.catalog[tab] = asked;
      }
      return T.catalog[tab];
    }

    const tabOf = (type) => (type === "interests" || type === "behaviors" ? type : "demographics");

    function browseAudience() {
      if (!window.SegmentBrowser) { announce(BROWSE_MISSING); return; }
      const w = ws();
      const opener = $("cz-mt-browse");
      window.SegmentBrowser.open({
        title: "Browse interests, behaviors & demographics",
        subtitle: "Meta’s own lists for this ad account. Meta counts people who match any pick, so each one you add widens the audience.",
        tabs: [{ key: "interests", label: "Interests" }, { key: "behaviors", label: "Behaviors" }, { key: "demographics", label: "Demographics" }],
        activeTab: "interests",
        load: (tab) => loadCatalog(tab),
        columns: [{ key: "name", label: "Name" }, { key: "category", label: "Category", value: (it) => list(it.path).join(" › ") }, { key: "size", label: "Audience size", numeric: true }],
        isChosen: (tab, item) => w.sel.detailed.some((x) => x.id === item.metaId && x.type === item.type),
        max: MAX_DETAILED,
        chosenCount: (tab) => w.sel.detailed.filter((x) => tabOf(x.type) === tab).length,
        noun: "options", nounSingular: "option", applyLabel: "Add to audience",
        returnFocus: opener,
        onApply: ({ added, removed }) => {
          const s = w.sel;
          removed.forEach(({ item }) => {
            const at = s.detailed.findIndex((x) => x.id === item.metaId && x.type === item.type);
            if (at >= 0) s.detailed.splice(at, 1);
          });
          added.forEach(({ item }) => {
            if (s.detailed.length < MAX_DETAILED && !s.detailed.some((x) => x.id === item.metaId && x.type === item.type)) {
              s.detailed.push({ id: item.metaId, name: item.name, type: item.type });
            }
          });
          changed();
          announce(`${s.detailed.length} chosen`);
        },
      });
    }

    function browseLanguages() {
      if (!window.SegmentBrowser) { announce(BROWSE_MISSING); return; }
      const w = ws();
      window.SegmentBrowser.open({
        title: "Browse languages",
        subtitle: "Meta’s own list. A language here is the one people have set Facebook or Instagram to, not who speaks it.",
        load: () => languageList().then(
          (items) => (items.length ? items.map((it) => ({ id: it.id, name: it.name })) : { error: "Meta sent back an empty list of languages just now. Press Retry to ask again." }),
          (error) => ({ error: text((error && error.message) || error) })),
        columns: [{ key: "name", label: "Language" }],
        searchPlaceholder: "Search languages",
        isChosen: (tab, item) => w.sel.languages.some((x) => x.id === item.id),
        max: MAX_LANGUAGES, noun: "languages", nounSingular: "language", applyLabel: "Add languages",
        returnFocus: one('[data-mt-act="browse-lang"]'),
        onApply: ({ added, removed }) => {
          removed.forEach(({ item }) => { w.sel.languages = w.sel.languages.filter((x) => x.id !== item.id); });
          addLanguages(w.sel, added.map(({ item }) => ({ id: item.id, name: item.name })));
          changed();
          announce(`${w.sel.languages.length} languages chosen`);
        },
      });
    }

    // ---------------------------------------------------------------- events (delegated from the two roots)
    function clearSection(key) {
      const s = sel();
      if (key === "where") s.geo = null;
      else if (key === "who") { s.ageMin = AGE_MIN; s.ageMax = AGE_MAX; s.gender = "all"; }
      else if (key === "lang") { s.languages = []; localMessage("lang", ""); }
      else if (key === "aud") s.detailed = [];
      changed();
      const sec = $(`cz-mt-sec-${key}`);
      const first = sec && sec.querySelector("[data-mt-ta-input], [data-mt-act=\"gender\"]");
      if (first) first.focus();
    }

    function onClick(event) {
      const option = event.target.closest(".aud-ta-opt");
      if (option) {
        taPick(option.closest("[data-mt-ta-list]").dataset.mtTaList, Number(option.dataset.k));
        return;
      }
      const target = event.target.closest("[data-mt-act]");
      if (!target || target.disabled) return;
      const s = sel(), d = target.dataset;
      const chipIndex = () => Array.from(target.closest("[data-mt-chips]").querySelectorAll(".aud-chip-x")).indexOf(target);
      switch (d.mtAct) {
        case "geotype": {
          s.geoType = d.type;
          sync();
          const q = taInput("geo").value.trim();
          if (q.length >= 2) geoSearch(q); else taClose("geo");
          break;
        }
        case "geo-suggest": {
          const input = taInput("geo");
          input.value = d.q;
          input.focus();
          geoSearch(d.q);
          break;
        }
        case "remove-geo":
          s.geo = null;
          changed();
          announce("Place removed");
          taInput("geo").focus();
          break;
        case "age-preset":
          s.ageMin = Number(d.min);
          s.ageMax = Number(d.max);
          changed();
          break;
        case "gender":
          s.gender = d.gender;
          changed();
          break;
        case "lang-quick": quickLanguage(d.key); break;
        case "remove-lang": {
          const at = chipIndex();
          s.languages = s.languages.filter((l) => fold(langFamily(l.name)) !== d.fam);
          changed();
          focusChips("lang", at, "cz-mt-lang-q");
          break;
        }
        case "remove-detailed": {
          const at = chipIndex();
          s.detailed.splice(Number(d.i), 1);
          changed();
          focusChips("aud", at, "cz-mt-aud-q");
          break;
        }
        case "browse": taClose("aud"); browseAudience(); break;
        case "browse-lang": browseLanguages(); break;
        case "lang-retry": {
          const q = taInput("lang").value.trim();
          if (q) langSearch(q);
          break;
        }
        case "clear": clearSection(d.sec); break;
        case "recheck": ensureStatus(true); break;
        default: break;
      }
    }

    function onInput(event) {
      const budget = event.target.closest("[data-mt-budget]");
      if (budget) {
        const w = ws();
        w.budget = budget.value;
        w.touched = true;
        syncBudget();
        markStale();
        return;
      }
      const input = event.target.closest("[data-mt-ta-input]");
      if (!input) return;
      const which = input.dataset.mtTaInput;
      const t = T.ta[which];
      clearTimeout(t.timer);
      const query = input.value.trim();
      if (which === "lang") { langSearch(query); return; }
      t.seq++;
      if (query.length < 2) {
        if (query) taShow(which, [{ note: "Type at least two letters." }]); else taClose(which);
        return;
      }
      taWait(which, "Searching Meta…");
      t.timer = setTimeout(() => (which === "geo" ? geoSearch(query) : audSearch(query)), 300);
    }

    function onChange(event) {
      const age = event.target.closest("[data-mt-age]");
      if (!age) return;
      const s = sel();
      if (age.dataset.mtAge === "min") s.ageMin = Number(age.value); else s.ageMax = Number(age.value);
      changed();
    }

    // The list was closed before its search answered (Escape, or the keyboard left the box) and the planner asks for it again with the arrow key: the
    // search is asked again, at once, so what they typed is always answered. (A list that did answer is simply shown again, with its hits.)
    function askAgain(which, query) {
      if (which === "lang") { if (query) langSearch(query); }
      else if (query.length >= 2) { if (which === "geo") geoSearch(query); else audSearch(query); }
    }

    function onKeydown(event) {
      const input = event.target.closest("[data-mt-ta-input]");
      if (!input) return;
      const which = input.dataset.mtTaInput;
      const t = T.ta[which];
      const box = taList(which);
      const open = !!box && !box.hidden;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (!open) {
          if (!t.rows.length) { askAgain(which, input.value.trim()); return; }
          taShow(which, t.rows);
        }
        taMove(which, event.key === "ArrowDown" ? 1 : -1);
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (open) {
          const first = t.active >= 0 ? t.active : t.opts.findIndex((o) => !o.disabled);
          if (first >= 0) taPick(which, first);
        } else if (which === "geo" && input.value.trim().length >= 2) {
          geoSearch(input.value.trim());
        }
      } else if (event.key === "Escape") {
        if (open) { event.preventDefault(); event.stopPropagation(); taClose(which); }
        else if (input.value) { event.preventDefault(); event.stopPropagation(); input.value = ""; taClose(which); }
      }
    }

    function onFocusOut(event) {
      const ta = event.target.closest && event.target.closest(".aud-ta");
      if (ta && !ta.contains(event.relatedTarget)) taClose(ta.dataset.mtTa);
    }

    // Wires the panel and the Where card (the dock mounts them apart; bind() and the first select both try, and a root is wired once).
    function wire() {
      roots().forEach((root) => {
        if (root._mtWired) return;
        root._mtWired = true;
        root.addEventListener("click", onClick);
        root.addEventListener("input", onInput);
        root.addEventListener("change", onChange);
        root.addEventListener("keydown", onKeydown);
        root.addEventListener("focusout", onFocusOut);
        root.addEventListener("mousedown", (e) => { if (e.target.closest(".aud-ta-list")) e.preventDefault(); });      // a click in a list must not take the keyboard out of its box
      });
    }

    function bind() {
      wire();
      renderAll();
    }

    // ---------------------------------------------------------------- lines: one workspace each
    function seedFrom(key) {
      const w = newWorkspace();
      const snap = key === "dock" ? null : opt("snapshot", key);
      const block = snap && isObj(snap.request) && isObj(snap.request.meta) ? snap.request.meta : null;
      if (block) w.sel = selFromBlock(block);
      return w;
    }

    // The dock was opened for another line, for the same line again (its workspace is kept) or for none. A line seen for the first time starts from its saved
    // estimate's choices; the dock on its own starts empty.
    function onLine(line) {
      const key = line && line.id != null ? text(line.id) : "dock";
      const switched = key !== T.active;
      T.active = key;
      if (!T.byKey[key]) T.byKey[key] = seedFrom(key);
      if (switched) {
        closeLists();
        ["geo", "lang", "aud"].forEach((which) => { const el = taInput(which); if (el) el.value = ""; });
        localMessage("lang", "");
      }
      wire();
      renderAll();
    }

    // Puts a saved request's choices into the active workspace - unless the planner has already changed it (the dock opened again for the same line keeps
    // their edits; the dock then shows the saved result with the reasons it no longer matches).
    function restore(block) {
      const w = ws();
      if (w.touched || !isObj(block)) return;
      w.sel = selFromBlock(block);
      sync();
    }

    // Reset (and a new proposal): `prevLid` is the line the dock was open for (null for the dock on its own). That line's workspace is forgotten, so its card
    // compares "no workspace" = unchanged; the dock on its own starts clean too, as the rest of the form does.
    function reset(prevLid) {
      if (prevLid != null) delete T.byKey[text(prevLid)];
      delete T.byKey.dock;
      T.active = "dock";
      closeLists();
      ["geo", "lang", "aud"].forEach((which) => { const el = taInput(which); if (el) el.value = ""; });
      localMessage("lang", "");
      const budget = $("cz-mt-budget");
      if (budget) budget.value = "";
      renderAll();
    }

    function onProposalReset() {
      T.byKey = {};
      T.active = "dock";
    }

    // ---------------------------------------------------------------- the pull
    function canPull() {
      if (!sel().geo) {
        const input = taInput("geo");
        if (input) input.focus();
        return { ok: false, reason: "Choose where first: search for a place and pick one from the list." };
      }
      return { ok: true };
    }

    // Not connected: the dock's button stays off with the reason beside it (the controls that ask Meta are off too, each would only answer the same).
    function goState() {
      return connected() === false ? { enabled: false, reason: offReason() } : { enabled: true };
    }

    // What a refused request says, in the planner's words. A plain message passes through; a refusal field by field (FastAPI's 422: detail is a list of
    // { loc, msg }) names the pick or setting it was about, read from the body that was sent, instead of a bare "String should have at most 20 characters".
    function refusal(data, fallback, body) {
      const detail = data && data.detail;
      if (!Array.isArray(detail) || !detail.length) return errorText(data, fallback);
      const sent = isObj(body) ? body : {};
      const sentMeta = isObj(sent.meta) ? sent.meta : {};
      const lines = [];
      detail.forEach((item) => {
        const loc = item && Array.isArray(item.loc) ? item.loc.filter((x) => x !== "body") : [];
        const inMeta = loc[0] === "meta";
        const path = inMeta ? loc.slice(1) : loc;
        const msg = text(item && (item.msg || item.message)).trim().replace(/\.$/, "");
        const key = text(path[0]);
        const at = typeof path[1] === "number" ? path[1] : -1;
        const pick = inMeta && key === "targeting" ? list(sentMeta.targeting)[at] : null;
        const language = inMeta && key === "languages" ? list(sentMeta.languages)[at] : null;
        let who = "";
        if (isObj(pick)) who = `“${text(pick.name)}” (${kindLabel(pick.type)})`;
        else if (isObj(language)) who = `the language “${text(language.name)}”`;
        else if (key) {
          const dotted = path.filter((x) => typeof x !== "number").slice(0, 2).join(".");
          who = FIELD_WORDS[dotted] || FIELD_WORDS[key] || key.replace(/_/g, " ");
        }
        const line = who ? `${who}: ${msg || "not accepted"}` : msg;
        if (line && lines.indexOf(line) < 0) lines.push(line);
      });
      return lines.length ? `${lines.join("; ")}. Change that choice and estimate again.` : (fallback || "Request failed.");
    }

    function onError(status) {
      if (status === 503) ensureStatus(true);                      // the connection changed under us: show it
    }

    function onResult() {
      if (connected() !== true) ensureStatus(true);                // an estimate came back, so Meta IS connected: a banner that says otherwise is out of date
    }

    // ---------------------------------------------------------------- the answer (data.meta of POST /api/constanza/estimate) - drawn from saved data too
    // Every reader below tolerates any shape: a saved estimate comes back from a proposal that anyone could have edited.
    function readMeta(data) {
      const meta = isObj(data) && isObj(data.meta) ? data.meta : null;
      if (!meta) return null;
      const sc = isObj(meta.scenarios) ? meta.scenarios : null;
      const mult = sc && isObj(sc.multipliers) ? sc.multipliers : {};
      const monthly = isObj(meta.monthly) ? meta.monthly : {};
      const scMonthly = sc && isObj(sc.monthly) ? sc.monthly : {};
      const slot = { conservative: "low", mid: "mid", upper: "high" };
      return {
        audience: isObj(meta.audience) ? meta.audience : null, sc, mult,
        monthlyOf: (k) => (scMonthly[k] != null ? scMonthly[k] : monthly[slot[k]]),
        flightOf: (k) => (sc && isObj(sc.flight) ? sc.flight[k] : null),
        spendOf: (k) => (sc && isObj(sc.retail_spend_at_flight) ? sc.retail_spend_at_flight[k] : null),
        months: sc && sc.months != null ? Number(sc.months) : (data && data.months != null ? Number(data.months) : 1),
        targeting: isObj(meta.targeting) ? meta.targeting : null, check: isObj(meta.budget_check) ? meta.budget_check : null,
        languageNote: meta.language_note ? text(meta.language_note) : "", asOf: text(meta.as_of).slice(0, 10),
      };
    }

    const unreadable = () => `<p class="mt-empty">This saved estimate has no Meta details, so it can’t be shown. Estimate again.</p>`;
    const rangeText = (a) => (a && Number(a.high) > Number(a.low) ? `${count(a.low)} – ${count(a.high)}` : count(a && a.low));

    // The planning rule the server applied, in words, from the multipliers of this very result.
    function ruleOf(r) {
      const m = r.mult;
      const has = m.mid != null;
      return { has, short: has ? `lower-end audience × ${text(m.mid)} (${text(m.conservative)} – ${text(m.upper)})` : "lower-end audience × the planning multipliers",
        long: has ? `the lower end of that audience × ${text(m.conservative)} (conservative), × ${text(m.mid)} (mid, the default) or × ${text(m.upper)} (upper)` : "the lower end of that audience × the planning multipliers", mid: has ? text(m.mid) : "the mid multiplier" };
    }

    function checkSentence(c) {
      return `${money(c.retail_budget)} retail → ${money(c.media_budget)} media at a ${money(c.retail_cpm)} retail CPM buys about ${count(c.budget_impressions)} impressions over the ${count(c.months)}-month flight. That is what the budget pays for, not avails.`;
    }

    const checkHtml = (c) => (c ? `<p class="mt-check"><strong>Budget cross-check:</strong> ${esc(checkSentence(c))}</p>` : "");
    const manualLabel = (a) => (a && a.source === "meta_api" ? "(from Meta)" : "(entered manually)");

    // The dock's headline: the lower-end audience, then the three scenarios as the figures a month, the mid one marked as the default.
    function headlineHtml(data) {
      const r = readMeta(data);
      if (!r || !r.audience) return `<div class="cz-headline mt-heads">${unreadable()}</div>`;
      const rule = ruleOf(r);
      const cards = SCENARIOS.map(([k, label]) => `<div class="cz-metric mt-sc${k === "mid" ? " mt-mid" : ""}">
          <span class="cz-mlabel">${label}${r.mult[k] != null ? ` · <span class="mt-mult">× ${esc(text(r.mult[k]))}</span>` : ""}</span>
          <span class="cz-mvalue">${count(r.monthlyOf(k))}</span><span class="cz-mrange">imps / month</span>
          <dl class="mt-sc-rows">${r.months > 1 ? `<dt>Flight · ${count(r.months)} mo</dt><dd>${count(r.flightOf(k))}</dd>` : ""}${r.spendOf(k) != null ? `<dt>Retail spend at the planning CPM</dt><dd>${money(r.spendOf(k))}</dd>` : ""}</dl></div>`).join("");
      return `<div class="cz-headline mt-heads">
        <div class="cz-metric main"><span class="cz-mlabel">Audience size · lower end, Meta accounts</span><span class="cz-mvalue">${count(r.audience.low)}</span><span class="cz-mrange">the base of the scenarios · Meta’s range ${esc(rangeText(r.audience))} ${esc(manualLabel(r.audience))}</span></div>
        <div class="mt-scen" role="group" aria-label="Impressions a month: ${esc(rule.short)}">${cards}</div>
      </div>`;
    }

    // What was asked (the words kept with this result when it was pulled, else the echo the server sent), the place, the cross-check, the language caveat.
    function bodyHtml(data, m, asked) {
      const r = readMeta(data);
      if (!r) return unreadable();
      const kept = asked && asked.method && Array.isArray(asked.method.summary) ? asked.method.summary : null;
      const f = fragments(r.targeting);
      const parts = kept || [f.gender, f.age, f.language, f.detailed].filter(Boolean).map(cap);
      const place = placeOf(r.targeting);
      const where = place.name ? `<span class="cz-chip cz-ok">${esc(place.name)} <small>${esc(place.kind)}</small></span>` : `<span class="cz-note">none</span>`;
      return `<div class="cz-section-title">What was asked of Meta</div>
        <div class="cz-chips">${parts.length ? parts.map((p) => `<span class="cz-chip">${esc(p)}</span>`).join("") : `<span class="cz-chip">Everyone in the place</span>`}</div>
        <div class="cz-section-title">Place sent to Meta</div>
        <div class="cz-chips">${where}</div>
        ${r.check ? `<div class="cz-section-title">Budget cross-check</div>${checkHtml(r.check)}` : ""}
        ${f.hasLanguage ? `<p class="mt-note">${esc(languageSentence())}</p>` : ""}`;
    }

    const languageSentence = () => "The language is the one people have set on Facebook or Instagram, not who speaks it, so it can be fewer than all speakers.";

    function aboutHtml(data) {
      const r = readMeta(data);
      if (!r) return "";
      const f = fragments(r.targeting);
      const place = placeOf(r.targeting);
      const items = [
        `Asked of Meta’s reach estimate (the one behind Ads Manager’s estimated audience size)${r.asOf ? `; as of ${esc(r.asOf)}` : ""}. It counts eligible accounts: it is not delivered reach and not a delivery forecast.`,
        `Monthly impressions are ${esc(ruleOf(r).long)}: Entravision’s planning scenarios, not a delivery forecast. A flight is the monthly figure times its months. Meta’s upper end is shown for context; it is not a base.`,
        `The Avails step takes the mid scenario as the monthly impressions, and the lower-end audience as est. uniques, so its frequency reads ${esc(ruleOf(r).mid)}.`,
        `Meta counts people on its default placements (Facebook, Instagram, Messenger, Audience Network). Exclusions, placements and account restrictions are not part of the estimate: check them in Ads Manager.`,
      ];
      if (place.kind === "TV market") items.push("Meta’s TV markets are Comscore markets, so a market’s footprint can differ from the Nielsen markets used elsewhere in this app.");
      if (f.hasLanguage) items.push(esc(r.languageNote || languageNote()));
      return `<details class="cz-details"><summary>About this estimate</summary><ul class="cz-list">${items.map((i) => `<li>${i}</li>`).join("")}</ul></details>`;
    }

    // The Step 05 card's method part (between the range table and the notes): the audience headline, what was asked, the retail spend each scenario's flight
    // implies, the cross-check. It may be handed a saved result of any shape.
    function cardHtml(data) {
      const r = readMeta(data);
      if (!r || !r.audience) return unreadable();
      const a = r.audience;
      const f = fragments(r.targeting);
      const pulled = r.targeting && r.targeting.geo ? `<p class="mt-pulled"><strong>Pulled for</strong> ${esc(pulledFor(r.targeting))}.</p>` : "";
      const spend = SCENARIOS.filter(([k]) => r.spendOf(k) != null).map(([k, label]) => `${esc(label)} <strong>${money(r.spendOf(k))}</strong>`).join(" / ");
      return `<p class="mt-headline"><strong>${count(a.low)}</strong><span>accounts at the lower end of Meta’s audience</span></p>
        <p class="mt-range">Meta’s range (lower – upper end): <strong>${esc(rangeText(a))}</strong> accounts ${esc(manualLabel(a))}.</p>
        ${pulled}
        <p class="mt-note">The scenarios use the <strong>lower end</strong>: it is the est. uniques the Avails step gets, so its frequency reads ${esc(ruleOf(r).mid)}.${f.hasLanguage ? ` ${esc(languageSentence())}` : ""}</p>
        ${spend ? `<p class="mt-spend">Retail spend the flight implies at the planning CPM: ${spend}</p>` : ""}
        ${checkHtml(r.check)}
        <small class="mt-foot">Planning scenarios, not a delivery forecast${r.asOf ? ` · ${esc(r.asOf)}` : ""}</small>`;
    }

    // The "Copy summary" lines: plain text.
    function summaryLines(data, asked) {
      const r = readMeta(data);
      if (!r || !r.audience) return [];
      const lines = [`Audience size, lower end (Meta accounts): ${count(r.audience.low)} (Meta’s range ${rangeText(r.audience)})`,
        `Impressions = ${ruleOf(r).short} per month (mid is the default)`];
      const spend = SCENARIOS.filter(([k]) => r.spendOf(k) != null).map(([k, label]) => `${label} ${money(r.spendOf(k))}`);
      if (spend.length) lines.push(`Retail spend the flight implies at the planning CPM: ${spend.join(" / ")}`);
      const kept = asked && asked.method && Array.isArray(asked.method.summary) ? asked.method.summary : null;
      const f = fragments(r.targeting);
      const parts = kept || [f.gender, f.age, f.language, f.detailed].filter(Boolean).map(cap);
      if (parts.length) lines.push(`Asked of Meta: ${parts.join(" · ")}`);
      const place = placeOf(r.targeting);
      if (place.name) lines.push(`Place sent to Meta: ${place.name} (${place.kind})`);
      if (r.check) lines.push(`Budget cross-check: ${checkSentence(r.check)}`);
      if (f.hasLanguage) lines.push(languageSentence());
      return lines;
    }

    // ---------------------------------------------------------------- the Step 05 card (a saved estimate, or none yet)
    function badgeHtml(data) {
      const r = readMeta(data);
      if (r && r.audience && r.audience.source === "manual") return `<span class="cz-badge cz-extended" title="This audience size was typed in, not asked of Meta">Entered manually</span>`;
      return `<span class="cz-badge cz-reported" title="${esc(r && r.asOf ? `From Meta’s own reach estimate, ${r.asOf}` : "From Meta’s own reach estimate")}">Meta API · live</span>`;
    }

    // The object stored as entry.geo_resolution when the estimate goes into Avails: the place Meta was asked about (nothing here is a Nielsen market).
    function geoInfo(data) {
      const r = readMeta(data);
      const place = r ? placeOf(r.targeting) : { name: "" };
      return { method: "meta_audience", dmas: [place.name || "entered audience"], unmatched_tokens: [], win_rate: null, hispanic_share: null };
    }

    function applyLabel(m) {
      return `Insert the mid-case (${count(m && m.month ? m.month.mid : null)} / month) into Avails →`;
    }

    // The facts the result was REALLY computed with: the budget cross-check carries the CPM the server used when the line sent none.
    function snapshotLine(data) {
      const r = readMeta(data);
      return { budget: r && r.check ? Number(r.check.retail_budget) : null, cpm: r && r.check ? Number(r.check.retail_cpm) : null };
    }

    // Meta has no reasons of its own beyond the generic ones (product, months, geography, target, budget, CPM) and the audience in the dock (choicesChanged).
    function staleReasons() {
      return [];
    }

    // Do THAT line's choices in the dock differ from the ones of its saved estimate? An order-free comparison; false when the line has no workspace (this
    // never creates one) or the saved estimate has no choices to compare (an entered-by-hand audience).
    function choicesChanged(lid, block) {
      const w = peek(lid);
      if (!w || !isObj(block)) return false;
      return signature(w.sel) !== signature(selFromBlock(block));
    }

    // Recalculate: the line's CURRENT choices (the workspace when one exists - unpulled edits are sent - else the saved estimate's) with the line's current
    // budget and CPM. The months come from the line through the dock.
    function cardRequest(lid, line, snapshot) {
      const w = peek(lid);
      const block = snapshot && isObj(snapshot.request) && isObj(snapshot.request.meta) ? snapshot.request.meta : null;
      const s = w ? w.sel : selFromBlock(block);
      const facts = isObj(line) ? { budget: Number(line.budget), cpm: Number(line.cpm) } : null;
      return metaBlock(s, facts);
    }

    function canAutoPull(line, snapshot) {
      const w = isObj(line) ? peek(line.id) : null;
      const block = snapshot && isObj(snapshot.request) && isObj(snapshot.request.meta) ? snapshot.request.meta : null;
      if (!w && !block) return { ok: false, reason: "There is nothing to repeat yet: choose the audience in Constanza first." };
      const s = w ? w.sel : selFromBlock(block);
      if (!s.geo) return { ok: false, reason: "The place was cleared in Constanza: choose one there first." };
      return { ok: true };
    }

    function connection() {
      ensureStatus(false);
      return connected();
    }

    // What the card says before any estimate exists - or for a budget-only saved line: the cross-check it did have, and a nudge to estimate an audience.
    function cardEmptyHtml(line, snapshot) {
      const legacy = snapshot && snapshot.legacy === true;
      if (legacy && !snapshot.result) {
        const c = isObj(snapshot.legacy_budget_check) ? snapshot.legacy_budget_check : null;
        return `<p class="mt-empty">Estimate an audience to see the scenarios.</p>${checkHtml(c)}`;
      }
      const budgetNote = isObj(line) && !(Number(line.budget) > 0) ? `<p class="mt-empty">Add a budget in Step 04 to see the budget cross-check.</p>` : "";
      return `<p class="mt-empty">Meta needs an audience: choose the place, age, language and interests in Constanza, then insert the result here.</p>${budgetNote}`;
    }

    // ---------------------------------------------------------------- a proposal saved before estimates were stored in forecast_snapshot
    // Converts the old availsData[lid].meta_forecast into a snapshot (pure: nothing is stored, nothing is asked of Meta). `saved` is that value (or the
    // entry that holds it). The six values the old card compared to call itself stale go into `line`, so stale / fresh is decided exactly as before.
    function adopt(saved, line) {
      const entry = isObj(saved) && !("metric_type" in saved) && isObj(saved.meta_forecast) ? saved.meta_forecast : saved;
      if (!isObj(entry)) return null;
      try {
        const months = Number(entry.months);
        const t = isObj(entry.meta_targeting) ? entry.meta_targeting : null;
        const audience = isObj(entry.potential_audience) ? entry.potential_audience : null;
        const sc = isObj(entry.scenarios) ? entry.scenarios : null;
        const snapshot = {
          v: 1, method: "meta", legacy: true, saved_at: entry.as_of || null,
          line: { product_name: entry.product_name, method: "meta", months, geo_text: entry.proposal_geo, target_text: entry.target_text, budget: Number(entry.retail_budget), cpm: Number(entry.retail_cpm) },
          request: null, result: null,
        };
        if (t && t.geo_key != null && t.geo_key !== "") {
          snapshot.request = {
            product_name: entry.product_name, geo_text: "", months,
            meta: {
              geo: { type: t.geo_type, key: text(t.geo_key), label: t.geo_name || null },
              age_min: t.age_min != null ? t.age_min : null, age_max: t.age_max != null ? t.age_max : null, gender: t.gender || "all",
              languages: list(t.languages).filter(isObj).map((x) => ({ id: text(x.id), name: text(x.name) })),
              targeting: list(t.detailed).filter(isObj).map((x) => ({ id: text(x.id), name: text(x.name), type: text(x.type || "interests") })),
              retail_budget: Number(entry.retail_budget), retail_cpm: Number(entry.retail_cpm),
            },
          };
        }
        const check = { retail_budget: entry.retail_budget, media_budget: entry.media_budget, retail_cpm: entry.retail_cpm, media_cpm_assumed: entry.media_cpm_assumed,
          retail_to_media_multiplier: entry.retail_to_media_multiplier, months: entry.months, budget_impressions: entry.budget_impressions };
        if (audience && sc) {
          const sm = isObj(sc.monthly) ? sc.monthly : {};
          const flight = isObj(sc.flight) ? sc.flight : { conservative: Number(sm.conservative) * months, mid: Number(sm.mid) * months, upper: Number(sm.upper) * months };
          const languages = t ? list(t.languages).filter(isObj).map((x) => ({ id: text(x.id), name: text(x.name) })) : [];
          snapshot.result = {
            method: "meta", product_name: entry.product_name, platform: "meta", confidence: "live_api", geo_method: "meta_audience", geo_confidence: "selected",
            months, imps_low: flight.conservative, imps_mid: flight.mid, imps_high: flight.upper,
            est_uniques: sc.est_uniques != null ? sc.est_uniques : audience.lower, reach_universe: null, series: null, weeks: null, notes: list(entry.notes),
            unmatched_geo_tokens: [], unmatched_geo_details: [], ambiguous: [], geo_interpretations: [], geo_suggestions: [], by_area: [],
            hispanic_targeting: { default: false, on: false, applies: false },
            meta: {
              audience: { low: audience.lower, high: audience.upper, source: audience.source },
              monthly: { low: sm.conservative, mid: sm.mid, high: sm.upper },
              scenarios: sc,
              targeting: t ? {
                geo: { type: t.geo_type, key: text(t.geo_key), label: t.geo_name || null }, age_min: t.age_min != null ? t.age_min : null, age_max: t.age_max != null ? t.age_max : null,
                gender: t.gender || "all", languages, detailed: list(t.detailed).filter(isObj).map((x) => ({ id: text(x.id), name: text(x.name), type: text(x.type || "interests") })),
              } : null,
              budget_check: check,
              language_note: languages.length ? LANGUAGE_NOTE : null,
              as_of: entry.as_of || null,
            },
          };
        } else {
          snapshot.legacy_budget_check = check;            // a budget-only line: no audience, but the cross-check it had (only on a snapshot adopted in memory)
        }
        return snapshot;
      } catch (e) {
        return null;
      }
    }

    // ---------------------------------------------------------------- what the plugin hands the dock
    return {
      goLabel: "Estimate audience →",
      channelLabel: "Meta · live audience estimate",
      sourceLabel: "Live from Meta",
      rangeLabels: ["Conservative", "Mid · default", "Upper"],
      caps: { ownsGeography: true, persist: true, autoPull: "saved", lineMonths: true },
      formHtml, whereHtml, bind, requestPart: () => ({ meta: metaBlock(sel(), factsNow()) }),
      context: () => ({ summary: summaryParts(), geo: sel().geo ? (sel().geo.label || sel().geo.name) : "" }),
      reset, onProposalReset, onLine, restore, canPull, goState, connection, onError, onResult, errorText: refusal,
      // Shown: the connection is (re)checked now, never at page load. A closed dock does not ask (the dock opens it again later through the same call).
      onSelect: (product) => {
        T.product = product || null;
        wire();
        renderAll();
        if (!(ctx.S && ctx.S.open === false)) ensureStatus(false);
      },
      headlineHtml, bodyHtml, aboutHtml, cardHtml, summaryLines, cardEmptyHtml, applyLabel, badgeHtml, geoInfo,
      staleReasons, choicesChanged, cardRequest, canAutoPull, snapshotLine, legacy: { key: "meta_forecast", adopt },
      _pure: {
        T, ws, sel, peek, changed, money, count, ageText, langFamily, langFamilies, langStarts, fold, compactSize, sizeText, highlight, geoSuggestions, shortNote, selFromBlock, signature,
        metaBlock, summaries, summaryParts, fragments, pulledFor, placeOf, refusal, readMeta, ruleOf, checkSentence, parseMoney, adopt, offReason, connected, factsNow,
        LANG_QUICK, SEARCH_MAX, MAX_LANGUAGES, MAX_DETAILED, LANGUAGE_NOTE, addLanguages, kindLabel, tabOf, renderStatus, applyStatus, languageNote, geoChips, langChips, detailedChips,
      },
    };
  });
})();
