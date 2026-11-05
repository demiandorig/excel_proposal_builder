/* ============================================================================
 * Constanza x TikTok - the TikTok panel of the avails dock.
 *
 * Choosing the TikTok ads product swaps the dock's Entravision audience block for the targeting TikTok Ads Manager offers when
 * you set up an ad group - age, gender, languages, interests & behaviors, spending power, household income, device, placement and
 * campaign objective - and the result card shows what TikTok's own audience-size estimator answers: how many people match, as a
 * range and TikTok's own verdict on how narrow that is. Impressions are the LOWER end of that range x 4.3 / 5.5 / 8.6 a month
 * (conservative / mid / upper, mid is the default; the multipliers come from the server); est. uniques is the lower-end audience.
 *
 * The panel is four numbered sections built from the shared audience kit (audience-kit.css, .aud-*), under a connection banner. The
 * geography and the flight are the dock's own cards above it (1 Where, 2 When), so the kit's numbering carries on from them:
 *   3 Who             age brackets (with presets) and gender
 *   4 Language        quick picks (Spanish speakers / English / Spanish + English, resolved live from TikTok's list), a search over the
 *                     whole list, chips, and - when the line targets Hispanic audiences - a suggestion to add Spanish (never silent)
 *   5 Audience        interests & behaviors: search + chips, and "Browse all categories" = the SegmentBrowser pop-up (segment-browser.js)
 *   6 More targeting  (folded) spending power, household income, device, placement, objective
 *
 * Loaded after constanza.js, which owns the dock: this file only registers a forecast method with it (registerMethod). It reaches the
 * dock only through the `ctx` it is given (documented at registerMethod in constanza.js), including ctx.targetsHispanic() for the
 * Spanish suggestion. The server side is app/services/constanza_tiktok.py (/api/constanza/tiktok/*, and /api/constanza/estimate for
 * the numbers).
 * ========================================================================== */
(function () {
  "use strict";
  if (!window.Constanza || typeof window.Constanza.registerMethod !== "function") return;

  window.Constanza.registerMethod("tiktok", function (ctx) {
    const { S, $, esc, num, compact, api, markStale, errorText, announce } = ctx;

    // ---------------------------------------------------------------- what TikTok can target (the lists that come from TikTok are fetched on demand)
    const KINDS = {
      general_interest: "Interest", additional_interest: "Interest keyword", purchase_intention: "Purchase intent",
      video_interaction: "Video interaction", creator_interaction: "Creator interaction", hashtag_interaction: "Hashtag",
    };
    const KIND_GROUPS = {                                            // the headings of the search popup, one group per kind
      general_interest: "Interests", additional_interest: "Interest keywords", purchase_intention: "Purchase intent",
      video_interaction: "Video interactions", creator_interaction: "Creator interactions", hashtag_interaction: "Hashtags",
    };
    const KIND_ORDER = Object.keys(KINDS);
    const BROWSE_TABS = [["general_interest", "Interests"], ["purchase_intention", "Purchase intent"], ["video_interaction", "Video"], ["creator_interaction", "Creators"], ["hashtag_interaction", "Hashtags"]];
    const FALLBACK = {
      age_groups: [["AGE_18_24", "18–24"], ["AGE_25_34", "25–34"], ["AGE_35_44", "35–44"], ["AGE_45_54", "45–54"], ["AGE_55_100", "55+"]],
      objectives: [["reach", "Reach"], ["video_views", "Video views"], ["traffic", "Traffic"]],
      household_income: [["TOP5", "Top 5% of ZIP codes"], ["TOP10", "Top 10%"], ["TOP10_25", "Top 10–25%"], ["TOP25_50", "Top 25–50%"]],
      network_types: [["WIFI", "Wi-Fi"], ["4G", "4G"], ["5G", "5G"], ["3G", "3G"], ["2G", "2G"]],
      operating_systems: [["IOS", "iOS"], ["ANDROID", "Android"]],
      placements: [["PLACEMENT_TIKTOK", "TikTok"], ["PLACEMENT_PANGLE", "Pangle (TikTok's partner apps)"]],       // the server's menus, to the letter: a rebuild on the first answer would drop the focus
    };
    const STAGES = { 1: "Too narrow", 2: "Narrow", 3: "Balanced", 4: "Fairly broad" };
    const TYPE_LABEL = { COUNTRY: "country", PROVINCE: "state", DMA: "TV market", CITY: "county / city", DISTRICT: "city", ZIP_CODE: "ZIP" };
    const PER_KIND_MAX = 50;                                         // TikTok takes at most this many categories of one kind
    const LANGUAGES_MAX = 20;                                        // ... and this many languages
    // One-click language picks. The codes are only what the pick LOOKS FOR in TikTok's own list (by code, else by name): a pick whose
    // language is not in the list is shown disabled, never sent blind.
    const LANG_PICKS = [
      { key: "es", label: "Spanish speakers", find: [{ code: "es", name: /^spanish\b/i }] },
      { key: "en", label: "English", find: [{ code: "en", name: /^english\b/i }] },
      { key: "es+en", label: "Spanish + English", find: [{ code: "es", name: /^spanish\b/i }, { code: "en", name: /^english\b/i }] },
    ];
    // Age presets (the TikTok brackets they cover are worked out from the menu: a preset whose brackets are not all there is left out).
    const AGE_PRESETS = [["18–34", 18, 34], ["25–54", 25, 54], ["35+", 35, 100]];

    // ---------------------------------------------------------------- state
    const fresh = () => ({
      age: [], gender: "", languages: [], picks: [], spending: "", income: [], os: "", networks: [], placements: ["PLACEMENT_TIKTOK"],
      objective: "reach", videoPeriod: 15,
    });
    const T = Object.assign(fresh(), {
      status: null, statusAt: 0, catalog: {}, languagesAll: null, langStatus: "idle", langError: "", searchSeq: 0, hits: [], hitsFor: null, active: -1,
      langHits: [], langActive: -1, tab: "general_interest", hispanicDismissed: "",
    });
    let searchTimer = null;
    const PER_KIND = 6;                                              // hits shown of each kind (the rest is counted under the list)

    const options = (key) => ((T.status && T.status.options && T.status.options[key]) ? T.status.options[key].map((o) => [o.id, o.label]) : FALLBACK[key]);
    const labelOf = (key, id) => { const hit = options(key).find((o) => o[0] === id); return hit ? hit[1] : id; };
    const toggled = (arr, v) => (arr.includes(v) ? arr.filter((x) => x !== v) : arr.concat(v));
    const kindIds = (kind) => T.picks.filter((p) => p.kind === kind).map((p) => p.id);
    const sortedIds = (kind) => kindIds(kind).sort();                // the request does not depend on the order things were clicked in
    const interactionPicked = () => T.picks.some((p) => ["video_interaction", "creator_interaction", "hashtag_interaction"].includes(p.kind));
    const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

    // ---------------------------------------------------------------- the request
    function targeting() {
      return {
        gender: T.gender || null, age_groups: T.age.slice().sort(), languages: T.languages.map((l) => l.code).sort(),
        interest_category_ids: sortedIds("general_interest"), interest_keyword_ids: sortedIds("additional_interest"),
        purchase_intention_ids: sortedIds("purchase_intention"), video_interaction_ids: sortedIds("video_interaction"),
        creator_interaction_ids: sortedIds("creator_interaction"), hashtag_ids: sortedIds("hashtag_interaction"),
        video_period: T.videoPeriod, spending_power: T.spending || null, household_income: T.income.slice().sort(),
        operating_system: T.os || null, network_types: T.networks.slice().sort(), placements: T.placements.slice().sort(), objective: T.objective,
      };
    }

    // The plain-language lines of what was asked (also what the result card and the copied summary say).
    function summaryParts() {
      const parts = [];
      if (T.gender) parts.push(T.gender === "male" ? "Men" : "Women");
      if (T.age.length) parts.push("Ages " + T.age.slice().sort().map((a) => labelOf("age_groups", a)).join(", "));
      if (T.languages.length) parts.push("Languages: " + T.languages.map((l) => l.name).join(", "));
      KIND_ORDER.forEach((kind) => {
        const names = T.picks.filter((p) => p.kind === kind).map((p) => p.name);
        if (names.length) parts.push(`${KINDS[kind]}${names.length > 1 ? "s" : ""}: ${names.join("; ")}`);
      });
      if (T.picks.some((p) => p.kind === "video_interaction")) parts.push("Video look-back: " + (T.videoPeriod ? `${T.videoPeriod} days` : "any time"));
      if (T.spending === "HIGH") parts.push("High spending power");
      if (T.income.length) parts.push("Household income: " + T.income.slice().sort().map((i) => labelOf("household_income", i)).join(", "));
      if (T.os) parts.push(labelOf("operating_systems", T.os));
      if (T.networks.length) parts.push("Connection: " + T.networks.slice().sort().map((n) => labelOf("network_types", n)).join(", "));
      return parts;
    }

    function selectedCount() {
      return (T.gender ? 1 : 0) + (T.age.length ? 1 : 0) + (T.languages.length ? 1 : 0) + T.picks.length;
    }
    function moreCount() {
      return (T.spending ? 1 : 0) + (T.income.length ? 1 : 0) + (T.os ? 1 : 0) + (T.networks.length ? 1 : 0) + (T.placements.length > 1 ? 1 : 0) + (T.objective !== "reach" ? 1 : 0);
    }

    // ---------------------------------------------------------------- the live summaries of the sections (the one line under each title)
    // "AGE_25_34" -> { lo: 25, hi: 34 }; "AGE_55_100" -> { lo: 55, hi: 100 } (100 = no upper end).
    function ageBounds(id) {
      const m = /^AGE_(\d+)_(\d+)$/.exec(String(id));
      return m ? { id, lo: Number(m[1]), hi: Number(m[2]) } : null;
    }

    // Neighbouring brackets read as one range: 25–34 + 35–44 = "25–44", 35–44 + 45–54 + 55+ = "35+", 18–24 + 35–44 = "18–24, 35–44".
    function ageSummary() {
      const known = T.age.map(ageBounds), loose = T.age.filter((_, i) => !known[i]);
      const runs = [];
      known.filter(Boolean).sort((a, b) => a.lo - b.lo).forEach((b) => {
        const last = runs[runs.length - 1];
        if (last && b.lo === last.hi + 1) last.hi = b.hi; else runs.push({ lo: b.lo, hi: b.hi });
      });
      return runs.map((r) => (r.hi >= 100 ? `${r.lo}+` : `${r.lo}–${r.hi}`)).concat(loose.map((id) => labelOf("age_groups", id))).join(", ");
    }

    const whoSummary = () => [T.gender ? (T.gender === "male" ? "Men" : "Women") : "", ageSummary()].filter(Boolean).join(" · ");
    const namesSummary = (names) => names.slice(0, 2).join(", ") + (names.length > 2 ? ` +${names.length - 2}` : "");
    function moreSummary() {
      const bits = [];
      if (T.spending === "HIGH") bits.push("High spenders");
      if (T.income.length) bits.push(T.income.length === 1 ? labelOf("household_income", T.income[0]) : `${T.income.length} income ranges`);
      if (T.os) bits.push(labelOf("operating_systems", T.os));
      if (T.networks.length) bits.push(T.networks.slice().sort().map((n) => labelOf("network_types", n)).join(", "));
      if (T.placements.length > 1) bits.push("TikTok + Pangle");
      if (T.objective !== "reach") bits.push(labelOf("objectives", T.objective));
      return bits.join(" · ");
    }

    // ---------------------------------------------------------------- the panel
    // The words stay "Clear"; the accessible name says what it clears (the Meta panel's wording), since a screen reader lists the buttons on their own.
    const CLEAR_LABEL = { who: "Clear age and gender", lang: "Clear languages", aud: "Clear interests and behaviors", more: "Clear more targeting" };
    const clearButton = (key, extraClass) => `<button type="button" class="aud-clear${extraClass ? " " + extraClass : ""}" id="cz-tt-clear-${key}" data-tt="clear" data-v="${key}" aria-label="${CLEAR_LABEL[key]}" hidden>Clear</button>`;

    // The head every section shares. A folded section's head is a <summary>, which may not hold a button (the toggle would be a control inside a
    // control): its Clear is clearButton() placed beside the <details> (see formHtml and .cz-fold in constanza.css), and the head keeps room for it
    // with an invisible twin of the link instead.
    const sectionHead = (key, title, folded) => `
      <span class="aud-step" aria-hidden="true"></span>
      <div class="aud-sec-text">${folded ? `<span class="aud-sec-title" id="cz-tt-${key}-title">${title}</span>` : `<h3 class="aud-sec-title" id="cz-tt-${key}-title">${title}</h3>`}<p class="aud-sec-sum" id="cz-tt-${key}-sum"></p></div>
      <span class="aud-count" id="cz-tt-count-${key}" hidden></span>
      ${folded ? `<span class="aud-clear cz-fold-twin" aria-hidden="true">Clear</span>` : clearButton(key)}`;

    function formHtml() {
      return `
<div class="cz-tt tt-panel" id="cz-tt">
  <div id="cz-tt-status" class="aud-notice aud-notice--info" data-state="checking" role="status"></div>
  <div class="aud-builder">

    <section class="aud-sec" id="cz-tt-sec-who" aria-labelledby="cz-tt-who-title">
      <div class="aud-sec-head">${sectionHead("who", "Who")}</div>
      <div class="aud-sec-body">
        <div class="aud-field">
          <div class="aud-label"><span id="cz-tt-age-l">Age</span><span class="aud-label-aside">18+ · TikTok leaves out under-18s</span></div>
          <div class="aud-range">
            <div class="aud-quick" id="cz-tt-age-presets" role="group" aria-label="Age presets"></div>
            <div class="aud-seg aud-seg--wrap" id="cz-tt-age" role="group" aria-labelledby="cz-tt-age-l"></div>
          </div>
        </div>
        <div class="aud-field">
          <span class="aud-label" id="cz-tt-gender-l">Gender</span>
          <div class="aud-seg" id="cz-tt-gender" role="group" aria-labelledby="cz-tt-gender-l">
            <button type="button" data-tt="gender" data-v="" aria-pressed="true">All</button>
            <button type="button" data-tt="gender" data-v="female" aria-pressed="false">Women</button>
            <button type="button" data-tt="gender" data-v="male" aria-pressed="false">Men</button>
          </div>
        </div>
      </div>
    </section>

    <section class="aud-sec" id="cz-tt-sec-lang" aria-labelledby="cz-tt-lang-title">
      <div class="aud-sec-head">${sectionHead("lang", "Language")}</div>
      <div class="aud-sec-body">
        <div class="aud-field">
          <div class="aud-quick" id="cz-tt-lang-quick" role="group" aria-label="Language quick picks"></div>
          <p class="aud-hint" id="cz-tt-lang-quick-note" role="status" hidden></p>
        </div>
        <div id="cz-tt-lang-suggest" hidden></div>
        <div class="aud-field">
          <label class="aud-label" for="cz-tt-lang-q"><span>Another language</span><span class="aud-label-aside" id="cz-tt-lang-hint">any language</span></label>
          <div class="aud-ta">
            <input type="search" class="aud-input" id="cz-tt-lang-q" placeholder="Search — Portuguese, French…" autocomplete="off"
                   role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="cz-tt-lang-results" aria-haspopup="listbox" />
            <div class="aud-ta-list" id="cz-tt-lang-results" role="listbox" aria-label="Matching languages" hidden></div>
          </div>
          <div><button type="button" class="aud-link" id="cz-tt-lang-browse" data-tt="lang-browse">Browse all languages</button></div>
          <div class="aud-chips" id="cz-tt-lang-chips"></div>
        </div>
        <p class="aud-hint">“Language” is the language people have set in their TikTok app — TikTok may also work it out from the videos they watch. It is not the language of your ad.</p>
      </div>
    </section>

    <section class="aud-sec" id="cz-tt-sec-aud" aria-labelledby="cz-tt-aud-title">
      <div class="aud-sec-head">${sectionHead("aud", "Audience")}</div>
      <div class="aud-sec-body">
        <div class="aud-field">
          <label class="aud-label" for="cz-tt-q"><span>Interests &amp; behaviors</span><span class="aud-label-aside" id="cz-tt-pick-hint"></span></label>
          <div class="aud-ta">
            <input type="search" class="aud-input" id="cz-tt-q" placeholder="Search — beauty, soccer, #cooking…" autocomplete="off"
                   role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="cz-tt-results" aria-haspopup="listbox" />
            <div class="aud-ta-list" id="cz-tt-results" role="listbox" aria-label="Matching interests and behaviors" hidden></div>
          </div>
          <button type="button" class="aud-browse" id="cz-tt-browse" data-tt="browse" aria-haspopup="dialog"><span class="aud-browse-label">Browse all categories</span></button>
          <div class="aud-chips" id="cz-tt-chips"></div>
          <div class="tt-notes" id="cz-tt-pick-note"></div>
        </div>
      </div>
    </section>

    <div class="cz-fold">
      ${clearButton("more", "cz-fold-clear")}
      <details class="aud-sec" id="cz-tt-more">
        <summary class="aud-sec-head cz-sec-head">${sectionHead("more", "More targeting", true)}</summary>
        <div class="aud-sec-body">
          <div class="aud-field">
            <span class="aud-label" id="cz-tt-spend-l">Spending power</span>
            <div class="aud-seg" id="cz-tt-spend" role="group" aria-labelledby="cz-tt-spend-l">
              <button type="button" data-tt="spending" data-v="" aria-pressed="true">All</button>
              <button type="button" data-tt="spending" data-v="HIGH" aria-pressed="false">High spenders</button>
            </div>
          </div>
          <div class="aud-field">
            <div class="aud-label"><span id="cz-tt-income-l">Household income</span><span class="aud-label-aside">U.S. · share of ZIP codes</span></div>
            <div class="aud-seg aud-seg--wrap" id="cz-tt-income" role="group" aria-labelledby="cz-tt-income-l"></div>
          </div>
          <div class="aud-field">
            <span class="aud-label" id="cz-tt-os-l">Device</span>
            <div class="aud-seg" id="cz-tt-os" role="group" aria-labelledby="cz-tt-os-l"></div>
            <div class="aud-seg aud-seg--wrap" id="cz-tt-net" role="group" aria-label="Connection type"></div>
          </div>
          <div class="aud-field">
            <span class="aud-label" id="cz-tt-place-l">Placement</span>
            <div class="aud-seg aud-seg--wrap" id="cz-tt-place" role="group" aria-labelledby="cz-tt-place-l"></div>
            <p class="aud-hint" id="cz-tt-place-note"></p>
          </div>
          <div class="aud-field">
            <div class="aud-label"><span id="cz-tt-obj-l">Campaign objective</span><span class="aud-label-aside">decides which targeting TikTok offers</span></div>
            <div class="aud-seg" id="cz-tt-obj" role="group" aria-labelledby="cz-tt-obj-l"></div>
          </div>
          <div class="aud-field" id="cz-tt-period" hidden>
            <span class="aud-label" id="cz-tt-period-l">Video interactions look back</span>
            <div class="aud-seg" role="group" aria-labelledby="cz-tt-period-l">
              <button type="button" data-tt="period" data-v="15" aria-pressed="true">15 days</button>
              <button type="button" data-tt="period" data-v="7" aria-pressed="false">7 days</button>
              <button type="button" data-tt="period" data-v="0" aria-pressed="false">Any time</button>
            </div>
          </div>
        </div>
      </details>
    </div>
  </div>
</div>`;
    }

    const seg = (attr, items, isOn, extra) => items.map(([id, label]) => `<button type="button" data-tt="${attr}" data-v="${esc(id)}" aria-pressed="${isOn(id)}"${extra ? " " + extra(id) : ""}>${esc(label)}</button>`).join("");

    // A group that holds the keyboard focus gets it back on the same button once it is rebuilt (an answer from the server can arrive
    // after a keypress), so redrawing a group never drops the focus to the page.
    function refill(el, html) {
      const at = document.activeElement;
      const had = at && el.contains(at) && at.dataset ? { tt: at.dataset.tt, v: at.dataset.v } : null;
      el.innerHTML = html;
      const back = had && Array.from(el.querySelectorAll("button")).find((b) => b.dataset.tt === had.tt && b.dataset.v === had.v && !b.disabled);
      if (back) back.focus({ preventScroll: true });
    }

    // ---------------------------------------------------------------- the connection banner
    // The banner is a status region that stays put (formHtml draws it once; only its contents are replaced here), so a change of answer -
    // checking, connected, not connected, couldn't check - is announced. It holds nothing to press, so drawing it again cannot take the keyboard
    // away; a control added to it must keep the focus the way constanza-meta.js's renderStatus does.
    function renderStatus() {
      const el = $("cz-tt-status");
      if (!el) return;
      const s = T.status;
      const say = (kind, state, html) => { el.className = `aud-notice aud-notice--${kind}`; el.dataset.state = state; el.innerHTML = `<div class="aud-notice-body">${html}</div>`; };
      if (!s) { say("info", "checking", `<span class="aud-notice-title">Checking the TikTok connection…</span>`); return; }
      if (s.error) { say("error", "error", `<span class="aud-notice-title">Couldn’t reach the app’s TikTok settings.</span> ${esc(s.error)}`); return; }
      if (!s.configured) {
        say("warn", "off", `<span class="aud-notice-title">TikTok isn’t connected yet.</span> An admin needs to add ${s.missing.map((m) => `<code>${esc(m)}</code>`).join(" and ")} to the app’s secrets, then test it in Admin → Forecast. You can set the audience up now; the estimate needs the connection.`);
        return;
      }
      say("connected", "on", `<span class="aud-notice-title">Connected to TikTok</span> ${s.advertiser ? `Ad account ${esc(s.advertiser)} · ` : ""}impressions = ${esc(ruleOf(s).short)} a month`);
    }

    // ---------------------------------------------------------------- the choice buttons
    // The buttons of a group are built once (and again only when TikTok's menus arrive); after that a change only flips their state in
    // place, so the keyboard focus stays on the button that was pressed.
    let builtFor = "";
    function buildChoices() {
      const sig = JSON.stringify([options("age_groups"), options("household_income"), options("operating_systems"), options("network_types"), options("placements"), options("objectives")]);
      if (sig === builtFor) return;
      builtFor = sig;
      const off = () => false;
      refill($("cz-tt-age"), seg("age", options("age_groups"), off));
      const presets = agePresets();
      refill($("cz-tt-age-presets"), (presets.length ? `<span class="aud-quick-label">Presets</span>` : "") + presets.map(([label, ids]) => `<button type="button" data-tt="age-preset" data-v="${esc(ids.join(","))}" aria-pressed="false">${esc(label)}</button>`).join(""));
      refill($("cz-tt-income"), seg("income", options("household_income"), off));
      refill($("cz-tt-os"), seg("os", [["", "Any device"]].concat(options("operating_systems")), off));
      refill($("cz-tt-net"), seg("network", options("network_types"), off));
      refill($("cz-tt-place"), seg("place", options("placements"), off));
      refill($("cz-tt-obj"), seg("objective", options("objectives"), off));
    }

    // [label, [bracket ids]] for each preset whose brackets all exist in TikTok's age menu.
    function agePresets() {
      const bounds = options("age_groups").map((o) => ageBounds(o[0]));
      if (bounds.some((b) => !b)) return [];
      return AGE_PRESETS.map(([label, from, to]) => [label, bounds.filter((b) => b.lo >= from && b.hi <= to).map((b) => b.id)]).filter(([, ids]) => ids.length);
    }
    const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));

    const press = (el, on) => el.querySelectorAll("button[data-v]").forEach((b) => b.setAttribute("aria-pressed", String(on(b.dataset.v))));

    function syncChoices() {
      press($("cz-tt-age"), (v) => T.age.includes(v));
      press($("cz-tt-age-presets"), (v) => sameSet(T.age, v.split(",")));
      press($("cz-tt-gender"), (v) => v === (T.gender || ""));
      press($("cz-tt-spend"), (v) => v === (T.spending || ""));
      press($("cz-tt-income"), (v) => T.income.includes(v));
      press($("cz-tt-os"), (v) => v === (T.os || ""));
      press($("cz-tt-net"), (v) => T.networks.includes(v));
      press($("cz-tt-place"), (v) => T.placements.includes(v));
      press($("cz-tt-obj"), (v) => T.objective === v);
      press($("cz-tt-period"), (v) => Number(v) === T.videoPeriod);
      const locked = interactionPicked();
      $("cz-tt-place").querySelectorAll('button[data-v="PLACEMENT_PANGLE"]').forEach((b) => { b.disabled = locked; });
      $("cz-tt-place-note").textContent = locked ? "Video, creator and hashtag interactions work only when TikTok is the only placement." : "TikTok alone is what the Tiktok Ads product runs on; add Pangle to see the audience including TikTok’s partner apps.";
      $("cz-tt-period").hidden = !T.picks.some((p) => p.kind === "video_interaction");
    }

    // ---------------------------------------------------------------- the sections: live summary, count, Clear
    function renderSections() {
      const sections = [
        { key: "who", sec: "cz-tt-sec-who", n: (T.gender ? 1 : 0) + (T.age.length ? 1 : 0), sum: whoSummary(), idle: "Everyone 18+" },
        { key: "lang", sec: "cz-tt-sec-lang", n: T.languages.length, sum: namesSummary(T.languages.map((l) => l.name)), idle: "Any language" },
        { key: "aud", sec: "cz-tt-sec-aud", n: T.picks.length, sum: namesSummary(T.picks.map((p) => p.name)), idle: "Any interest or behavior" },
        { key: "more", sec: "cz-tt-more", n: moreCount(), sum: moreSummary(), idle: "TikTok placement · Reach objective" },
      ];
      sections.forEach((s) => {
        const sum = $(`cz-tt-${s.key}-sum`), count = $(`cz-tt-count-${s.key}`), clear = $(`cz-tt-clear-${s.key}`);
        if (!sum) return;
        sum.textContent = s.n ? s.sum : s.idle;
        sum.classList.toggle("is-set", !!s.n);
        count.textContent = s.n;
        count.hidden = !s.n;
        clear.hidden = !s.n;
        $(s.sec).classList.toggle("has-value", !!s.n);
      });
    }

    function renderPicks() {
      $("cz-tt-chips").innerHTML = T.picks.map((p, i) =>
        `<span class="aud-chip aud-chip--interest" title="${esc(p.path || KINDS[p.kind])}"><span class="aud-chip-label">${esc(p.name)}</span> <small class="aud-chip-meta">${esc(KINDS[p.kind])}</small><button type="button" class="aud-chip-x" data-tt="rm-pick" data-i="${i}" aria-label="Remove ${esc(p.name)}">×</button></span>`).join("");
      $("cz-tt-pick-hint").textContent = T.picks.length ? `${T.picks.length} chosen` : "";
      const notice = (kind, html) => `<div class="aud-notice aud-notice--${kind}"><div class="aud-notice-body">${html}</div></div>`;
      const notes = [];
      if (T.picks.some((p) => p.kind === "purchase_intention") && T.objective !== "traffic") {
        notes.push(notice("warn", `Purchase intent isn’t available for a ${esc(labelOf("objectives", T.objective))} campaign. <button type="button" class="aud-notice-action" data-tt="fix-objective">Use the Traffic objective</button>`));
      }
      if (T.picks.some((p) => p.kind === "purchase_intention") && T.picks.some((p) => p.kind === "additional_interest")) {
        notes.push(notice("warn", `Purchase intent and interest keywords can’t be used together — TikTok treats them as conflicting. Remove one.`));
      }
      if (T.picks.filter((p) => p.kind === "general_interest").length > 1) notes.push(`<p class="aud-hint">Several interests widen the audience: TikTok reaches people who match <em>any</em> of them.</p>`);
      $("cz-tt-pick-note").innerHTML = notes.join("");
    }

    function renderLanguages() {
      $("cz-tt-lang-chips").innerHTML = T.languages.map((l, i) =>
        `<span class="aud-chip aud-chip--lang" title="TikTok language code ${esc(l.code)}"><span class="aud-chip-label">${esc(l.name)}</span><button type="button" class="aud-chip-x" data-tt="rm-lang" data-i="${i}" aria-label="Remove ${esc(l.name)}">×</button></span>`).join("");
      $("cz-tt-lang-hint").textContent = T.languages.length ? `${T.languages.length} chosen` : "any language";
      renderLangQuick();
      renderHispanicHint();
    }

    // ---------------------------------------------------------------- language: the quick picks and the Hispanic suggestion
    // The entry of TikTok's list a pick stands for (by code first, then by name), or null when the list has none.
    function findLanguage(rule) {
      const all = T.languagesAll || [];
      return all.find((l) => String(l.code).toLowerCase() === rule.code) || all.find((l) => rule.name.test(String(l.name))) || null;
    }
    const spanish = () => findLanguage(LANG_PICKS[0].find[0]);
    // The languages a quick pick adds ([] when the list is missing one of them).
    const pickLanguages = (pick) => { const found = pick.find.map(findLanguage); return found.every(Boolean) ? found : []; };

    function renderLangQuick() {
      const box = $("cz-tt-lang-quick"), note = $("cz-tt-lang-quick-note");
      if (!box) return;
      const ready = T.langStatus === "ready" && T.languagesAll;
      const notConnected = !!(T.status && T.status.configured === false);
      let reason = "";
      if (!ready) reason = T.langStatus === "error" ? (notConnected ? "Quick picks need the TikTok connection." : `Quick picks need TikTok’s language list. ${T.langError}`) : "Loading TikTok’s language list…";
      const picks = LANG_PICKS.filter((p) => !ready || p.find.length === 1 || pickLanguages(p).length);        // “Spanish + English” only when both exist
      const sig = JSON.stringify([ready ? picks.map((p) => [p.key, pickLanguages(p).map((l) => l.code)]) : [reason]]);
      if (box.dataset.sig !== sig) {
        box.dataset.sig = sig;
        const missing = (p) => (ready && !pickLanguages(p).length ? `TikTok’s list has no ${p.key === "es" ? "Spanish" : "English"}.` : reason);
        refill(box, `<span class="aud-quick-label">Quick picks</span>` + picks.map((p) => {
          const why = !ready ? reason : missing(p);
          return `<button type="button" data-tt="lang-quick" data-v="${p.key}" aria-pressed="false"${why ? ` disabled title="${esc(why)}"` : ""}>${esc(p.label)}</button>`;
        }).join(""));
      }
      press(box, (v) => { const p = LANG_PICKS.find((x) => x.key === v), langs = p ? pickLanguages(p) : []; return langs.length > 0 && langs.every((l) => T.languages.some((x) => x.code === l.code)); });
      note.hidden = ready || !reason;
      if (!note.hidden) {
        note.innerHTML = `${esc(reason)}${T.langStatus === "error" ? ` <button type="button" class="aud-link" data-tt="lang-retry">Try again</button>` : ""}`;
      }
    }

    // Does the line the dock is open for target Hispanic audiences? The dock answers that (ctx.targetsHispanic(), read fresh each time: its own
    // Hispanic switch when it was told, else the page's reading of the line's targeting text). A suggestion only: it is never applied by itself.
    function targetsHispanic() {
      if (typeof ctx.targetsHispanic === "function") return !!ctx.targetsHispanic();
      return reachedHispanic();
    }
    // What the panel did before the dock offered ctx.targetsHispanic(): reach for the dock's state and the page's helper itself. Only the fallback
    // for a dock that does not have the function.
    function reachedHispanic() {
      if (S.hispanic === true) return true;
      try {
        if (S.lineId && typeof state !== "undefined" && state && Array.isArray(state.lineItems) && typeof _targetsHispanic === "function") {
          const li = state.lineItems.find((x) => x.id === S.lineId);
          return !!(li && _targetsHispanic(li));
        }
      } catch (e) { /* the page has no such helper: no suggestion */ }
      return false;
    }
    const hispanicKey = () => String(S.lineId || "dock");
    // Taking Spanish out again is an answer to the suggestion: it is not made again for this line.
    function spanishRemoved(codes) {
      const es = spanish();
      if (es && codes.includes(es.code)) T.hispanicDismissed = hispanicKey();
    }

    function renderHispanicHint() {
      const box = $("cz-tt-lang-suggest");
      if (!box) return;
      const es = spanish();
      const show = !!es && !T.languages.some((l) => l.code === es.code) && T.hispanicDismissed !== hispanicKey() && targetsHispanic();
      box.hidden = !show;
      if (!show) { box.innerHTML = ""; box.dataset.shown = ""; return; }
      if (box.dataset.shown === "1") return;                       // already drawn: a redraw would drop the focus from its buttons
      box.dataset.shown = "1";
      box.innerHTML = `<div class="aud-notice aud-notice--info"><div class="aud-notice-body"><span class="aud-notice-title">This line targets Hispanic audiences — add Spanish?</span>
        Spanish speakers are reached through TikTok’s language setting.
        <button type="button" class="aud-notice-action" data-tt="lang-hispanic">Add Spanish</button>
        <button type="button" class="aud-notice-action" data-tt="lang-dismiss">Not now</button></div></div>`;
    }

    // ---------------------------------------------------------------- the pop-up's two buttons
    // The pop-up is segment-browser.js, loaded before this file. When it did not load (a blocked script, a stale cache), the two buttons that open
    // it are hidden - as the dock hides its own Browse button - instead of left on the screen to do nothing; the search boxes still work.
    const canBrowse = () => !!window.SegmentBrowser;
    const orBrowse = () => (canBrowse() ? ", or browse all categories" : "");        // the hints that point at the button, only while there is one
    function syncBrowse() {
      ["cz-tt-browse", "cz-tt-lang-browse"].forEach((id) => { const b = $(id); if (b) b.hidden = !canBrowse(); });
    }

    // ---------------------------------------------------------------- redrawing
    function renderAll() {
      renderStatus(); buildChoices(); syncChoices(); renderPicks(); renderLanguages(); renderSections(); syncBrowse();
    }

    function changed() {
      // an interaction (video, creator, hashtag) works only when TikTok is the ONLY placement: whatever picked it, the lock holds
      if (interactionPicked() && !(T.placements.length === 1 && T.placements[0] === "PLACEMENT_TIKTOK")) T.placements = ["PLACEMENT_TIKTOK"];
      syncChoices(); renderPicks(); renderLanguages(); renderSections();
      markStale();
    }

    // ---------------------------------------------------------------- the connection
    async function loadStatus(force) {
      if (!force && T.status && T.status.configured && Date.now() - T.statusAt < 30000) return;      // only a good answer is kept: an admin may add the secrets any minute
      try {
        const r = await api("/api/constanza/tiktok/status");
        T.status = r.ok ? r.data : { error: errorText(r.data, "The status couldn’t be read."), options: null };
      } catch (e) {
        T.status = { error: String(e), options: null };
      }
      T.statusAt = Date.now();
      renderAll();
    }

    // ---------------------------------------------------------------- the categories: search and browse
    const pickFull = (kind) => kindIds(kind).length >= PER_KIND_MAX;
    // The state change alone (no page): false when the pick is already there or the kind is full.
    function pushPick(kind, item) {
      if (T.picks.some((p) => p.kind === kind && p.id === String(item.id))) return false;
      if (pickFull(kind)) return false;
      T.picks.push({ kind, id: String(item.id), name: item.name, path: item.path || "" });
      return true;
    }

    const fullNote = (n) => `<div class="aud-notice aud-notice--warn"><div class="aud-notice-body">At most ${PER_KIND_MAX} of one kind${n ? ` — ${plural(n, "category was", "categories were")} not added` : ""}.</div></div>`;

    function addPick(kind, item) {
      if (!pushPick(kind, item)) {
        if (pickFull(kind)) $("cz-tt-pick-note").innerHTML = fullNote(0);
        return;
      }
      $("cz-tt-q").value = "";
      hideHits();
      changed();
    }

    function hideHits() {
      const box = $("cz-tt-results");
      box.hidden = true; box.classList.remove("is-pending");
      $("cz-tt-q").setAttribute("aria-expanded", "false");
      $("cz-tt-q").removeAttribute("aria-activedescendant");
      T.hits = []; T.active = -1; T.hitsFor = null;
    }

    // The list on screen is an answer for some text: it may be picked from only while the box still holds that text (a newer search is
    // pending otherwise, and Enter or a click would add a category the person is no longer looking for).
    const hitsAreFresh = () => T.hits.length > 0 && T.hitsFor === $("cz-tt-q").value.trim();

    // The matched part of a name in <mark>: built from escaped pieces, so nothing from TikTok is ever markup.
    function marked(text, q) {
      text = String(text == null ? "" : text);
      const at = q ? text.toLowerCase().indexOf(q.toLowerCase()) : -1;
      if (at < 0 || text.toLowerCase().length !== text.length) return esc(text);
      return `${esc(text.slice(0, at))}<mark>${esc(text.slice(at, at + q.length))}</mark>${esc(text.slice(at + q.length))}`;
    }

    // The list opens downwards over the dock's scrolling body, and a field near the bottom edge (or the sticky action bar) leaves it mostly out of
    // sight: once it has an answer, bring all of it into view - the dock's own interests typeahead does the same.
    const reveal = (box) => { if (box.scrollIntoView) box.scrollIntoView({ block: "nearest" }); };

    // The highlighted option of a typeahead: the class and aria-selected move together, the input names it, and it is always on screen.
    function markOption(box, input, items, active, hits, idPrefix) {
      items.forEach((el, i) => { el.classList.toggle("is-active", i === active); el.setAttribute("aria-selected", String(i === active)); });
      if (active >= 0 && hits[active]) {
        input.setAttribute("aria-activedescendant", `${idPrefix}${active}`);
        const cur = items[active];
        if (active === 0) box.scrollTop = 0;                         // the first option: its group heading is on screen too
        else if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: "nearest" });
      } else input.removeAttribute("aria-activedescendant");
    }
    const markHit = () => markOption($("cz-tt-results"), $("cz-tt-q"), $("cz-tt-results").querySelectorAll(".aud-ta-opt"), T.active, T.hits, "cz-tt-hit-");

    async function searchPicks() {
      const input = $("cz-tt-q");
      const q = input.value.trim();
      if (q.length < 2) { hideHits(); return; }
      const seq = ++T.searchSeq;
      let r;
      try { r = await api(`/api/constanza/tiktok/search?q=${encodeURIComponent(q)}`); } catch (e) { r = { ok: false, data: {} }; }
      // superseded by a newer keystroke, or the person has moved on to something else: the answer is not shown
      if (seq !== T.searchSeq || input.value.trim() !== q || document.activeElement !== input) return;
      const box = $("cz-tt-results");
      box.hidden = false; box.classList.remove("is-pending");
      input.setAttribute("aria-expanded", "true");
      if (!r.ok) {
        T.hits = []; T.active = -1; T.hitsFor = null;
        box.innerHTML = `<div class="aud-ta-note">${esc(errorText(r.data, "The category search isn’t available right now."))}</div>`;
        markHit();
        reveal(box);
        return;
      }
      const found = r.data.results || {};
      T.hits = [];
      const more = [];
      KIND_ORDER.forEach((kind) => {
        const items = found[kind] || [];
        items.slice(0, PER_KIND).forEach((item) => T.hits.push({ kind, item }));        // six of each kind: every kind shows
        if (items.length > PER_KIND) { const n = items.length - PER_KIND; more.push(`${n} more ${KINDS[kind].toLowerCase()}${n === 1 ? "" : "s"}`); }
      });
      T.hitsFor = q;
      T.active = T.hits.length ? 0 : -1;
      box.innerHTML = T.hits.length
        ? T.hits.map((h, i) => (i === 0 || T.hits[i - 1].kind !== h.kind ? `<div class="aud-ta-group" role="presentation">${esc(KIND_GROUPS[h.kind])}</div>` : "")
            + `<div class="aud-ta-opt${i === T.active ? " is-active" : ""}" role="option" id="cz-tt-hit-${i}" data-i="${i}" aria-selected="${i === T.active}"><span class="aud-ta-text"><span class="aud-ta-name">${marked(h.item.name, q)}</span>${h.item.path ? `<span class="aud-ta-path">${esc(h.item.path)}</span>` : ""}</span></div>`).join("")
          + (more.length ? `<div class="aud-ta-note tt-more" role="presentation">And ${esc(more.join(", "))} — add a word to narrow the list${orBrowse()}.</div>` : "")
        : `<div class="aud-ta-note">Nothing in TikTok’s categories matches “${esc(q)}”. Try a simpler word${orBrowse()}.</div>`;
      markHit();
      reveal(box);
    }

    const loading = {};                                             // a list already being fetched is not fetched again by the next keystroke
    function loadCatalog(kind) {
      if (T.catalog[kind]) return Promise.resolve(T.catalog[kind]);
      if (!loading[kind]) {
        loading[kind] = (async () => {
          let r;
          try { r = await api(`/api/constanza/tiktok/targeting?kind=${encodeURIComponent(kind)}`); } catch (e) { r = { ok: false, data: {} }; }
          if (!r.ok) return { error: errorText(r.data, "TikTok’s list isn’t available right now.") };
          const items = r.data.items || [];
          if (items.length) T.catalog[kind] = items;                // a failure, or an empty answer, is not remembered: the next open tries again
          return items;
        })().finally(() => { delete loading[kind]; });
      }
      return loading[kind];
    }

    // ---------------------------------------------------------------- the pop-up: every category, sortable
    // TikTok's limit is 50 PER KIND, which is what the pop-up's `maxPerTab` is for: a kind that is full stops taking ticks (the other kinds
    // stay open), and pushPick() below still refuses the 51st as a last line of defence, exactly as the chips do.
    function browseItems(items) {
      return items.map((i) => ({ id: String(i.id), name: i.name, path: i.path || "", top: !i.path && !!i.has_children }));
    }

    function openBrowse() {
      const opener = $("cz-tt-browse");
      if (!canBrowse()) {                                            // (the button is hidden then; this is for a pop-up that went missing after it was drawn)
        syncBrowse();
        $("cz-tt-pick-note").innerHTML = `<div class="aud-notice aud-notice--warn"><div class="aud-notice-body">The full list isn’t available right now. Use the search box instead.</div></div>`;
        return;
      }
      hideHits();
      window.SegmentBrowser.open({
        title: "Browse TikTok interests & behaviors",
        subtitle: `Tick as many as you like — TikTok takes up to ${PER_KIND_MAX} of each kind. People who match any of the interests you pick are reached.`,
        tabs: BROWSE_TABS.map(([key, label]) => ({ key, label })),
        activeTab: T.tab,
        noun: "categories", nounSingular: "category", applyLabel: "Add to audience",
        searchPlaceholder: "Search by name or category",
        maxPerTab: PER_KIND_MAX, chosenCount: (tab) => kindIds(tab).length,
        maxNote: (tab) => `TikTok allows at most ${PER_KIND_MAX} ${(KINDS[tab] || "category").toLowerCase()} categories at once. Untick one to make room.`,
        load: (tab) => loadCatalog(tab).then((items) => (items.error ? { error: items.error } : browseItems(items))),
        columns: [
          { key: "name", label: "Name" },
          { key: "category", label: "Category", value: (i) => i.path || null, format: (v, i) => v || (i.top ? "Top level" : "—") },
        ],
        isChosen: (tab, item) => T.picks.some((p) => p.kind === tab && p.id === String(item.id)),
        returnFocus: opener,
        onApply: ({ added, removed }) => {
          removed.forEach(({ tab, item }) => { T.picks = T.picks.filter((p) => !(p.kind === tab && p.id === String(item.id))); });
          let refused = 0;
          added.forEach(({ tab, item }) => { if (!pushPick(tab, item)) refused += 1; });
          if (added.length) T.tab = added[0].tab;                    // the pop-up opens where the last pick was made
          changed();
          const said = [];
          if (added.length - refused) said.push(`Added ${plural(added.length - refused, "category", "categories")}`);
          if (removed.length) said.push(`removed ${removed.length}`);
          if (refused) $("cz-tt-pick-note").insertAdjacentHTML("beforeend", fullNote(refused));
          if (announce) announce(`${said.join(", ") || "No change"}${refused ? `; ${refused} not added, at most ${PER_KIND_MAX} of one kind` : ""}.`);
        },
      });
    }

    // ---------------------------------------------------------------- languages
    // TikTok's whole language list: asked when the panel is shown (so the quick picks are ready), once at a time, kept once it is a real answer.
    let langLoading = null;
    function loadLanguages() {
      if (T.languagesAll) return Promise.resolve(T.languagesAll);
      if (!langLoading) {
        T.langStatus = "loading";
        langLoading = (async () => {
          let r;
          try { r = await api("/api/constanza/tiktok/targeting?kind=languages"); } catch (e) { r = { ok: false, data: {} }; }
          const items = r.ok ? (r.data.items || []) : [];
          if (items.length) { T.languagesAll = items; T.langStatus = "ready"; T.langError = ""; return items; }        // a failure, or an empty list, is not remembered: the next try asks again
          T.langStatus = "error";
          T.langError = r.ok ? "TikTok’s language list came back empty. Try again in a moment." : errorText(r.data, "TikTok’s language list isn’t available right now.");
          return { error: T.langError };
        })().finally(() => { langLoading = null; if ($("cz-tt")) { renderLangQuick(); renderHispanicHint(); } });
      }
      return langLoading;
    }

    function hideLangHits() {
      $("cz-tt-lang-results").hidden = true;
      $("cz-tt-lang-q").setAttribute("aria-expanded", "false");
      $("cz-tt-lang-q").removeAttribute("aria-activedescendant");
      T.langHits = []; T.langActive = -1;
    }

    const markLang = () => markOption($("cz-tt-lang-results"), $("cz-tt-lang-q"), $("cz-tt-lang-results").querySelectorAll(".aud-ta-opt"), T.langActive, T.langHits, "cz-tt-lang-");

    async function searchLanguages() {
      const input = $("cz-tt-lang-q"), box = $("cz-tt-lang-results");
      const q = input.value.trim().toLowerCase();
      const say = (html) => {
        box.hidden = false;
        input.setAttribute("aria-expanded", "true");
        box.innerHTML = html;
        T.langHits = []; T.langActive = -1;
        markLang();
        reveal(box);
      };
      if (!T.languagesAll) {
        const res = await loadLanguages();
        if (document.activeElement !== input) return;              // the person moved on while it loaded: nothing opens behind them
        if (res.error) { say(`<div class="aud-ta-note">${esc(res.error)}</div>`); return; }
      }
      if (input.value.trim().toLowerCase() !== q || document.activeElement !== input) return;
      const all = T.languagesAll.filter((l) => !T.languages.some((x) => x.code === l.code) && (!q || l.name.toLowerCase().includes(q) || l.code.toLowerCase() === q));
      T.langHits = all.slice(0, 60);
      T.langActive = T.langHits.length ? 0 : -1;
      box.hidden = false;
      input.setAttribute("aria-expanded", "true");
      box.innerHTML = T.langHits.length
        ? T.langHits.map((l, i) => `<div class="aud-ta-opt${i === T.langActive ? " is-active" : ""}" role="option" id="cz-tt-lang-${i}" data-i="${i}" aria-selected="${i === T.langActive}"><span class="aud-ta-text"><span class="aud-ta-name">${marked(l.name, q)}</span></span><span class="aud-ta-size">${esc(l.code)}</span></div>`).join("")
          + (all.length > T.langHits.length ? `<div class="aud-ta-note tt-more" role="presentation">${all.length - T.langHits.length} more — type to narrow the list.</div>` : "")
        : `<div class="aud-ta-note">No language matches “${esc(q)}”.</div>`;
      markLang();
      reveal(box);
    }

    // The state change alone: false when the language is already there or the list is full.
    function pushLanguage(l) {
      if (T.languages.some((x) => x.code === l.code) || T.languages.length >= LANGUAGES_MAX) return false;
      T.languages.push({ code: l.code, name: l.name });
      return true;
    }

    function addLanguage(l) {
      if (!pushLanguage(l)) return;
      $("cz-tt-lang-q").value = "";
      hideLangHits();
      changed();
    }

    function openLanguageBrowse() {
      const opener = $("cz-tt-lang-browse");
      if (!canBrowse()) {
        syncBrowse();
        if (announce) announce("The full list of languages isn’t available right now. Use the search box instead.");
        return;
      }
      hideLangHits();
      window.SegmentBrowser.open({
        title: "Browse TikTok languages",
        subtitle: `People are reached by the language their TikTok app is set to. Pick up to ${LANGUAGES_MAX}.`,
        noun: "languages", nounSingular: "language", applyLabel: "Add languages", searchPlaceholder: "Search languages",
        load: () => loadLanguages().then((items) => (items.error ? { error: items.error } : items.map((l) => ({ id: String(l.code), name: l.name, code: String(l.code) })))),
        columns: [{ key: "name", label: "Language" }, { key: "code", label: "TikTok code" }],
        isChosen: (_tab, item) => T.languages.some((l) => l.code === item.id),
        chosenCount: () => T.languages.length, max: LANGUAGES_MAX,
        returnFocus: opener,
        onApply: ({ added, removed }) => {
          spanishRemoved(removed.map(({ item }) => item.id));
          removed.forEach(({ item }) => { T.languages = T.languages.filter((l) => l.code !== item.id); });
          added.forEach(({ item }) => pushLanguage({ code: item.id, name: item.name }));
          changed();
          if (announce) announce(`Languages: ${T.languages.map((l) => l.name).join(", ") || "any"}.`);
        },
      });
    }

    // A quick pick adds the languages it stands for (those not there yet), or - when they are all there - takes them out again.
    function quickLanguages(key) {
      const pick = LANG_PICKS.find((p) => p.key === key), langs = pick ? pickLanguages(pick) : [];
      if (!langs.length) return false;
      if (langs.every((l) => T.languages.some((x) => x.code === l.code))) {
        spanishRemoved(langs.map((l) => l.code));
        T.languages = T.languages.filter((x) => !langs.some((l) => l.code === x.code));
      } else langs.forEach(pushLanguage);
      return true;
    }

    // ---------------------------------------------------------------- Clear (one per section)
    const CLEARS = {
      who: () => { T.age = []; T.gender = ""; },
      lang: () => { spanishRemoved(T.languages.map((l) => l.code)); T.languages = []; },
      aud: () => { T.picks = []; },
      more: () => { Object.assign(T, { spending: "", income: [], os: "", networks: [], placements: ["PLACEMENT_TIKTOK"], objective: "reach", videoPeriod: 15 }); },
    };
    const CLEARED = { who: "Who", lang: "Language", aud: "Audience", more: "More targeting" };
    // where the focus goes when its Clear button disappears (it hides at zero): the first control of the section
    const CLEAR_FOCUS = {
      who: () => $("cz-tt-age").querySelector("button"), lang: () => $("cz-tt-lang-q"), aud: () => $("cz-tt-q"),
      more: () => $("cz-tt-more").querySelector("summary"),
    };

    // ---------------------------------------------------------------- wiring
    // A chip's × is gone once the chip is removed: the focus goes to the next chip's ×, else the one before, else the box the chips belong to.
    function refocus(chipsId, i, fallbackId) {
      const xs = $(chipsId).querySelectorAll(".aud-chip-x");
      (xs[i] || xs[xs.length - 1] || $(fallbackId)).focus({ preventScroll: true });
    }

    function bind() {
      const root = $("cz-tt");
      renderAll();
      root.addEventListener("click", (e) => {
        const b = e.target.closest("[data-tt]");
        if (!b || !root.contains(b)) return;
        const v = b.dataset.v;
        const hadFocus = document.activeElement === b;
        let after = null;                                            // what to do once the screen is redrawn, if the pressed button had the focus
        switch (b.dataset.tt) {
          case "age": T.age = toggled(T.age, v); break;
          case "age-preset": { const ids = v.split(","); T.age = sameSet(T.age, ids) ? [] : ids; break; }
          case "gender": T.gender = v; break;
          case "spending": T.spending = v; break;
          case "income": T.income = toggled(T.income, v); break;
          case "os": T.os = v; break;
          case "network": T.networks = toggled(T.networks, v); break;
          case "place":
            if (v === "PLACEMENT_TIKTOK" || b.disabled) return;      // TikTok is always a placement: it is what the product runs on
            T.placements = toggled(T.placements, v);
            break;
          case "objective": T.objective = v; break;
          case "period": T.videoPeriod = Number(v); break;
          case "fix-objective":
            T.objective = "traffic";
            after = () => {
              // the warning (and the button that was pressed) is gone: the focus goes to the objective that just changed, or - when that part of the
              // panel is folded away - to the box the person was filling in; either way the change is announced
              const traffic = $("cz-tt-obj").querySelector('button[data-v="traffic"]');
              (traffic && $("cz-tt-more").open ? traffic : $("cz-tt-q")).focus({ preventScroll: true });
              if (announce) announce("The campaign objective is now Traffic.");
            };
            break;
          case "rm-pick": { const i = Number(b.dataset.i); T.picks.splice(i, 1); after = () => refocus("cz-tt-chips", i, "cz-tt-q"); break; }
          case "rm-lang": { const i = Number(b.dataset.i); spanishRemoved([T.languages[i].code]); T.languages.splice(i, 1); after = () => refocus("cz-tt-lang-chips", i, "cz-tt-lang-q"); break; }
          case "lang-quick":
            if (!quickLanguages(v)) return;
            break;                                                    // the pill is flipped in place: it keeps the focus
          case "lang-hispanic": {
            const es = spanish();
            if (!es) return;
            pushLanguage(es);
            after = () => { const pill = $("cz-tt-lang-quick").querySelector('button[data-v="es"]'); (pill && !pill.disabled ? pill : $("cz-tt-lang-q")).focus({ preventScroll: true }); };
            break;
          }
          case "lang-dismiss":
            T.hispanicDismissed = hispanicKey();
            changed();
            if (hadFocus) $("cz-tt-lang-q").focus({ preventScroll: true });
            return;
          case "lang-retry":
            loadLanguages();
            renderLangQuick();                                        // the "Try again" button is replaced by the loading line: the focus goes to the language box
            if (hadFocus) $("cz-tt-lang-q").focus({ preventScroll: true });
            return;
          case "lang-browse": openLanguageBrowse(); return;
          case "browse": openBrowse(); return;
          case "clear": {
            if (!CLEARS[v]) return;                                   // ("More targeting" folds, but its Clear sits beside the <details>, not in the toggle)
            CLEARS[v]();
            after = () => { const to = CLEAR_FOCUS[v](); if (to) to.focus({ preventScroll: true }); if (announce) announce(`${CLEARED[v]} cleared.`); };
            break;
          }
          default: return;
        }
        changed();
        if (hadFocus && after) after();
      });

      // A popup closes when the focus leaves the box and its list (Tab, or a click elsewhere): left open, it would still be on screen
      // for Escape to find, and Escape would then close the whole dock. Choosing an option with the mouse must not move the focus away first.
      const leaves = (e, ...ids) => !(e.relatedTarget && ids.some((id) => $(id).contains(e.relatedTarget)));
      $("cz-tt-results").addEventListener("mousedown", (e) => e.preventDefault());
      $("cz-tt-lang-results").addEventListener("mousedown", (e) => e.preventDefault());

      // categories search
      $("cz-tt-q").addEventListener("input", () => {
        clearTimeout(searchTimer);
        if (T.hits.length && T.hitsFor !== $("cz-tt-q").value.trim()) {              // the list on screen is for older text: nothing in it can be picked now
          T.active = -1;
          $("cz-tt-results").classList.add("is-pending");
          markHit();
        }
        searchTimer = setTimeout(searchPicks, 220);
      });
      $("cz-tt-q").addEventListener("focusout", (e) => { if (leaves(e, "cz-tt-q", "cz-tt-results")) { clearTimeout(searchTimer); hideHits(); } });
      $("cz-tt-q").addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          if (!hitsAreFresh()) return;
          e.preventDefault();
          T.active = (T.active + (e.key === "ArrowDown" ? 1 : -1) + T.hits.length) % T.hits.length;
          markHit();
        } else if (e.key === "Enter") {
          e.preventDefault();
          if (hitsAreFresh() && T.hits[T.active]) addPick(T.hits[T.active].kind, T.hits[T.active].item);
          else if ($("cz-tt-q").value.trim().length >= 2) { clearTimeout(searchTimer); searchPicks(); }          // ask for THIS text now
        } else if (e.key === "Escape" && !$("cz-tt-results").hidden) { e.preventDefault(); e.stopPropagation(); hideHits(); }
      });
      $("cz-tt-results").addEventListener("click", (e) => {
        const el = e.target.closest(".aud-ta-opt");
        if (el && hitsAreFresh() && T.hits[Number(el.dataset.i)]) { const h = T.hits[Number(el.dataset.i)]; addPick(h.kind, h.item); }
      });

      // languages: the list opens on a click, on typing or on ArrowDown - not on every Tab into the field
      $("cz-tt-lang-q").addEventListener("click", searchLanguages);
      $("cz-tt-lang-q").addEventListener("input", searchLanguages);
      $("cz-tt-lang-q").addEventListener("focusout", (e) => { if (leaves(e, "cz-tt-lang-q", "cz-tt-lang-results")) hideLangHits(); });
      $("cz-tt-lang-q").addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          if (!T.langHits.length) { if (e.key === "ArrowDown") { e.preventDefault(); searchLanguages(); } return; }
          e.preventDefault();
          T.langActive = (T.langActive + (e.key === "ArrowDown" ? 1 : -1) + T.langHits.length) % T.langHits.length;
          markLang();
        } else if (e.key === "Enter") {
          e.preventDefault();
          if (T.langHits[T.langActive]) addLanguage(T.langHits[T.langActive]);
        } else if (e.key === "Escape" && !$("cz-tt-lang-results").hidden) { e.preventDefault(); e.stopPropagation(); hideLangHits(); }
      });
      $("cz-tt-lang-results").addEventListener("click", (e) => {
        const el = e.target.closest(".aud-ta-opt");
        if (el && T.langHits[Number(el.dataset.i)]) addLanguage(T.langHits[Number(el.dataset.i)]);
      });
    }

    function reset() {
      Object.assign(T, fresh(), { hits: [], hitsFor: null, active: -1, langHits: [], langActive: -1, tab: "general_interest", hispanicDismissed: "" });
      if (!$("cz-tt")) return;
      ["cz-tt-q", "cz-tt-lang-q"].forEach((id) => { if ($(id)) $(id).value = ""; });
      hideHits(); hideLangHits();
      renderAll();
    }

    // ---------------------------------------------------------------- the result
    const money = (n) => num(n);

    // The planning rule the server applied: the lower end of TikTok's audience x conservative / mid / upper (platform_scenarios.py).
    function ruleOf(source) {
      const sc = source && source.scenarios;
      const m = (sc && sc.multipliers) || { conservative: source && source.impressions_per_person, mid: source && source.impressions_per_person, upper: source && source.impressions_per_person };
      return { m, short: `lower-end audience × ${m.mid} (${m.conservative} – ${m.upper})`,
        long: `the lower end of that audience × ${m.conservative} (conservative), × ${m.mid} (mid, the default) or × ${m.upper} (upper)` };
    }

    function headlineHtml(data, m) {
      const tt = data.tiktok, a = tt.audience;
      const rule = ruleOf(tt);
      const flight = m.months > 1 ? `<div class="cz-metric"><span class="cz-mlabel">Flight total · ${m.months} mo</span><span class="cz-mvalue">${money(m.total.mid)}</span><span class="cz-mrange">${compact(m.total.low)} – ${compact(m.total.high)}</span></div>` : "";
      return `<div class="cz-headline">
        <div class="cz-metric main"><span class="cz-mlabel">Audience size · lower end, TikTok accounts 18+</span><span class="cz-mvalue">${money(a.low)}</span><span class="cz-mrange">the base of the scenarios · range ${compact(a.low)} – ${compact(a.high)}</span></div>
        <div class="cz-metric"><span class="cz-mlabel">Impressions · per month</span><span class="cz-mvalue">${money(m.month.mid)}</span><span class="cz-mrange">${compact(m.month.low)} – ${compact(m.month.high)} · ${esc(rule.short)}</span></div>
        ${flight}
      </div>
      ${stageHtml(tt)}`;
    }

    function stageHtml(tt) {
      if (!tt.stage) return "";
      const pills = [1, 2, 3, 4].map((s) => `<span class="cz-stage ${tt.stage === s ? "on s" + s : ""}">${STAGES[s]}</span>`).join("");
      return `<div class="cz-stagebar" role="img" aria-label="TikTok’s verdict on this audience: ${esc(tt.stage_label || "")}">${pills}</div>
        <p class="cz-note">TikTok: <strong>${esc(tt.stage_label || "")}</strong> — ${esc(tt.stage_note || "")}.</p>`;
    }

    function bodyHtml(data, m, asked) {
      const tt = data.tiktok;
      const parts = (asked && asked.method && asked.method.summary) || [];
      const asks = parts.concat([`Objective: ${tt.objective}`, `Placement: ${(tt.placements || []).map((p) => (p === "PLACEMENT_PANGLE" ? "Pangle" : "TikTok")).join(" + ")}`]);
      const where = (tt.locations || []).map((l) => `<span class="cz-chip cz-ok" title="TikTok ID ${esc(l.id)}">${esc(l.name)} <small>${esc(TYPE_LABEL[l.geo_type] || l.geo_type)}</small></span>`).join("");
      return `<div class="cz-section-title">What was asked of TikTok</div>
        <div class="cz-chips">${asks.map((p) => `<span class="cz-chip">${esc(p)}</span>`).join("")}</div>
        <div class="cz-section-title">Places sent to TikTok</div>
        <div class="cz-chips">${where || `<span class="cz-note">none</span>`}</div>`;
    }

    function aboutHtml(data) {
      const tt = data.tiktok;
      const items = [
        `Asked of TikTok’s audience-size estimator (the one behind Ads Manager’s audience panel)${tt.request_id ? `; request ${esc(tt.request_id)}` : ""}${tt.cached ? "; answered from the last ten minutes, not asked again" : ""}.`,
        `The audience is TikTok’s own count of the accounts that match; it leaves out under-18s. Monthly impressions are ${esc(ruleOf(tt).long)} — Entravision’s planning scenarios, not a delivery forecast.`,
        `The Avails step takes the mid scenario as the monthly impressions, and the lower-end audience as est. uniques, so its frequency reads ${esc(String(ruleOf(tt).m.mid))}.`,
      ];
      return `<details class="cz-details"><summary>About this estimate</summary><ul class="cz-list">${items.map((i) => `<li>${i}</li>`).join("")}</ul></details>`;
    }

    // The Step 05 per-line card: the same facts, in the table the other products use.
    function cardHtml(data) {
      const tt = data.tiktok, a = tt.audience;
      return `<p class="constanza-uniques">Audience size, lower end (TikTok accounts, 18+): <strong>${num(a.low)}</strong> <small>(range ${num(a.low)} – ${num(a.high)})</small>${tt.stage_label ? ` · TikTok: ${esc(tt.stage_label)}` : ""}</p>
        <p class="cz-note">Impressions are the ${esc(ruleOf(tt).short)} a month. This is the geography alone — <strong>Refine in Constanza</strong> to set age, gender and interests.</p>`;
    }

    function summaryLines(data, asked) {
      const tt = data.tiktok, a = tt.audience;
      const lines = [`Audience size, lower end (TikTok accounts, 18+): ${num(a.low)} (range ${num(a.low)} – ${num(a.high)})${tt.stage_label ? `; TikTok says: ${tt.stage_label}` : ""}`,
        `Impressions = ${ruleOf(tt).short} per month (mid is the default)`];
      const parts = (asked && asked.method && asked.method.summary) || [];
      if (parts.length) lines.push(`Asked of TikTok: ${parts.join(" · ")}`);
      if ((tt.locations || []).length) lines.push(`Places sent to TikTok: ${tt.locations.map((l) => l.name).join("; ")}`);
      return lines;
    }

    // Nothing is refused here: the server says "TikTok isn't connected yet" itself, and its answer is always current, where a status read
    // when the dock opened is not (an admin may have added the secrets since).
    function canPull() {
      return { ok: true };
    }

    function onError(status) {
      if (status === 503) loadStatus(true);                       // the connection changed under us: show it
    }

    function onResult() {
      if (!T.status || !T.status.configured) loadStatus(true);    // an estimate came back, so TikTok IS connected: a banner that says otherwise is out of date
    }

    // Where the dock shows TikTok in its lists and on a result (the dock reads these instead of naming TikTok), and what an Insert files under
    // entry.geo_resolution: TikTok has no per-market rows, so the places it sent stand in for the markets.
    function badgeHtml() {
      return `<span class="cz-badge cz-reported" title="Asked of TikTok's own audience-size estimator just now">TikTok API · live</span>`;
    }

    function geoInfo(data) {
      const fromAreas = [...new Set((data.by_area || []).map((d) => d.dma).filter(Boolean))];
      const fromTikTok = [...new Set(((data.tiktok && data.tiktok.locations) || []).map((l) => l.source).filter(Boolean))];
      return {
        method: data.geo_method,
        dmas: fromAreas.length ? fromAreas : fromTikTok,
        unmatched_tokens: data.unmatched_geo_tokens || [],
        win_rate: null,                                           // (TikTok has no win rate and no Census cut)
        hispanic_share: null,
      };
    }

    return {
      goLabel: "Estimate audience →",
      channelLabel: "TikTok · live audience estimate",
      sourceLabel: "Live from TikTok",
      caps: { autoPull: "geo" },                                  // Step 05 pulls it from the proposal's geography, as it always has
      badgeHtml, geoInfo,
      formHtml, bind, reset, canPull, onError, onResult,
      // Shown: the connection is (re)checked and TikTok's language list is fetched now, so the quick picks are ready before anyone asks for them.
      // The dock sets which line it is open for just AFTER it selects the product, so the Hispanic suggestion is read once more a moment later.
      onSelect: () => {
        renderAll(); loadStatus(false); loadLanguages();
        if (typeof setTimeout === "function") setTimeout(() => { if ($("cz-tt")) renderHispanicHint(); }, 0);
      },
      requestPart: () => ({ tiktok: targeting() }),
      context: () => ({ summary: summaryParts() }),
      headlineHtml, bodyHtml, aboutHtml, cardHtml, summaryLines,
      _pure: {
        FALLBACK, targeting, summaryParts, stageHtml, headlineHtml, bodyHtml, aboutHtml, cardHtml, summaryLines, canPull, T, pushPick, selectedCount, moreCount,
        toggled, formHtml, whoSummary, ageSummary, moreSummary, namesSummary, agePresets, findLanguage, pickLanguages, targetsHispanic, browseItems, LANG_PICKS,
      },
    };
  });
})();
